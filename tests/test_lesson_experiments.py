"""Controlled context comparisons; fake provider responses and no model key needed."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('jsonschema', reason='Install the lessons extra for experiment checks')

from lesson_pipeline import experiments
from lesson_pipeline.experiment_prompts import make_prompt, shared_schema


PACKAGE = Path(__file__).resolve().parents[1] / 'lesson_pipeline'


@pytest.fixture
def schema():
    return json.loads((PACKAGE / 'schema' / 'lesson.schema.json').read_text())


@pytest.fixture
def lesson():
    return {
        'schemaVersion': 1, 'id': 'trial', 'folder': 'trial', 'title': 'A move',
        'subtitle': 'Observe the direction change.', 'video': 'video.mp4',
        'sourceNote': 'the supplied video',
        'shots': [{'start': 0, 'end': 2, 'label': 'Demonstration'}],
        'skills': [{
            'id': 'move', 'name': 'Direction change', 'short': 'Change', 'tab': 'Skill',
            'ballFoot': 'right', 'range': [0, 2], 'watch': [0, 2],
            'intro': 'Watch how the player changes direction.',
            'phases': [
                {'id': 'set', 'name': 'Set up', 'start': 0, 'end': 1, 'key': .5, 'cue': 'Approach slowly.'},
                {'id': 'go', 'name': 'Exit', 'start': 1, 'end': 2, 'key': 1.5, 'cue': 'Move into space.'},
            ],
            'drills': [{'id': 'walk', 'title': 'Walk through', 'lvl': 'Walking', 'reps': 3,
                        'how': 'Rehearse the sequence slowly.', 'model': [0, 2, .5],
                        'checks': ['Keep the ball close.']}],
        }],
    }


@pytest.fixture
def work(tmp_path):
    work = tmp_path / 'work'
    (work / 'analysis').mkdir(parents=True)
    example = PACKAGE / 'examples' / 'hocus-pocus'
    (work / 'analysis' / 'analysis.json').write_text((example / 'analysis.json').read_text())
    (work / 'upload.mp4').write_bytes(b'fixture-video')
    (work / 'evidence').mkdir()
    (work / 'evidence' / 'evidence.md').write_text('# Evidence\n\n## Shot 0\n- Touch burst: 3.7 s\n')
    return work


def test_response_validation_accepts_the_format_without_claiming_teaching_quality(lesson, schema):
    checked = experiments.check_response(json.dumps(lesson), schema, 2)
    assert checked['errors'] == []
    assert checked['lesson']['skills'][0]['name'] == 'Direction change'
    assert isinstance(checked['warnings'], list)


@pytest.mark.parametrize('text', ['not JSON', '[]', '{"title":NaN}', '{"title":1e999}'])
def test_non_json_non_object_and_nonfinite_results_are_recorded_as_failures(text, schema):
    checked = experiments.check_response(text, schema, 2)
    assert checked['errors']
    assert checked['lesson'] is None


def test_malformed_nested_model_output_is_a_result_not_a_crash(lesson, schema):
    lesson['skills'] = [None]
    checked = experiments.check_response(json.dumps(lesson), schema, 2)
    assert checked['errors']
    assert checked['lesson'] is None


@pytest.mark.parametrize('where', ['shot', 'skill', 'phase', 'drill'])
def test_timestamps_outside_video_are_detected_without_reconstruction_feedback(lesson, schema, where):
    if where == 'shot':
        lesson['shots'][0]['end'] = 3
    elif where == 'skill':
        lesson['skills'][0]['range'] = [0, 3]
    elif where == 'phase':
        lesson['skills'][0]['phases'][0]['key'] = 3
    else:
        lesson['skills'][0]['drills'][0]['model'] = [0, 3, .5]
    checked = experiments.check_response(json.dumps(lesson), schema, 2)
    assert checked['errors']


def test_experiment_preparation_writes_a_plan_without_a_provider_key(work, tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    out = tmp_path / 'experiment'
    result = experiments.prepare(work, out, 'trial', repeats=2)
    assert result.is_file()
    assert result.name == 'manifest.json'
    manifest = json.loads(result.read_text())
    assert manifest
    assert list(out.rglob('*.md'))


def test_preparation_does_not_overwrite_an_existing_experiment(work, tmp_path):
    out = tmp_path / 'experiment'
    manifest = experiments.prepare(work, out, 'trial')
    before = manifest.read_bytes()
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        experiments.prepare(work, out, 'different')
    assert manifest.read_bytes() == before


def test_context_arms_share_rules_and_do_not_leak_reconstruction_into_baseline(work):
    baseline = make_prompt(work, 'trial', 'video')
    shots = make_prompt(work, 'trial', 'shots')
    full = make_prompt(work, 'trial', 'reconstruction')
    assert shots.startswith(baseline)
    assert full.startswith(shots)
    assert 'Touch burst' not in baseline and 'Touch burst' not in shots
    assert 'candidate contact' in full
    assert 'Advisory camera-cut context' not in baseline
    assert 'Advisory camera-cut context' in shots
    assert '{{' not in baseline
    assert shared_schema()['$defs']['time']['type'] == 'number'


class FakeFiles:
    def __init__(self):
        self.uploads, self.deletes = [], []

    def upload(self, **kwargs):
        self.uploads.append(kwargs)
        return SimpleNamespace(name='files/test-video', uri='https://provider.invalid/test-video',
                               mime_type='video/mp4', state=SimpleNamespace(name='ACTIVE'))

    def delete(self, *, name):
        self.deletes.append(name)


class FakeUsage:
    prompt_token_count = 20
    candidates_token_count = 10
    total_token_count = 30

    def model_dump(self, **kwargs):
        return {'prompt_token_count': 20, 'candidates_token_count': 10, 'total_token_count': 30}


class FakeModels:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def generate_content(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply, model_version='fake-model-version', usage_metadata=FakeUsage())


def fake_client(replies=()):
    return SimpleNamespace(files=FakeFiles(), models=FakeModels(replies))


def test_six_independent_calls_use_one_upload_and_are_not_repeated_on_resume(work, tmp_path, lesson):
    out = tmp_path / 'experiment'
    experiments.prepare(work, out, 'trial', repeats=2)
    sdk = fake_client([json.dumps(lesson)] * 6)
    experiments.run(out, client=sdk)
    assert len(sdk.files.uploads) == 1
    assert len(sdk.models.requests) == 6
    assert sdk.files.deletes == ['files/test-video']
    for request in sdk.models.requests:
        assert request['model'] == 'gemini-4-argon'
        # Every request starts independently; no prior generated lesson is a turn.
        assert len(request['contents']) == 1
    experiments.run(out, client=sdk)
    assert len(sdk.models.requests) == 6
    assert len(sdk.files.uploads) == 1


def test_invalid_content_is_preserved_without_an_automatic_repair(work, tmp_path, lesson):
    out = tmp_path / 'experiment'
    experiments.prepare(work, out, 'trial', repeats=2)
    sdk = fake_client(['not JSON'] + [json.dumps(lesson)] * 5)
    experiments.run(out, client=sdk)
    assert len(sdk.models.requests) == 6
    assert any('not JSON' in path.read_text(errors='replace')
               for path in out.rglob('*') if path.is_file() and path.suffix in ('.txt', '.json', '.md'))


def test_review_report_escapes_provider_text(work, tmp_path, lesson):
    out = tmp_path / 'experiment'
    experiments.prepare(work, out, 'trial', repeats=1)
    lesson['skills'][0]['name'] = '<script>alert(1)</script>'
    sdk = fake_client([json.dumps(lesson)] * 3)
    experiments.run(out, client=sdk)
    report = experiments.render_report(out)
    content = report.read_text()
    assert '<script>alert(1)</script>' not in content
    assert '&lt;script&gt;' in content


def test_transport_failure_is_recorded_without_repair_or_secret_details(work, tmp_path, lesson):
    out = tmp_path / 'experiment'
    experiments.prepare(work, out, 'trial', repeats=2)
    sdk = fake_client([RuntimeError('api-key=private-value')] + [json.dumps(lesson)] * 5)
    experiments.run(out, client=sdk)
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['jobs'][0]['status'] == 'api_error'
    assert sum(j['status'] == 'complete' for j in manifest['jobs']) == 5
    assert len(sdk.models.requests) == 6
    for path in out.rglob('*'):
        if path.is_file() and path.suffix in ('.json', '.txt', '.html', '.md'):
            assert 'private-value' not in path.read_text(errors='replace')
    experiments.run(out, client=sdk)
    assert len(sdk.models.requests) == 6


def test_auth_rejection_stops_remaining_calls_and_cleans_upload(work, tmp_path, lesson):
    out = tmp_path / 'experiment'
    experiments.prepare(work, out, 'trial', repeats=2)
    error = RuntimeError('credential secret')
    error.code = 401
    sdk = fake_client([error])
    with pytest.raises(RuntimeError):
        experiments.run(out, client=sdk)
    manifest = json.loads((out / 'manifest.json').read_text())
    assert len(sdk.models.requests) == 1
    assert manifest['jobs'][0]['status'] == 'api_error'
    assert sum(j['status'] == 'pending' for j in manifest['jobs']) == 5
    assert sdk.files.deletes == ['files/test-video']
    assert not (out / '.running').exists()


def test_resume_does_not_rebill_a_request_with_a_lost_response(work, tmp_path, lesson):
    out = tmp_path / 'experiment'
    path = experiments.prepare(work, out, 'trial', repeats=2)
    manifest = json.loads(path.read_text())
    manifest['jobs'][0]['status'] = 'requesting'
    path.write_text(json.dumps(manifest))
    sdk = fake_client([json.dumps(lesson)] * 5)
    experiments.run(out, client=sdk)
    assert len(sdk.models.requests) == 5
    assert json.loads(path.read_text())['jobs'][0]['status'] == 'requesting'


@pytest.mark.parametrize('which', ['video', 'prompt'])
def test_changed_experiment_input_is_rejected_before_upload(work, tmp_path, which):
    out = tmp_path / 'experiment'
    path = experiments.prepare(work, out, 'trial', repeats=2)
    manifest = json.loads(path.read_text())
    if which == 'video':
        (work / 'upload.mp4').write_bytes(b'different-video')
    else:
        prompt = next(iter(manifest['prompts'].values()))
        (out / prompt['file']).write_text('different prompt')
    sdk = fake_client()
    with pytest.raises(ValueError):
        experiments.run(out, client=sdk)
    assert sdk.files.uploads == []
