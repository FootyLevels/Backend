"""Video-only lesson checks use format and time, never reconstruction evidence."""
import copy
import math

import pytest

pytest.importorskip('jsonschema', reason='Install the lessons extra for structure checks')

from lesson_pipeline.validate import validate_structure


@pytest.fixture
def lesson():
    phases = [{'id': name, 'name': name.title(), 'start': i, 'end': i + 1, 'key': i + .5,
               'cue': 'Observe the movement.', 'shows': 'The player changes direction.'}
              for i, name in enumerate(('set', 'turn', 'exit'))]
    drills = [{'id': f'drill-{i}', 'title': 'Rehearse it', 'lvl': 'Walking', 'reps': 3,
               'how': 'Rehearse the visible sequence.', 'model': [0, 3, .5],
               'checks': ['Keep control.', 'Stay balanced.', 'Look ahead.']} for i in range(4)]
    return {
        'schemaVersion': 1, 'id': 'baseline', 'folder': 'baseline', 'title': 'A move',
        'subtitle': 'Watch the direction change.', 'sourceNote': 'the supplied video',
        'video': 'video.mp4', 'shots': [{'start': 0, 'end': 3, 'label': 'Demonstration'}],
        'skills': [{'id': 'turn', 'name': 'Direction change', 'short': 'Turn', 'tab': 'Skill',
                    'ballFoot': 'right', 'range': [0, 3], 'watch': [0, 3],
                    'intro': 'Watch the player turn away.', 'notice': [{'t': 1, 'title': 'Turn', 'text': 'Watch the turn.'}],
                    'phases': phases, 'drills': drills, 'challenges': [
                        {'type': 'choice', 't': 1, 'title': 'Direction', 'prompt': 'What changes?',
                         'options': ['Direction', 'Nothing'], 'answer': 0, 'explain': 'The player turns.'},
                        {'type': 'tap', 'from': 0, 'target': 1, 'label': 'Tap', 'title': 'Spot the turn',
                         'prompt': 'Tap when the player turns.', 'explain': 'Watch the change of direction.'},
                        {'type': 'order', 'ids': ['set', 'turn', 'exit'], 'shuffled': ['exit', 'set', 'turn']},
                    ]}],
    }


def test_numeric_lesson_needs_only_a_duration_and_has_no_content_judgment(lesson):
    assert validate_structure(lesson, 3) == ([], [])
    lesson['skills'][0]['phases'][0]['shows'] = 'The left foot moves 40 cm.'
    # Content accuracy and measured-looking prose are human-review concerns, not hidden 3D checks.
    assert validate_structure(lesson, 3) == ([], [])


@pytest.mark.parametrize('duration', [0, -1, True, math.inf, math.nan, '3'])
def test_invalid_source_duration_is_rejected(lesson, duration):
    assert validate_structure(lesson, duration)[0]


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(skills=[None]),
    lambda d: d['shots'][0].update(end=4),
    lambda d: d['skills'][0].update(range=[0, 4]),
    lambda d: d['skills'][0].update(watch=[0, 4]),
    lambda d: d['skills'][0]['notice'][0].update(t=4),
    lambda d: d['skills'][0]['phases'][0].update(start=1, end=0),
    lambda d: d['skills'][0]['phases'][0].update(key=2),
    lambda d: d['skills'][0]['phases'][1].update(start=.5),
    lambda d: d['skills'][0]['drills'][0].update(model=[0, 4, .5]),
    lambda d: d['skills'][0]['challenges'][0].update(t=4),
    lambda d: d['skills'][0]['challenges'][0].update(answer=2),
    lambda d: d['skills'][0]['challenges'][0].update(play=[0, 4, .5]),
    lambda d: d['skills'][0]['challenges'][1].update(target=4),
    lambda d: d['skills'][0]['challenges'][1].update(target=0),
    lambda d: d['skills'][0]['challenges'][2].update(ids=['set', 'turn', 'unknown']),
    lambda d: d['skills'][0]['challenges'][2].update(ids=['set', 'set', 'exit']),
    lambda d: d['skills'][0]['challenges'][2].update(shuffled=['set', 'set', 'exit']),
    lambda d: d['skills'][0]['phases'][0].update(shows='{{drop 0 1}}'),
    lambda d: d['skills'][0]['phases'][0].update(start='{{firstTouch 0 1}}'),
    lambda d: d['skills'][0]['phases'][0].update(key=math.nan),
])
def test_invalid_structure_or_playback_reference_is_rejected(lesson, mutate):
    mutate(lesson)
    assert validate_structure(lesson, 3)[0]


@pytest.mark.parametrize('group', ['skills', 'phases', 'drills'])
def test_identifiers_must_be_unique_within_their_scope(lesson, group):
    items = lesson['skills'] if group == 'skills' else lesson['skills'][0][group]
    items.append(copy.deepcopy(items[0]))
    assert validate_structure(lesson, 3)[0]


def test_generated_shots_are_checked_without_an_external_cut_list(lesson):
    lesson['shots'] = [{'start': 0, 'end': 1.5, 'label': 'First'}, {'start': 1.5, 'end': 3, 'label': 'Second'}]
    assert validate_structure(lesson, 3) == ([], [])
    lesson['shots'][1]['start'] = 1
    assert validate_structure(lesson, 3)[0]
