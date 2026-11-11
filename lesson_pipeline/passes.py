"""The four model passes of grounded authoring: screen, look, teach and verify.

Each is a prompt (``prompting.py``), a small JSON answer and a check whose findings drive a
bounded repair through ``generation.Session.ask``. Checks fix what the model may not choose
(ids, the timeline, the output file names) and reject what it may not invent (typed numbers,
times outside the clip, unanswered claims).
"""
from __future__ import annotations

import math
import re

from . import prompting
from .clips import Clip
from .generation import GenerationError
from .measures import PLACEHOLDER, PlaceholderError, resolve_lesson
from .validate import TYPED_NUMBER, validate

VERDICTS = {'lesson', 'not_football', 'no_clear_player', 'unclear'}
CONFIDENCE = {'high', 'medium', 'low'}
CLAIM_VERDICTS = {'supported', 'unclear', 'unsupported'}
MAX_MOVES = 4
MIN_MOVE = 0.4


class Rejected(GenerationError):
    """The screen pass found nothing to teach; ``code`` is the verdict."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


def _finite(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _text(value, limit=400) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


def slug(name: str, taken: set[str]) -> str:
    base = re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')[:30] or 'move'
    if not base[0].isalnum():
        base = 'm' + base
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f'{base[:27]}-{n}', n + 1
    taken.add(candidate)
    return candidate


# --- 1. Screen ------------------------------------------------------------------------------------
def check_screen(duration: float):
    def check(value):
        if not isinstance(value, dict) or value.get('verdict') not in VERDICTS:
            return None, ['Return {"verdict", "reason", "moves"} with a verdict from the list.']
        errors = []
        if not _text(value.get('reason')):
            errors.append('Give a short "reason".')
        moves = value.get('moves')
        if not isinstance(moves, list):
            return None, ['"moves" must be a list.']
        if value['verdict'] != 'lesson':
            return ({'verdict': value['verdict'], 'reason': str(value.get('reason', ''))[:400], 'moves': []},
                    errors)
        if not 1 <= len(moves) <= MAX_MOVES:
            errors.append(f'List 1–{MAX_MOVES} moves for a lesson verdict.')
        out, taken, previous = [], set(), 0.0
        for i, move in enumerate(moves[:MAX_MOVES]):
            where = f'moves[{i}]'
            if not isinstance(move, dict) or not _text(move.get('name'), 80):
                errors.append(f'{where}: needs a short name.')
                continue
            start, end = move.get('start'), move.get('end')
            if not (_finite(start) and _finite(end)):
                errors.append(f'{where}: start and end must be numbers of seconds.')
                continue
            if not (0 <= start < end <= duration + 0.05):
                errors.append(f'{where}: {start}–{end} must be ordered and inside 0–{duration:.2f} s.')
            elif end - start < MIN_MOVE:
                errors.append(f'{where}: a move lasts at least {MIN_MOVE} s; merge or drop it.')
            if start < previous - 0.05:
                errors.append(f'{where}: moves must be in time order without overlapping.')
            previous = max(previous, end)
            out.append({'id': slug(move['name'], taken), 'name': move['name'].strip(),
                        'start': round(max(0.0, start), 3), 'end': round(min(duration, end), 3)})
        return {'verdict': 'lesson', 'reason': value['reason'].strip(), 'moves': out}, errors
    return check


def screen(session, video, duration: float, *, max_attempts=2) -> dict:
    """Moves with rough ranges; raises ``Rejected`` when there is nothing to teach."""
    result = session.ask('screen', video, prompting.screen_prompt(duration), check_screen(duration),
                         fps=5, max_attempts=max_attempts)
    if result['verdict'] != 'lesson' or not result['moves']:
        raise Rejected(result['verdict'], result['reason'] or 'Nothing to teach in this video.')
    return result


# --- 3. Look closely --------------------------------------------------------------------------
def check_look(clip: Clip):
    limit = clip.duration

    def check(value):
        if not isinstance(value, dict) or not isinstance(value.get('phases'), list):
            return None, ['Return {"ballFoot", "phases", "unsure"}.']
        errors = []
        if value.get('ballFoot') not in ('left', 'right', 'unclear'):
            errors.append('"ballFoot" must be "left", "right" or "unclear".')
        phases = value['phases']
        if not 3 <= len(phases) <= 6:
            errors.append('Give 3–6 phases.')
        out, previous, taken = [], 0.0, set()
        for i, phase in enumerate(phases[:6]):
            where = f'phases[{i}]'
            if not isinstance(phase, dict) or not _text(phase.get('name'), 60):
                errors.append(f'{where}: needs a short name.')
                continue
            start, end, key = (phase.get(k) for k in ('start', 'end', 'key'))
            if not all(_finite(v) for v in (start, end, key)):
                errors.append(f'{where}: start, end and key must be numbers of clip seconds.')
                continue
            if not (-0.01 <= start < end <= limit + 0.05):
                errors.append(f'{where}: {start}–{end} must be ordered and inside 0–{limit:.2f} s of this clip.')
            if not start - 0.01 <= key <= end + 0.01:
                errors.append(f'{where}: key {key} must lie inside the phase.')
            if start < previous - 0.05:
                errors.append(f'{where}: phases must be in time order without overlapping.')
            previous = max(previous, end)
            if phase.get('conf') not in CONFIDENCE:
                errors.append(f'{where}: conf must be high, medium or low.')
            if not _text(phase.get('why')):
                errors.append(f'{where}: say why ("why") the confidence is what it is.')
            if phase.get('shows') is not None and not _text(phase.get('shows'), 600):
                errors.append(f'{where}: "shows" must be a short description.')
            if TYPED_NUMBER.search(str(phase.get('shows', ''))):
                errors.append(f'{where}: "shows" types a measurement; describe it in words.')
            a, b = clip.to_source(start), clip.to_source(end)
            out.append({'id': slug(phase['name'], taken), 'name': phase['name'].strip(),
                        'start': round(a, 3), 'end': round(max(b, a + 0.01), 3),
                        'key': round(min(max(clip.to_source(key), a), b), 3),
                        'shows': str(phase.get('shows') or '').strip(), 'conf': phase.get('conf'),
                        'why': str(phase.get('why') or '').strip()})
        unsure = value.get('unsure', [])
        if not isinstance(unsure, list) or not all(_text(u) for u in unsure):
            errors.append('"unsure" must be a list of short sentences.')
            unsure = []
        return {'ballFoot': value.get('ballFoot'), 'phases': out, 'unsure': [u.strip() for u in unsure][:6]}, errors
    return check


def look(session, clip: Clip, name: str, hints: list[str], *, max_attempts=3) -> dict:
    prompt = prompting.look_prompt(name, clip.duration, clip.speed, hints)
    return session.ask('look-' + clip.path.stem, clip.path, prompt, check_look(clip), fps=10,
                       max_attempts=max_attempts)


# --- 4. Teach ---------------------------------------------------------------------------------
def fix_to_timeline(lesson: dict, lesson_id: str, timeline: list[dict]) -> list[str]:
    """Pin what teach may not change: identity, skills, phase timing. Returns findings."""
    lesson.update(schemaVersion=1, id=lesson_id, folder=lesson_id, video='video.mp4')
    lesson.pop('draft', None)
    errors = []
    skills = lesson.get('skills')
    if not isinstance(skills, list) or [s.get('id') if isinstance(s, dict) else None for s in skills] != [m['id'] for m in timeline]:
        return ['Write exactly one skill per timeline move, in order, with the timeline ids: '
                + ', '.join(m['id'] for m in timeline) + '.']
    for skill, move in zip(skills, timeline):
        skill['range'] = [move['start'], move['end']]
        skill['ballFoot'] = move['ballFoot']
        phases = skill.get('phases')
        expected = [p['id'] for p in move['phases']]
        if not isinstance(phases, list) or [p.get('id') if isinstance(p, dict) else None for p in phases] != expected:
            errors.append(f"skill {move['id']!r}: keep its phases {expected} in order.")
            continue
        for phase, fixed in zip(phases, move['phases']):
            phase.update(start=fixed['start'], end=fixed['end'], key=fixed['key'])
            phase.setdefault('conf', fixed['conf'])
            phase.setdefault('why', fixed['why'])
    return errors


def check_teach(lesson_id: str, timeline: list[dict], analysis: dict, validator):
    def check(value):
        if not isinstance(value, dict):
            return None, ['Return one lesson JSON object.']
        lesson = value
        errors = fix_to_timeline(lesson, lesson_id, timeline)
        if errors:
            return lesson, errors
        errors = [f"schema {'.'.join(map(str, e.absolute_path))}: {e.message[:300]}"
                  for e in list(validator.iter_errors(lesson))[:20]]
        if errors:
            return lesson, errors
        typed = [where for where, text in _texts(lesson) if TYPED_NUMBER.search(PLACEHOLDER.sub('', text))]
        if typed:
            errors.append('Typed measurements found; use placeholders instead: ' + ', '.join(typed[:8]))
        errors += validate(lesson, analysis, strict=True)[0]
        return lesson, errors
    return check


def _texts(node, where='lesson'):
    if isinstance(node, str):
        yield where, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _texts(v, f'{where}.{k}')
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _texts(v, f'{where}[{i}]')


def teach(session, video, lesson_id: str, duration: float, timeline: list[dict], analysis: dict, *,
          max_attempts=3) -> dict:
    from jsonschema import Draft202012Validator
    schema = prompting.authored_schema()
    prompt = prompting.teach_prompt(lesson_id, duration, timeline, schema)
    return session.ask('teach', video, prompt, check_teach(lesson_id, timeline, analysis, Draft202012Validator(schema)),
                       fps=5, max_attempts=max_attempts)


# --- 5. Verify --------------------------------------------------------------------------------
def claims_for(skill: dict, analysis: dict, clip: Clip) -> list[dict]:
    """What a skill states about the video, resolved, in close-up clip time."""
    try:
        resolved = resolve_lesson({'skills': [skill]}, analysis)['skills'][0]
    except (PlaceholderError, KeyError, TypeError, ValueError):
        resolved = skill
    claims = []

    def add(kind, index, start, end, text):
        claims.append({'id': f'{kind}-{index}', 'kind': kind, 'index': index,
                       'start': round(clip.to_clip(max(start, clip.start)), 2),
                       'end': round(clip.to_clip(min(end, clip.end)), 2), 'text': text})
    for i, phase in enumerate(resolved.get('phases', [])):
        if phase.get('shows'):
            add('shows', i, phase['start'], phase['end'], f"{phase['name']}: {phase['shows']}")
    for i, notice in enumerate(resolved.get('notice', [])):
        add('notice', i, notice['t'] - 0.3, notice['t'] + 0.3, f"{notice['title']}: {notice['text']}")
    for i, challenge in enumerate(resolved.get('challenges', [])):
        if challenge.get('type') == 'choice':
            answer = challenge['options'][challenge['answer']] if 0 <= challenge.get('answer', -1) < len(challenge.get('options', [])) else ''
            add('choice', i, challenge['t'] - 0.4, challenge['t'] + 0.4,
                f"{challenge['prompt']} Answer: {answer}. {challenge.get('explain', '')}".strip())
    return [c for c in claims if c['start'] <= c['end']]


def check_verify(claims: list[dict]):
    wanted = {c['id'] for c in claims}

    def check(value):
        if not isinstance(value, dict) or not isinstance(value.get('claims'), list):
            return None, ['Return {"claims": [...]}.']
        verdicts, errors = {}, []
        for item in value['claims']:
            if not isinstance(item, dict) or item.get('id') not in wanted or item.get('verdict') not in CLAIM_VERDICTS:
                errors.append('Each entry needs a listed claim id and a verdict: supported, unclear or unsupported.')
                continue
            if item['id'] in verdicts:
                errors.append(f"Answer claim {item['id']} once.")
            verdicts[item['id']] = item['verdict']
        missing = sorted(wanted - verdicts.keys())
        if missing:
            errors.append('Answer every claim; missing: ' + ', '.join(missing))
        return verdicts, errors
    return check


def verify(session, clip: Clip, name: str, claims: list[dict], *, max_attempts=2) -> dict:
    if not claims:
        return {}
    public = [{k: c[k] for k in ('id', 'start', 'end', 'text')} for c in claims]
    prompt = prompting.verify_prompt(name, clip.duration, clip.speed, public)
    return session.ask('verify-' + clip.path.stem, clip.path, prompt, check_verify(claims), fps=10,
                       max_attempts=max_attempts)


def apply_verdicts(skill: dict, claims: list[dict], verdicts: dict) -> int:
    """Remove unsupported claims, move unclear ones to ``unsure``. Returns the number removed."""
    removed = {'shows': set(), 'notice': set(), 'choice': set()}
    unsure = list(skill.get('unsure') or [])
    for claim in claims:
        verdict = verdicts.get(claim['id'], 'unclear')
        if verdict == 'supported':
            continue
        removed[claim['kind']].add(claim['index'])
        if verdict == 'unclear':
            note = 'The clip does not clearly show this: ' + claim['text']
            if note not in unsure:
                unsure.append(note[:400])
    for i in removed['shows']:
        skill['phases'][i].pop('shows', None)
        if skill['phases'][i].get('conf') == 'high':
            skill['phases'][i]['conf'] = 'medium'
    skill['notice'] = [n for i, n in enumerate(skill.get('notice', [])) if i not in removed['notice']]
    skill['challenges'] = [c for i, c in enumerate(skill.get('challenges', [])) if i not in removed['choice']]
    if unsure:
        skill['unsure'] = unsure
    return sum(len(v) for v in removed.values())

