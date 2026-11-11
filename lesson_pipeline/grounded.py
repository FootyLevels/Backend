"""Grounded multi-pass authoring (``generate --mode grounded``).

  1. screen     whole clip, 5 fps → moves with rough ranges, or a rejection (before GPU time)
  2. measure    the existing reconstruction + ``analysis.json`` (done before this module runs)
  3. look       per move: player-cropped, half-speed clip at 10 fps + measured events as hints
                → phases with start / end / key
  4. teach      the checked timeline + schema → cues, drills, challenges; numbers as placeholders
  5. verify     per move: every claim supported / unclear / unsupported; unsupported removed,
                unclear moved to ``unsure``
  6. checks     ball foot against measured contacts, keys snapped to measured events, challenge
                top-up from the timeline, placeholders resolved, ``validate(strict=True)``

The baseline single call stays in ``generation.generate`` so both can be compared on one clip.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from . import clips, passes
from .generation import DEFAULT_MODEL, GenerationError, Session, guarded
from .prompting import PromptError, video_metadata, video_path
from .validate import validate

KEY_SNAP = 0.2          # seconds: a key this close to a measured event moves onto it
MARGIN = 0.2            # seconds of context either side of a move's close-up


def measured_events(analysis: dict, a: float, b: float) -> list[tuple[float, str]]:
    fps = float(analysis['fps'])
    events = [(t['frame'] / fps, f"ball contact by the {t['foot']} foot") for t in analysis['events'].get('touches', [])]
    events += [(p['start'] / fps, f"{p['foot']} foot plants") for p in analysis['events'].get('plants', [])]
    return sorted((t, what) for t, what in events if a <= t <= b)


def decide_ball_foot(seen: str, analysis: dict, a: float, b: float) -> tuple[str, str | None]:
    """The skill's ball foot and, when video and measurement disagree, a note for ``unsure``."""
    fps = float(analysis['fps'])
    counts = Counter(t['foot'] for t in analysis['events'].get('touches', []) if a <= t['frame'] / fps <= b)
    total = sum(counts.values())
    top, n = counts.most_common(1)[0] if counts else (None, 0)
    measured = top if total >= 2 and n / total >= 0.75 else None
    if seen in ('left', 'right'):
        if measured and measured != seen and total >= 3:
            return measured, 'Video and 3D disagree on which foot works the ball; the lesson follows the 3D contacts.'
        return seen, None
    if measured or top:
        return measured or top, 'The ball foot is hard to see; the lesson follows the 3D contacts.'
    return 'right', 'Neither the video nor the 3D clearly shows which foot works the ball.'


def snap_keys(phases: list[dict], analysis: dict) -> None:
    for phase in phases:
        near = [t for t, _ in measured_events(analysis, phase['start'], phase['end'])
                if abs(t - phase['key']) <= KEY_SNAP]
        if near:
            phase['key'] = round(min(near, key=lambda t: abs(t - phase['key'])), 3)


def lesson_shots(analysis: dict) -> list[dict]:
    fps = float(analysis['fps'])
    return [{'start': round(s['start'] / fps, 3), 'end': round(s['end'] / fps, 3), 'label': f'Camera {i + 1}',
             **({} if s.get('ballTrusted', True) else {'quality': 'low'})} for i, s in enumerate(analysis['shots'])]


def top_up_challenges(skill: dict, analysis: dict) -> None:
    """Keep three challenges after Verify, using only the checked timeline and measured contacts."""
    challenges = skill.setdefault('challenges', [])
    ids = [p['id'] for p in skill['phases']]
    if len(challenges) < 3 and len(ids) >= 3 and not any(c.get('type') == 'order' for c in challenges):
        challenges.append({'type': 'order', 'title': 'Put it in order',
                           'prompt': 'Tap the phases in the order they happen.', 'ids': ids,
                           'shuffled': ids[1:] + ids[:1]})
    a, b = skill['range']
    fps = float(analysis['fps'])
    touches = [t['frame'] / fps for t in analysis['events'].get('touches', [])
               if t['foot'] == skill['ballFoot'] and a + 0.3 <= t['frame'] / fps <= b]
    if len(challenges) < 3 and touches and not any(c.get('type') == 'tap' for c in challenges):
        first = touches[0]
        challenges.append({'type': 'tap', 'from': round(max(a, first - 1.0), 3),
                           'target': f'{{{{firstTouch {a} {b} ball}}}}', 'label': 'Touch', 'rate': 0.5,
                           'title': 'Spot the first touch',
                           'prompt': 'Tap the moment the ball foot first touches the ball.',
                           'explain': 'The 3D tracking places the first ball-foot contact here.'})
    # Last resort: name the phase at its key moment; the answer is the checked timeline itself.
    names = [p['name'] for p in skill['phases']]
    for i, phase in enumerate(skill['phases']):
        if len(challenges) >= 3 or len(set(names)) < 2:
            break
        if any(c.get('type') == 'choice' and c.get('t') == phase['key'] for c in challenges):
            continue
        challenges.append({'type': 'choice', 't': phase['key'], 'title': 'Which phase is this?',
                           'prompt': 'The video is paused. Which part of the move is this?',
                           'options': names, 'answer': i, 'play': [phase['start'], phase['end'], 0.5],
                           'explain': f"This is “{phase['name']}”: {phase['cue']}"})


def author(work: Path, lesson_id: str, *, model=DEFAULT_MODEL, client=None, cache=None, screened=None,
           max_attempts=3, poll_seconds=2, processing_timeout=300, log=print) -> Path:
    """Write ``work/lesson.json`` (with placeholders) and ``work/authoring.json``; return the lesson path."""
    try:
        from jsonschema import Draft202012Validator  # noqa: F401  (needed by teach)
    except ImportError:
        raise GenerationError('Install the lesson dependencies: pip install -e ".[lessons]"') from None
    work = Path(work).expanduser().resolve()
    analysis_path = work / 'analysis' / 'analysis.json'
    if not analysis_path.is_file():
        raise PromptError(f'Grounded authoring needs the 3D analysis at {analysis_path}; run `run` or `analyze` first.')
    analysis = json.loads(analysis_path.read_text())
    metadata = video_metadata(work)
    if abs(metadata['duration'] - float(analysis['duration'])) > 0.25:
        raise PromptError('The video and its analysis have different lengths; they must share one clock.')
    video = video_path(work)
    tracking = clips.load_tracking(work)
    duration = float(analysis['duration'])
    session = Session(work, model=model, client=client, poll_seconds=poll_seconds,
                      processing_timeout=processing_timeout, cache=cache, context='grounded')
    session.record['mode'] = 'grounded'
    status = 'failed'
    try:
        def run():
            found = screened or passes.screen(session, video, duration)
            log(f"screen: {len(found['moves'])} move(s)")
            timeline, close_ups, notes = [], {}, {}
            for move in found['moves']:
                a, b = max(0.0, move['start'] - MARGIN), min(duration, move['end'] + MARGIN)
                clip = clips.close_up(video, session.audit / 'clips' / f"{move['id']}.mp4", a, b,
                                      fps=metadata['fps'], width=metadata['width'], height=metadata['height'],
                                      tracking=tracking)
                hints = [f'{clip.to_clip(t):.2f} s: {what}' for t, what in measured_events(analysis, clip.start, clip.end)]
                seen = passes.look(session, clip, move['name'], hints, max_attempts=max_attempts)
                foot, note = decide_ball_foot(seen['ballFoot'], analysis, clip.start, clip.end)
                snap_keys(seen['phases'], analysis)
                start = round(min(move['start'], seen['phases'][0]['start']), 3)
                end = round(max(move['end'], seen['phases'][-1]['end']), 3)
                timeline.append({'id': move['id'], 'name': move['name'], 'start': start, 'end': end,
                                 'ballFoot': foot, 'phases': seen['phases']})
                close_ups[move['id']] = clip
                notes[move['id']] = [*seen['unsure'], *([note] if note else [])]
                log(f"look: {move['id']} → {len(seen['phases'])} phases, ball foot {foot}")
            lesson = passes.teach(session, video, lesson_id, duration, timeline, analysis, max_attempts=max_attempts)
            removed = 0
            for skill, move in zip(lesson['skills'], timeline):
                clip = close_ups[move['id']]
                claims = passes.claims_for(skill, analysis, clip)
                verdicts = passes.verify(session, clip, move['name'], claims)
                removed += passes.apply_verdicts(skill, claims, verdicts)
                skill['unsure'] = list(dict.fromkeys([*(skill.get('unsure') or []), *notes[move['id']]]))[:8]
            log(f'verify: {removed} claim(s) removed or moved to unsure')
            # 6. Deterministic checks; no further model calls.
            lesson['shots'] = lesson_shots(analysis)
            for skill in lesson['skills']:
                snap_keys(skill['phases'], analysis)
                top_up_challenges(skill, analysis)
            errors, _ = validate(lesson, analysis, strict=True)
            if errors:
                raise GenerationError('The grounded lesson failed its final checks: ' + '; '.join(errors[:10]))
            return lesson, removed, timeline
        lesson, removed, timeline = guarded(run)
        candidate = session.audit / 'lesson.json'
        candidate.write_text(json.dumps(lesson, ensure_ascii=False, indent=1, allow_nan=False))
        steps = session.record['steps']
        prompt_hash = hashlib.sha256(json.dumps(
            {k: v['promptSha256'] for k, v in sorted(steps.items())}, separators=(',', ':')).encode()).hexdigest()
        report = {'mode': 'grounded', 'model': model, 'promptSha256': prompt_hash, 'claimsRemoved': removed,
                  'calls': session.record['calls'], 'cacheHits': session.record['cacheHits'],
                  'moves': [{'id': m['id'], 'name': m['name'], 'phases': len(m['phases'])} for m in timeline]}
        (session.audit / 'authoring.json').write_text(json.dumps(report, indent=2))
        candidate.replace(work / 'lesson.json')
        (session.audit / 'authoring.json').replace(work / 'authoring.json')
        status = 'complete'
        return work / 'lesson.json'
    finally:
        session.close(status)
