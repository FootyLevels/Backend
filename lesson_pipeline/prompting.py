"""Build the fixed lesson request from video measurements, schema and writing rules."""
from __future__ import annotations

import json
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
EXAMPLE = 'hocus-pocus'          # the shortest finished lesson: one skill, every field in use


class PromptError(RuntimeError):
    pass


def _between(text: str, start: str, end: str) -> str:
    try:
        a = text.index(start) + len(start)
        return text[a:text.index(end, a)].strip()
    except ValueError:
        raise PromptError(f'could not find "{start}" … "{end}"; PLAYBOOK.md or SPEC.md changed shape')


def _rules() -> tuple[str, str, str]:
    playbook = (PACKAGE / 'PLAYBOOK.md').read_text()
    spec = (PACKAGE / 'SPEC.md').read_text()
    return (_between(playbook, '## 3. Decide the skills', '## 4.'),
            _between(playbook, 'Then edit `lesson.json`. Rules:', '## 5.'),
            _between(spec, '## Placeholders: numbers come from the 3D', '## `analysis.json`'))


def _measurements(work: Path) -> str:
    """evidence.md without its pictures: the model watches the video instead."""
    path = work / 'evidence' / 'evidence.md'
    if not path.exists():
        raise PromptError(f'no {path}; run `run` (or `evidence`) first')
    lines = [l for l in path.read_text().splitlines() if '](' not in l and not l.startswith('# ')]
    return '\n'.join('#' + l if l.startswith('## ') else l for l in lines).strip()   # shots nest under this section


def make_prompt(work: Path, lesson_id: str) -> str:
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', lesson_id):
        raise PromptError(f'lesson id {lesson_id!r} must be a short lowercase slug, e.g. hocus-pocus')
    analysis = json.loads((work / 'analysis' / 'analysis.json').read_text())
    video = next((work / n for n in ('upload.mp4', 'source.mp4') if (work / n).exists()), None)
    if video is None:
        raise PromptError(f'no video in {work}')

    fps, duration = analysis['fps'], analysis['duration']
    shots = [(round(s['start'] / fps, 2), round(s['end'] / fps, 2)) for s in analysis['shots']]
    skills_rules, writing_rules, placeholders = _rules()
    schema = (PACKAGE / 'schema' / 'lesson.schema.json').read_text().strip()
    example = json.dumps(json.loads((PACKAGE / 'examples' / EXAMPLE / 'lesson.json').read_text()), ensure_ascii=False, indent=1)
    shot_list = ', '.join(f'{a:g}–{b:g} s' for a, b in shots)

    prompt = f"""# Write a football skills lesson from the attached video

You are writing one lesson for FootyLevels, an app that teaches football skills. The attached video
(video.mp4, {duration:.2f} s) shows one or more moves. Watch all of it, more than once and slowly where
the move is fast. The lesson teaches each move in four stages: Watch → Break it down → Practise → Test
yourself. The app shows your text beside the video and a 3D body reconstructed from it.

## What to send back

Reply with one JSON object and nothing else. Do not use Markdown code fences. It must follow "The lesson
format" below exactly: the app only understands that format. Every time is seconds from the start of
this video, the same clock the measurements below use.

Fixed values (use exactly these):
- "schemaVersion": 1, "id": "{lesson_id}", "folder": "{lesson_id}", "video": "video.mp4"
- "shots": one entry per camera shot, with exactly these times: {shot_list}. Write each "label" yourself.

Where the rules below say "evidence", "sheets" or "the draft", use the video itself; there is no draft.

## Deciding the skills

{skills_rules}

## How to write it

{writing_rules}

## Numbers come from the 3D

{placeholders}

## What the 3D measured in this video

{_measurements(work)}

## The lesson format (JSON Schema)

```json
{schema}
```

## A finished lesson, for a different video

Match its tone, detail and honesty, not its content.

```json
{example}
```
"""
    return prompt

