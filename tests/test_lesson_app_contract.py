"""The Python mirror of the app's LessonPackage.load/check accepts what the app opens."""
import copy
import json
from pathlib import Path

import pytest

from lesson_pipeline import app_contract

EXAMPLES = Path(__file__).resolve().parents[1] / 'lesson_pipeline' / 'examples'
NAMES = ['chop-and-spin', 'hocus-pocus', 'skill-ladder', 'three-1v1-moves', 'three-levels']
MESH = {'vertexCount': 400, 'footSets': {'left': [1, 2, 3], 'right': [4, 5, 6]}}


def example(name):
    folder = EXAMPLES / name
    return json.loads((folder / 'lesson.resolved.json').read_text()), json.loads((folder / 'analysis.json').read_text())


@pytest.mark.parametrize('name', NAMES)
def test_shipped_examples_pass(name):
    lesson, analysis = example(name)
    app_contract.check(lesson, analysis, MESH)


def mutate(path, value):
    def apply(lesson, analysis, mesh):
        target = {'lesson': lesson, 'analysis': analysis, 'mesh': mesh}
        *parents, last = path
        node = target
        for key in parents:
            node = node[key]
        if value is KeyError:
            del node[last]
        else:
            node[last] = value(node[last]) if callable(value) else value
    return apply


CASES = {
    'skill outside clip': mutate(['lesson', 'skills', 0, 'range'], lambda r: [r[0], 999]),
    'one phase': mutate(['lesson', 'skills', 0, 'phases'], lambda p: p[:1]),
    'phases out of order': mutate(['lesson', 'skills', 0, 'phases'], lambda p: [p[1], p[0], *p[2:]]),
    'key outside phase': mutate(['lesson', 'skills', 0, 'phases', 0, 'key'], 999),
    'drill outside skill': mutate(['lesson', 'skills', 0, 'drills', 0, 'model'], lambda m: [m[0], 999, m[2]]),
    'fractional reps': mutate(['lesson', 'skills', 0, 'drills', 0, 'reps'], 2.5),
    'missing cue': mutate(['lesson', 'skills', 0, 'phases', 0, 'cue'], KeyError),
    'unknown foot': mutate(['lesson', 'skills', 0, 'ballFoot'], 'both'),
    'placeholder left in a time': mutate(['lesson', 'skills', 0, 'phases', 0, 'start'], '{{firstTouch 1 2}}'),
    'schema 2': mutate(['lesson', 'schemaVersion'], 2),
    'short frame series': mutate(['analysis', 'frames', 'yaw'], lambda y: y[:-1]),
    'no shots': mutate(['analysis', 'shots'], []),
    'foot set outside mesh': mutate(['mesh', 'footSets', 'left'], [400]),
    'empty foot set': mutate(['mesh', 'footSets', 'right'], []),
    'missing plants': mutate(['analysis', 'events', 'plants'], KeyError),
}


@pytest.mark.parametrize('case', sorted(CASES))
def test_mutated_lessons_fail(case):
    lesson, analysis = example('chop-and-spin')
    mesh = copy.deepcopy(MESH)
    CASES[case](lesson, analysis, mesh)
    with pytest.raises(app_contract.ContractError):
        app_contract.check(lesson, analysis, mesh)


def test_answer_index_and_order_ids_are_checked():
    lesson, analysis = example('chop-and-spin')
    skill = lesson['skills'][0]
    choice = next(c for c in skill['challenges'] if c['type'] == 'choice')
    choice['answer'] = len(choice['options'])
    with pytest.raises(app_contract.ContractError, match='no right answer'):
        app_contract.check(lesson, analysis, MESH)
    choice['answer'] = 0
    order = next(c for c in skill['challenges'] if c['type'] == 'order')
    order['ids'] = [*order['ids'], 'missing-phase']
    with pytest.raises(app_contract.ContractError, match="doesn't exist"):
        app_contract.check(lesson, analysis, MESH)


def test_bundle_needs_video_and_motion(tmp_path):
    lesson, analysis = example('chop-and-spin')
    for name, value in (('lesson.json', lesson), ('analysis.json', analysis), ('mesh.json', MESH)):
        (tmp_path / name).write_text(json.dumps(value))
    with pytest.raises(app_contract.ContractError, match='video.mp4'):
        app_contract.check_bundle(tmp_path)
    (tmp_path / 'video.mp4').write_bytes(b'v')
    (tmp_path / 'motion').mkdir()
    (tmp_path / 'motion' / 'sequence.json').write_text('{}')
    app_contract.check_bundle(tmp_path)
