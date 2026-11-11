"""The evidence pack a model reads before writing a lesson: what happens, when, and how well measured.

Writes ``work/evidence/``:
  evidence.md            shots, 3D quality, and a timeline of touches, plants, turns and lowering
  shot-<n>.jpg           an overview contact sheet per shot (timestamped)
  shot-<n>-burst-<k>.jpg close-ups around each burst of ball touches
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TILE_W = 240


def _frames(video: Path, times: list[float]) -> list[np.ndarray | None]:
    cap = cv2.VideoCapture(str(video))
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(t, 0) * 1000)
        ok, img = cap.read()
        out.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if ok else None)
    cap.release()
    return out


def contact_sheet(video: Path, times: list[float], out: Path, cols=6) -> Path:
    imgs = _frames(video, times)
    first = next((i for i in imgs if i is not None), None)
    if first is None:
        raise ValueError(f'could not read frames from {video}')
    h = int(first.shape[0] * TILE_W / first.shape[1])
    rows = (len(times) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * TILE_W + (cols - 1) * 4, rows * h + (rows - 1) * 4), (20, 24, 28))
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    for k, (t, img) in enumerate(zip(times, imgs)):
        if img is None:
            continue
        tile = Image.fromarray(img).resize((TILE_W, h))
        d = ImageDraw.Draw(tile)
        label = f'{t:.2f} s'
        d.rectangle([0, 0, 8 + 10 * len(label), 26], fill=(0, 0, 0))
        d.text((5, 3), label, fill=(255, 210, 120), font=font)
        sheet.paste(tile, ((k % cols) * (TILE_W + 4), (k // cols) * (h + 4)))
    sheet.save(out, quality=88)
    return out


def touch_bursts(touches: list[dict], fps: float, a: float, b: float, gap=0.35) -> list[tuple[float, float, list]]:
    """Touches in [a, b] grouped into bursts separated by more than ``gap`` seconds."""
    times = [(e['frame'] / fps, e) for e in touches if a <= e['frame'] / fps <= b]
    bursts = []
    for t, e in times:
        if bursts and t - bursts[-1][1] <= gap:
            bursts[-1] = (bursts[-1][0], t, bursts[-1][2] + [e])
        else:
            bursts.append((t, t, [e]))
    return bursts


def turns(yaw: list[float], fps: float, a: float, b: float, window=0.5, least=90.0) -> list[tuple[float, float, float]]:
    """Spells where the hips turn more than ``least`` degrees within ``window`` seconds."""
    i0, i1, w = int(a * fps), int(b * fps), max(2, int(window * fps))
    found, i = [], i0
    while i + w <= i1:
        d = yaw[i + w - 1] - yaw[i]
        if abs(d) >= least:
            j = i + w - 1
            while j + 1 < i1 and np.sign(yaw[j + 1] - yaw[j]) == np.sign(d) and abs(yaw[j + 1] - yaw[j]) > 2:
                j += 1
            found.append((i / fps, j / fps, yaw[j] - yaw[i]))
            i = j + 1
        else:
            i += max(1, w // 4)
    return found


def build_evidence(work: Path, video: Path, cuts_s: list[float]) -> Path:
    a = json.loads((work / 'analysis' / 'analysis.json').read_text())
    F, fps, dur = a['frames'], a['fps'], a['duration']
    ev = work / 'evidence'
    ev.mkdir(exist_ok=True)
    lines = [f'# Evidence for {work.name}', '',
             f'Video: {dur:.2f} s at {fps:g} fps. Cuts (s): {", ".join(f"{c:.2f}" for c in cuts_s) or "none"}.',
             'Feet are labelled left/right by continuity within each shot; the 3D can confuse which leg is which when',
             'legs cross, so lessons name feet by job (ball foot, standing foot). Times are seconds on the video clock.', '']
    for k, s in enumerate(a['shots']):
        s0, s1 = s['start'] / fps, s['end'] / fps
        quality = 'good' if (s.get('planeRmsCm') or 99) < 5 else 'rough'
        lines += [f'## Shot {k}: {s0:.2f}–{s1:.2f} s ({s1 - s0:.1f} s)', '',
                  f'- 3D: {s["validFrames"]} of {s["end"] - s["start"]} frames have a body; pitch fit {s.get("planeRmsCm")} cm '
                  f'({quality}); camera {"height " + str(s["camera"][1]) + " m" if s.get("camera") else "unknown"}.',
                  f'- Ball: {"tracked and trusted" if s.get("ballTrusted") else "not trusted (hidden in lessons)"}.']
        sheet = contact_sheet(video, list(np.linspace(s0 + 0.05, s1 - 0.05, 12)), ev / f'shot-{k}.jpg')
        lines.append(f'- Overview: ![shot {k}]({sheet.name})')
        # The foot with most touches is probably the ball foot here (the draft's ballFoot does the same).
        counts = {f: sum(1 for e in a['events']['touches'] if s0 <= e['frame'] / fps < s1 and e['foot'] == f) for f in ('left', 'right')}
        ball_side = max(counts, key=counts.get) if any(counts.values()) else None
        role = {ball_side: 'ball foot', ('left' if ball_side == 'right' else 'right'): 'standing foot'} if ball_side else {}
        if ball_side:
            lines.append(f'- Feet: the {ball_side} foot makes most touches, so it is probably the ball foot and the other the '
                         f'standing foot. In placeholders write `ball` / `stand`, not sides.')
        bursts = touch_bursts(a['events']['touches'], fps, s0, s1)
        for bi, (t0, t1, es) in enumerate(bursts):
            feet = ', '.join(f"{e['frame'] / fps:.2f} {e['foot']}" for e in es)
            lines.append(f'- Touch burst {bi}: {t0:.2f}–{t1:.2f} s ({len(es)} touch{"" if len(es) == 1 else "es"}: {feet})')
            detail = contact_sheet(video, list(np.linspace(max(s0, t0 - 0.4), min(s1 - 0.02, t1 + 0.4), 8)),
                                   ev / f'shot-{k}-burst-{bi}.jpg', cols=8)
            lines.append(f'  - Close-up: ![burst {bi}]({detail.name})')
        for t0, t1, deg in turns(F['yaw'], fps, s0, s1):
            lines.append(f'- Turn: {t0:.2f}–{t1:.2f} s, about {abs(deg):.0f}° (hips relative to the camera: if the camera '
                         f'pans or turns with him, this angle is wrong, so describe the turn instead of quoting it)')
        plants = [p for p in a['events']['plants'] if s0 <= p['start'] / fps < s1]
        for p in plants:
            job = f" ({role[p['foot']]})" if p['foot'] in role else ''
            lines.append(f"- Plant: {p['foot']} foot{job} {p['start'] / fps:.2f}–{p['end'] / fps:.2f} s")
        i0, i1 = s['start'], s['end']
        drop = F['comDropCm'][i0:i1]
        if drop:
            j = int(np.argmax(drop))
            lines.append(f'- Lowest body: {drop[j]:.0f} cm below standing at {(i0 + j) / fps:.2f} s')
        lines += ['', '| t (s) | body lower (cm) | hips (°) | planted L/R | gap L/R (cm) | ball |', '|---|---|---|---|---|---|']
        step = max(1, int(round(0.2 * fps)))
        for i in range(i0, i1, step):
            gl, gr = F['gapL'][i], F['gapR'][i]
            ball = 'air' if F['ballAir'][i] else ('–' if F['ball'][i] is None else f"{F['ballSpeed'][i]:.1f} m/s")
            lines.append(f"| {i / fps:.2f} | {F['comDropCm'][i]:.0f} | {F['yaw'][i]:.0f} | "
                         f"{'●' if F['leftPlanted'][i] else '○'}{'●' if F['rightPlanted'][i] else '○'} | "
                         f"{'–' if gl > 5 else round(gl * 100)}/{'–' if gr > 5 else round(gr * 100)} | {ball} |")
        lines.append('')
    out = ev / 'evidence.md'
    out.write_text('\n'.join(lines))
    return out
