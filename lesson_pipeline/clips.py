"""Close-up clips for the "look closely" and "verify" passes.

Each move gets its own clip: cropped to the player (the union of SAM's person boxes from
``tracking.json`` over the move, padded, so the frame does not jitter), slowed to half speed so a
10 fps model sampling sees about 20 fps of real motion. Times convert both ways with ``Clip``.
FFmpeg runs as in ``remote.cut_frames``: argument lists, no shell, frame-exact selection.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .generation import GenerationError

SPEED = 0.5                 # playback rate of a close-up clip
PAD = 0.25                  # fraction of the player box added on every side
MAX_HEIGHT = 720
MIN_SIDE = 64               # pixels; smaller crops are not worth a model call


class ClipError(GenerationError):
    pass


@dataclass(frozen=True)
class Clip:
    path: Path
    start: float            # source seconds where the clip begins
    end: float
    speed: float = SPEED
    crop: tuple[int, int, int, int] | None = None   # x, y, width, height in source pixels

    def to_clip(self, t: float) -> float:
        return (t - self.start) / self.speed

    def to_source(self, t: float) -> float:
        return min(self.end, max(self.start, self.start + t * self.speed))

    @property
    def duration(self) -> float:
        return (self.end - self.start) / self.speed


def player_box(tracking: dict, start_frame: int, end_frame: int, width: int, height: int):
    """Union of the person boxes in [start_frame, end_frame), padded and kept inside the frame.

    Returns None when the player is not tracked there or the box would cover most of the frame.
    """
    boxes = []
    for record in tracking.get('frames', [])[start_frame:end_frame]:
        person = (record or {}).get('person') or {}
        box = person.get('bbox')
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            boxes.append(box)
    if not boxes:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    x0, x1 = max(0.0, x0 - PAD * w), min(float(width), x1 + PAD * w)
    y0, y1 = max(0.0, y0 - PAD * h), min(float(height), y1 + PAD * h)
    # Even sizes keep yuv420p encoders happy.
    left, top = int(x0) // 2 * 2, int(y0) // 2 * 2
    cw, ch = (int(x1) - left) // 2 * 2, (int(y1) - top) // 2 * 2
    if cw < MIN_SIDE or ch < MIN_SIDE or cw * ch > 0.85 * width * height:
        return None
    return left, top, cw, ch


def close_up(video: Path, out: Path, start: float, end: float, *, fps: float, width: int, height: int,
             tracking: dict | None = None, speed: float = SPEED) -> Clip:
    """Write the move between ``start`` and ``end`` seconds as a cropped, slowed clip."""
    if not 0 <= start < end:
        raise ClipError('A close-up needs an ordered time range.')
    first, last = int(round(start * fps)), max(int(round(start * fps)) + 1, int(round(end * fps)))
    crop = player_box(tracking, first, last, width, height) if tracking else None
    filters = [f"select='between(n,{first},{last - 1})'", f'setpts=(N/FRAME_RATE/TB)/{speed}']
    if crop:
        x, y, w, h = crop
        filters.append(f'crop={w}:{h}:{x}:{y}')
    filters.append(f"scale=-2:'min({MAX_HEIGHT},ih)'")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(video), '-vf', ','.join(filters), '-an',
                        '-r', f'{fps * speed:g}', '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(out)],
                       check=True, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        raise ClipError('Could not cut a close-up clip with FFmpeg.') from None
    return Clip(out, first / fps, last / fps, speed, crop)


def load_tracking(work: Path) -> dict | None:
    """SAM's tracking for the whole clip, when the reconstruction left it."""
    for path in (Path(work) / 'project' / 'motion' / 'tracking.json', Path(work) / 'analysis' / 'tracking.json'):
        if path.is_file():
            try:
                return json.loads(path.read_text())
            except ValueError:
                return None
    return None
