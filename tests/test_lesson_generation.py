"""API authoring at the SDK boundary; no provider, key, GPU or network required."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lesson_pipeline import generation


EXAMPLE = Path(__file__).resolve().parents[1] / 'lesson_pipeline' / 'examples' / 'hocus-pocus'


@pytest.fixture
def work(tmp_path):
    (tmp_path / 'analysis').mkdir()
    (tmp_path / 'analysis' / 'analysis.json').write_text((EXAMPLE / 'analysis.json').read_text())
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'evidence' / 'evidence.md').write_text('# Evidence\n\n## Shot 0\n- Measurements available.\n')
    (tmp_path / 'upload.mp4').write_bytes(b'fixture-video')
    return tmp_path


@pytest.fixture
def lesson():
    return json.loads((EXAMPLE / 'lesson.json').read_text())


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


def test_a_semantic_warning_requires_repair(work, lesson):
    broken = copy.deepcopy(lesson)
    broken['skills'][0]['phases'][0]['shows'] = 'The left foot drops 40 cm.'
    sdk = client([json.dumps(broken), json.dumps(lesson)])
    run(work, sdk, max_attempts=2)
    assert len(sdk.models.requests) == 2
    feedback = str(sdk.models.requests[1]['contents'])
    assert 'placeholder' in feedback or 'left' in feedback


def test_model_cannot_change_shot_boundaries(work, lesson):
    broken = copy.deepcopy(lesson)
    broken['shots'][0]['end'] -= 0.1
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
