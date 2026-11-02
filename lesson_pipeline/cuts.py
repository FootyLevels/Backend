"""Scene cuts in a skill video, found from FFmpeg's scene-change score.

The backend's own detector uses one fixed threshold and missed cuts in two of five test clips.
Here a cut is a frame whose score clearly stands out from the frames around it, which also skips
the bursts of high scores that motion blur and whip pans produce.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np


def scene_scores(video: Path) -> tuple[np.ndarray, np.ndarray]:
    """(times, scores) for every frame after the first."""
    out = subprocess.run(
        ['ffmpeg', '-hide_banner', '-nostats', '-i', str(video), '-an', '-vf',
         "scale=180:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score", '-f', 'null', '-'],
        capture_output=True, text=True, check=True).stderr
    times, scores, t = [], [], None
    for line in out.splitlines():
        m = re.search(r'pts_time:([0-9.]+)', line)
        if m:
            t = float(m.group(1))
        m = re.search(r'lavfi\.scene_score=([0-9.]+)', line)
        if m and t is not None:
            times.append(t)
            scores.append(float(m.group(1)))
    return np.array(times), np.array(scores)


def find_cuts(times: np.ndarray, scores: np.ndarray, floor=0.15, contrast=4.0, spacing=0.5, edge=0.3) -> list[float]:
    """Cut times in seconds: scores above ``floor`` that are ``contrast`` times the local median."""
    if len(scores) == 0:
        return []
    end = times[-1]
    picked = []
    for i in np.argsort(-scores):
        s, t = scores[i], times[i]
        if s < floor:
            break
        if t < edge or t > end - edge:
            continue
        lo, hi = max(0, i - 15), min(len(scores), i + 16)
        around = np.delete(scores[lo:hi], i - lo)
        if len(around) and s < contrast * np.median(around):
            continue
        # A burst of high scores (blur, a whip pan, a flash) isn't a cut unless one frame dominates it.
        burst = scores[max(0, i - 6):i + 7]
        if np.sum(burst >= 0.5 * s) > 2:
            continue
        if any(abs(t - p) < spacing for p in picked):
            continue
        picked.append(float(t))
    return sorted(round(p, 2) for p in picked)


def detect_cuts(video: Path) -> list[float]:
    return find_cuts(*scene_scores(video))
