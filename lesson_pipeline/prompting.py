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
