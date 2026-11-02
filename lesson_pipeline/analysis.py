"""Measurements and a light 3D export for one reconstructed video.

The backend's whole-video motion (``motion/sequence.json`` and its chunks) is camera-relative
with Y up but no ground alignment, so each shot gets its own pitch plane, fitted to where the
lower foot rests (or, when the contacts barely move, to balance), and is rotated onto it.

Writes into ``out_dir``:
  analysis.json         per-frame measurements and detected events (see SPEC.md)
  mesh.json             the light mesh: vertex count, uint16 faces (base64) and foot sets
  motion/sequence.json  the light mesh's motion in the backend's MotionSequence format,
  motion/faces.bin      so the app's existing motion player can load it unchanged
  motion/body-*.bin.gz
"""
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import numpy as np

CHUNK = 60
BALL_R = 0.11
REFS = Path(__file__).resolve().parent / 'refs'


def _load_motion(motion: Path, vertex_count: int):
    seq = json.loads((motion / 'sequence.json').read_text())
    V, N = seq['vertexCount'], seq['frameCount']
    if V != vertex_count:
        raise ValueError(f'unexpected body mesh: {V} vertices (expected {vertex_count})')
    lo, span = np.array(seq['min'], np.float32), np.array(seq['span'], np.float32)
    pos = np.zeros((N, V, 3), np.float32)
    for c in seq['chunks']:
        raw = np.frombuffer(gzip.decompress((motion / c['file']).read_bytes()), '<u2')
        pos[c['start']:c['start'] + c['count']] = lo + raw.reshape(c['count'], V, 3).astype(np.float32) * (span / 65535)
    track = json.loads((motion / 'tracking.json').read_text())['frames']
    return seq, pos, track


def _rotation_to_y(n):
    """Smallest rotation taking unit vector n onto +Y."""
    y = np.array([0.0, 1.0, 0.0])
    v = np.cross(n, y)
    s = np.linalg.norm(v)
    c = float(n @ y)
    if s < 1e-9:
        return np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s ** 2)


def analyze(project_dir: Path, out_dir: Path, extra_cuts=(), parts=(), log=print) -> dict:
    """Runs the analysis. ``parts`` is a list of (project_dir, start_frame) for segments that were
    re-run as their own clip (e.g. after a missed cut) and replace those frames."""
    project_dir, out_dir = Path(project_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = json.loads((REFS / 'refsets.json').read_text())
    lite = json.loads((REFS / 'lite.json').read_text())
    footL, footR = np.array(ref['footL']), np.array(ref['footR'])
    thighL, thighR = np.array(ref['thighL']), np.array(ref['thighR'])
    keep = np.array(lite['keep'])

    seq, pos, track = _load_motion(project_dir / 'motion', ref['vertexCount'])
    V, N, fps = seq['vertexCount'], seq['frameCount'], float(seq['fps'])
    faces = np.frombuffer((project_dir / 'motion' / seq['topologyFile']).read_bytes(), np.uint32).reshape(-1, 3)
    frames = seq['frames']
    valid = np.array([bool(f.get('valid')) for f in frames])
    backend_shot = np.array([int(f['shot']) for f in frames])
    ball = np.array([f['ball'] if f.get('ball') else [np.nan] * 3 for f in frames], np.float32)
    offsets = {int(x['id']): np.array(x.get('offset', [0, 0, 0]), np.float32) for x in seq['shots']}

    # A segment the backend couldn't track, re-run as its own clip, replaces those frames.
    for part_dir, start in parts:
        ps, ppos, ptrack = _load_motion(Path(part_dir) / 'motion', V)
        if abs(ps['fps'] - fps) > 1e-6 or start + ps['frameCount'] > N:
            raise ValueError(f'part {part_dir} does not fit at frame {start}')
        n = ps['frameCount']
        pos[start:start + n] = ppos
        base = int(max(offsets)) + 1
        for k, f in enumerate(ps['frames']):
            valid[start + k] = bool(f.get('valid'))
            backend_shot[start + k] = base + int(f['shot'])
            ball[start + k] = f['ball'] if f.get('ball') else [np.nan] * 3
        for x in ps['shots']:
            offsets[base + int(x['id'])] = np.array(x.get('offset', [0, 0, 0]), np.float32)
        track[start:start + n] = ptrack[:n]
        log(f'  merged {Path(part_dir).name}: frames {start}-{start + n - 1}, {int(valid[start:start + n].sum())} valid')

    starts = sorted(set([0] + [int(round(c * fps)) for c in extra_cuts if 0 < c * fps < N]
                        + [int(i) for i in np.flatnonzero(np.diff(backend_shot)) + 1]))
    shot = np.searchsorted(starts, np.arange(N), side='right') - 1

    # On screen, a dribbled ball sits near the player's feet. If the tracked ball rarely does, the
    # tracker followed something else (a spare ball, a marker disc) and that shot's ball is dropped.
    near = np.zeros(N, bool)
    high = np.zeros(N, bool)          # on screen, the ball is above the player's shins: in the air
    for i, tf in enumerate(track[:N]):
        pb, bb = (tf.get('person') or {}).get('bbox'), (tf.get('ball') or {}).get('bbox')
        if pb and bb:
            w, h = pb[2] - pb[0], pb[3] - pb[1]
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            near[i] = (pb[0] - 0.5 * w < cx < pb[2] + 0.5 * w) and (pb[1] + 0.55 * h < cy < pb[3] + 0.25 * h)
            high[i] = (pb[0] - 0.8 * w < cx < pb[2] + 0.8 * w) and cy < pb[1] + 0.6 * h
    ball_trusted = {}
    for sid in sorted(set(shot.tolist())):
        m = (shot == sid) & ~np.isnan(ball[:, 0])
        frac = float(near[m].mean()) if m.any() else 0.0
        ball_trusted[sid] = frac >= 0.35
        if not ball_trusted[sid]:
            ball[shot == sid] = np.nan
    ball_air = np.zeros(N, bool)
    ball_fit = np.full((N, 3), np.nan, np.float32)    # depth fitted to the feet: used to measure touches

    # Hold the last good pose through failed frames so playback never shows garbage.
    for i in range(N):
        if not valid[i]:
            j = i - 1
            while j >= 0 and not valid[j]:
                j -= 1
            if j >= 0 and shot[j] == shot[i]:
                pos[i] = pos[j]

    com = np.zeros((N, 3), np.float32)
    for a in range(0, N, 64):
        q = pos[a:a + 64]
        v0, v1, v2 = q[:, faces[:, 0]], q[:, faces[:, 1]], q[:, faces[:, 2]]
        vol = np.einsum('nfi,nfi->nf', v0, np.cross(v1, v2)) / 6
        with np.errstate(invalid='ignore', divide='ignore'):
            com[a:a + 64] = np.einsum('nf,nfi->ni', vol, (v0 + v1 + v2) / 4) / vol.sum(1)[:, None]
    bad = ~np.isfinite(com).all(1)
    if bad.any():
        good = np.flatnonzero(~bad)
        for i in np.flatnonzero(bad):
            com[i] = com[good[np.argmin(np.abs(good - i))]] if len(good) else 0

    shots_out = []
    for s in sorted(set(shot.tolist())):
        idx = np.where((shot == s) & valid)[0]
        allidx = np.where(shot == s)[0]
        if len(idx) < 3:
            shots_out.append({'id': int(s), 'start': int(allidx[0]), 'end': int(allidx[-1]) + 1, 'validFrames': int(len(idx)),
                              'planeRmsCm': None, 'ballTrusted': False})
            continue
        up = np.array([0.0, 1.0, 0.0])
        for _ in range(4):
            hl = pos[idx][:, footL] @ up
            hr = pos[idx][:, footR] @ up
            sl = pos[idx][np.arange(len(idx))[:, None], footL[hl.argmin(1)][:, None]][:, 0]
            sr = pos[idx][np.arange(len(idx))[:, None], footR[hr.argmin(1)][:, None]][:, 0]
            lower = np.where((sl @ up < sr @ up)[:, None], sl, sr)          # the foot nearer the ground
            pts = lower
            for _trim in range(3):                                           # robust plane fit
                c0 = pts.mean(0)
                _, _, vt = np.linalg.svd(pts - c0)
                n = vt[2] if vt[2] @ up > 0 else -vt[2]
                r = (pts - c0) @ n
                pts = pts[r < np.percentile(r, 75) + 0.01]                   # drop raised feet (flight, kicks)
            # A plane through contacts that barely move (one standing foot) has no reliable tilt.
            sv = np.linalg.svd(pts - pts.mean(0), compute_uv=False) / np.sqrt(len(pts))
            if sv[1] < 0.15:
                break
            up = n
        tilt = np.degrees(np.arccos(np.clip(up[1], -1, 1)))
        method = 'turf contacts' if sv[1] >= 0.15 and tilt < 40 else 'balance'
        if method == 'balance':
            # When balanced, the body's centre sits over the supporting foot: average that direction.
            sup = np.where((sl[:, 1] < sr[:, 1])[:, None], sl, sr)
            v = com[idx] - sup
            v = v / np.linalg.norm(v, axis=1, keepdims=True)
            up = v.mean(0)
            up /= np.linalg.norm(up)
            c0 = np.percentile(sup @ up, 20) * up
        d = float(c0 @ up)
        R = _rotation_to_y(up)
        origin = -offsets.get(int(backend_shot[allidx[0]]), np.zeros(3))
        if not 0.3 < float((origin - up * d) @ R.T @ np.array([0, 1, 0])) < 3.0:
            # Implausible camera height: keep the camera's own up axis and the backend's floor.
            method += ' (fallback: camera up)'
            up = np.array([0.0, 1.0, 0.0])
            R = np.eye(3)
            d = float(np.percentile(np.minimum(pos[idx][:, footL, 1].min(1), pos[idx][:, footR, 1].min(1)), 20))
        resid = (lower - c0) @ up
        rms = float(np.sqrt(np.mean(np.clip(resid, None, np.percentile(resid, 75)) ** 2)))
        cam = (origin - up * d) @ R.T                                        # the filming camera, on the new axes
        for i in allidx:
            pos[i] = (pos[i] - up * d) @ R.T
            com[i] = (com[i] - up * d) @ R.T
            if not np.isnan(ball[i, 0]):
                ball[i] = (ball[i] - up * d) @ R.T
        # The ball's size-based depth disagrees with the body's scale by a roughly constant factor
        # per shot. Its direction from the camera is reliable, so slide it along that ray by the one
        # factor that brings it into contact with the feet at their closest moments.
        bi = np.array([i for i in allidx if not np.isnan(ball[i, 0]) and valid[i]], int)
        scale = 1.0
        if len(bi) >= 5:
            feet = np.concatenate([pos[bi][:, footL[::4]], pos[bi][:, footR[::4]]], axis=1)
            best = None
            for k in np.linspace(0.4, 2.2, 73):
                bk = cam + k * (ball[bi] - cam)
                g = np.linalg.norm(feet - bk[:, None, :], axis=2).min(1) - BALL_R
                low = np.sort(g)[:max(3, len(g) // 7)]
                score = np.mean(np.abs(low)) + 2 * np.mean(np.clip(-g, 0, None))
                if best is None or score < best[0]:
                    best = (score, k)
            scale = float(best[1])
        for i in allidx:
            if not np.isnan(ball[i, 0]):
                ball[i] = cam + scale * (ball[i] - cam)
                ball_fit[i] = ball[i]
                # Low beside his feet on screen: rolling, so on the turf. Visibly up: keep the height.
                ball_air[i] = bool(high[i])
                if not high[i]:
                    ball[i, 1] = BALL_R
        shots_out.append({'id': int(s), 'start': int(allidx[0]), 'end': int(allidx[-1]) + 1, 'validFrames': int(len(idx)),
                          'planeRmsCm': round(rms * 100, 1), 'tiltDeg': round(float(np.degrees(np.arccos(np.clip(up[1], -1, 1)))), 1),
                          'camera': [round(float(x), 3) for x in cam], 'ballDepthScale': round(scale, 2), 'upFrom': method,
                          'ballTrusted': bool(ball_trusted.get(int(s), False))})

    def per_shot_diff(arr):
        """Central difference per second, never across a cut."""
        out = np.zeros_like(arr)
        for so in shots_out:
            a, b = so['start'], so['end']
            if b - a >= 3:
                out[a + 1:b - 1] = (arr[a + 2:b] - arr[a:b - 2]) * fps / 2
                out[a] = out[a + 1]
                out[b - 1] = out[b - 2]
        return out

    L = pos[:, footL].mean(1)
    Rf = pos[:, footR].mean(1)
    # One camera sometimes swaps which leg is which. The feet move continuously, so a frame where
    # each label lands where the other foot just was is a swap.
    swap = np.zeros(N, bool)
    state = False
    for i in range(1, N):
        if shot[i] != shot[i - 1]:
            state = False
            continue
        a, b = (Rf[i], L[i]) if state else (L[i], Rf[i])
        pl, pr = (Rf[i - 1], L[i - 1]) if swap[i - 1] else (L[i - 1], Rf[i - 1])
        if np.linalg.norm(b - pl) + np.linalg.norm(a - pr) + 0.12 < np.linalg.norm(a - pl) + np.linalg.norm(b - pr):
            state = not state
        swap[i] = state
    L, Rf = np.where(swap[:, None], Rf, L), np.where(swap[:, None], L, Rf)
    soleL = np.where(swap, pos[:, footR, 1].min(1), pos[:, footL, 1].min(1))
    soleR = np.where(swap, pos[:, footL, 1].min(1), pos[:, footR, 1].min(1))
    spdL = np.linalg.norm(per_shot_diff(L)[:, [0, 2]], axis=1)
    spdR = np.linalg.norm(per_shot_diff(Rf)[:, [0, 2]], axis=1)

    def fill_gaps(p, longest=3):
        """A planted foot doesn't lift for one or two frames: close short gaps inside a shot."""
        p = p.copy()
        i = 0
        while i < N:
            if not p[i] and i > 0 and p[i - 1]:
                j = i
                while j < N and not p[j] and shot[j] == shot[i - 1]:
                    j += 1
                if j < N and p[j] and j - i <= longest and shot[j] == shot[i - 1]:
                    p[i:j] = True
                i = j
            else:
                i += 1
        return p
    plantL = fill_gaps((soleL < 0.035) & (spdL < 0.9))
    plantR = fill_gaps((soleR < 0.035) & (spdR < 0.9))

    # Weight between the feet: 0 = over the left foot, 1 = over the right.
    seg = (Rf - L)[:, [0, 2]]
    t = np.einsum('ni,ni->n', (com - L)[:, [0, 2]], seg) / np.maximum(np.einsum('ni,ni->n', seg, seg), 1e-6)
    weight = np.clip(t, 0, 1)
    weight = np.where(plantL & ~plantR, np.minimum(weight, 0.15), weight)
    weight = np.where(plantR & ~plantL, np.maximum(weight, 0.85), weight)

    drop = np.zeros(N)
    for so in shots_out:
        a, b = so['start'], so['end']
        drop[a:b] = np.clip((np.percentile(com[a:b, 1], 85) - com[a:b, 1]) * 100, 0, None)

    # Facing: perpendicular to the hip line, on the pitch plane; front/back flips are not turns.
    lat = pos[:, thighL].mean(1) - pos[:, thighR].mean(1)
    facing = np.cross(lat, np.array([0, 1, 0]))
    yaw = np.degrees(np.arctan2(facing[:, 0], facing[:, 2]))
    for so in shots_out:
        a, b = so['start'], so['end']
        if b - a < 2:
            continue
        dd = (np.diff(yaw[a:b]) + 180) % 360 - 180
        dd = np.where(np.abs(dd) > 100, dd - 180 * np.sign(dd), dd)
        yaw[a:b] = yaw[a] + np.concatenate([[0], np.cumsum(dd)])

    bspeed = np.linalg.norm(per_shot_diff(np.nan_to_num(ball)), axis=1)
    bspeed[np.isnan(ball[:, 0])] = 0

    def gap(fs):
        dist = np.linalg.norm(pos[:, fs] - ball_fit[:, None, :], axis=2).min(1) - BALL_R
        return np.where(np.isnan(dist), 9.0, dist)
    gL, gR = gap(footL), gap(footR)
    gapL, gapR = np.where(swap, gR, gL), np.where(swap, gL, gR)

    touches = []
    for i in range(1, N - 1):
        for side, g in (('left', gapL), ('right', gapR)):
            if g[i] < 0.06 and g[i] <= g[i - 1] and g[i] <= g[i + 1]:
                other = gapR if side == 'left' else gapL
                if other[i] < g[i] - 0.005:
                    continue
                if touches and touches[-1]['frame'] >= i - 3 and touches[-1]['foot'] == side:
                    continue
                touches.append({'frame': i, 'foot': side, 'gapCm': round(float(g[i]) * 100, 1),
                                'ballSpeed': round(float(bspeed[min(i + 2, N - 1)]), 2)})
    plants = []
    for side, p in (('left', plantL), ('right', plantR)):
        for i in range(N):
            if p[i] and (i == 0 or not p[i - 1] or shot[i] != shot[i - 1]):
                j = i
                while j + 1 < N and p[j + 1] and shot[j + 1] == shot[i]:
                    j += 1
                if j - i >= 2:
                    plants.append({'foot': side, 'start': i, 'end': j})
    plants.sort(key=lambda e: e['start'])

    r3 = lambda a: np.round(np.asarray(a, float), 3).tolist()
    analysis = {
        'version': 1, 'source': seq.get('coordinates'), 'fps': fps, 'frameCount': N, 'duration': N / fps,
        'shots': shots_out, 'validFrames': int(valid.sum()), 'ballFrames': int((~np.isnan(ball[:, 0])).sum()),
        'ballDiameter': 2 * BALL_R,
        'frames': {
            'com': r3(com), 'comDropCm': np.round(drop, 1).tolist(), 'weight': np.round(weight, 3).tolist(),
            'left': r3(L), 'right': r3(Rf), 'leftSole': np.round(soleL, 3).tolist(), 'rightSole': np.round(soleR, 3).tolist(),
            'leftPlanted': plantL.astype(int).tolist(), 'rightPlanted': plantR.astype(int).tolist(),
            'yaw': np.round(yaw, 1).tolist(),
            'ball': [None if np.isnan(b[0]) else [round(float(x), 3) for x in b] for b in ball],
            'ballSpeed': np.round(bspeed, 2).tolist(), 'gapL': np.round(gapL, 3).tolist(), 'gapR': np.round(gapR, 3).tolist(),
            'valid': valid.astype(int).tolist(), 'ballAir': ball_air.astype(int).tolist(), 'legSwap': swap.astype(int).tolist(),
        },
        'events': {'touches': touches, 'plants': plants},
    }
    (out_dir / 'analysis.json').write_text(json.dumps(analysis, separators=(',', ':')))

    # The light mesh, in the backend's MotionSequence format (uint16 positions, uint32 faces).
    lite_faces = np.frombuffer(base64.b64decode(lite['faces']), '<u2').reshape(-1, 3)
    (out_dir / 'mesh.json').write_text(json.dumps({'vertexCount': int(len(keep)), 'faces': lite['faces'],
                                                   'footSets': lite['footSets']}, separators=(',', ':')))
    litepos = pos[:, keep, :]
    llo = litepos.reshape(-1, 3).min(0)
    lspan = np.maximum(litepos.reshape(-1, 3).max(0) - llo, 1e-6)
    q = np.round((litepos - llo) / lspan * 65535).astype('<u2')
    motion = out_dir / 'motion'
    motion.mkdir(exist_ok=True)
    for old in motion.glob('body-*.bin.gz'):
        old.unlink()
    (motion / 'faces.bin').write_bytes(lite_faces.astype('<u4').tobytes())
    chunks = []
    for a in range(0, N, CHUNK):
        blob = gzip.compress(q[a:a + CHUNK].tobytes(), compresslevel=9)
        name = f'body-{a:06d}.bin.gz'
        (motion / name).write_bytes(blob)
        chunks.append({'start': a, 'count': int(min(CHUNK, N - a)), 'file': name, 'bytes': len(blob)})
    (motion / 'sequence.json').write_text(json.dumps({
        'vertexCount': int(len(keep)), 'faceCount': int(len(lite_faces)), 'frameCount': N,
        'min': np.round(llo, 6).tolist(), 'span': np.round(lspan, 6).tolist(), 'topologyFile': 'faces.bin',
        'encoding': 'uint16-le-xyz-gzip', 'chunks': chunks, 'version': 1, 'revision': 'lesson-lite',
        'fps': fps, 'duration': N / fps, 'validBodyFrames': int(valid.sum()), 'validBallFrames': analysis['ballFrames'],
        'coordinates': 'pitch-aligned per shot (lesson pipeline); light mesh',
        'ball': {'file': None, 'diameter': 2 * BALL_R}, 'ground': None,
        'frames': [{'index': i, 'time': i / fps, 'shot': int(shot[i]), 'valid': bool(valid[i]),
                    'ball': analysis['frames']['ball'][i]} for i in range(N)],
    }, separators=(',', ':')))

    summary = {'frames': N, 'fps': fps, 'valid': int(valid.sum()), 'ballFrames': analysis['ballFrames'],
               'touches': len(touches), 'plants': len(plants),
               'motionMB': round(sum(c['bytes'] for c in chunks) / 1e6, 1), 'shots': shots_out}
    for so in shots_out:
        log(f"  shot {so['id']}: {so['start'] / fps:.2f}-{so['end'] / fps:.2f} s, valid {so['validFrames']}, "
            f"plane fit {so['planeRmsCm']} cm, ball {'kept' if so.get('ballTrusted') else 'dropped'}")
    return summary
