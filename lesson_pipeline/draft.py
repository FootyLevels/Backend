"""The automatic first draft of a lesson, built only from the measurements.

One skill per shot that is long enough and has a body for most frames. Phases come from what the
3D measured: an approach, each burst of ball touches, any big turn, and the exit. The text is
deliberately generic and every number is a placeholder; the author (a person or a model following
PLAYBOOK.md) names the move and rewrites the cues from the evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from .evidence import touch_bursts, turns

MIN_SKILL = 1.2      # seconds; shorter shots are titles, replays or transitions


def _r(x: float) -> float:
    return round(float(x), 2)


def _quality(shot: dict) -> tuple[str, str]:
    rms = shot.get('planeRmsCm') or 99
    if rms < 5 and shot.get('ballTrusted'):
        return 'medium', 'Single camera; positions and depths are estimates.'
    if rms < 5:
        return 'low', 'The ball isn’t tracked reliably in this clip.'
    return 'low', 'The 3D is rough here (moving camera, blur or low light).'


PHASE_TEXT = {
    'approach': dict(name='Approach', cue='Run at the defender with the ball close.',
                     video='He brings the ball towards the defender.',
                     shows='{{touches @A @B}} touches on the way in.',
                     ov=dict(prints=True, paths=True, ghost=False, arrow=True), focus=None, view='video'),
    'touch': dict(name='Key touch', cue='Small, controlled touch; stay over the ball.',
                  video='Watch which foot moves the ball and where it goes.',
                  shows='{{touches @A @B ball}} touches by the ball foot, body about {{drop @A @B}} cm lower than standing.',
                  ov=dict(prints=True, paths=True, ghost=True, arrow=False), focus='ball', view='video'),
    'turn': dict(name='Change direction', cue='Turn on your standing foot and take it the other way.',
                 video='His body turns as he takes the ball the other way.',
                 shows='His hips turn about {{turn @T0 @T1}}° in this moment.',
                 ov=dict(prints=True, paths=False, ghost=True, arrow=True), focus='turn', view='top'),
    'exit': dict(name='Exit', cue='Push it into space and accelerate.',
                 video='He takes the ball away from the defender.',
                 shows='He drops about {{drop @A @B}} cm into his first steps.',
                 ov=dict(prints=False, paths=True, ghost=False, arrow=True), focus='drop', view='video'),
}


def _phase(kind: str, pid: str, start: float, end: float, key: float, conf, why, extra=None) -> dict:
    t = PHASE_TEXT[kind]
    shows = t['shows'].replace('@A', f'{start:.2f}').replace('@B', f'{end:.2f}')
    if extra:
        shows = shows.replace('@T0', f'{extra[0]:.2f}').replace('@T1', f'{extra[1]:.2f}')
    p = dict(id=pid, name=t['name'], start=_r(start), end=_r(end), key=_r(key), cue=t['cue'], video=t['video'],
             shows=shows, conf=conf, why=why, ov=t['ov'])
    if t['focus']:
        p['focus'] = t['focus']
    if kind == 'turn':
        p['turn'] = True
    p['view'] = t['view']
    return p


def draft_skill(a: dict, k: int, shot: dict) -> dict | None:
    F, fps = a['frames'], a['fps']
    s0, s1 = shot['start'] / fps, shot['end'] / fps
    if s1 - s0 < MIN_SKILL or shot['validFrames'] < 0.5 * (shot['end'] - shot['start']):
        return None
    conf, why = _quality(shot)
    touches = [e for e in a['events']['touches'] if s0 <= e['frame'] / fps <= s1]
    by_foot = {'left': 0, 'right': 0}
    for e in touches:
        by_foot[e['foot']] += 1
    ball_foot = 'left' if by_foot['left'] > by_foot['right'] else 'right'
    ball_frames = [i for i in range(shot['start'], shot['end']) if F['ball'][i]]
    air = sum(F['ballAir'][i] for i in ball_frames)
    leaves_ground = bool(ball_frames) and air >= 5 and air >= 0.2 * len(ball_frames)

    bursts = touch_bursts(a['events']['touches'], fps, s0, s1)
    if len(bursts) > 3:
        keep = sorted(sorted(bursts, key=lambda b: -len(b[2]))[:3], key=lambda b: b[0])
        bursts = keep
    spins = turns(F['yaw'], fps, s0, s1)

    # Key events: each burst of touches, and each big turn that isn't part of one. At most four.
    events = [[t0, 'touch', t0, None, t1, len(es)] for t0, t1, es in bursts]
    for t0, t1, deg in spins:
        if abs(deg) < 120:
            continue
        inside = next((e for e in events if e[1] == 'touch' and t0 - 0.3 <= e[0] <= t1 + 0.15), None)
        if inside:
            inside[1], inside[3] = 'turn', (t0, t1)
        else:
            events.append([t0, 'turn', t0, (t0, t1), t1, 3])
    events = sorted(sorted(events, key=lambda e: -e[5])[:4], key=lambda e: e[0])
    marks = []                                  # (start, kind, key, extra)
    if events:
        if events[0][0] - s0 > 0.4:
            marks.append((s0, 'approach', (s0 + events[0][0]) / 2, None))
        for t0, kind, key, extra, t1, _ in events:
            marks.append((max(s0, t0 - 0.1), kind, key, extra))
        last = max(e[4] for e in events)
        if s1 - last > 0.4:
            marks.append((last + 0.15, 'exit', min(s1, last + 0.4), None))
    else:
        low = shot['start'] + max(range(shot['end'] - shot['start']), key=lambda j: F['comDropCm'][shot['start'] + j])
        lt = low / fps
        marks = [(s0, 'approach', (s0 + lt) / 2, None), (max(s0 + 0.3, lt - 0.3), 'touch', lt, None)]
        if s1 - lt > 0.6:
            marks.append((lt + 0.3, 'exit', min(s1, lt + 0.6), None))
    marks.sort(key=lambda m: m[0])
    phases, counts = [], {}
    for j, (start, kind, key, extra) in enumerate(marks):
        end = marks[j + 1][0] if j + 1 < len(marks) else s1
        if j == 0:
            start = s0
        if end - start < 0.05:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        pid = kind if counts[kind] == 1 else f'{kind}{counts[kind]}'
        key = min(max(key, start), end)
        phases.append(_phase(kind, pid, start, end, key, conf, why, extra))
    # Number repeated kinds in order: "Key touch 1", "Key touch 2".
    seen = {}
    for p in phases:
        kind = p['id'].rstrip('0123456789')
        if counts.get(kind, 0) > 1:
            seen[kind] = seen.get(kind, 0) + 1
            p['name'] += f' {seen[kind]}'

    main = next((p for p in phases if p['id'].startswith(('touch', 'turn'))), phases[0])
    exit_p = next((p for p in phases if p['id'] == 'exit'), None)
    body = [p for p in phases if p['id'] not in ('approach',)]
    challenges = []
    first = next((e['frame'] / fps for e in touches if e['foot'] == ball_foot and e['frame'] / fps >= main['start']), None)
    if first is not None:
        challenges.append(dict(type='tap', title='Spot the key touch', prompt='Tap the moment the ball foot plays the key touch.',
                               **{'from': _r(max(s0, first - 0.5))}, target=_r(first), label='Now!', rate=0.5, tolerance=0.2,
                               explain='That is the touch the move is built around.'))
    else:
        challenges.append(dict(type='tap', title='Spot the key moment', prompt='Tap the moment the move happens.',
                               **{'from': _r(max(s0, main['key'] - 0.6))}, target=_r(main['key']), label='Now!', rate=0.5,
                               tolerance=0.25, explain='Replace with what happens at this moment.'))
    # Freeze just before the last step and ask what comes next.
    last = exit_p or phases[-1]
    if last['start'] - 0.05 > s0:
        t = _r(last['start'] - 0.05)
        challenges.append(dict(type='choice', t=t, title='What happens next?', prompt='Frozen just before the last step. Predict the next movement.',
                               options=['He takes the ball away and accelerates', 'He stops and passes back', 'He shoots'], answer=0,
                               explain='The move only works if you accelerate out of it.', play=[t, _r(min(s1, t + 1.0)), 0.5]))
    if len(phases) >= 3:
        ids = [p['id'] for p in phases]
        challenges.append(dict(type='order', title='Put it in order', prompt='Tap the moments in the order they happen.',
                               ids=ids, shuffled=ids[1:] + ids[:1]))
    core = (body[0]['start'], (exit_p or phases[-1])['start'] if len(body) > 1 else s1)
    if core[1] - core[0] < 0.3:
        core = (s0, s1)
    drills = [
        dict(id='walk', title='Walk it through', lvl='Ball · walking pace', reps=10,
             how='Do the move at walking pace, one touch at a time. Stop and check your balance after each step.',
             model=[_r(core[0]), _r(core[1]), 0.5], checks=['I used the same foot as the model', 'The ball stayed close', 'I kept my balance']),
        dict(id='jog', title='At jogging pace', lvl='Ball · jogging', reps=8, how='Do the whole move from a jog without stopping between touches.',
             model=[_r(core[0]), _r(core[1]), 0.5], checks=['No stop between touches', 'The ball stayed close', 'I sped up after the move']),
        dict(id='cone', title='Past a cone', lvl='Ball · cone · building speed', reps=8, how='Use a cone as the defender. Start the move a stride before it.',
             model=[_r(s0), _r(s1), 0.5], checks=['I started at the right distance', 'I finished past the cone', 'I kept my head up after the move']),
        dict(id='live', title='Against a passive defender', lvl='Partner · full speed', reps=6, how='A partner steps in but doesn’t tackle. Beat them with the move.',
             model=[_r(s0), _r(s1), 1], checks=['They reacted to the move', 'I got past them', 'I kept the ball']),
    ]
    unsure = ['The move’s name: the draft doesn’t know it yet.',
              'Which leg is which when the legs cross: the lesson names feet by job.']
    if not shot.get('ballTrusted'):
        unsure.append('The ball: it isn’t tracked reliably in this clip, so it’s hidden.')
    if (shot.get('planeRmsCm') or 99) >= 5:
        unsure.append('Detail in this clip: the 3D is rough (moving camera, blur or low light).')
    return dict(
        id=f'move{k + 1}', name=f'Move {k + 1}', short=f'Move {k + 1}', tab=f'Clip {k + 1}', ballFoot=ball_foot,
        ballLeavesGround=leaves_ground, range=[_r(s0), _r(s1)], watch=[_r(s0), _r(s1)], watchTitle='Watch it first',
        intro='Replace with one sentence describing the move as the video shows it.',
        notice=[dict(t=p['key'], title=p['name'], text=p['video']) for p in phases],
        adds='The 3D body beside the video is reconstructed from it. Turn the camera and follow the feet and the ball.',
        unsure=unsure, phases=phases, drills=drills, challenges=challenges)


def build_draft(work: Path, lesson_id: str, title: str | None = None) -> Path:
    a = json.loads((work / 'analysis' / 'analysis.json').read_text())
    skills = [sk for k, shot in enumerate(a['shots']) if (sk := draft_skill(a, k, shot)) is not None]
    for n, s in enumerate(skills):
        s['id'], s['name'], s['short'], s['tab'] = f'move{n + 1}', f'Move {n + 1}', f'Move {n + 1}', f'Skill {n + 1}'
    if not skills:
        raise ValueError('No shot is long enough with a tracked body to make a skill from.')
    fps = a['fps']
    lesson = dict(
        schemaVersion=1, id=lesson_id, folder=lesson_id, draft=True,
        title=title or 'Skill Lesson', accent='', subtitle='Replace with one line about what this video teaches.',
        video='video.mp4', videoLabel='Original skill video',
        sourceNote='one skills video, the body motion reconstructed from it, and measurements taken from that motion.',
        shots=[dict(start=_r(s['start'] / fps), end=_r(s['end'] / fps), label=f'Clip {k + 1}',
                    **({'quality': 'low'} if (s.get('planeRmsCm') or 99) >= 5 else {})) for k, s in enumerate(a['shots'])],
        skills=skills,
        notes=dict(limits=[], tests=[]),
        reviewNotes=['Name each move (only if the video names it or it is unmistakable); otherwise describe what it does.',
                     'Rewrite intro, cues and "video" lines from the contact sheets; keep every number as a placeholder.',
                     'Check each phase boundary against the close-up sheets; merge or split phases so each is one idea.',
                     'Replace the generic drills and challenge options with ones specific to this move.',
                     'Remove "draft": true when done.'])
    out = work / 'lesson.draft.json'
    out.write_text(json.dumps(lesson, ensure_ascii=False, indent=1))
    return out
