"""Grounded multi-pass authoring with scripted provider replies; no key, network or GPU."""
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('jsonschema', reason='Install the lessons extra for authoring tests')

from lesson_pipeline import clips, generation, grounded, passes
from lesson_pipeline.clips import Clip
from lesson_pipeline.measures import resolve_lesson

EXAMPLE = Path(__file__).resolve().parents[1] / 'lesson_pipeline' / 'examples' / 'chop-and-spin'
FPS = 30


class Files:
    def __init__(self):
        self.uploads, self.deletes = [], []

    def upload(self, *, file, config):
        self.uploads.append(Path(file).name)
        return SimpleNamespace(name=f'files/{len(self.uploads)}', uri=f'https://provider.invalid/{len(self.uploads)}',
                               mime_type='video/mp4', state=SimpleNamespace(name='ACTIVE'))

    def get(self, *, name):
        raise AssertionError('already active')

    def delete(self, *, name):
        self.deletes.append(name)


class Models:
    """Replies are strings or callables taking the prompt text."""

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate_content(self, *, model, contents, config):
        prompt = contents[0]['parts'][1]['text']
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        return SimpleNamespace(text=reply(prompt) if callable(reply) else reply)


def sdk(replies):
    return SimpleNamespace(files=Files(), models=Models(replies))


@pytest.fixture
def analysis():
    return json.loads((EXAMPLE / 'analysis.json').read_text())


@pytest.fixture
def authored():
    lesson = json.loads((EXAMPLE / 'lesson.json').read_text())
    lesson['skills'].sort(key=lambda s: s['range'][0])        # the screen lists moves in time order
    return lesson


def session(tmp_path, replies, **kwargs):
    return generation.Session(tmp_path, client=sdk(replies), poll_seconds=0, **kwargs)


# --- screen -----------------------------------------------------------------------------------
def test_screen_rejects_before_any_gpu_work(tmp_path):
    (tmp_path / 'v.mp4').write_bytes(b'v')
    s = session(tmp_path, [json.dumps({'verdict': 'not_football', 'reason': 'A cooking video.', 'moves': []})])
    with pytest.raises(passes.Rejected) as caught:
        passes.screen(s, tmp_path / 'v.mp4', 9.3)
    assert caught.value.code == 'not_football'
    s.close()
    assert s.client.files.deletes == ['files/1']


def test_screen_repairs_are_bounded(tmp_path):
    (tmp_path / 'v.mp4').write_bytes(b'v')
    bad = json.dumps({'verdict': 'lesson', 'reason': 'x', 'moves': [{'name': 'a', 'start': 5, 'end': 99}]})
    s = session(tmp_path, [bad, bad, bad])
    with pytest.raises(generation.GenerationError, match='did not pass validation after 2'):
        passes.screen(s, tmp_path / 'v.mp4', 9.3, max_attempts=2)
    assert len(s.client.models.prompts) == 2
    assert 'inside 0–9.30' in s.client.models.prompts[1]


def test_screen_assigns_unique_slugs_in_order():
    check = passes.check_screen(9.0)
    result, errors = check({'verdict': 'lesson', 'reason': 'Clear.', 'moves': [
        {'name': 'Step Over', 'start': 0, 'end': 2}, {'name': 'step over', 'start': 2, 'end': 4}]})
    assert errors == [] and [m['id'] for m in result['moves']] == ['step-over', 'step-over-2']
    _, errors = check({'verdict': 'lesson', 'reason': 'x', 'moves': [
        {'name': 'a', 'start': 3, 'end': 5}, {'name': 'b', 'start': 1, 'end': 2}]})
    assert any('time order' in e for e in errors)


# --- look -------------------------------------------------------------------------------------
def test_look_converts_close_up_time_to_source_time(tmp_path):
    clip = Clip(tmp_path / 'm.mp4', start=4.0, end=6.0, speed=0.5)
    result, errors = passes.check_look(clip)({'ballFoot': 'right', 'unsure': [], 'phases': [
        {'name': 'Set', 'start': 0, 'end': 1, 'key': 0.5, 'conf': 'high', 'why': 'clear', 'shows': 'steps in'},
        {'name': 'Chop', 'start': 1, 'end': 2.4, 'key': 2, 'conf': 'medium', 'why': 'blurred'},
        {'name': 'Away', 'start': 2.4, 'end': 4, 'key': 3, 'conf': 'low', 'why': 'leaves frame'}]})
    assert errors == []
    assert [(p['start'], p['end'], p['key']) for p in result['phases']] == [(4.0, 4.5, 4.25), (4.5, 5.2, 5.0), (5.2, 6.0, 5.5)]


def test_look_rejects_typed_measurements_and_bad_keys(tmp_path):
    clip = Clip(tmp_path / 'm.mp4', start=0, end=2)
    _, errors = passes.check_look(clip)({'ballFoot': 'left', 'phases': [
        {'name': 'a', 'start': 0, 'end': 1, 'key': 3, 'conf': 'high', 'why': 'x', 'shows': 'drops 12 cm'},
        {'name': 'b', 'start': 1, 'end': 2, 'key': 1.5, 'conf': 'high', 'why': 'x'},
        {'name': 'c', 'start': 2, 'end': 3, 'key': 2.5, 'conf': 'high', 'why': 'x'}]})
    assert any('key 3' in e for e in errors) and any('measurement' in e for e in errors)


# --- teach ------------------------------------------------------------------------------------
def timeline_from(lesson):
    return [{'id': s['id'], 'name': s['name'], 'start': s['range'][0], 'end': s['range'][1], 'ballFoot': s['ballFoot'],
             'phases': [{k: p[k] for k in ('id', 'name', 'start', 'end', 'key')} | {'conf': 'high', 'why': 'clear'}
                        for p in s['phases']]} for s in lesson['skills']]


def test_teach_pins_the_timeline_and_resolves_placeholders(authored, analysis):
    from jsonschema import Draft202012Validator
    from lesson_pipeline.prompting import authored_schema
    timeline = timeline_from(authored)
    reply = copy.deepcopy(authored)
    reply['id'] = 'renamed-by-model'
    reply['skills'][0]['phases'][0]['start'] = 0.5          # the model may not move the timeline
    lesson, errors = passes.check_teach('my-lesson', timeline, analysis, Draft202012Validator(authored_schema()))(reply)
    assert errors == []
    assert lesson['id'] == 'my-lesson' and lesson['skills'][0]['phases'][0]['start'] == timeline[0]['phases'][0]['start']
    resolved = resolve_lesson(lesson, analysis)
    assert '{{' not in json.dumps(resolved)


def test_teach_rejects_typed_numbers_and_changed_phases(authored, analysis):
    from jsonschema import Draft202012Validator
    from lesson_pipeline.prompting import authored_schema
    check = passes.check_teach('my-lesson', timeline_from(authored), analysis, Draft202012Validator(authored_schema()))
    typed = copy.deepcopy(authored)
    typed['skills'][0]['intro'] = 'The hips drop 14 cm here.'
    assert any('Typed measurements' in e for e in check(typed)[1])
    renamed = copy.deepcopy(authored)
    renamed['skills'][0]['phases'][0]['id'] = 'invented'
    assert any('keep its phases' in e for e in check(renamed)[1])


# --- verify -----------------------------------------------------------------------------------
def test_unsupported_claims_are_removed_and_unclear_moved_to_unsure(authored, analysis, tmp_path):
    skill = authored['skills'][0]
    clip = Clip(tmp_path / 'm.mp4', start=skill['range'][0], end=skill['range'][1])
    claims = passes.claims_for(skill, analysis, clip)
    assert claims and '{{' not in json.dumps(claims)              # verified as the learner will read it
    shows = [c for c in claims if c['kind'] == 'shows']
    verdicts = {c['id']: 'supported' for c in claims}
    verdicts[shows[0]['id']], verdicts[shows[1]['id']] = 'unsupported', 'unclear'
    before = len(skill.get('unsure') or [])
    removed = passes.apply_verdicts(skill, claims, verdicts)
    assert removed == 2
    assert 'shows' not in skill['phases'][shows[0]['index']] and 'shows' not in skill['phases'][shows[1]['index']]
    assert len(skill['unsure']) == before + 1 and skill['unsure'][-1].startswith('The clip does not clearly show')


def test_verify_requires_every_claim_once():
    check = passes.check_verify([{'id': 'shows-0'}, {'id': 'choice-1'}])
    _, errors = check({'claims': [{'id': 'shows-0', 'verdict': 'supported'}]})
    assert any('missing: choice-1' in e for e in errors)
    verdicts, errors = check({'claims': [{'id': 'shows-0', 'verdict': 'supported'}, {'id': 'choice-1', 'verdict': 'unclear'}]})
    assert errors == [] and verdicts == {'shows-0': 'supported', 'choice-1': 'unclear'}


# --- deterministic checks -----------------------------------------------------------------------
def test_ball_foot_follows_strong_measurement_and_explains_disagreement(analysis):
    fps = analysis['fps']
    rights = [t['frame'] / fps for t in analysis['events']['touches'] if t['foot'] == 'right']
    a, b = rights[0] - 0.1, rights[2] + 0.1
    assert grounded.decide_ball_foot('right', analysis, a, b) == ('right', None)
    foot, note = grounded.decide_ball_foot('left', analysis, a, b)
    assert foot == 'right' and 'disagree' in note
    assert grounded.decide_ball_foot('unclear', analysis, a, b)[0] == 'right'


def test_keys_snap_to_nearby_measured_events_only(analysis):
    touch = analysis['events']['touches'][0]['frame'] / analysis['fps']
    phases = [{'start': touch - 0.5, 'end': touch + 0.5, 'key': touch + 0.1},
              {'start': touch + 0.6, 'end': touch + 0.7, 'key': touch + 0.65}]
    grounded.snap_keys(phases, analysis)
    assert phases[0]['key'] == round(touch, 3) and phases[1]['key'] == touch + 0.65


def test_challenges_are_topped_up_from_the_timeline(authored, analysis):
    skill = next(s for s in authored['skills'] if s['id'] == 'chop')
    skill['challenges'] = []
    grounded.top_up_challenges(skill, analysis)
    kinds = [c['type'] for c in skill['challenges']]
    assert kinds[0] == 'order' and set(skill['challenges'][0]['ids']) == {p['id'] for p in skill['phases']}


# --- whole chain --------------------------------------------------------------------------------
def make_work(tmp_path, analysis):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg is required for close-up clips')
    work = tmp_path / 'work'
    (work / 'analysis').mkdir(parents=True)
    (work / 'analysis' / 'analysis.json').write_text(json.dumps(analysis))
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc=size=320x240:rate={FPS}',
                    '-frames:v', str(analysis['frameCount']), '-pix_fmt', 'yuv420p', str(work / 'upload.mp4')], check=True)
    return work


def scripted(authored, analysis, verdict='supported'):
    """Replies for screen, one look per move, teach and one verify per move."""
    replies = [json.dumps({'verdict': 'lesson', 'reason': 'One player, clear skills.', 'moves': [
        {'name': s['id'], 'start': s['range'][0], 'end': s['range'][1]} for s in authored['skills']]})]
    for s in authored['skills']:
        first = int(round(max(0.0, s['range'][0] - grounded.MARGIN) * FPS))
        clip = Clip(Path('x'), first / FPS, 0, 0.5)
        replies.append(json.dumps({'ballFoot': s['ballFoot'], 'unsure': [], 'phases': [
            {'name': p['id'], 'start': clip.to_clip(p['start']), 'end': clip.to_clip(p['end']),
             'key': clip.to_clip(p['key']), 'conf': 'medium', 'why': 'Visible but quick.'} for p in s['phases']]}))
    replies.append(json.dumps(authored))

    def verify(prompt):
        ids = re.findall(r'"id": "([a-z]+-\d+)"', prompt)
        return json.dumps({'claims': [{'id': i, 'verdict': verdict, 'reason': 'x'} for i in ids]})
    replies += [verify] * len(authored['skills'])
    return replies


def test_grounded_author_publishes_a_checked_lesson_and_caches_steps(tmp_path, authored, analysis):
    work = make_work(tmp_path, analysis)
    cache = tmp_path / 'cache'
    client = sdk(scripted(authored, analysis))
    path = grounded.author(work, 'my-lesson', client=client, cache=cache, poll_seconds=0, log=lambda *_: None)
    lesson = json.loads(path.read_text())
    assert lesson['id'] == 'my-lesson' and [s['id'] for s in lesson['skills']] == ['flick', 'chop', 'spin']
    report = json.loads((work / 'authoring.json').read_text())
    assert report['mode'] == 'grounded' and report['calls'] == 1 + 3 + 1 + 3 and report['claimsRemoved'] == 0
    # One provider upload per distinct clip (whole video + three close-ups), all deleted.
    assert client.files.uploads.count('upload.mp4') == 1 and len(client.files.uploads) == 4
    assert len(client.files.deletes) == 4
    look = client.models.prompts[1]
    assert '0.5× speed' in look and 'ball contact' in look
    # A retry with the same inputs is served from the cache: no new provider calls or uploads.
    again = sdk([])
    grounded.author(work, 'my-lesson', client=again, cache=cache, poll_seconds=0, log=lambda *_: None)
    assert again.models.prompts == [] and again.files.uploads == []


def test_grounded_author_removes_unsupported_claims_and_still_passes_strict(tmp_path, authored, analysis):
    work = make_work(tmp_path, analysis)
    path = grounded.author(work, 'my-lesson', client=sdk(scripted(authored, analysis, 'unsupported')),
                           poll_seconds=0, log=lambda *_: None)
    lesson = json.loads(path.read_text())
    assert json.loads((work / 'authoring.json').read_text())['claimsRemoved'] > 0
    assert all(c['title'] == 'Which phase is this?' for s in lesson['skills'] for c in s['challenges'] if c['type'] == 'choice')
    assert all(len(s['challenges']) >= 3 for s in lesson['skills'])
    assert all('shows' not in p for s in lesson['skills'] for p in s['phases'])


def test_grounded_author_needs_the_analysis(tmp_path):
    (tmp_path / 'upload.mp4').write_bytes(b'v')
    with pytest.raises(generation.PromptError, match='analysis'):
        grounded.author(tmp_path, 'x', client=sdk([]))


def test_player_box_is_padded_and_bounded():
    tracking = {'frames': [{'person': {'bbox': [100, 50, 140, 150]}}, {'person': {'bbox': [110, 60, 150, 160]}}, {'person': None}]}
    x, y, w, h = clips.player_box(tracking, 0, 3, 320, 240)
    assert (x, y) == (86, 22) and x + w <= 320 and y + h <= 240 and w % 2 == 0 == h % 2
    assert clips.player_box({'frames': [{'person': {'bbox': [0, 0, 320, 240]}}]}, 0, 1, 320, 240) is None
