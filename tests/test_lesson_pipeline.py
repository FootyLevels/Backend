"""The lesson pipeline's offline steps: placeholders, checking, drafting, building and adding to the app.

The five finished lessons in lesson_pipeline/examples, with the measurements they were written
against, are the fixtures. Nothing here needs the GPU server or a video.
"""
import copy
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pytest

from lesson_pipeline import build, draft
from lesson_pipeline.cuts import find_cuts
from lesson_pipeline.measures import Measures, PlaceholderError, resolve_lesson
from lesson_pipeline.validate import validate

PACKAGE = Path(__file__).resolve().parents[1] / 'lesson_pipeline'
EXAMPLES = sorted(p.name for p in (PACKAGE / 'examples').iterdir() if (p / 'lesson.json').exists())
SCHEMA = json.loads((PACKAGE / 'schema' / 'lesson.schema.json').read_text())


def example(name):
    folder = PACKAGE / 'examples' / name
    return json.loads((folder / 'lesson.json').read_text()), json.loads((folder / 'analysis.json').read_text())


def test_all_five_examples_are_present():
    assert EXAMPLES == ['chop-and-spin', 'hocus-pocus', 'skill-ladder', 'three-1v1-moves', 'three-levels']


# --- examples -----------------------------------------------------------------------------------

@pytest.mark.parametrize('name', EXAMPLES)
def test_examples_pass_the_strict_check(name):
    lesson, analysis = example(name)
    errors, warnings = validate(lesson, analysis, strict=True)
    assert errors == [] and warnings == []


@pytest.mark.parametrize('name', EXAMPLES)
def test_examples_still_resolve_to_their_published_text(name):
    lesson, analysis = example(name)
    expected = json.loads((PACKAGE / 'examples' / name / 'lesson.resolved.json').read_text())
    assert resolve_lesson(lesson, analysis) == expected


@pytest.mark.parametrize('name', EXAMPLES)
def test_examples_quote_measurements_only_through_placeholders(name):
    lesson, _ = example(name)
    shows = [p.get('shows', '') for s in lesson['skills'] for p in s['phases']]
    assert any('{{' in text for text in shows)
    resolved = json.dumps(resolve_lesson(*example(name)))
    assert '{{' not in resolved


# --- placeholders -------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def chop():
    lesson, analysis = example('chop-and-spin')
    return Measures(analysis, 'right'), analysis


def test_placeholders_become_the_measured_numbers(chop):
    m, analysis = chop
    assert m.fill_text('about {{drop 7.6 8.1}} cm') == f'about {round(m.drop(7.6, 8.1))} cm'
    assert m.fill_text('{{turn 7.0 7.6}}°') == f'{round(m.turn(7.0, 7.6))}°'
    assert m.fill_text('{{touches 0.8 2.0 ball}}') == str(len(m.touch_list(0.8, 2.0, 'ball')))
    assert m.fill_text('{{travel 4.43 4.95}} m') == f'{m.travel(4.43, 4.95):.1f} m'
    start, end = m.plant_run('stand', 0.6)
    assert m.fill_text('{{plantStart stand 0.6}}–{{plantEnd stand 0.6}}') == f'{start:.2f}–{end:.2f}'


def test_a_lone_placeholder_in_a_number_field_becomes_a_number(chop):
    m, _ = chop
    value = m.fill_number('{{firstTouch 4.55 5.3 ball}}')
    assert isinstance(value, float) and 4.55 <= value <= 5.3
    assert m.fill_number(5.0) == 5.0
    with pytest.raises(PlaceholderError):
        m.fill_number('at {{firstTouch 4.55 5.3 ball}}')


def test_the_gap_never_claims_less_than_a_centimetre():
    _, analysis = example('three-1v1-moves')
    m = Measures(analysis, 'right')
    assert m.gap('ball', 1.6, 2.15) < 0.5          # the foot meets the ball here
    assert m.fill_text('{{gap ball 1.6 2.15}}') == '1'


@pytest.mark.parametrize('text', ['{{dropp 1 2}}', '{{drop one 2}}', '{{gap 1 2 3}}'])
def test_bad_placeholders_are_reported(chop, text):
    m, _ = chop
    with pytest.raises(PlaceholderError):
        m.fill_text(text)


def test_a_plant_that_did_not_happen_is_reported(chop):
    m, analysis = chop
    planted = analysis['frames']['rightPlanted']
    t = next(i for i, p in enumerate(planted) if p == 0) / analysis['fps']
    with pytest.raises(PlaceholderError):
        m.plant_run('ball', t)


# --- the checker --------------------------------------------------------------------------------

def _first(lesson, kind):
    skill = lesson['skills'][0]
    if kind == 'skill':
        return skill
    if kind in ('phase', 'drill'):
        return skill[kind + 's'][0]
    return next(c for s in lesson['skills'] for c in s['challenges'] if c['type'] == kind)


def _required():
    defs = SCHEMA['$defs']
    yield 'lesson', SCHEMA['required']
    for kind in ('skill', 'phase', 'drill'):
        yield kind, defs[kind]['required']
    for variant in defs['challenge']['oneOf']:
        yield variant['properties']['type']['const'], variant['required']


@pytest.mark.parametrize('kind,key', [(kind, key) for kind, keys in _required() for key in keys])
def test_every_field_the_app_needs_is_enforced(kind, key):
    """The schema lists what the app needs to open a lesson; the checker must catch each one missing."""
    lesson, analysis = example('chop-and-spin')
    target = lesson if kind == 'lesson' else _first(lesson, kind)
    del target[key]
    errors, _ = validate(lesson, analysis)
    assert errors, f'removing {kind}.{key} went unnoticed'


def test_typed_numbers_and_named_sides_are_flagged():
    lesson, analysis = example('chop-and-spin')
    phase = lesson['skills'][0]['phases'][1]
    phase['shows'] = 'His hips swing 90° as the left foot plants.'
    _, warnings = validate(lesson, analysis)
    assert any('placeholder' in w for w in warnings)
    assert any('left' in w.lower() for w in warnings)


def test_phases_out_of_order_are_errors():
    lesson, analysis = example('chop-and-spin')
    phases = lesson['skills'][0]['phases']
    phases[0], phases[1] = phases[1], phases[0]
    errors, _ = validate(lesson, analysis)
    assert any('previous phase' in e for e in errors)


def test_a_draft_only_fails_the_strict_check():
    lesson, analysis = example('chop-and-spin')
    lesson['draft'] = True
    assert validate(lesson, analysis)[0] == []
    assert validate(lesson, analysis, strict=True)[0]


# --- drafting -----------------------------------------------------------------------------------

@pytest.mark.parametrize('name', EXAMPLES)
def test_the_automatic_draft_is_a_valid_starting_point(tmp_path, name):
    _, analysis = example(name)
    (tmp_path / 'analysis').mkdir()
    (tmp_path / 'analysis' / 'analysis.json').write_text(json.dumps(analysis))
    lesson = json.loads(draft.build_draft(tmp_path, name, 'Working title').read_text())
    assert lesson['draft'] is True and lesson['reviewNotes']
    errors, _ = validate(lesson, analysis)
    assert errors == []
    assert validate(lesson, analysis, strict=True)[0]          # a draft can't ship as it is
    for skill in lesson['skills']:
        assert 2 <= len(skill['phases']) <= 6
        kinds = {c['type'] for c in skill['challenges']}
        assert {'tap', 'choice'} <= kinds
        assert ('order' in kinds) == (len(skill['phases']) >= 3)


# --- building and adding to the app -------------------------------------------------------------

@pytest.fixture
def work(tmp_path):
    """A work folder as `run` leaves it, with stand-in motion and video."""
    lesson, analysis = example('hocus-pocus')
    root = tmp_path / 'work'
    motion = root / 'analysis' / 'motion'
    motion.mkdir(parents=True)
    (root / 'analysis' / 'analysis.json').write_text(json.dumps(analysis))
    (root / 'analysis' / 'mesh.json').write_text(json.dumps({'vertexCount': 3869, 'faces': '', 'footSets': {'left': [1], 'right': [2]}}))
    (motion / 'sequence.json').write_text(json.dumps({
        'vertexCount': 3869, 'frameCount': analysis['frameCount'], 'fps': analysis['fps'], 'min': [0, 0, 0], 'span': [1, 1, 1],
        'chunks': [{'start': 0, 'count': analysis['frameCount'], 'file': 'body-000000.bin.gz', 'bytes': 4}]}))
    (motion / 'faces.bin').write_bytes(b'\0' * 12)
    (motion / 'body-000000.bin.gz').write_bytes(b'gzip')
    (root / 'upload.mp4').write_bytes(b'video')
    (root / 'lesson.json').write_text(json.dumps(lesson))
    return root


def test_the_app_bundle_holds_a_resolved_lesson_and_the_motion(work, tmp_path):
    bundle = build.build_app(work, work / 'lesson.json', tmp_path / 'app')
    assert bundle.name == 'Lesson-hocus-pocus.bundle'
    names = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    assert names == {'lesson.json', 'analysis.json', 'mesh.json', 'video.mp4',
                     'motion/sequence.json', 'motion/faces.bin', 'motion/body-000000.bin.gz'}
    lesson = json.loads((bundle / 'lesson.json').read_text())
    assert '{{' not in json.dumps(lesson) and 'draft' not in lesson
    assert json.loads((bundle / 'mesh.json').read_text()) == {'vertexCount': 3869, 'footSets': {'left': [1], 'right': [2]}}


def test_numeric_lesson_build_reuses_assets_without_legacy_measurement_validation(work, tmp_path):
    numeric = json.loads((PACKAGE / 'examples' / 'hocus-pocus' / 'lesson.resolved.json').read_text())
    (work / 'lesson.json').write_text(json.dumps(numeric))
    bundle = build.build_app(work, work / 'lesson.json', tmp_path / 'numeric-app')
    result = json.loads((bundle / 'lesson.json').read_text())
    assert result['skills'] == numeric['skills']
    assert '{{' not in (bundle / 'lesson.json').read_text()
    assert (bundle / 'motion' / 'faces.bin').read_bytes() == (work / 'analysis' / 'motion' / 'faces.bin').read_bytes()


def test_the_web_lesson_ships_motion_as_text(work, tmp_path):
    folder = build.build_web(work, work / 'lesson.json', tmp_path / 'web')
    assert (folder / 'index.html').read_text().count('<title>Hocus Pocus Lesson</title>') == 1
    motion = json.loads((folder / 'data' / 'motion.json').read_text())
    assert [c['file'] for c in motion['chunks']] == ['motion-000.gz.txt']
    assert (folder / 'data' / 'motion-000.gz.txt').read_text() == 'Z3ppcA=='


def test_model_title_cannot_break_out_of_the_exported_html_title(work, tmp_path):
    payload = '</title><script id="model-injection">alert(1)</script><title>'
    lesson = json.loads((work / 'lesson.json').read_text())
    lesson['title'] = payload
    (work / 'lesson.json').write_text(json.dumps(lesson))
    folder = build.build_web(work, work / 'lesson.json', tmp_path / 'web')

    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.injected = False
            self.in_title = False
            self.title = ''

        def handle_starttag(self, tag, attrs):
            self.injected |= tag == 'script' and ('id', 'model-injection') in attrs
            if tag == 'title':
                self.in_title = True

        def handle_endtag(self, tag):
            if tag == 'title':
                self.in_title = False

        def handle_data(self, data):
            if self.in_title:
                self.title += data

    parsed = Tags()
    parsed.feed((folder / 'index.html').read_text())
    assert not parsed.injected
    assert parsed.title == payload


def test_exported_sibling_links_reject_executable_and_cross_origin_relative_urls():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed to execute the web player URL guard')
    template = build.WEB_TEMPLATE.read_text()
    start = template.index('function safeURL(')
    helper = template[start:template.index('\n}', start) + 2]
    inputs = ['../another-lesson/', '/lessons/move', 'https://example.com/lesson',
              'http://localhost:8000/lesson', 'javascript:alert(1)', 'JaVaScRiPt:alert(1)',
              'data:text/html,<script>alert(1)</script>', '//evil.example/lesson',
              '\\\\evil.example/lesson', 'https://example.com/\nlesson', 'file:///private/video',
              'blob:https://example.com/id', 'http://[invalid']
    script = helper + '\nprocess.stdout.write(JSON.stringify(' + json.dumps(inputs) + '.map(safeURL)));'
    result = subprocess.run([node, '-e', script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == inputs[:4] + ['#'] * (len(inputs) - 4)


@pytest.mark.parametrize('folder', ['../victim', '/tmp/victim', 'nested/victim'])
def test_web_output_folder_cannot_escape_the_lesson_export_root(work, tmp_path, folder):
    victim = tmp_path / 'victim'
    (victim / 'data').mkdir(parents=True)
    sentinel = victim / 'data' / 'keep.txt'
    sentinel.write_text('existing lesson')
    lesson = json.loads((work / 'lesson.json').read_text())
    lesson['folder'] = folder
    (work / 'lesson.json').write_text(json.dumps(lesson))
    with pytest.raises(build.BuildError):
        build.build_web(work, work / 'lesson.json', tmp_path / 'web')
    assert sentinel.read_text() == 'existing lesson'


def test_web_output_uses_lesson_id_when_optional_folder_is_absent(work, tmp_path):
    lesson = json.loads((work / 'lesson.json').read_text())
    del lesson['folder']
    (work / 'lesson.json').write_text(json.dumps(lesson))
    folder = build.build_web(work, work / 'lesson.json', tmp_path / 'web')
    assert folder == tmp_path / 'web' / lesson['id']
    assert (folder / 'index.html').is_file()


def test_a_lesson_with_errors_is_not_built(work, tmp_path):
    lesson = json.loads((work / 'lesson.json').read_text())
    lesson['skills'][0]['range'] = [0, 999]
    (work / 'lesson.json').write_text(json.dumps(lesson))
    with pytest.raises(build.BuildError):
        build.build_app(work, work / 'lesson.json', tmp_path / 'app')


def test_integrate_replaces_the_bundle_inside_the_app(work, tmp_path):
    frontend = tmp_path / 'Frontend'
    (frontend / 'FootyLevels-Frontend').mkdir(parents=True)
    bundle = build.build_app(work, work / 'lesson.json', tmp_path / 'app')
    dest = build.integrate(bundle, frontend)
    assert dest == frontend / 'FootyLevels-Frontend' / 'SampleData' / 'Lessons' / 'Lesson-hocus-pocus.bundle'
    (dest / 'stale.txt').write_text('old')
    build.integrate(bundle, frontend)
    assert not (dest / 'stale.txt').exists()
    with pytest.raises(build.BuildError):
        build.integrate(bundle, tmp_path)


# --- cuts ---------------------------------------------------------------------------------------

def test_a_cut_is_one_dominant_frame():
    times = np.arange(0, 10, 1 / 30)
    scores = np.full(len(times), 0.02)
    scores[120] = 0.6                       # a cut at 4.0 s
    scores[200:206] = 0.4                   # a whip pan: several high frames, not a cut
    scores[5] = 0.9                         # too close to the start to trust
    assert find_cuts(times, scores) == [4.0]


def test_strict_holds_a_skill_to_the_definition_of_done():
    lesson, analysis = example('chop-and-spin')
    lesson['skills'][0]['drills'] = lesson['skills'][0]['drills'][:2]
    errors, warnings = validate(lesson, analysis)
    assert errors == [] and any('2 drills' in w for w in warnings)
    assert any('2 drills' in e for e in validate(lesson, analysis, strict=True)[0])


def test_a_placeholder_measuring_another_moment_is_flagged():
    lesson, analysis = example('chop-and-spin')
    phase = lesson['skills'][0]['phases'][1]              # the chop, about 5 s
    phase['shows'] = 'His body drops about {{drop 0.5 1.0}} cm.'
    _, warnings = validate(lesson, analysis)
    assert any('outside this phase' in w for w in warnings)


def test_touch_counts_read_as_words(chop):
    m, _ = chop
    n = len(m.touch_list(0.8, 2.0, 'ball'))
    assert m.fill_text('{{touchesText 0.8 2.0 ball}}') == f'{n} touch' + ('' if n == 1 else 'es')
    assert m.fill_text('{{touchesText 8.9 9.0 ball}}') in ('no touches', '1 touch')


def test_the_gpu_backend_may_belong_to_another_account(monkeypatch):
    from lesson_pipeline import remote
    monkeypatch.setattr(remote.Server, 'ssh', lambda self, command, timeout=120: '/home/me')
    own = remote.Server('me@gpu')
    assert own.root == '/home/me/skill-lab'
    shared = remote.Server('me@gpu', 8012, '/home/teammate/skill-lab')
    assert shared.root == '/home/teammate/skill-lab' and shared.home == '/home/me'
    with pytest.raises(remote.RemoteError):
        remote.Server('-oProxyCommand=x')


def test_a_reading_of_zero_is_flagged():
    lesson, analysis = example('chop-and-spin')
    m = Measures(analysis, 'right')
    flat = next(t / 10 for t in range(0, 40) if round(m.drop(t / 10, t / 10 + 0.1)) == 0)
    lesson['skills'][2]['phases'][0]['shows'] = f'His body drops about {{{{drop {flat:g} {flat + 0.1:g}}}}} cm.'
    _, warnings = validate(lesson, analysis)
    assert any('comes out as 0' in w for w in warnings)


# --- the API authoring prompt -------------------------------------------------------------------

@pytest.fixture
def prompt_work(tmp_path, monkeypatch):
    from lesson_pipeline import prompting
    work = tmp_path / 'video-only'
    work.mkdir()
    (work / 'upload.mp4').write_bytes(b'video')
    monkeypatch.setattr(prompting, 'video_metadata',
                        lambda *args, **kwargs: {'duration': 9.23, 'fps': 30, 'frameCount': 277}, raising=False)
    return work


def test_the_prompt_contains_only_video_instructions_and_schema(prompt_work):
    from lesson_pipeline.prompting import make_prompt
    prompt = make_prompt(prompt_work, 'my-move')
    assert 'my-move' in prompt and '9.23' in prompt
    assert '"$schema"' in prompt
    assert 'Watch' in prompt and 'Practise' in prompt
    for excluded in ('A finished lesson', 'hocus-pocus', 'What the 3D measured',
                     'Touch burst', '{{touchesText', '4.43'):
        assert excluded not in prompt
    assert not (prompt_work / 'analysis').exists()
    assert not (prompt_work / 'evidence').exists()


def test_optional_analysis_and_evidence_do_not_change_the_minimal_prompt(prompt_work):
    from lesson_pipeline.prompting import make_prompt
    baseline = make_prompt(prompt_work, 'my-move')
    (prompt_work / 'analysis').mkdir()
    (prompt_work / 'analysis' / 'analysis.json').write_text('{"forbidden":"SHOULD_NOT_SEND_ANALYSIS"}')
    (prompt_work / 'evidence').mkdir()
    (prompt_work / 'evidence' / 'evidence.md').write_text('SHOULD_NOT_SEND_EVIDENCE')
    assert make_prompt(prompt_work, 'my-move') == baseline
