"""Minimal authoring context: source video, shared instructions and the lesson schema."""
from __future__ import annotations

import json
import math
import re
import subprocess
from fractions import Fraction
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
VIDEO_MIMES = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
               '.m4v': 'video/mp4', '.mpeg': 'video/mpeg', '.mpg': 'video/mpeg',
               '.avi': 'video/avi', '.flv': 'video/x-flv', '.wmv': 'video/wmv', '.3gp': 'video/3gpp'}


class PromptError(RuntimeError):
    pass


def video_path(work: Path) -> Path:
    for name in ('upload.mp4', *(f'source{suffix}' for suffix in VIDEO_MIMES)):
        path = Path(work) / name
        if path.is_file():
            return path
    raise PromptError(f'No source video in {work}; supply VIDEO to generate.')


def video_metadata(work: Path) -> dict:
    video = video_path(work)
    try:
        response = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=duration,avg_frame_rate,r_frame_rate,width,height,nb_frames:format=duration',
            '-of', 'json', str(video)], check=True, capture_output=True, text=True, timeout=30)
        data = json.loads(response.stdout)
        stream = data['streams'][0]
        try:
            duration = float(stream['duration'])
        except (KeyError, ValueError, TypeError):
            duration = float(data['format']['duration'])
        try:
            fps = float(Fraction(stream['avg_frame_rate']))
        except (KeyError, ValueError, ZeroDivisionError):
            fps = float(Fraction(stream['r_frame_rate']))
        if not math.isfinite(duration) or duration <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError()
        if stream['width'] <= 0 or stream['height'] <= 0:
            raise ValueError()
        frame_count = stream.get('nb_frames', '')
        frames = int(frame_count) if str(frame_count).isdecimal() else round(duration * fps)
        return {'duration': duration, 'fps': fps, 'frameCount': frames,
                'width': stream['width'], 'height': stream['height'], 'mimeType': VIDEO_MIMES[video.suffix.lower()]}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, ZeroDivisionError):
        raise PromptError('Could not inspect the video. Check the file and FFprobe installation.') from None


def lesson_schema() -> dict:
    """Keep the Swift v1 shape, with numeric times and qualitative movement descriptions."""
    schema = json.loads((PACKAGE / 'schema' / 'lesson.schema.json').read_text())
    schema['$id'] = 'lesson.baseline.schema.json'
    schema['description'] = 'A video-grounded learning lesson with numeric video timestamps.'
    definitions = schema['$defs']
    definitions.pop('placeholder', None)
    definitions['time'] = {'type': 'number', 'minimum': 0, 'description': 'Seconds from the video start.'}
    definitions['skill']['properties']['ballFoot']['description'] = (
        'App display-colouring hint for the apparent ball foot. Explain ambiguity in unsure.')
    definitions['phase']['properties']['shows']['description'] = (
        'Qualitative visible movement to inspect; avoid unsupported physical measurements.')
    return schema


def authored_schema() -> dict:
    """The lesson format as an author writes it: numeric times or lone measurement placeholders."""
    return json.loads((PACKAGE / 'schema' / 'lesson.schema.json').read_text())


def make_prompt(work: Path, lesson_id: str, *, metadata=None) -> str:
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', lesson_id):
        raise PromptError('Lesson id must be a short lowercase slug.')
    data = metadata if metadata is not None else video_metadata(work)
    return f'''Create a football learning lesson from the supplied video ({data['duration']:.6f} seconds).

Teach the visible moves through Watch → Break it down → Practise → Test yourself.
Identify the moves and useful teaching phases from the video. A camera edit need not be a new skill.
Use short, actionable cues, explain what to watch, offer progressive drills and concrete self-checks,
and include questions about visible actions. Prefer feet named by their role in teaching text.
Use established move names only when supported; otherwise use descriptive names.
Keep explanations qualitative. Do not invent physical measurements, forces or precise contact.
Disclose unclear or hidden actions and give confidence reasons based on visibility.
Do not score the player. Do not claim that questions or self-checks measure physical performance.

Return one JSON object following the schema, without Markdown fences.
Set schemaVersion to 1, id and folder to "{lesson_id}", and video to "video.mp4".
All times are numeric seconds on this video's clock. Infer shot and phase boundaries from the video;
keep every time range within it and every skill's subranges within that skill.
Use unique IDs and valid phase references. Credit only a source identifiable from the video;
otherwise describe it as the supplied video. Include no template placeholders or draft flags.

Lesson schema:
{json.dumps(lesson_schema(), ensure_ascii=False, indent=1)}
'''


# --- Grounded multi-pass authoring (grounded.py) --------------------------------------------------
# Each pass has one narrow job and a small JSON answer. Times are always on the clip the model is
# shown; grounded.py converts close-up clip times back to the source video.

PLACEHOLDER_GUIDE = '''Numbers come from the 3D measurement, never from you. Where a sentence needs one, write a
placeholder and the build fills it in (times are source-video seconds; role is "ball" or "stand"):
  {{drop A B}} cm the body centre drops · {{turn A B}} degrees the hips turn · {{touches A B [role]}}
  {{touchesText A B [role]}} e.g. "3 touches" · {{firstTouch A B [role]}} / {{lastTouch A B [role]}} s
  {{plantStart role T}} / {{plantEnd role T}} / {{plantLength role T}} s · {{gap role A B}} cm
  {{travel A B}} m · {{ballSpeed A B}} m/s · {{weightPct role A B}} %
A numeric field (a challenge "target" or "t") may be a lone placeholder such as "{{firstTouch 1.6 2.2 ball}}".
Never type a measured number with a unit (cm, °, m/s, degrees). Use a placeholder only for a window
where that thing happens: a touch count for a window with touches, a plant for a planted moment.'''


def screen_prompt(duration: float) -> str:
    return f'''Screen this video ({duration:.3f} seconds) before any lesson is written.

Decide whether it shows one clearly visible player performing football (soccer) ball skills that
could be taught. Answer with one JSON object:
{{"verdict": "lesson" | "not_football" | "no_clear_player" | "unclear",
  "reason": "one short sentence",
  "moves": [{{"name": "established or descriptive move name", "start": seconds, "end": seconds}}]}}

Rules: "moves" lists 1–4 distinct teachable moves in time order, each at least 0.4 s long, inside the
video and not overlapping; give each the whole move, from set-up to finish. A camera edit is not a
new move. Leave "moves" empty unless the verdict is "lesson". Do not score or judge the player.'''


def look_prompt(name: str, duration: float, speed: float, hints: list[str]) -> str:
    hint_text = '\n'.join('  - ' + h for h in hints) or '  (no measured events in this clip)'
    return f'''Look closely at one football move, "{name}". This clip is cropped to the player and slowed to
{speed:g}× speed; it lasts {duration:.3f} seconds. Answer in this clip's seconds.

Break the move into 3–6 teaching phases in time order (set-up, contacts, turn, exit, …). For each
phase give start, end and key (the clearest single moment to freeze on, inside the phase), a short
name, what is visible in "shows" (qualitative, no measurements), and confidence with a visibility
reason. Say which foot works the ball ("left", "right" or "unclear" if hidden or ambiguous).

The 3D measurement detected these events; they are fallible hints, use them only where the video agrees:
{hint_text}

Answer with one JSON object:
{{"ballFoot": "left" | "right" | "unclear",
  "phases": [{{"name": "...", "start": s, "end": s, "key": s, "shows": "...",
              "conf": "high" | "medium" | "low", "why": "..."}}],
  "unsure": ["what this clip cannot show"]}}'''


def teach_prompt(lesson_id: str, duration: float, timeline: list[dict], schema: dict) -> str:
    return f'''Write a football learning lesson for this video ({duration:.3f} seconds): Watch → Break it down →
Practise → Test yourself. The moves and their phases were already found and checked; keep them.

Timeline (source-video seconds; ballFoot was checked against the 3D measurement):
{json.dumps(timeline, ensure_ascii=False, indent=1)}

Write one skill per timeline move, with the same skill "id", "range" and "ballFoot", and the same
phases: same "id", "start", "end" and "key" values, in the same order. You may refine phase names
and "shows". Per skill: an intro; "watch" inside the range; a short imperative "cue" per phase
(≤ 10 words); 4–5 progressive drills (walking pace → match speed) whose "model" clip is
[start, end, rate] inside the range, each with concrete self-checks; 4 challenges of which at least
two are "tap" or "order" challenges built on the phases; and "unsure" listing what the clip cannot
show. Name feet by job ("ball foot", "standing foot"), never left or right.

{PLACEHOLDER_GUIDE}

Keep explanations qualitative. Do not score the player or claim that drills or questions measure
performance. Credit only a source identifiable from the video; otherwise "the supplied video".
Return one JSON object following this schema; set schemaVersion 1, id and folder "{lesson_id}",
video "video.mp4"; include no draft flag.

Lesson schema:
{json.dumps(schema, ensure_ascii=False, indent=1)}'''


def verify_prompt(name: str, duration: float, speed: float, claims: list[dict]) -> str:
    return f'''Check a lesson's claims about one football move, "{name}", against this clip. The clip is cropped to
the player and slowed to {speed:g}× speed; it lasts {duration:.3f} seconds; claim times are in this
clip's seconds.

For each claim answer "supported" (clearly visible), "unclear" (hidden, blurred, off-frame or
ambiguous) or "unsupported" (the video shows something else). Judge the visible action only; any
measured numbers come from 3D tracking and are not yours to judge.

Claims:
{json.dumps(claims, ensure_ascii=False, indent=1)}

Answer with one JSON object: {{"claims": [{{"id": "...", "verdict": "...", "reason": "..."}}]}},
one entry per claim id.'''
