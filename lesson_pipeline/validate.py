"""Checks a lesson document against its video's analysis before anything is built.

Errors stop the build. Warnings point at things an author should look at again.
"""
from __future__ import annotations

import re

from .measures import PLACEHOLDER, Measures, PlaceholderError, resolve_lesson

SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}$')
VIEWS = {'video', 'side', 'behind', 'top'}
FOCUS = {'weight', 'stand', 'ball', 'turn', 'balls', 'drop'}
OVERLAYS = {'prints', 'paths', 'ghost', 'arrow', 'follow'}
# The playbook's definition of done; `check --strict` holds a lesson to it.
PHASES, DRILLS, CHALLENGES = (3, 6), (4, 5), (3, 4)
PLANT_PLACEHOLDERS = {'plantStart', 'plantEnd', 'plantLength'}
TYPED_NUMBER = re.compile(r'\b\d+(?:\.\d+)?\s?(?:cm|°|m/s|degrees)')
SIDE_CLAIM = re.compile(r'\b(left|right) (foot|leg)\b', re.I)


ZERO_READS_ODDLY = {'drop', 'turn', 'travel', 'ballSpeed', 'weightPct', 'touches'}


def _zero_readings(text: str, analysis: dict, ball_foot) -> list[str]:
    """Placeholders in ``text`` that come out as 0, which reads oddly in a sentence."""
    out = []
    m = Measures(analysis, ball_foot if ball_foot in ('left', 'right') else 'right')
    for match in PLACEHOLDER.finditer(text):
        name = match.group(1)
        if name not in ZERO_READS_ODDLY:
            continue
        try:
            shown = m.evaluate(name, match.group(2).split())[1]
        except PlaceholderError:
            continue                                            # reported when the lesson is resolved
        if shown in ('0', '-0', '0.0'):
            out.append(match.group(0)[2:-2].strip())
    return out


def _placeholder_windows(text: str, analysis: dict, ball_foot) -> list[tuple[str, tuple[float, float]]]:
    """The stretch of video each placeholder in ``text`` measures; for a plant, the planted spell itself."""
    out = []
    for m in PLACEHOLDER.finditer(text):
        name, args = m.group(1), m.group(2).split()
        times = [float(a) for a in args if re.fullmatch(r'\d+(?:\.\d+)?', a)]
        if not times:
            continue
        if name in PLANT_PLACEHOLDERS:
            try:
                run = Measures(analysis, ball_foot if ball_foot in ('left', 'right') else 'right').plant_run(args[0], times[0])
            except (PlaceholderError, IndexError):
                continue                                        # reported when the lesson is resolved
            out.append((m.group(0)[2:-2].strip(), (run[0], run[1])))
        else:
            out.append((m.group(0)[2:-2].strip(), (min(times), max(times))))
    return out


def validate(lesson: dict, analysis: dict, strict: bool = False, hand_typed: bool = True) -> tuple[list[str], list[str]]:
    """``hand_typed`` is False for lesson scripts, whose numbers are computed, not typed."""
    errors, warnings = [], []
    err, warn = errors.append, warnings.append

    def missing(item, keys, where):
        for k in keys:
            if not isinstance(item.get(k), str) or not item[k].strip():
                err(f'{where}: "{k}" is required')
    dur = float(analysis['duration'])

    if lesson.get('schemaVersion') != 1:
        err('schemaVersion must be 1')
    if not SLUG.match(str(lesson.get('id', ''))):
        err('id must be a short lowercase slug (letters, digits, hyphens)')
    for k in ('title', 'subtitle', 'video', 'sourceNote'):
        if not isinstance(lesson.get(k), str) or not lesson[k].strip():
            err(f'"{k}" is required')
    if lesson.get('draft'):
        (err if strict else warn)('the lesson is still marked "draft": true')
    if not isinstance(lesson.get('shots'), list):
        err('"shots" is required (a list, which may be empty)')
    for s in lesson.get('shots') or []:
        if not (0 <= s.get('start', -1) < s.get('end', -1) <= dur + 0.05):
            err(f'shot {s} is outside the video (0–{dur:.2f} s)')
        if not s.get('label'):
            err(f'shot {s} needs a label')
    raw_skills = lesson.get('skills') or []
    if not raw_skills:
        err('a lesson needs at least one skill')
    # Structure is checked on the resolved copy (a time may itself be a placeholder);
    # wording is checked on what the author wrote.
    try:
        skills = resolve_lesson(lesson, analysis)['skills'] if raw_skills else []
    except (PlaceholderError, KeyError, TypeError) as e:
        err(f'placeholder: {e}')
        skills = raw_skills
    seen = set()
    for si, (sk, raw) in enumerate(zip(skills, raw_skills)):
        where = f'skill {sk.get("id", si)!r}'
        if not SLUG.match(str(sk.get('id', ''))) or sk.get('id') in seen:
            err(f'{where}: id must be a unique slug')
        seen.add(sk.get('id'))
        for k in ('name', 'short', 'tab', 'intro'):
            if not isinstance(sk.get(k), str) or not sk[k].strip():
                err(f'{where}: "{k}" is required')
        if sk.get('ballFoot') not in ('left', 'right'):
            err(f'{where}: ballFoot must be "left" or "right"')
        rng = sk.get('range') or [0, 0]
        a, b = (float(x) if isinstance(x, (int, float)) else 0 for x in rng)
        if not (0 <= a < b <= dur + 0.05):
            err(f'{where}: range {rng} must lie inside the video (0–{dur:.2f} s)')
        w = sk.get('watch') or [0, 0]
        if not (a - 0.01 <= w[0] < w[1] <= b + 0.01):
            err(f'{where}: watch {w} must lie inside the range')
        for n in sk.get('notice', []):
            if not (a - 0.01 <= n.get('t', -1) <= b + 0.01) or not n.get('title') or not n.get('text'):
                err(f'{where}: notice {n.get("title")!r} needs t inside the range, a title and text')
        phases = sk.get('phases') or []
        if len(phases) < 2:
            err(f'{where}: needs at least two phases')
        for label, items, (lo, hi) in (('phases', phases, PHASES), ('drills', sk.get('drills') or [], DRILLS),
                                       ('challenges', sk.get('challenges') or [], CHALLENGES)):
            if not lo <= len(items) <= hi:
                (err if strict else warn)(f'{where}: has {len(items)} {label}; a finished skill has {lo}–{hi}')
        ids, prev = set(), a - 0.01
        for p in phases:
            pw = f'{where}, phase {p.get("id")!r}'
            if not p.get('id') or p['id'] in ids:
                err(f'{pw}: id must be unique within the skill')
            ids.add(p.get('id'))
            for k in ('name', 'cue'):
                if not p.get(k):
                    err(f'{pw}: "{k}" is required')
            start, end, key = (p.get(x) for x in ('start', 'end', 'key'))
            if all(isinstance(x, (int, float)) for x in (start, end)):
                if not (a - 0.01 <= start < end <= b + 0.01):
                    err(f'{pw}: {start}–{end} must lie inside the skill range {a}–{b}')
                if start < prev - 0.01:
                    err(f'{pw}: starts before the previous phase ends (phases must be in order, not overlapping)')
                prev = end
                if not isinstance(key, (int, float)):
                    err(f'{pw}: key (the moment to freeze on) is required')
                elif not (start - 0.01 <= key <= end + 0.01):
                    err(f'{pw}: key {key} must lie inside the phase')
            else:
                err(f'{pw}: start and end must be numbers')
            if p.get('conf') not in (None, 'high', 'medium', 'low'):
                err(f'{pw}: conf must be high, medium or low')
            if p.get('conf') and not p.get('why'):
                warn(f'{pw}: say why ("why") the confidence is what it is')
            if p.get('view') and p['view'] not in VIEWS:
                err(f'{pw}: view must be one of {sorted(VIEWS)}')
            if p.get('focus') and p['focus'] not in FOCUS:
                err(f'{pw}: focus must be one of {sorted(FOCUS)}')
            if set((p.get('ov') or {}).keys()) - OVERLAYS:
                err(f'{pw}: ov keys must be among {sorted(OVERLAYS)}')
            raw_p = next((q for q in raw.get('phases', []) if q.get('id') == p.get('id')), {})
            if hand_typed and TYPED_NUMBER.search(PLACEHOLDER.sub('', str(raw_p.get('shows', '')))):
                warn(f'{pw}: "shows" types a measured number; use a placeholder so it comes from the 3D')
            if all(isinstance(x, (int, float)) for x in (start, end)):
                for name in _zero_readings(str(raw_p.get('shows', '')), analysis, sk.get('ballFoot')):
                    warn(f'{pw}: {{{{{name}}}}} comes out as 0; say it in words instead (e.g. "stays upright", "no touches")')
                for name, window in _placeholder_windows(str(raw_p.get('shows', '')), analysis, sk.get('ballFoot')):
                    if window[1] < start - 0.35 or window[0] > end + 0.35:
                        warn(f'{pw}: {{{{{name}}}}} measures {window[0]:.2f}–{window[1]:.2f} s, outside this phase '
                             f'({start:.2f}–{end:.2f} s); check it describes this moment')
        for d in sk.get('drills', []):
            dw = f'{where}, drill {d.get("id")!r}'
            missing(d, ('id', 'title', 'lvl', 'how'), dw)
            m = d.get('model') or []
            if len(m) != 3 or not (a - 0.01 <= m[0] < m[1] <= b + 0.01) or not (0.1 <= m[2] <= 2):
                err(f'{dw}: model must be [start, end, rate] inside the range')
            if not isinstance(d.get('reps'), int) or not 1 <= d['reps'] <= 50:
                err(f'{dw}: reps must be a whole number from 1 to 50')
            if not d.get('checks'):
                err(f'{dw}: needs at least one self-check')
        if not sk.get('drills'):
            err(f'{where}: needs at least one drill')
        for c in sk.get('challenges', []):
            cw = f'{where}, challenge {c.get("title")!r}'
            kind = c.get('type')
            if kind in ('choice', 'tap'):
                missing(c, ('title', 'prompt', 'explain'), cw)
            if kind == 'choice':
                opts = c.get('options') or []
                if len(opts) < 2 or not isinstance(c.get('answer'), int) or not 0 <= c['answer'] < len(opts):
                    err(f'{cw}: needs two or more options and a valid answer index')
                if not isinstance(c.get('t'), (int, float)) or not a - 0.01 <= c['t'] <= b + 0.01:
                    err(f'{cw}: t (the moment to freeze on) must lie inside the range')
            elif kind == 'tap':
                f0, tg = c.get('from'), c.get('target')
                if not all(isinstance(x, (int, float)) for x in (f0, tg)) or not (a - 0.01 <= f0 < tg <= b + 0.01):
                    err(f'{cw}: from < target, both inside the range')
                if not c.get('label'):
                    err(f'{cw}: needs a button label')
            elif kind == 'order':
                order = c.get('ids') or []
                if len(order) < 3 or not set(order) <= ids:
                    err(f'{cw}: ids must be three or more of this skill\'s phase ids')
                if set(c.get('shuffled', order)) != set(order):
                    err(f'{cw}: shuffled must hold the same ids')
            else:
                err(f'{cw}: type must be choice, tap or order')
        if not sk.get('challenges'):
            warn(f'{where}: no challenges')
        if not sk.get('unsure'):
            warn(f'{where}: list what the clip can\'t tell us in "unsure"')
        text = str(raw)
        if 'Replace with' in text:
            (err if not lesson.get('draft') else warn)(f'{where}: still has draft placeholder text ("Replace with ...")')
        if SIDE_CLAIM.search(text):
            warn(f'{where}: names a left/right foot; the 3D can swap legs, so prefer "ball foot" / "standing foot"')
    return errors, warnings
