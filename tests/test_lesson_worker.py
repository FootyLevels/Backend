"""One buildLesson attempt with fake jobs, storage, GPU adapter and provider; no cloud or GPU."""
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('jsonschema', reason='Install the lessons extra')

from footylevels import lesson_worker
from footylevels.assets import StorageUnavailable
from footylevels.model_requests import lesson_input_hash
from footylevels.records import AssetRef, JobRecord
from footylevels.repositories import RecordConflict
from tests.test_lesson_passes import EXAMPLE, scripted, sdk

MODEL = 'gemini-4-carbon'
ENGINE = {'modelName': 'SAM 3D Body', 'upstreamCommit': 'b' * 40, 'checkpointHash': 'c' * 64, 'configHash': 'd' * 64}

ADAPTER = r'''
import json, os, shutil, sys
from pathlib import Path
fixture, mode, directory = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
(directory / 'child-env.json').write_text(json.dumps(sorted(os.environ)))
work = directory / 'work'
shutil.copytree(fixture, work)
shutil.copyfile(directory / 'input.mp4', work / 'upload.mp4')
if mode == 'bad-mesh':
    mesh = json.loads((work / 'analysis' / 'mesh.json').read_text())
    mesh['footSets']['left'] = [mesh['vertexCount'] + 5]
    (work / 'analysis' / 'mesh.json').write_text(json.dumps(mesh))
(directory / 'result.json').write_text(json.dumps({'engine': %s, 'frames': 280}))
''' % json.dumps(ENGINE)


class Jobs:
    def __init__(self, conflict_at=None):
        self.stages, self.reserved, self.published, self.failed = [], [], [], []
        self.conflict_at = conflict_at

    def checkpoint(self, claim, stage=None):
        if stage:
            self.stages.append(stage)
        if stage and stage == self.conflict_at:
            raise RecordConflict('cancelled')

    def reserve_outputs(self, claim, assets):
        self.reserved.append(list(assets))

    def publish_lesson(self, claim, lesson):
        self.published.append(lesson)
        return lesson

    def fail(self, claim, code, *, retry):
        self.failed.append((code, retry))


class Storage:
    def __init__(self, video, broken=False):
        self.video, self.broken, self.puts = video, broken, []

    def download_file(self, asset, path):
        shutil.copyfile(self.video, path)

    def put_file(self, asset, path):
        if self.broken:
            raise StorageUnavailable()
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == asset.sha256
        self.puts.append(asset.object_key)


@pytest.fixture(scope='module')
def fixture(tmp_path_factory):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg is required')
    root = tmp_path_factory.mktemp('lesson-fixture')
    analysis = json.loads((EXAMPLE / 'analysis.json').read_text())
    video = root / 'video.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=30',
                    '-frames:v', str(analysis['frameCount']), '-pix_fmt', 'yuv420p', str(video)], check=True)
    work = root / 'work'
    motion = work / 'analysis' / 'motion'
    motion.mkdir(parents=True)
    (work / 'analysis' / 'analysis.json').write_text(json.dumps(analysis))
    (work / 'analysis' / 'mesh.json').write_text(json.dumps({'vertexCount': 50, 'faces': '', 'footSets': {'left': [1], 'right': [2]}}))
    n = analysis['frameCount']
    (motion / 'body-000000.bin.gz').write_bytes(gzip.compress(bytes(n * 50 * 6)))
    (motion / 'faces.bin').write_bytes(bytes(12))
    (motion / 'sequence.json').write_text(json.dumps({'vertexCount': 50, 'frameCount': n, 'fps': 30, 'min': [0, 0, 0],
        'span': [1, 1, 1], 'chunks': [{'start': 0, 'count': n, 'file': 'body-000000.bin.gz', 'bytes': 1}]}))
    adapter = root / 'adapter.py'
    adapter.write_text(ADAPTER)
    return SimpleNamespace(video=video, work=work, adapter=adapter, root=root)


def make_claim(video):
    source = AssetRef(bucket='private-media', object_key='users/alice/projects/p1/media/' + 'a' * 64 + '/1/video.mp4',
                      sha256=hashlib.sha256(video.read_bytes()).hexdigest(), byte_size=video.stat().st_size,
                      content_type='video/mp4')
    return JobRecord(id='e' * 64, owner_id='alice', project_id='p1', kind='buildLesson', input_revision=1,
                     input_hash=lesson_input_hash(source, MODEL), profile_version='lesson-3d-v1', source_asset=source,
                     status='running', attempt_count=1, lease_token='t' * 48)


def lesson_replies():
    authored = json.loads((EXAMPLE / 'lesson.json').read_text())
    authored['skills'].sort(key=lambda s: s['range'][0])
    return scripted(authored, None)


def run(fixture, tmp_path, jobs, storage, client, mode='ok', claim=None):
    staging = tmp_path / 'staging'
    staging.mkdir(exist_ok=True)
    return lesson_worker.run_job(jobs, storage, claim or make_claim(fixture.video),
                                 [sys.executable, str(fixture.adapter), str(fixture.work), mode], staging,
                                 model=MODEL, cache_root=tmp_path / 'cache', client=client, heartbeat_seconds=60)


def test_publishes_a_contract_checked_bundle_and_keeps_the_key_from_the_child(fixture, tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'never-for-the-child')
    seen = {}
    real = lesson_worker._child

    def spy(command, directory, *args):
        real(command, directory, *args)
        seen['env'] = json.loads((directory / 'child-env.json').read_text())
    monkeypatch.setattr(lesson_worker, '_child', spy)
    jobs, storage, client = Jobs(), Storage(fixture.video), sdk(lesson_replies())
    assert run(fixture, tmp_path, jobs, storage, client) == 'succeeded'
    assert jobs.stages == ['downloadingInput', 'screeningVideo', 'generatingBodies', 'writingLesson',
                           'checkingLesson', 'publishingAssets']
    assert not any('GEMINI' in name or 'GOOGLE' in name for name in seen['env'])
    lesson = jobs.published[0]
    prefix = 'users/alice/projects/p1/lesson/' + 'e' * 64 + '/1/'
    names = {a.object_key[len(prefix):] for a in lesson.artifacts}
    assert {'lesson.json', 'analysis.json', 'mesh.json', 'video.mp4', 'motion/sequence.json'} <= names
    assert storage.puts == [a.object_key for a in lesson.artifacts] == [a.object_key for a in jobs.reserved[0]]
    assert lesson.authoring.mode == 'grounded' and lesson.ai_generated and lesson.title
    assert not (tmp_path / 'cache' / ('e' * 64)).exists()                       # cache cleared after success


def test_a_retried_attempt_reuses_accepted_model_steps(fixture, tmp_path):
    first = sdk(lesson_replies())
    assert run(fixture, tmp_path, Jobs(), Storage(fixture.video, broken=True), first) == 'retrying'
    assert len(first.models.prompts) == 8
    again = sdk([])
    jobs = Jobs()
    assert run(fixture, tmp_path, jobs, Storage(fixture.video), again) == 'succeeded'
    assert again.models.prompts == [] and again.files.uploads == [] and jobs.published


def test_rejected_video_stops_before_gpu_work(fixture, tmp_path, monkeypatch):
    monkeypatch.setattr(lesson_worker, '_child', lambda *a: pytest.fail('GPU step must not run'))
    jobs = Jobs()
    client = sdk([json.dumps({'verdict': 'no_clear_player', 'reason': 'Crowd shot.', 'moves': []})])
    assert run(fixture, tmp_path, jobs, Storage(fixture.video), client) == 'rejected'
    assert jobs.failed == [('lesson_rejected', False)] and jobs.stages[-1] == 'screeningVideo'


def test_a_bundle_the_app_cannot_open_is_never_published(fixture, tmp_path):
    jobs = Jobs()
    assert run(fixture, tmp_path, jobs, Storage(fixture.video), sdk(lesson_replies()), mode='bad-mesh') == 'lesson-failed'
    assert jobs.failed == [('lesson_unavailable', True)] and not jobs.reserved and not jobs.published


def test_superseded_lease_stops_without_publishing(fixture, tmp_path):
    jobs = Jobs(conflict_at='writingLesson')
    assert run(fixture, tmp_path, jobs, Storage(fixture.video), sdk(lesson_replies())) == 'superseded'
    assert not jobs.published and not jobs.failed


def test_job_for_another_model_is_not_authored(fixture, tmp_path):
    claim = make_claim(fixture.video).model_copy(update={'input_hash': 'f' * 64})
    jobs = Jobs()
    assert run(fixture, tmp_path, jobs, Storage(fixture.video), sdk([]), claim=claim) == 'superseded'
    assert jobs.stages == []
