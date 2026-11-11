"""Python mirror of the app's lesson loader: ``LessonPackage.load`` and ``LessonPackage.check``.

Source: ``Frontend/FootyLevels-Frontend/Models/LessonPackage.swift``. A bundle that passes here
decodes and opens in the app; one that fails would show "This lesson's data doesn't fit together"
(or not decode at all). Keep the two in step: a change to the Swift decoder or check belongs here
too, with a test in ``tests/test_lesson_app_contract.py``.

Unlike ``validate.validate`` (authoring rules, warnings, placeholders), this checks only what the
app needs to open a built bundle without trapping.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

FRAME_SERIES = ('com', 'comDropCm', 'weight', 'left', 'right', 'leftPlanted', 'rightPlanted', 'yaw',
                'ball', 'ballSpeed', 'valid', 'ballAir', 'legSwap')


class ContractError(ValueError):
    pass


def _number(value, where):
    # Swift's JSONDecoder takes any JSON number as Double; booleans are not numbers.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f'{where} must be a number')
    return float(value)


def _integer(value, where):
    # Decoding Int from JSON requires an integral value; 3.0 is accepted, 3.5 is not.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value):
        raise ContractError(f'{where} must be a whole number')
    return int(value)


def _string(value, where):
    if not isinstance(value, str):
        raise ContractError(f'{where} must be text')
    return value


def _boolean(value, where):
    if not isinstance(value, bool):
        raise ContractError(f'{where} must be true or false')
    return value


def _optional(item, key, kind, where):
    if key not in item or item[key] is None:
        return None
    return kind(item[key], f'{where}.{key}')


def _strings(value, where):
    if not isinstance(value, list):
        raise ContractError(f'{where} must be a list of text')
    return [_string(v, f'{where}[{i}]') for i, v in enumerate(value)]


def _object(value, where):
    if not isinstance(value, dict):
        raise ContractError(f'{where} must be an object')
    return value


def _list(value, where):
    if not isinstance(value, list):
        raise ContractError(f'{where} must be a list')
    return value


def _require(item, key, where):
    if key not in item or item[key] is None:
        raise ContractError(f'{where} is missing "{key}"')
    return item[key]


def _decode_lesson(document):
    """Every field ``LessonDocument`` decodes, with its Swift type."""
    d = _object(document, 'lesson')
    _integer(_require(d, 'schemaVersion', 'lesson'), 'schemaVersion')
    for key in ('id', 'title', 'subtitle', 'sourceNote'):
        _string(_require(d, key, 'lesson'), key)
    for key in ('accent', 'videoLabel'):
        _optional(d, key, _string, 'lesson')
    for i, shot in enumerate(_list(_require(d, 'shots', 'lesson'), 'shots')):
        where = f'shots[{i}]'
        shot = _object(shot, where)
        _number(_require(shot, 'start', where), where + '.start')
        _number(_require(shot, 'end', where), where + '.end')
        _string(_require(shot, 'label', where), where + '.label')
        _optional(shot, 'quality', _string, where)
    skills = _list(_require(d, 'skills', 'lesson'), 'skills')
    for i, skill in enumerate(skills):
        where = f'skills[{i}]'
        s = _object(skill, where)
        for key in ('id', 'name', 'short', 'tab', 'intro'):
            _string(_require(s, key, where), f'{where}.{key}')
        if _require(s, 'ballFoot', where) not in ('left', 'right'):
            raise ContractError(f'{where}.ballFoot must be "left" or "right"')
        _optional(s, 'ballLeavesGround', _boolean, where)
        for key in ('range', 'watch'):
            values = _list(_require(s, key, where), f'{where}.{key}')
            if len(values) != 2:
                raise ContractError(f'{where}.{key} must be [start, end]')
            for j, v in enumerate(values):
                _number(v, f'{where}.{key}[{j}]')
        for key in ('watchTitle', 'adds', 'breakdownIntro'):
            _optional(s, key, _string, where)
        if s.get('unsure') is not None:
            _strings(s['unsure'], f'{where}.unsure')
        for j, n in enumerate(_list(s.get('notice') or [], f'{where}.notice')):
            w = f'{where}.notice[{j}]'
            n = _object(n, w)
            _number(_require(n, 't', w), w + '.t')
            _string(_require(n, 'title', w), w + '.title')
            _string(_require(n, 'text', w), w + '.text')
        for j, p in enumerate(_list(_require(s, 'phases', where), f'{where}.phases')):
            w = f'{where}.phases[{j}]'
            p = _object(p, w)
            for key in ('id', 'name', 'cue'):
                _string(_require(p, key, w), f'{w}.{key}')
            for key in ('start', 'end', 'key'):
                _number(_require(p, key, w), f'{w}.{key}')
            for key in ('video', 'shows', 'conf', 'why', 'focus', 'view'):
                _optional(p, key, _string, w)
            _optional(p, 'turn', _boolean, w)
            if p.get('ov') is not None:
                overlays = _object(p['ov'], w + '.ov')
                for key in ('prints', 'paths', 'ghost', 'arrow', 'follow'):
                    _optional(overlays, key, _boolean, w + '.ov')
        for j, dr in enumerate(_list(_require(s, 'drills', where), f'{where}.drills')):
            w = f'{where}.drills[{j}]'
            dr = _object(dr, w)
            for key in ('id', 'title', 'lvl', 'how'):
                _string(_require(dr, key, w), f'{w}.{key}')
            _integer(_require(dr, 'reps', w), w + '.reps')
            for k, v in enumerate(_list(_require(dr, 'model', w), w + '.model')):
                _number(v, f'{w}.model[{k}]')
            _strings(_require(dr, 'checks', w), w + '.checks')
        for j, c in enumerate(_list(s.get('challenges') or [], f'{where}.challenges')):
            w = f'{where}.challenges[{j}]'
            c = _object(c, w)
            kind = _require(c, 'type', w)
            if kind == 'choice':
                _number(_require(c, 't', w), w + '.t')
                for key in ('title', 'prompt', 'explain'):
                    _string(_require(c, key, w), f'{w}.{key}')
                _strings(_require(c, 'options', w), w + '.options')
                _integer(_require(c, 'answer', w), w + '.answer')
                if c.get('play') is not None:
                    for k, v in enumerate(_list(c['play'], w + '.play')):
                        _number(v, f'{w}.play[{k}]')
                _optional(c, 'view', _string, w)
            elif kind == 'tap':
                for key in ('from', 'target'):
                    _number(_require(c, key, w), f'{w}.{key}')
                for key in ('label', 'title', 'prompt', 'explain'):
                    _string(_require(c, key, w), f'{w}.{key}')
                for key in ('rate', 'tolerance'):
                    _optional(c, key, _number, w)
            elif kind == 'order':
                _strings(_require(c, 'ids', w), w + '.ids')
                for key in ('title', 'prompt'):
                    _optional(c, key, _string, w)
                if c.get('shuffled') is not None:
                    _strings(c['shuffled'], w + '.shuffled')
            else:
                raise ContractError(f'{w}.type must be choice, tap or order')


def _decode_analysis(analysis):
    a = _object(analysis, 'analysis')
    _number(_require(a, 'fps', 'analysis'), 'fps')
    _integer(_require(a, 'frameCount', 'analysis'), 'frameCount')
    _number(_require(a, 'duration', 'analysis'), 'duration')
    _number(_require(a, 'ballDiameter', 'analysis'), 'ballDiameter')
    for i, shot in enumerate(_list(_require(a, 'shots', 'analysis'), 'analysis.shots')):
        shot = _object(shot, f'analysis.shots[{i}]')
        _integer(_require(shot, 'start', 'shot'), 'shot.start')
        _integer(_require(shot, 'end', 'shot'), 'shot.end')
    frames = _object(_require(a, 'frames', 'analysis'), 'analysis.frames')
    for key in FRAME_SERIES:
        _list(_require(frames, key, 'analysis.frames'), f'frames.{key}')
    events = _object(_require(a, 'events', 'analysis'), 'analysis.events')
    for i, plant in enumerate(_list(_require(events, 'plants', 'analysis.events'), 'events.plants')):
        plant = _object(plant, f'events.plants[{i}]')
        if plant.get('foot') not in ('left', 'right'):
            raise ContractError('a plant names an unknown foot')
        _integer(_require(plant, 'start', 'plant'), 'plant.start')
        _integer(_require(plant, 'end', 'plant'), 'plant.end')


def check(lesson: dict, analysis: dict, mesh: dict) -> None:
    """Raise ContractError where ``LessonPackage.load`` would fail for this bundle content."""
    _decode_lesson(lesson)
    _decode_analysis(analysis)
    mesh = _object(mesh, 'mesh')
    vertex_count = _integer(_require(mesh, 'vertexCount', 'mesh'), 'mesh.vertexCount')
    foot_sets = _object(_require(mesh, 'footSets', 'mesh'), 'mesh.footSets')
    left, right = (_list(_require(foot_sets, side, 'footSets'), 'footSets.' + side) for side in ('left', 'right'))

    # --- LessonPackage.check, in the same order -------------------------------------------------
    n, frames = int(analysis['frameCount']), analysis['frames']
    if not (n > 0 and analysis['fps'] > 0 and all(len(frames[key]) == n for key in FRAME_SERIES)):
        raise ContractError("the measurements don't cover every frame")
    shots = analysis['shots']
    if not shots or not all(0 <= s['start'] < s['end'] <= n for s in shots):
        raise ContractError("the shots don't fit the clip")
    indices = [*left, *right]
    if not left or not right or not all(isinstance(v, int) and 0 <= v < vertex_count for v in indices):
        raise ContractError("the foot sets don't fit the body mesh")
    if lesson['schemaVersion'] != 1 or not lesson['skills']:
        raise ContractError('unsupported lesson format')
    duration = analysis['duration'] + 0.05
    for skill in lesson['skills']:
        lo, hi = skill['range']
        hi = max(lo, hi)                                          # Swift: lower...max(lower, upper)
        if not (lo >= 0 and hi <= duration and len(skill['phases']) >= 2):
            raise ContractError(f"skill {skill['id']} is outside the clip")
        previous = lo - 0.01
        for phase in skill['phases']:
            if not (phase['start'] >= previous - 0.01 and phase['start'] < phase['end']
                    and phase['end'] <= hi + 0.01 and phase['start'] - 0.01 <= phase['key'] <= phase['end'] + 0.01):
                raise ContractError(f"phase {phase['id']} of {skill['id']} is out of order or outside its skill")
            previous = phase['end']
        for drill in skill['drills']:
            model = drill['model']
            # Swift: fewer than two values decode as 0...0.
            start, end = (model[0], max(model[0], model[1])) if len(model) >= 2 else (0.0, 0.0)
            if not (lo - 0.01 <= start <= hi + 0.01) or end > hi + 0.01:
                raise ContractError(f"drill {drill['id']} of {skill['id']} is outside its skill")
        ids = {p['id'] for p in skill['phases']}
        for challenge in skill.get('challenges') or []:
            if challenge['type'] == 'order' and not set(challenge['ids']) <= ids:
                raise ContractError(f"an order challenge in {skill['id']} names a phase that doesn't exist")
            if challenge['type'] == 'choice' and not 0 <= challenge['answer'] < len(challenge['options']):
                raise ContractError(f"a question in {skill['id']} has no right answer")


def check_bundle(bundle: Path) -> None:
    """The whole ``Lesson-<id>.bundle`` folder, as the app would open it."""
    bundle = Path(bundle)

    def read(name):
        try:
            return json.loads((bundle / name).read_text())
        except (OSError, ValueError):
            raise ContractError(f'This lesson is missing {name}.') from None
    check(read('lesson.json'), read('analysis.json'), read('mesh.json'))
    for name in ('video.mp4', 'motion/sequence.json'):
        if not (bundle / name).is_file():
            raise ContractError(f'This lesson is missing {name}.')
