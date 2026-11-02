import json
from types import SimpleNamespace

import pytest

from lesson_pipeline import judging


@pytest.fixture
def results(tmp_path):
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'fixture')
    jobs = []
    for i in range(6):
        identifier = f'lesson-{i+1:02}'
        lesson = {'title': f'Candidate {i}', 'subtitle': 'A cue', 'shots': [], 'skills': [],
                  'notes': {'limits': ['Visibility uncertain'], 'tests': ['SECRET_GENERATION_CONTEXT']},
                  'sourceNote': 'SECRET_AUTHOR_METADATA'}
        (tmp_path / (identifier + '.checks.json')).write_text(json.dumps({'lesson': lesson}))
        jobs.append({'id': identifier, 'label': f'Lesson {i+1:02}', 'status': 'complete',
                     'context': 'SECRET_GENERATION_CONTEXT'})
    (tmp_path / 'manifest.json').write_text(json.dumps({'model': 'gemini-4-argon', 'video': str(video),
        'videoSha256': judging._hash(video), 'jobs': jobs}))
    return tmp_path


def review(aliases):
    return {'evaluations': [{'candidate': alias, 'scores': {k: 3 for k in judging.CRITERIA},
                            'strengths': [], 'concerns': [], 'abstention': None} for alias in aliases],
            'ranking_tiers': [list(aliases)], 'preference_reason': '<script>unsafe</script>'}


def test_prepared_judge_is_blind_to_method_and_reverses_order(results):
    output = judging.prepare(results)
    prompts = [(output / f'prompt-{i}.md').read_text() for i in (1, 2)]
    for prompt in prompts:
        assert 'SECRET_' not in prompt
        assert 'generation context' not in prompt
    payloads = [json.loads(p.split('## Candidate lessons (aliases are arbitrary)\n')[1]) for p in prompts]
    assert payloads[1] == list(reversed(payloads[0]))
    assert len({p['candidate'] for p in payloads[0]}) == 6
    assert all('limits' in p['lesson'] for p in payloads[0])
    with pytest.raises(ValueError):
        judging.prepare(results)


def test_checker_accepts_ties_and_abstention_but_rejects_bad_scores():
    aliases = ['K7', 'M2']
    value = review(aliases)
    value['evaluations'][0]['scores']['video_fidelity'] = None
    assert judging.check_review(json.dumps(value), aliases)
    value['evaluations'][0]['scores']['video_fidelity'] = 6
    with pytest.raises(ValueError):
        judging.check_review(json.dumps(value), aliases)


def test_checker_rejects_missing_or_duplicated_candidates():
    value = review(['K7', 'K7'])
    with pytest.raises(ValueError):
        judging.check_review(json.dumps(value), ['K7', 'M2'])


class FakeClient:
    def __init__(self, response):
        self.requests, self.deletes = [], []
        self.files = SimpleNamespace(upload=self.upload, delete=lambda **kw: self.deletes.append(kw))
        self.models = SimpleNamespace(generate_content=self.generate)
        self.response = response

    def upload(self, **kwargs):
        return SimpleNamespace(name='files/fixture', uri='https://provider.invalid/fixture', state='ACTIVE')

    def generate(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return SimpleNamespace(text=json.dumps(self.response), model_version='test-model', usage_metadata=None)


def test_two_fresh_reviews_cleanup_escape_and_resume_without_rebilling(results):
    output = judging.prepare(results)
    plan = json.loads((output / 'plan.json').read_text())
    client = FakeClient(review(plan['aliases']))
    report = judging.run(results, client=client)
    assert len(client.requests) == 2
    assert all(len(r['contents']) == 1 for r in client.requests)
    assert len(client.deletes) == 1
    assert '<script>unsafe</script>' not in report.read_text()
    assert '&lt;script&gt;' in report.read_text()
    judging.run(results, client=client)
    assert len(client.requests) == 2


def test_failure_is_sanitized_and_request_is_not_replayed(results):
    output = judging.prepare(results)
    client = FakeClient(RuntimeError('API_KEY_SECRET'))
    with pytest.raises(judging.generation.GenerationError) as error:
        judging.run(results, client=client)
    assert 'API_KEY_SECRET' not in str(error.value)
    assert len(client.requests) == 1 and len(client.deletes) == 1
    assert json.loads((output / 'plan.json').read_text())['passes'][0]['status'] == 'requesting'
    assert not (output / '.running').exists()


def test_excerpt_audit_distinguishes_existing_and_invented_quotes(results):
    output = judging.prepare(results)
    prompt = (output / 'prompt-1.md').read_text()
    candidates = json.loads(prompt.split('## Candidate lessons (aliases are arbitrary)\n')[1])
    value = review([c['candidate'] for c in candidates])
    value['evaluations'][0]['concerns'] = [
        {'quote': candidates[0]['lesson']['title']}, {'quote': 'An invented quotation 12345'}]
    (output / 'review-1.json').write_text(json.dumps(value))
    entries = judging.audit_quotes(results)['1']
    assert [e['excerptMatched'] for e in entries] == [True, False]
