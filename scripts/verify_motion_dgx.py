#!/usr/bin/env python3
"""Opt-in real Development motion/API/storage probe. Synthetic verified identity.

No Firebase Auth account or client credential is created. The original permitted
clip is unchanged. Exact fixtures are removed only after CPU/GPU writers quiesce.
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src'), str(ROOT/'infra/dgx'),
               str(ROOT/'runtime/skill_lab'), str(ROOT/'scripts')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    args = parser.parse_args()
    if os.getenv('FOOTYLEVELS_LIVE_MOTION_TEST') != '1':
        raise SystemExit('Set FOOTYLEVELS_LIVE_MOTION_TEST=1 to opt in')
    from cloud_config import load
    load('dev')
    assert os.environ['FIREBASE_PROJECT_ID'] == 'footylevels-dev-20261009'
    from google.cloud import firestore
    from fastapi.testclient import TestClient
    from api.cloud import create_app
    from backend.model_bridge import send, receive
    from reconstruction_adapter import validate_sequence
    from footylevels.authentication import Identity
    from footylevels.assets import SupabaseAssets
    from footylevels.media_jobs import FirestoreMediaJobs
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.records import ReconstructionRecord, utc_now
    from footylevels.repositories import RecordConflict
    from verify_media_worker_dgx import cleanup
    base = Path.home()/'.local/share/footylevels'
    bridge = base/'model-bridge'
    staging = base/'reconstruction-worker/dev'
    ffmpeg = Path.home()/'skill-lab/.media/bin/ffmpeg'
    adapter = [sys.executable, str(ROOT/'infra/dgx/reconstruction_adapter.py'),
        '--socket', str(bridge/'inference.sock'), '--ffmpeg', str(ffmpeg)]
    def health():
        with socket.socket(socket.AF_UNIX) as stream:
            stream.settimeout(10)
            stream.connect(str(bridge/'inference.sock'))
            send(stream, {'op': 'health'})
            return receive(stream)
    before = health()
    assert before['motionEnabled'] and before['activeRequests'] == 0
    client = firestore.Client.from_service_account_json(os.environ['GOOGLE_APPLICATION_CREDENTIALS'],
        project=os.environ['FIREBASE_PROJECT_ID'])
    jobs, media_jobs = FirestoreReconstructionJobs(client), FirestoreMediaJobs(client)
    storage = SupabaseAssets(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SECRET_KEY'])
    uid = 'fp39-model-'+uuid.uuid4().hex
    class FixtureVerifier:
        def verify(self, token):
            assert token == uid  # This isolated in-process test app is never served.
            return Identity(uid, 'Temporary motion verification')
    api = TestClient(create_app(jobs, FixtureVerifier(), assets=storage, uploads=media_jobs))
    headers = {'Authorization': 'Bearer '+uid}
    recovery = base/'cloud-api/fp39-last-fixture.json'
    with recovery.open('x') as saved:
        os.chmod(recovery, 0o600)
        saved.write(json.dumps({'ownerId': uid}))
    project_id, probe_jobs = None, set()
    evidence = {'ticket': 'FP-39', 'environment': 'dev', 'gpuUsed': True,
        'realFirebaseLogin': False, 'fullIosEndToEnd': False, 'httpIdentity': 'synthetic verified fixture'}
    def quiescent():
        for directory in staging.glob('reconstruction-*'):
            path = directory/'job.json'
            try:
                if path.exists() and json.loads(path.read_text()).get('ownerId') == uid:
                    return False
            except (OSError, ValueError):
                return False
        return health()['activeRequests'] == 0
    try:
        with tempfile.TemporaryDirectory(prefix='fp39-verification-') as temporary:
            directory = Path(temporary)
            source = directory/'source.mp4'
            subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', str(args.fixture),
                '-t', '2', '-an', '-c:v', 'libx264', '-threads', '2', '-crf', '25', str(source)],
                check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert api.post('/api/me/bootstrap', headers=headers).status_code == 200
            project = api.post('/api/projects', headers=headers, json={'title': 'FP39 isolated motion fixture', 'source': 'gallery'})
            assert project.status_code == 201
            project_id = project.json()['id']
            recovery.write_text(json.dumps({'ownerId': uid, 'projectId': project_id}))
            upload = media_jobs.begin(uid, project_id, 1, source.stat().st_size,
                hashlib.sha256(source.read_bytes()).hexdigest(), 'video/mp4', uuid.uuid4().hex)
            storage.put_file(upload.asset, source)
            media_jobs.commit_upload(uid, upload.id)
            deadline = time.monotonic()+240
            while jobs.get_project(uid, project_id).state == 'preparing' and time.monotonic() < deadline:
                time.sleep(2)
            ready = jobs.get_project(uid, project_id)
            assert ready.state == 'ready' and ready.prepared_media
            path = f'/api/projects/{project_id}/reconstructions/motion'
            body = {'inputRevision': ready.input_revision, 'segment': {'startFrame': 15, 'endFrame': 45}}
            response = api.post(path, headers=headers, json=body)
            assert response.status_code == 202
            job_id = response.json()['id']
            probe_jobs.add(job_id)
            abandoned = jobs.claim(job_id, 'fp39-abandoned-probe')
            assert abandoned and abandoned.attempt_count == 1
            client.collection('jobs').document(job_id).update({'leaseExpiresAt': utc_now()-timedelta(seconds=1)})
            print(json.dumps({'phase': 'awaiting-real-motion'}), flush=True)
            deadline = time.monotonic()+900
            job = jobs.get_job(uid, job_id)
            while job.status in {'queued', 'running', 'retrying'} and time.monotonic() < deadline:
                time.sleep(2)
                job = jobs.get_job(uid, job_id)
            assert job.status in {'succeeded', 'partial'} and job.attempt_count == 2, job.status
            result = jobs.get_reconstruction(uid, job_id)
            assert result.mode == 'motion' and result.requested_frame_count == 30
            assert result.source_asset == ready.prepared_media.video
            assert jobs.get_project(uid, project_id).video == upload.asset
            assert api.get('/api/jobs/'+job_id, headers=headers).json()['reconstructionId'] == result.id
            assert api.get('/api/reconstructions/'+result.id, headers=headers).status_code == 200
            grant = api.post('/api/reconstructions/'+result.id+'/assets', headers=headers)
            assert grant.status_code == 200 and len(grant.json()['assets']) == len(result.artifacts)
            # Do not print bearer URLs. Verify downloaded bytes and actual buffers.
            for artifact in result.artifacts:
                storage.verify(artifact)
                storage.download_file(artifact, directory/artifact.object_key.rsplit('/', 1)[-1])
            sequence = json.loads((directory/'sequence.json').read_text())
            names = {asset.object_key.rsplit('/', 1)[-1] for asset in result.artifacts}
            assert validate_sequence(sequence, directory, names, 30, result.contract.topology_version) == result.reconstructed_frame_count
            assert sequence['sourceTimeOffset'] == .5 and sequence['frames'][0]['sourceTimestamp'] == .5
            assert sequence['vertexCount'] == 18439 and sequence['faceCount'] == 36874
            assert len(sequence['previews']) == 6 and sequence['groundAligned'] is False
            completed = health()
            assert completed['motionCalls'] == before['motionCalls']+1
            with tempfile.TemporaryDirectory(prefix='reconstruction-', dir=staging) as replay_folder:
                replay = Path(replay_folder)
                (replay/'artifacts').mkdir()
                storage.download_file(job.source_asset, replay/'input.mp4')
                (replay/'job.json').write_text(job.model_dump_json(by_alias=True,
                    exclude={'lease_token', 'worker_id', 'lease_expires_at'}))
                subprocess.run([*adapter, str(replay)], env={'PATH': os.defpath, 'HOME': str(replay)},
                    check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                repeated = ReconstructionRecord.model_validate_json((replay/'result.json').read_bytes())
                assert repeated.artifacts == result.artifacts
                assert health()['motionCalls'] == completed['motionCalls']
            assert api.post(path, headers=headers, json=body).json()['id'] == job_id
            assert jobs.claim(job_id, 'duplicate-after-completion') is None
            try: jobs.checkpoint(abandoned)
            except RecordConflict: pass
            else: raise AssertionError('Old claim retained publication rights')
            assert jobs.publish_reconstruction(result, 'lost-ack-replay') == result
            evidence.update({'runningDispatcherVerified': True, 'abandonedClaimRecovered': True, 'attemptCount': 2,
                'bodyFrames': result.reconstructed_frame_count, 'missingFrames': result.missing_frame_count,
                'outputChecksumsVerified': len(result.artifacts), 'previews': 6,
                'verticesPerBody': 18439, 'facesPerBody': 36874, 'sourceTimeOffset': .5,
                'cachedReplayAddedInference': False, 'oldLeaseRejected': True, 'duplicateClaimsIgnored': True,
                'completionReplayVerified': True, 'privateAssetGrantVerified': True, 'originalSourcePreserved': True,
                'engine': result.engine.model_dump(mode='json', by_alias=True), 'topologyVersion': result.contract.topology_version})
            print(json.dumps({'phase': 'motion-published-and-replay-verified'}), flush=True)
            # Cancel after SAM 3 has actually inferred its seed, during propagation.
            cancel_body = {**body, 'segment': {'startFrame': 0, 'endFrame': 30}}
            cancel = api.post(path, headers=headers, json=cancel_body)
            assert cancel.status_code == 202
            cancel_id = cancel.json()['id']
            probe_jobs.add(cancel_id)
            deadline = time.monotonic()+420
            observed = False
            while time.monotonic() < deadline:
                for identity in (bridge/'motion-cache').glob('*/request.json'):
                    if json.loads(identity.read_text()).get('jobId') == cancel_id:
                        log = identity.parent/'motion/segment_video.py.log'
                        if log.exists() and 'Shot 0: person' in log.read_text()[-65536:]:
                            observed = True
                            break
                if observed: break
                if jobs.get_job(uid, cancel_id).status in {'failed', 'succeeded', 'partial'}: break
                time.sleep(.1)
            assert observed, 'Real segmentation seed was not observed before cancellation'
            assert api.post('/api/jobs/'+cancel_id+'/cancel', headers=headers).json()['status'] == 'cancelled'
            deadline = time.monotonic()+250
            while not quiescent() and time.monotonic() < deadline:
                time.sleep(.5)
            assert quiescent()
            assert not client.collection('reconstructions').document(cancel_id).get().exists
            assert jobs.get_project(uid, project_id).active_job_id is None
            evidence.update({'realInFlightMotionCancellationVerified': True, 'cancelledResultNotPublished': True,
                'adapterAndGpuStagesStopped': True, 'modelQuiescentAfterCancellation': True, 'passed': True})
    finally:
        if project_id:
            for job_id in probe_jobs:
                try: jobs.cancel_job(uid, job_id)
                except Exception: pass
            deadline = time.monotonic()+250
            while not quiescent() and time.monotonic() < deadline:
                time.sleep(.5)
            if not quiescent():
                raise ValueError('Writers still active; retain private recovery evidence')
            evidence['fixtureCleanup'] = cleanup(client, media_jobs, storage, uid, project_id)
        else:
            client.collection('users').document(uid).delete()
        for identity in (bridge/'motion-cache').glob('*/request.json'):
            if json.loads(identity.read_text()).get('jobId') in probe_jobs:
                shutil.rmtree(identity.parent)
        recovery.unlink()
        client.close()
    print(json.dumps(evidence), flush=True)


if __name__ == '__main__':
    main()
