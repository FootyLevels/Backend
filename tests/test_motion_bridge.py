"""Motion IPC, bounded real decoder/adapter and process cancellation; no GPU."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import pytest

from tests.test_model_bridge import Bridge, METADATA, receive, send

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load('motion_bridge_fixture', ROOT/'runtime/skill_lab/backend/motion_bridge.py')
packer = load('motion_packer_fixture', ROOT/'runtime/skill_lab/backend/motion_data.py')


@pytest.fixture
def bridge():
    with tempfile.TemporaryDirectory(prefix='fl-m-', dir='/tmp') as temporary, ThreadPoolExecutor(max_workers=1) as executor:
        root = Path(temporary).resolve()
        source = root/'inputs/dev/reconstruction-abcdefgh/motion-input.mp4'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'fixture source')
        def infer(request, directory, stop):
            output = directory/'motion'
            output.mkdir(exist_ok=True)
            (output/'fixture.json').write_text('{}')
            return [{'name': 'fixture.json', 'byteSize': 2, 'sha256': hashlib.sha256(b'{}').hexdigest()}]
        motion = module.MotionTasks(root/'model/motion-cache', executor,
            {**METADATA, 'segmentation': {'model': 'fixture', 'checkpointHash': 'a'*64}}, infer, input_root=root/'inputs')
        instance = Bridge(root/'model', executor, lambda *_: pytest.fail('Unexpected body inference'), METADATA, motion=motion)
        instance.start()
        try:
            yield instance, source
        finally:
            instance.close()


def request(source, **changes):
    return {'op': 'motion', 'jobId': 'a'*64, 'inputHash': 'b'*64, 'profileVersion': module.PROFILE,
        'sourcePath': str(source), 'sourceBytes': source.stat().st_size,
        'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'startFrame': 15, 'endFrame': 45, **changes}


def call(bridge, value):
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(10)
        stream.connect(str(bridge.socket_path))
        send(stream, value)
        return receive(stream)


def test_motion_replay_restart_and_config_pin(bridge):
    instance, source = bridge
    value = request(source)
    response = call(instance, value)
    assert response['status'] == 'ready' and instance.motion.calls == 1
    assert call(instance, value) == response
    instance.motion.tasks.clear()  # private disk replay after a model-process restart
    assert call(instance, value) == response and instance.motion.calls == 1
    instance.motion.metadata = {**instance.motion.metadata, 'engine': {**METADATA['engine'], 'configHash': 'f'*64}}
    assert call(instance, value)['directory'] != response['directory']
    assert instance.motion.calls == 2


@pytest.mark.parametrize('change', [
    {'startFrame': True}, {'endFrame': 44}, {'endFrame': 316}, {'sourceBytes': 52428801},
    {'profileVersion': 'unversioned'}, {'sourcePath': '/etc/passwd'}, {'extra': 'field'},
    {'sourceSha256': 'f'*64},
])
def test_motion_rejects_untrusted_or_changed_input(bridge, change):
    instance, source = bridge
    assert call(instance, request(source, **change)) == {'error': 'invalid_request'}
    assert instance.motion.calls == 0


def test_motion_rejects_symlink_and_tampered_cache(bridge):
    instance, source = bridge
    response = call(instance, request(source))
    (Path(response['directory'])/'fixture.json').write_text('changed')
    instance.motion.tasks.clear()
    assert call(instance, request(source)) == {'error': 'invalid_request'}
    target = source.with_name('target.mp4')
    source.rename(target)
    source.symlink_to(target)
    assert call(instance, request(source)) == {'error': 'invalid_request'}


def test_motion_cancel_disconnect_stops_active_work_then_resumes(bridge):
    instance, source = bridge
    started, stopped = threading.Event(), threading.Event()
    original = instance.motion.infer
    def slow(request, directory, stop):
        started.set()
        assert stop.wait(3)
        stopped.set()
        raise RuntimeError('Cancelled fixture')
    instance.motion.infer = slow
    with socket.socket(socket.AF_UNIX) as stream:
        stream.connect(str(instance.socket_path))
        send(stream, request(source))
        assert started.wait(2)
        health = call(instance, {'op': 'health'})
        assert health['activeRequests'] == 1 and health['motionCalls'] == 1
    assert stopped.wait(2)
    for _ in range(20):
        if all(task[0].done() for task in instance.motion.tasks.values()): break
        time.sleep(.05)
    assert not list(instance.motion.root.glob('*/result.json'))
    instance.motion.infer = original
    assert call(instance, request(source))['status'] == 'ready'
    assert instance.motion.calls == 2


def test_motion_and_keyframes_share_the_serial_executor(bridge):
    from tests.test_model_bridge import request as body_request, GLB
    instance, source = bridge
    started, release = threading.Event(), threading.Event()
    original = instance.motion.infer
    def slow(request, directory, stop):
        started.set()
        assert release.wait(3)
        return original(request, directory, stop)
    instance.motion.infer = slow
    def body(image, bbox, output):
        assert release.is_set()
        output.write_bytes(GLB)
        return {}
    instance.infer = body
    with ThreadPoolExecutor(max_workers=2) as callers:
        motion = callers.submit(call, instance, request(source))
        assert started.wait(2)
        keyframe = callers.submit(call, instance, body_request())
        time.sleep(.1)
        assert instance.calls == 0
        release.set()
        assert motion.result()['status'] == keyframe.result()['status'] == 'ready'


@pytest.mark.parametrize('missing', [False, True])
def test_real_motion_decoder_and_adapter_preserve_source_time_and_missing_frames(bridge, missing):
    from footylevels.model_requests import MOTION_PROFILE, motion_input_hash
    from footylevels.records import AssetRef, JobRecord, MotionSegment, ReconstructionRecord
    from footylevels.reconstruction_worker import validate_output
    instance, clip = bridge
    directory = clip.parent
    (directory/'artifacts').mkdir()
    source = directory/'input.mp4'
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg: pytest.skip('Real FFmpeg needed')
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=32x24:rate=30:duration=2', '-c:v', 'libx264', '-threads', '2', str(source)], check=True, timeout=10)
    clip.unlink()
    source_asset = AssetRef(bucket='private-media', object_key='users/test/projects/test/prepared/video.mp4',
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), byte_size=source.stat().st_size, content_type='video/mp4')
    segment = MotionSegment(start_frame=15, end_frame=45)
    job = JobRecord(id='a'*64, owner_id='test', project_id='test', input_revision=2, kind='reconstructMotion',
        profile_version=MOTION_PROFILE, motion_segment=segment, source_asset=source_asset, attempt_count=2,
        input_hash=motion_input_hash(source_asset, segment))
    (directory/'job.json').write_text(job.model_dump_json(by_alias=True))
    def infer(request, cache, stop):
        output = cache/'motion'
        vertices = np.random.default_rng(21).normal(size=(30, 7, 3)).astype(np.float32)
        valid = np.ones(30, dtype=bool)
        if missing:
            valid[10] = False
            vertices[10] = np.nan
        sequence = packer.pack_sequence(vertices, np.array([[0, 1, 2], [3, 4, 5]]), valid, output)
        sequence.update(fps=30, duration=1, validBodyFrames=int(valid.sum()), validBallFrames=0,
            ball={'file': None, 'diameter': .22}, ground=None,
            frames=[{'index': i, 'time': i/30, 'shot': 0, 'valid': bool(valid[i]), 'ball': None} for i in range(30)])
        (output/'sequence.json').write_text(json.dumps(sequence))
        return [{'name': p.name, 'byteSize': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in output.iterdir()]
    instance.motion.infer = infer
    instance.motion.metadata = {**instance.motion.metadata,
        'topologyVersion': 'mhr-'+hashlib.sha256(np.array([[0, 1, 2], [3, 4, 5]], dtype='<u4').tobytes()).hexdigest()[:24]+'-v1'}
    subprocess.run([sys.executable, str(ROOT/'infra/dgx/reconstruction_adapter.py'), '--socket', str(instance.socket_path),
        '--ffmpeg', ffmpeg, str(directory)], env={'PATH': os.defpath, 'HOME': str(directory)}, check=True, timeout=15)
    result = ReconstructionRecord.model_validate_json((directory/'result.json').read_bytes())
    assert result.outcome == ('partial' if missing else 'ready')
    assert result.requested_frame_count == 30 and result.missing_frame_count == int(missing)
    assert result.source_asset == source_asset and result.contract.encoding == 'motionBuffers'
    assert len(validate_output(job, result, directory)) == 4
    sequence = json.loads((directory/'artifacts/sequence.json').read_text())
    assert sequence['sourceTimeOffset'] == .5 and sequence['attempt'] == 2
    assert sequence['frames'][0]['time'] == 0 and sequence['frames'][0]['sourceTimestamp'] == .5
    assert sequence['frames'][-1]['sourceFrameIndex'] == 44
    assert sequence['frames'][10]['valid'] == (not missing)
    assert np.frombuffer(gzip.decompress((directory/'artifacts/body-000000.bin.gz').read_bytes()), '<u2').size == 30*7*3


@pytest.mark.parametrize('ending', ['timeout', 'terminate'])
def test_model_stage_reaps_its_process_group(tmp_path, ending):
    pid_file = tmp_path/'child.pid'
    child_code = 'import time; time.sleep(60)'
    parent_code = 'import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,"-c",sys.argv[2]]); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)'
    command = [sys.executable, str(ROOT/'infra/dgx/motion_stage.py'), '--timeout', '1' if ending == 'timeout' else '10',
        '--directory', str(tmp_path), sys.executable, '-c', parent_code, str(pid_file), child_code]
    process = subprocess.Popen(command)
    try:
        for _ in range(40):
            if pid_file.exists(): break
            time.sleep(.05)
        assert pid_file.exists()
        if ending == 'terminate': process.terminate()
        assert process.wait(timeout=7) == 1
        child = int(pid_file.read_text())
        # An orphan zombie may await the platform init reaper, but cannot run or
        # hold CUDA memory. Accept only absent or zombie, never sleeping/running.
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(child)], capture_output=True, text=True).stdout.strip()
        assert not state or state.startswith('Z')
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=7)


def test_model_stage_reaps_after_runtime_parent_loss(tmp_path):
    pid_file = tmp_path/'gpu-fixture.pid'
    wrapper_pid = tmp_path/'wrapper.pid'
    stage = [sys.executable, str(ROOT/'infra/dgx/motion_stage.py'), '--timeout', '10', '--directory', str(tmp_path),
        sys.executable, '-c', 'import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)', str(pid_file)]
    parent = subprocess.Popen([sys.executable, '-c',
        'import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen(sys.argv[2:]); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)',
        str(wrapper_pid), *stage])
    try:
        for _ in range(40):
            if pid_file.exists(): break
            time.sleep(.05)
        assert pid_file.exists()
        parent.kill()
        parent.wait(timeout=5)
        for pid in [int(pid_file.read_text()), int(wrapper_pid.read_text())]:
            for _ in range(50):
                state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
                if not state or state.startswith('Z'): break
                time.sleep(.1)
            assert not state or state.startswith('Z')
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=5)


def test_interrupted_raw_checkpoint_is_not_reused_as_a_pose(tmp_path):
    """Run the real packing script twice with fake inference and a write crash."""
    output = tmp_path/'motion'
    (output/'source').mkdir(parents=True)
    import cv2
    for i in range(30):
        cv2.imwrite(str(output/'source'/f'{i:06d}.jpg'), np.zeros((24, 32, 3), dtype=np.uint8))
    (output/'tracking.json').write_text(json.dumps({'fps': 30, 'frames': [
        {'index': i, 'timestamp': i/30, 'shot': 0, 'person': {'bbox': [0, 0, 1, 1]}, 'ball': None} for i in range(30)]}))
    code = '''
import runpy, sys
from pathlib import Path
from types import SimpleNamespace, ModuleType
import numpy as np
vertices=np.array([[0,0,0],[1,0,0],[0,1,0]],dtype=np.float32)
person={'pred_vertices':vertices,'pred_cam_t':np.array([0,0,2]),'focal_length':np.array([700]),
        'pred_keypoints_3d':vertices,'pred_keypoints_2d':vertices[:,:2]}
provider=ModuleType('backend.providers.local')
provider.LocalProvider=type('LocalProvider',(),{'load':lambda self: SimpleNamespace(faces=np.array([[0,1,2]]),process_one_image=lambda *a,**kw:[person])})
sys.modules['backend.providers.local']=provider
if sys.argv[2]=='crash':
    def interrupted(destination, **values):
        destination.write(b'partial checkpoint')
        raise SystemExit(1)
    np.savez_compressed=interrupted
script=sys.argv[3]
sys.argv=[script,sys.argv[1]]
runpy.run_path(script,run_name='__main__')
'''
    command = [sys.executable, '-c', code, str(tmp_path), 'crash', str(ROOT/'runtime/skill_lab/scripts/reconstruct_motion.py')]
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'runtime/skill_lab')}
    first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert first.returncode == 1
    assert (output/'raw/000000.tmp').exists() and not (output/'raw/000000.npz').exists(), first.stderr
    command[-2] = 'resume'
    resumed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads((output/'sequence.json').read_text())['validBodyFrames'] == 30
    assert np.isfinite(np.load(output/'raw/000000.npz')['vertices']).all()
