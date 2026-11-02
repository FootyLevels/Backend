# Lesson package format (version 1)

A lesson teaches the moves in one football video, in four stages: **Watch → Break it down →
Practise → Test yourself**. Default authoring uses the original video, shared instructions and
schema. It produces qualitative teaching content and numeric timestamps. When matching 3D assets
are available, the app/web package plays that body and the original video on one clock.
One video can hold several skills (moves); each skill has its own phases, drills and challenges.

The base machine-readable contract is [`schema/lesson.schema.json`](schema/lesson.schema.json).
The default API request uses its numeric-timestamp form, with qualitative `shows` text. The
checker (`python -m lesson_pipeline check`) checks structure and timing; `--measured` explicitly
enables the retained analysis/placeholder checks for older measured lessons.

## Work folder

Use one folder per source video. Authoring needs only a supported local video; reconstruction
and evidence files are optional and are not included in the default model request.

| Path | What |
|---|---|
| `source.<extension>` / `upload.mp4` | selected source or existing prepared copy; authoring uses the chosen video's clock and available audio |
| `cuts.json` | optional reconstruction scene cuts in seconds |
| `project/`, `part-*/` | optional backend motion and segment reruns |
| `analysis/` | optional `analysis.json`, `mesh.json`, `motion/`; required by existing 3D exporters |
| `evidence/` | optional measured evidence and contact sheets for experiments/operator inspection |
| `lesson.draft.json` | optional diagnostic draft from motion analysis |
| `generation/run-<uuid>/` | API audit: `prompt.md`, `response-N.txt`, `validation-N.json` and `run.json` |
| `lesson.json` | the validated API-authored lesson; a failed run preserves the previous valid lesson |
| `build/` | built outputs |

## API authoring

`generate [VIDEO]` sends the selected video and a fixed prompt to a configurable Gemini model, defaulting
through `--model`, then `GEMINI_MODEL`, then `gemini-4-argon`. Credentials come only from
`GEMINI_API_KEY` or `GOOGLE_API_KEY`. The prompt includes only shared instructions, the lesson
schema and basic metadata from the video. It includes no finished example, externally detected cuts,
motion evidence, measurements or draft. Video is sampled at 5 fps for fast skill movements.

The generator owns `schemaVersion`, `id`, `folder` and `video`; model output cannot choose an asset
path. The returned JSON must pass mechanical structure, finite-number, reference and timing checks. Repair
attempts are bounded (`--attempts`, default 3, maximum 5), and invalid output is retained only as
an audit response. Successful output replaces `lesson.json` atomically. API requests and uploads
are provider operations; offline tests do not verify a real model's response quality. The checker
does not establish educational effectiveness or factual coaching accuracy. Human review remains
separate from mechanical acceptance.

The uploaded provider file is removed on a best-effort basis when the run ends. Audit files contain
prompts, generated content and validation outcomes; do not publish them alongside private source
videos without reviewing their contents. The key is never included in these files.

## `lesson.json`

```jsonc
{
  "schemaVersion": 1,
  "id": "chop-and-spin",              // slug; also the app bundle name: Lesson-<id>.bundle
  "folder": "chop-and-spin",          // web output folder
  "title": "Chop and Spin Lesson",    // 2–4 words, a name not a sentence
  "accent": "Spin",                   // optional word of the title drawn in orange
  "subtitle": "One line: what this video teaches.",
  "video": "video.mp4",
  "videoLabel": "Original Cruyff chop and Antony spin video",   // accessibility label
  "sourceNote": "one public skills clip (credit its creator before any real use), …",
  "shots": [{ "start": 0, "end": 4.43, "label": "Hook", "quality": "low" }],  // quality optional
  "skills": [ /* see below */ ],
  "notes": { "limits": [], "tests": [], "choices": [] },     // extra lines for the About section
  "siblings": [{ "title": "…", "url": "…", "self": false }]  // optional links between web lessons
}
```

### Skill

| Field | Meaning |
|---|---|
| `id`, `name`, `short`, `tab` | slug; full name; short name for headings; small label above the tab (e.g. "Skill 1 · named", "Level 3") |
| `ballFoot` | `"left"` or `"right"`: display-colouring hint for the foot working the ball. Default authoring infers it from the video; uncertain side identity belongs in `unsure`. Existing measured lessons may derive it from reconstructed contacts. Prefer *ball foot* and *standing foot* in teaching text. |
| `ballLeavesGround` | `false` keeps the ball on the turf and never says "in the air" (ground moves). |
| `range`, `watch` | `[start, end]` seconds of the video this skill covers; `watch` is the part played at full speed in stage 1. |
| `watchTitle`, `intro`, `adds`, `breakdownIntro` | stage text; `adds` can explain what to inspect from another view when matching 3D playback is available. |
| `notice` | `[{t, title, text}]` moments to look for in stage 1 |
| `unsure` | what this clip cannot tell us (shown in stage 1) |
| `phases` | ordered, non-overlapping, inside `range` |
| `drills` | 4–5, from walking pace to a live defender |
| `challenges` | 3–4: usually `tap`, `choice`, `order` |

### Phase

```jsonc
{ "id": "chop", "name": "Plant and chop", "start": 4.95, "end": 5.30, "key": 5.03,
  "cue": "Plant beside the ball; chop it behind your standing leg.",   // the coaching cue, imperative
  "video": "What to look at in the video.",
  "shows": "He plants beside the ball, turns his body and changes direction.",
  "conf": "medium", "why": "Why that confidence.",
  "ov": { "prints": true, "paths": true, "ghost": true, "arrow": true },   // overlays to switch on
  "focus": "turn",       // reading to highlight: weight | stand | ball | turn | balls | drop
  "view": "top",         // 3D camera: video | side | behind | top
  "turn": true }         // show the turn readout during this phase
```

`key` is the frame the lesson freezes on: the clearest moment of the phase. Default `shows` text
is qualitative. Overlay, focus and camera options control the renderer when playback assets exist;
they do not establish calibrated measurements.

### Drill
`{ id, title, lvl, reps, how, model: [start, end, rate], checks: [3 self-checks] }` — `model` is
the clip the "Model" button loops; `rate` 0.25, 0.5 or 1.

### Challenges
- `choice`: `{ type, t, title, prompt, options[], answer (index), explain, play?: [s, e, rate], view?, ov? }` —
  freezes at `t`; after answering, optionally plays `play`.
- `tap`: `{ type, from, target, label, rate, tolerance, title, prompt, explain }` — plays from `from`;
  the learner taps the button at `target` (± `tolerance` s).
- `order`: `{ type, title, prompt, ids[], shuffled[] }` — `ids` are phase ids in the right order.

## Placeholders: numbers come from the 3D

The following syntax is retained for existing measured lessons and optional experiments. It is
not part of default API authoring, which uses numeric video timestamps and qualitative descriptions.
Use it only with matching analysis and an explicit measured check. Do not invent measured values.

In a measured lesson, write a placeholder and the build fills it in. `A`, `B` and `T`
are seconds on the video clock; `role` is `ball` (the skill's `ballFoot`) or `stand` (the other foot),
never `left` or `right`; `[role]` may be left out to count both feet.

| Placeholder | Becomes |
|---|---|
| `{{drop A B}}` | most the body centre drops below standing height, cm |
| `{{turn A B}}` | hip turn from A to B, degrees (measured against the camera: don't quote it where the camera pans) |
| `{{touches A B [ball\|stand]}}` | number of ball contacts |
| `{{touchesText A B [role]}}` | the same in words, with the right plural: "1 touch", "3 touches", "no touches" |
| `{{firstTouch A B [role]}}`, `{{lastTouch A B [role]}}` | time of the contact, s |
| `{{plantStart role T}}`, `{{plantEnd role T}}`, `{{plantLength role T}}` | the planted spell that includes T |
| `{{gap role A B}}` | closest the foot gets to the ball's surface, cm (at least 1: the ball's position is an estimate) |
| `{{travel A B}}` | how far the body moves across the pitch, m |
| `{{ballSpeed A B}}` | fastest tracked ball speed, m/s |
| `{{weightPct role A B}}` | average share of the weight toward that foot, % |

A numeric field may be a lone placeholder: `"target": "{{firstTouch 1.6 2.2 ball}}"`. If a
placeholder can't be computed (no touch there, foot not planted), `check --measured` reports it.

## `analysis.json`

Per-frame arrays (one value per video frame): `com`, `comDropCm`, `weight` (0 = over the left
foot, 1 = over the right), `left`/`right` foot positions, `leftSole`/`rightSole`,
`leftPlanted`/`rightPlanted`, `yaw` (hip facing, degrees, unwrapped), `ball` (or null), `ballSpeed`,
`gapL`/`gapR`, `valid`, `ballAir`, `legSwap`. Plus `shots` (with `camera`, `planeRmsCm`,
`ballTrusted`) and `events` (`touches`, `plants`). Positions are metres, Y up, pitch at y = 0,
per-shot origin, relative to the filming camera.

## Outputs

**Web lesson** (`build --web OUT`): `OUT/<folder>/index.html` and `data/` with `lesson.json`
(placeholders filled), `analysis.json`, `mesh.json`, `motion.json`, `motion-NNN.gz.txt` (base64,
because some hosts refuse binaries) and `video.mp4`.

**App bundle** (`build --app OUT` or `integrate`): `Lesson-<id>.bundle/` with `lesson.json`,
`analysis.json`, `mesh.json` (light mesh foot sets), `video.mp4` and `motion/` in the backend's
MotionSequence format (`sequence.json`, `faces.bin` uint32, `body-NNNNNN.bin.gz` uint16), so the
app's motion player loads it unchanged. The app lists every `Lesson-*.bundle` it ships with.

Both existing exporters require the matching analysis, mesh and motion assets. A valid video-only
authored `lesson.json` is not by itself a complete 3D lesson bundle. Asset preparation and actual
Swift/device acceptance are separate steps; the developer CLI adds no authenticated lesson route.
