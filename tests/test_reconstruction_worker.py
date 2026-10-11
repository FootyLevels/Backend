"""Execution boundary tests: actual child process, lease loss and private files."""
import json
import os
from pathlib import Path
import sys
from datetime import timedelta

import pytest

from footylevels.assets import StorageUnavailable
from footylevels.records import AssetRef, JobRecord, utc_now
from footylevels.reconstruction_worker import run_job
from footylevels.repositories import RecordConflict


ADAPTER = '''
import hashlib, json, os, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
job = json.loads((root / 'job.json').read_text())
assert 'leaseToken' not in job and 'workerId' not in job
assert not any(k.startswith(('SUPABASE', 'FIREBASE', 'GOOGLE')) for k in os.environ)
assert (root / 'input.mp4').read_bytes() == b'fixture-clip'
MODE
payload = b'fixture-mesh'
file = root / 'artifacts' / 'model.glb'
file.write_bytes(payload)
asset = {'bucket': 'private-media', 'objectKey': f"users/{job['ownerId']}/projects/{job['projectId']}/reconstruction/{job['id']}/{job['attemptCount']}/model.glb", 'sha256': hashlib.sha256(payload).hexdigest(), 'byteSize': len(payload), 'contentType': 'model/gltf-binary'}
result = {'id': job['id'], 'jobId': job['id'], 'ownerId': job['ownerId'], 'projectId': job['projectId'], 'inputRevision': job['inputRevision'], 'profileVersion': job['profileVersion'], 'sourceAsset': job['sourceAsset'], 'mode': 'motion', 'outcome': 'ready', 'engine': {'modelName': 'fixture', 'upstreamCommit': 'c'*40, 'checkpointHash': 'd'*64, 'configHash': 'e'*64}, 'contract': {'topologyVersion': 'fixture-v1', 'encoding': 'glb'}, 'artifacts': [asset], 'requestedFrameCount': 1, 'reconstructedFrameCount': 1, 'missingFrameCount': 0}
MUTATE
(root / 'result.json').write_text(json.dumps(result))
'''


class Jobs:
    def __init__(self, lose=False):
        self.stages, self.reserved, self.published, self.failed = [], [], [], []
        self.lose = lose
    def checkpoint(self, claim, stage=None):
        if stage is None and self.lose:
            raise RecordConflict()
        self.stages.append(stage)
    def reserve_outputs(self, claim, assets): self.reserved.extend(assets)
    def publish_reconstruction(self, result, token):
        assert token == 'secret-lease'
        self.published.append(result)
    def fail(self, claim, code, retry): self.failed.append(code)


class Storage:
    def __init__(self): self.uploaded, self.verified = [], []
    def download_file(self, source, path): path.write_bytes(b'fixture-clip')
    def put_file(self, asset, path):
        assert path.read_bytes() == b'fixture-mesh'
        self.uploaded.append(asset)
        self.verify(asset)
    def verify(self, asset): self.verified.append(asset)


@pytest.fixture
def claim():
    return JobRecord(id='j'*64, owner_id='fixture-owner', project_id='fixture-project', kind='reconstructMotion',
        input_revision=2, input_hash='b'*64, profile_version='fixture-v1', status='running', stage='downloadingInput',
        source_asset=AssetRef(bucket='private-media', object_key='users/fixture-owner/projects/fixture-project/source/video.mp4',
            sha256='a'*64, byte_size=12, content_type='video/mp4'),
        lease_token='secret-lease', attempt_count=1, lease_expires_at=utc_now()+timedelta(minutes=5))


def adapter(tmp_path, mode='', mutate=''):
    path = tmp_path / 'adapter.py'
    path.write_text(ADAPTER.replace('MODE', mode).replace('MUTATE', mutate))
    staging = tmp_path / 'staging'
    staging.mkdir()
    return [sys.executable, str(path)], staging


def test_adapter_receives_no_secrets_and_parent_verifies_before_publication(tmp_path, claim, monkeypatch):
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'must-not-cross-process')
    command, root = adapter(tmp_path)
    jobs, store = Jobs(), Storage()
    assert run_job(jobs, store, claim, command, root) == 'ready'
    assert len(jobs.published) == 1
    assert jobs.reserved == store.uploaded == store.verified == list(jobs.published[0].artifacts)
    assert [s for s in jobs.stages if s] == ['downloadingInput', 'generatingBodies', 'packingAssets', 'publishingAssets']
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('mutate', [
    "result['ownerId'] = 'someone-else'",
    "asset['sha256'] = 'f'*64",
    "asset['objectKey'] = asset['objectKey'].replace('/1/', '/2/')",
    "file.unlink(); file.symlink_to(root / 'input.mp4')",
])
def test_bad_adapter_output_never_reserves_uploads_or_publishes(tmp_path, claim, mutate):
    command, root = adapter(tmp_path, mutate=mutate)
    jobs, store = Jobs(), Storage()
    assert run_job(jobs, store, claim, command, root) == 'adapter-failed'
    assert not jobs.reserved and not store.uploaded and not jobs.published
    assert jobs.failed == ['reconstruction_failed']
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('reason', ['cancel', 'timeout'])
def test_stalled_adapter_is_killed_and_reaped_before_staging_cleanup(tmp_path, claim, reason):
    pid_file = tmp_path / 'child.pid'
    command, root = adapter(tmp_path, mode=f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)")
    jobs, store = Jobs(lose=reason == 'cancel'), Storage()
    outcome = run_job(jobs, store, claim, command, root, heartbeat_seconds=0.2, command_timeout=0.5)
    assert outcome == ('superseded' if reason == 'cancel' else 'adapter-failed')
    assert not store.uploaded and not jobs.published
    child_pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError): os.kill(child_pid, 0)
    assert list(root.iterdir()) == []


def test_network_loss_preserves_retryable_durable_attempt(tmp_path, claim):
    command, root = adapter(tmp_path)
    jobs, store = Jobs(), Storage()
    def offline(*args): raise StorageUnavailable()
    store.put_file = offline
    assert run_job(jobs, store, claim, command, root) == 'retrying'
    assert jobs.failed == ['model_unavailable'] and not jobs.published
    assert list(root.iterdir()) == []
