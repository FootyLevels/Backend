"""API authoring at the SDK boundary; no provider, key, GPU or network required."""
import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('jsonschema', reason='Install the optional lessons extra for API authoring tests')

from lesson_pipeline import generation, prompting


EXAMPLE = Path(__file__).resolve().parents[1] / 'lesson_pipeline' / 'examples' / 'hocus-pocus'


@pytest.fixture
def work(tmp_path, monkeypatch):
    metadata = {'duration': 9.233333333333333, 'fps': 30, 'frameCount': 277}
    probe = lambda *args, **kwargs: metadata.copy()
    monkeypatch.setattr(generation, 'video_metadata', probe, raising=False)
    (tmp_path / 'upload.mp4').write_bytes(b'fixture-video')
    return tmp_path


@pytest.fixture
def lesson():
    return json.loads((EXAMPLE / 'lesson.resolved.json').read_text())


def uploaded(state='ACTIVE'):
    return SimpleNamespace(name='files/test-video', uri='https://provider.invalid/files/test-video',
                           mime_type='video/mp4', state=SimpleNamespace(name=state))


class Files:
    def __init__(self, states=('ACTIVE',), error=None, delete_error=None):
        self.states = iter(states)
        self.error = error
        self.delete_error = delete_error
        self.uploads = []
        self.gets = []
        self.deletes = []

    def upload(self, **kwargs):
        self.uploads.append(kwargs)
        if self.error:
            raise self.error
        return uploaded(next(self.states))

    def get(self, *, name):
        self.gets.append(name)
        return uploaded(next(self.states))

    def delete(self, *, name):
        self.deletes.append(name)
        if self.delete_error:
            raise self.delete_error


class Models:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def generate_content(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)


def client(replies=(), **kwargs):
    return SimpleNamespace(files=Files(**kwargs), models=Models(replies))


def run(work, sdk, **kwargs):
    return generation.generate(work, 'my-move', client=sdk, poll_seconds=0, **kwargs)


def test_generation_uploads_the_video_and_publishes_a_checked_lesson(work, lesson):
    sdk = client([json.dumps(lesson)])
    result = run(work, sdk, model='gemini-4-argon')
    written = json.loads(result.read_text())
    assert result == work / 'lesson.json'
    assert written['id'] == 'my-move' and written['folder'] == 'my-move'
    assert written['schemaVersion'] == 1 and written['video'] == 'video.mp4'
    assert sdk.files.uploads[0]['file'] == work / 'upload.mp4'
    assert sdk.files.uploads[0]['config']['mime_type'] == 'video/mp4'
    assert sdk.files.deletes == ['files/test-video']
    request = sdk.models.requests[0]
    assert request['model'] == 'gemini-4-argon'
    assert request['config']['response_mime_type'] == 'application/json'
    assert 'fixture-video' not in str(request)
    assert 'files/test-video' in str(request['contents'])
    assert list((work / 'generation').glob('run-*/run.json'))
    assert list((work / 'generation').glob('run-*/response-1.txt'))


def test_direct_mov_input_is_uploaded_with_its_actual_mime_type(work, lesson):
    (work / 'upload.mp4').rename(work / 'source.mov')
    sdk = client([json.dumps(lesson)])
    run(work, sdk)
    assert sdk.files.uploads[0]['file'] == work / 'source.mov'
    assert sdk.files.uploads[0]['config']['mime_type'] == 'video/quicktime'


def test_generation_waits_for_upload_processing(work, lesson):
    sdk = client([json.dumps(lesson)], states=('PROCESSING', 'PROCESSING', 'ACTIVE'))
    run(work, sdk)
    assert sdk.files.gets == ['files/test-video', 'files/test-video']
    assert len(sdk.models.requests) == 1
    assert sdk.files.deletes == ['files/test-video']


def test_failed_upload_processing_is_cleaned_up_without_generation(work):
    sdk = client(states=('PROCESSING', 'FAILED'))
    with pytest.raises(generation.GenerationError):
        run(work, sdk)
    assert sdk.models.requests == []
    assert sdk.files.deletes == ['files/test-video']
    assert not (work / 'lesson.json').exists()


def test_processing_timeout_is_bounded_and_cleans_the_upload(work, monkeypatch):
    sdk = client(states=('PROCESSING',) * 100)
    ticks = iter([0, 2])
    monkeypatch.setattr(generation.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(generation.GenerationError):
        run(work, sdk, processing_timeout=1)
    assert sdk.models.requests == []
    assert sdk.files.deletes == ['files/test-video']


@pytest.mark.parametrize('broken', [
    'not JSON',
    '{"schemaVersion": 1, "skills": [null]}',
    '{"schemaVersion": 1, "skills": [{"range": [0]}]}',
    '{"title": NaN}',
    '{"title": 1e999}',
])
def test_malformed_model_output_is_repaired_using_a_bounded_attempt(work, lesson, broken):
    sdk = client([broken, json.dumps(lesson)])
    result = run(work, sdk, max_attempts=2)
    assert json.loads(result.read_text())['id'] == 'my-move'
    assert len(sdk.models.requests) == 2
    assert sdk.files.deletes == ['files/test-video']
    assert list((work / 'generation').glob('run-*/validation-1.json'))


def test_a_video_only_lesson_needs_no_analysis_or_evidence(work, lesson):
    assert not (work / 'analysis').exists() and not (work / 'evidence').exists()
    sdk = client([json.dumps(lesson)])
    run(work, sdk)
    assert len(sdk.models.requests) == 1
    receipt = json.loads(next((work / 'generation').glob('run-*/run.json')).read_text())
    assert 'analysisSha256' not in receipt
    request = str(sdk.models.requests[0])
    assert 'A finished lesson' not in request
    assert 'What the 3D measured' not in request


def test_model_can_choose_shot_boundaries_from_the_video(work, lesson):
    lesson['shots'] = [{'start': 0, 'end': 9.23, 'label': 'Full clip'}]
    sdk = client([json.dumps(lesson)])
    written = json.loads(run(work, sdk).read_text())
    assert written['shots'] == lesson['shots']
    assert len(sdk.models.requests) == 1


def test_out_of_video_times_require_structural_repair(work, lesson):
    broken = copy.deepcopy(lesson)
    broken['skills'][0]['phases'][0]['key'] = 99
    sdk = client([json.dumps(broken), json.dumps(lesson)])
    run(work, sdk, max_attempts=2)
    assert len(sdk.models.requests) == 2
    assert '99' in str(sdk.models.requests[1]['contents'])


def test_placeholder_output_requires_repair_without_hidden_measurements(work, lesson):
    broken = copy.deepcopy(lesson)
    broken['skills'][0]['phases'][0]['shows'] = 'The body drops {{drop 0 1}} cm.'
    sdk = client([json.dumps(broken), json.dumps(lesson)])
    run(work, sdk, max_attempts=2)
    assert len(sdk.models.requests) == 2


def test_repair_exhaustion_keeps_existing_lesson_and_records_feedback(work, lesson):
    original = json.dumps(lesson)
    (work / 'lesson.json').write_text(original)
    sdk = client(['{}', '{}'])
    with pytest.raises(generation.GenerationError):
        run(work, sdk, max_attempts=2)
    assert (work / 'lesson.json').read_text() == original
    assert len(sdk.models.requests) == 2
    assert sdk.files.deletes == ['files/test-video']
    assert list((work / 'generation').glob('run-*/validation-2.json'))


def test_provider_errors_are_sanitized_and_not_retried_or_published(work, lesson):
    secret = 'private-api-key=do-not-log'
    (work / 'lesson.json').write_text(json.dumps(lesson))
    sdk = client([RuntimeError(secret)])
    with pytest.raises(generation.GenerationError) as error:
        run(work, sdk)
    assert secret not in str(error.value)
    assert len(sdk.models.requests) == 1
    assert sdk.files.deletes == ['files/test-video']
    for path in (work / 'generation').rglob('*'):
        if path.is_file():
            assert secret not in path.read_text()
    assert json.loads((work / 'lesson.json').read_text()) == lesson


def test_upload_error_is_sanitized_and_does_not_generate(work):
    sdk = client(error=RuntimeError('api-key=secret'))
    with pytest.raises(generation.GenerationError) as error:
        run(work, sdk)
    assert 'secret' not in str(error.value)
    assert sdk.models.requests == []
    assert sdk.files.deletes == []


def test_failed_provider_cleanup_warns_without_losing_a_successful_lesson(work, lesson):
    sdk = client([json.dumps(lesson)], delete_error=RuntimeError('storage credential secret'))
    with pytest.warns(RuntimeWarning):
        result = run(work, sdk)
    assert json.loads(result.read_text())['id'] == 'my-move'
    assert sdk.files.deletes == ['files/test-video']
    receipt = next((work / 'generation').glob('run-*/run.json'))
    assert json.loads(receipt.read_text())['cleanup'] == 'failed'
    assert 'secret' not in receipt.read_text()


@pytest.mark.parametrize('reply', ['', None])
def test_empty_or_refused_response_is_terminal_and_cleans_upload(work, reply):
    sdk = client([reply])
    with pytest.raises(generation.GenerationError):
        run(work, sdk)
    assert len(sdk.models.requests) == 1
    assert sdk.files.deletes == ['files/test-video']
    assert not (work / 'lesson.json').exists()


def test_missing_key_fails_before_upload_or_changing_a_lesson(work, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    with pytest.raises(generation.GenerationError, match='GEMINI_API_KEY'):
        generation.generate(work, 'my-move')
    assert not (work / 'lesson.json').exists()


def test_invalid_attempt_count_fails_before_any_provider_action(work):
    sdk = client()
    with pytest.raises((ValueError, generation.GenerationError)):
        run(work, sdk, max_attempts=0)
    assert sdk.files.uploads == []
    assert sdk.models.requests == []


def test_cli_generate_forwards_selected_model_and_attempt_budget(work, monkeypatch):
    from lesson_pipeline import cli
    requests = []

    def generate(folder, lesson_id, **kwargs):
        requests.append((folder, lesson_id, kwargs))
        return folder / 'lesson.json'

    monkeypatch.setattr(cli.generation, 'generate', generate)
    assert cli.main(['generate', '--work', str(work), '--id', 'my-move',
                     '--model', 'gemini-test', '--attempts', '2']) == 0
    assert requests == [(work.resolve(), 'my-move', {'model': 'gemini-test', 'max_attempts': 2})]


@pytest.mark.parametrize('command', ['prompt', 'import'])
def test_cli_has_no_chat_app_handoff_command(command):
    from lesson_pipeline import cli
    with pytest.raises(SystemExit) as error:
        cli.main([command, '--work', '/does-not-matter'])
    assert error.value.code == 2


def test_cli_run_forwards_remote_data_location(tmp_path, monkeypatch):
    from lesson_pipeline import cli
    video = tmp_path / 'clip.mp4'
    video.write_bytes(b'fixture')
    work = tmp_path / 'new-work'
    servers = []
    monkeypatch.setattr(cli.cuts, 'detect_cuts', lambda video: [])
    monkeypatch.setattr(cli.remote, 'Server', lambda *args, **kwargs: servers.append((args, kwargs)))
    monkeypatch.setattr(cli.remote, 'reconstruct', lambda *args, **kwargs:
                        {'projectId': 'fixture', 'status': 'complete', 'parts': []})
    monkeypatch.setattr(cli, '_analyze', lambda folder: None)
    monkeypatch.setattr(cli.evidence, 'build_evidence', lambda *args: 'evidence')
    monkeypatch.setattr(cli.draft, 'build_draft', lambda *args: 'draft')
    assert cli.main(['run', str(video), '--work', str(work), '--host', 'owner@gpu',
                     '--port', '8123', '--server-root', '/srv/lessons',
                     '--server-data', '/srv/private-data']) == 0
    assert servers == [(('owner@gpu', 8123, '/srv/lessons'), {'data_dir': '/srv/private-data'})]


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg tools required')
def test_metadata_comes_from_the_video_without_motion_analysis(tmp_path):
    video = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=64x64:r=30:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
    metadata = prompting.video_metadata(tmp_path)
    assert metadata['duration'] == pytest.approx(1, abs=.05)
    assert metadata['fps'] == pytest.approx(30)
    assert not (tmp_path / 'analysis').exists()


@pytest.mark.skipif(not shutil.which('ffprobe'), reason='FFprobe required')
def test_corrupt_video_metadata_fails_before_any_provider_request(tmp_path):
    (tmp_path / 'source.mp4').write_bytes(b'not a video')
    with pytest.raises((prompting.PromptError, ValueError)):
        prompting.video_metadata(tmp_path)


def test_cli_generate_accepts_a_video_without_gpu_preparation(tmp_path, monkeypatch):
    from lesson_pipeline import cli
    source = tmp_path / 'recording.mov'
    source.write_bytes(b'video-with-audio')
    destination = tmp_path / 'minimal-work'
    requests = []

    def generate(work, lesson_id, **kwargs):
        requests.append((work, lesson_id))
        assert (work / 'source.mov').read_bytes() == source.read_bytes()
        assert not (work / 'analysis').exists()
        assert not (work / 'evidence').exists()
        return work / 'lesson.json'

    monkeypatch.setattr(cli.generation, 'generate', generate)
    assert cli.main(['generate', str(source), '--work', str(destination), '--id', 'my-move']) == 0
    assert requests == [(destination.resolve(), 'my-move')]


def test_import_does_not_replace_a_different_existing_video(tmp_path):
    from lesson_pipeline import cli
    work = tmp_path / 'work'
    work.mkdir()
    saved = work / 'source.mp4'
    saved.write_bytes(b'original')
    incoming = tmp_path / 'replacement.mp4'
    incoming.write_bytes(b'different')
    with pytest.raises((ValueError, prompting.PromptError)):
        cli._import_video(incoming, work)
    assert saved.read_bytes() == b'original'


def test_cli_check_uses_only_video_metadata_for_a_numeric_lesson(work, lesson, monkeypatch):
    from lesson_pipeline import cli
    (work / 'lesson.json').write_text(json.dumps(lesson))
    monkeypatch.setattr(cli, 'video_metadata', lambda folder: {'duration': 9.233333333333333})
    assert not (work / 'analysis').exists()
    assert cli.main(['check', '--work', str(work), '--strict']) == 0
