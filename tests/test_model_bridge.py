"""Real IPC/replay/disconnect tests, without model loading or cloud access."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time

from PIL import Image
import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('private_model_bridge', ROOT/'runtime/skill_lab/backend/model_bridge.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Bridge, send, receive = module.Bridge, module.send, module.receive
METADATA = {'engine': {'modelName': 'fixture', 'upstreamCommit': 'c'*40,
    'checkpointHash': 'd'*64, 'configHash': 'e'*64}, 'topologyVersion': 'fixture-v1'}
GLB = b'glTF' + struct.pack('<II', 2, 12)


def request(index=0, attempt=1):
    image = io.BytesIO()
    Image.new('RGB', (32, 24), (index, 50, 100)).save(image, format='JPEG')
    payload = image.getvalue()
    return {'jobId': 'a'*64, 'attempt': attempt, 'frameIndex': index,
        'inputHash': 'b'*64, 'profileVersion': 'body-keyframes-v1', 'bbox': [0, 0, 1, 1],
        'imageBase64': base64.b64encode(payload).decode(), 'imageSha256': hashlib.sha256(payload).hexdigest()}


def call(bridge, value):
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(5)
        stream.connect(str(bridge.socket_path))
        send(stream, value)
        return receive(stream)


@pytest.fixture
def bridge():
    def infer(image, bbox, output):
        output.write_bytes(GLB)
        return {'vertexCount': 3}
    with tempfile.TemporaryDirectory(prefix='fl-bridge-', dir='/tmp') as root, ThreadPoolExecutor(max_workers=1) as executor:
        instance = Bridge(root, executor, infer, METADATA)
        instance.start()
        try: yield instance
        finally: instance.close()


def test_duplicate_lost_response_and_process_restart_replay(bridge):
    started, release = threading.Event(), threading.Event()
    def infer(image, bbox, output):
        started.set()
        assert release.wait(3)
        output.write_bytes(GLB)
        return {'vertexCount': 3}
    bridge.infer = infer
    stream = socket.socket(socket.AF_UNIX)
    stream.connect(str(bridge.socket_path))
    send(stream, request())
    assert started.wait(2)
    stream.close()  # real lost acknowledgement, inference already owns the GPU
    release.set()
    response = call(bridge, request())
    assert response['sha256'] == hashlib.sha256(GLB).hexdigest()
    assert bridge.calls == 1
    bridge.close()
    bridge.start()
    bridge.tasks.clear()  # emulate fresh process memory, retaining private disk cache
    assert call(bridge, request()) == response
    assert bridge.calls == 1
    assert call(bridge, request(attempt=2)) == response
    assert bridge.calls == 2  # a new attempt cannot reuse an old-attempt identity
    assert bridge.root.stat().st_mode & 0o777 == 0o700
    assert bridge.socket_path.stat().st_mode & 0o777 == 0o600


def test_disconnected_queued_request_is_cancelled_without_overlap(bridge):
    release = threading.Event()
    blocker = bridge.executor.submit(lambda: release.wait(3))
    stream = socket.socket(socket.AF_UNIX)
    stream.connect(str(bridge.socket_path))
    send(stream, request())
    for _ in range(100):
        if bridge.tasks: break
        time.sleep(.01)
    stream.close()
    for _ in range(100):
        if all(task.cancelled() for task in bridge.tasks.values()): break
        time.sleep(.01)
    else: pytest.fail('Disconnected queued work remained live')
    release.set()
    blocker.result(timeout=2)
    assert bridge.calls == 0
    assert call(bridge, request())['status'] == 'ready'
    assert bridge.calls == 1


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(attempt=0), lambda r: r.update(bbox=[0, 0, 0, 1]),
    lambda r: r.update(imageSha256='f'*64), lambda r: r.update(profileVersion='other'),
    lambda r: r.update(frameIndex=1802), lambda r: r.update(secret='no'),
])
def test_invalid_requests_never_enter_inference(bridge, mutate):
    value = request()
    mutate(value)
    assert call(bridge, value) == {'error': 'invalid_request'}
    assert bridge.calls == 0


def test_configuration_and_image_changes_invalidate_cache(bridge):
    call(bridge, request())
    call(bridge, request(1))
    bridge.metadata = {**METADATA, 'topologyVersion': 'changed-v2'}
    call(bridge, request())
    assert bridge.calls == 3


def test_prune_is_bounded_and_does_not_skip_entries(bridge):
    for n in range(110):
        (bridge.cache/f'{n:064x}').mkdir()
    bridge.prune()
    assert len(list(bridge.cache.iterdir())) == 99
    for path in bridge.cache.iterdir(): os.utime(path, (0, 0))
    bridge.prune()
    assert not list(bridge.cache.iterdir())


def test_one_owner_and_inference_watchdog(bridge):
    other = Bridge(bridge.root, bridge.executor, bridge.infer, METADATA)
    with pytest.raises(RuntimeError, match='Another owned'): other.start()
    fired = threading.Event()
    bridge.fatal, bridge.inference_seconds = fired.set, .05
    def infer(image, bbox, output):
        assert fired.wait(2)
        output.write_bytes(GLB)
        return {}
    bridge.infer = infer
    assert call(bridge, request())['status'] == 'ready'
    assert fired.is_set()


@pytest.mark.parametrize('outcome', ['ready', 'partial', 'corrupt'])
def test_real_decoder_and_adapter_child_publish_verified_manifest(bridge, tmp_path, outcome):
    import shutil
    import subprocess
    import sys
    from footylevels.records import AssetRef, JobRecord, KeyframeInput, ReconstructionRecord
    from footylevels.model_requests import BODY_PROFILE, body_input_hash
    from footylevels.reconstruction_worker import validate_output
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg: pytest.skip('Real FFmpeg needed')
    def infer(image, bbox, output):
        if outcome == 'partial' and bridge.calls == 2:
            raise RuntimeError('fixture missing body')
        output.write_bytes(GLB + (b'bad-length' if outcome == 'corrupt' else b''))
        return {'vertexCount': 3}
    bridge.infer = infer
    directory = tmp_path / 'attempt'
    directory.mkdir()
    (directory/'artifacts').mkdir()
    source = directory/'input.mp4'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=32x24:rate=30:duration=1', '-c:v', 'libx264', '-threads', '2', str(source)], check=True, timeout=10)
    source_asset = AssetRef(bucket='private-media', object_key='users/test/projects/test/prepared/video.mp4',
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), byte_size=source.stat().st_size, content_type='video/mp4')
    selections = [KeyframeInput(frame_index=i, bbox=(0, 0, 1, 1)) for i in [0, 12]]
    job = JobRecord(id='a'*64, owner_id='test', project_id='test', input_revision=2, kind='reconstructKeyframes',
        profile_version=BODY_PROFILE, keyframes=selections, source_asset=source_asset, attempt_count=1,
        input_hash=body_input_hash(source_asset, selections))
    (directory/'job.json').write_text(job.model_dump_json(by_alias=True))
    command = [sys.executable, str(ROOT/'infra/dgx/body_keyframes_adapter.py'), '--socket', str(bridge.socket_path),
        '--ffmpeg', ffmpeg, str(directory)]
    if outcome == 'corrupt':
        with pytest.raises(subprocess.CalledProcessError):
            subprocess.run(command, env={'PATH': os.defpath, 'HOME': str(directory)}, check=True, timeout=10)
        assert not (directory/'result.json').exists()
        return
    subprocess.run(command, env={'PATH': os.defpath, 'HOME': str(directory)}, check=True, timeout=10)
    result = ReconstructionRecord.model_validate_json((directory/'result.json').read_text())
    assert result.reconstructed_frame_count == (1 if outcome == 'partial' else 2) and result.outcome == outcome
    assert len(validate_output(job, result, directory)) == (2 if outcome == 'partial' else 3)
    manifest = json.loads((directory/'artifacts/keyframes.json').read_text())
    assert [f['timestamp'] for f in manifest['frames']] == ([0] if outcome == 'partial' else [0, .4])
    assert manifest['missingFrameIndices'] == ([12] if outcome == 'partial' else [])
    assert manifest['sourceSha256'] == source_asset.sha256
    assert bridge.calls == 2
