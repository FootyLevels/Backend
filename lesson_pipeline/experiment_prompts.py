"""Matched authoring prompts for the context experiment, without hidden 3D requirements.

The experiment asks for qualitative teaching content and numeric video timestamps.
Production authoring and its measurement placeholders remain unchanged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .prompting import PACKAGE, PromptError

CONTEXTS = ('video', 'shots', 'reconstruction')


def _measurements(work: Path) -> str:
    """evidence.md without its pictures: the model watches the video instead."""
    path = work / 'evidence' / 'evidence.md'
    if not path.exists():
        raise PromptError(f'no {path}; run `run` (or `evidence`) first')
    lines = [l for l in path.read_text().splitlines() if '](' not in l and not l.startswith('# ')]
    return '\n'.join('#' + l if l.startswith('## ') else l for l in lines).strip()   # shots nest under this section


def shared_schema() -> dict:
    """A fresh schema with the same Swift lesson shape for every experiment arm."""
    schema = json.loads((PACKAGE / 'schema' / 'lesson.schema.json').read_text())
    schema['$id'] = 'lesson.experiment.schema.json'
    schema['description'] = ('A complete lesson for the context experiment. All timestamps are '
                             'numeric seconds in the supplied video; teaching explanations are qualitative.')
    definitions = schema['$defs']
    definitions.pop('placeholder', None)
    definitions['time'] = {'type': 'number', 'minimum': 0,
                           'description': 'Seconds from the start of this video.'}
    skill = definitions['skill']['properties']
    skill['ballFoot']['description'] = ('The apparent foot working the ball, for display colouring. '
                                       'If uncertain, make a display choice and explain the uncertainty in unsure.')
    skill['phases']['minItems'] = 3
    skill['drills']['minItems'] = 4
    skill['challenges']['minItems'] = 3
    skill['challenges']['maxItems'] = 4
    definitions['phase']['properties']['shows']['description'] = (
        'A qualitative movement observation to help the learner inspect this phase. '
        'Describe visible evidence and uncertainty without physical measurements.')
    return schema


def _example() -> dict:
    """Use the same unrelated reference in all arms, with no 3D-derived readings."""
    path = PACKAGE / 'examples' / 'hocus-pocus' / 'lesson.resolved.json'
    lesson = json.loads(path.read_text())
    lesson['subtitle'] = 'Roll it back, tap it in, flick it behind your standing leg.'
    lesson['sourceNote'] = 'one tutorial clip; source-video observations include uncertainty.'
    skill = lesson['skills'][0]
    skill['intro'] = ('Stop the ball under your sole, roll it back, tap it in, then bring the ball '
                      'foot behind your standing leg and go. Watch the full-speed demo, then the tutorial.')
    skill['adds'] = ('Compare the tutorial with the side-on replay to inspect the ball foot as it '
                      'moves around the standing leg. Use the clearest view of each action.')
    skill['unsure'] = [
        'The foot is hidden behind the standing leg during the flick, so its exact contact surface is unclear.',
        'The night demonstration is dark and fast; exact contact timing is uncertain.'
    ]
    cues = [
        'Keep the ball close; prepare your standing foot.',
        'Roll the ball back under you with your sole.',
        'Tap it across towards the standing foot.',
        'Wrap behind the standing leg; flick the ball across.',
        'Straighten up and follow the ball away.',
        'Watch the same pattern from the side.'
    ]
    for phase, cue in zip(skill['phases'], cues):
        phase['cue'] = cue
        phase['shows'] = phase['video']
        phase['conf'] = 'medium'
        phase['why'] = 'The visible movement supports this description; exact contact can be obscured.'
    challenge = skill['challenges'][0]
    challenge.update(
        title='Which foot moves the ball?',
        prompt='During the roll back, which foot moves the ball?',
        options=['The foot with its sole on the ball', 'The supporting foot', 'Both feet together'],
        answer=0,
        explain='The foot on top of the ball draws it back while the other foot supports the player.'
    )
    skill['challenges'][1]['explain'] = (
        'Look for the ball foot moving behind the supporting leg. The exact contact is partly hidden.')
    lesson['notes'] = {'limits': skill['unsure'].copy()}
    return lesson


def _advisory_measurements(work: Path) -> str:
    text = _measurements(work)
    text = text.replace('tracked and trusted', 'candidate track available; reliability unverified')
    text = text.replace('not trusted (hidden in lessons)', 'unreliable candidate track; avoid relying on it')
    text = re.sub(r'\btouches\b', 'candidate contacts', text, flags=re.IGNORECASE)
    text = re.sub(r'\btouch\b', 'candidate contact', text, flags=re.IGNORECASE)
    text = text.replace('(good)', '(small fitted residual; physical accuracy unverified)')
    return text


def make_prompt(work: Path, lesson_id: str, context: str) -> str:
    """Build one matched prompt; only the optional context section differs by arm."""
    if context not in CONTEXTS:
        raise PromptError(f'Unknown experiment context: {context}')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', lesson_id):
        raise PromptError('Experiment lesson id must be a short lowercase slug.')
    work = Path(work)
    analysis = json.loads((work / 'analysis' / 'analysis.json').read_text())
    duration = float(analysis['duration'])
    prompt = f'''# Write a football learning lesson from the supplied video

The video is {duration:.6f} seconds long. Watch the visible actions and write one lesson teaching
the moves it contains in four stages: Watch → Break it down → Practise → Test yourself.
The lesson will accompany the source video and an interactive body viewer. Judge the actions
from the video. Information outside the video, if provided, is fallible supporting context.

## Output contract

Return one complete JSON object with no Markdown fences. Use schemaVersion 1,
id "{lesson_id}", folder "{lesson_id}" and video "video.mp4". Every timestamp must be a numeric
number of seconds from the start of this video. Do not use measurement placeholders.

## Common authoring rules

- Identify distinct moves from the video. A camera cut does not define a new skill, and a
  demonstration and slow replay of the same move can belong to one skill. Infer editorial
  shots from the video and label them; a continuous clip can have one full-length shot.
- Use an established move name only when visible text identifies it or the move is clear;
  otherwise use a descriptive name and explain the uncertainty. Do not invent attribution.
- Each skill has 3–6 ordered, non-overlapping phases within its range. Choose phases for useful
  learning actions, with a clear key frame, a short imperative cue (at most 10 words), a video
  observation, and qualitative shows text. Avoid physical measurements such as angles,
  distances, force, weight percentages or exact contact counts. Timing and practice doses
  are allowed. Do not infer force or precise contact from an estimated body model.
- Name feet by job in teaching text: ball foot and standing/supporting foot. ballFoot is a
  display-colouring choice (left/right); explain uncertainty when the side is unclear.
- Include 3–5 notice moments for Watch; use the clearest available demonstration range.
- Include 4–5 progressive drills, from controlled footwork to connected execution, with
  repetition targets, a model-video range and at least three practical self-checks each.
- Include 3–4 challenges: spot a visible event (tap), explain or predict it (choice), and put
  phases in order (order). Use video-observed numeric times; acknowledge ambiguous contact.
- Keep phase, skill, drill and challenge times inside the video. Use unique stable IDs and
  valid phase references. Select useful overlay, focus and view options from the schema.
- Keep explanations plain and concise. Ground claims in visible actions. List uncertainty
  in unsure and give confidence reasons based on visibility. Do not score the player.
- External context may be ignored or corrected when it disagrees with the video. Avoid
  copying a reference lesson's actions, timing or number of skills into this video's lesson.

## Shared lesson format

```json
{json.dumps(shared_schema(), ensure_ascii=False, indent=1)}
```

## Shared reference lesson from a different video

This illustrates the output shape and writing style. It does not describe the supplied video.

```json
{json.dumps(_example(), ensure_ascii=False, indent=1)}
```
'''
    if context in ('shots', 'reconstruction'):
        fps = analysis['fps']
        shots = [{'start': round(s['start'] / fps, 2), 'end': round(s['end'] / fps, 2)}
                 for s in analysis['shots']]
        prompt += f'''
## Advisory camera-cut context

An external process proposed these editorial shot ranges (seconds):
{json.dumps(shots)}
Use them only as navigation suggestions. Verify them against the video, correct them if needed,
and choose skill and teaching-phase boundaries independently. These are not supplied skill labels.
'''
    if context == 'reconstruction':
        prompt += f'''
## Advisory reconstructed-motion context

The following is generated by an approximate single-camera body/ball reconstruction and heuristics.
These are estimates and candidate events, not independently measured facts. In particular,
candidate contacts are inferred from reconstructed proximity after estimating ball depth near
the feet; they do not establish actual contact. Foot plants and body lowering can be wrong.
Hip turns are relative to the filming camera, not a calibrated world frame. Weight-related
signals are body-position proxies, not measured forces. A small fit residual or an available
track does not establish physical accuracy. Check every proposed event against the video.
Use a detail only if it helps explain the visible movement; you may disregard all of it.
Keep the lesson's explanations qualitative and retain uncertainty.

{_advisory_measurements(work)}
'''
    return prompt
