# Lesson pipeline: football video → lesson in the app

This developer pipeline turns one football skills video into a lesson with a fixed structure:
**Watch → Break it down → Practise → Test yourself**. The output is a `Lesson-<id>.bundle` for the
FootyLevels Learn tab and, optionally, a standalone web lesson. One video can teach several moves.

Code owns the lesson format, motion measurements, validation and packaging. A multimodal model
watches the video and fills that format with move names, phases, cues, drills and questions. Lesson
authoring uses the Gemini API with an API key; there is no Gemini app, clipboard or manual reply
handoff. The default model is `gemini-4-argon`; `--model` selects another compatible Gemini model.

The pipeline was extracted from
[`FootyLevels/3d-model-backend`, `FP-72-lesson-pipeline`](https://github.com/FootyLevels/3d-model-backend/tree/FP-72-lesson-pipeline),
commit `0d06b891805ef0f68e9cc0544567ae5124489461`. Its version-1 lesson format, five examples and Swift
bundle layout are preserved. This is an operator CLI in Backend; adding it does not create an
authenticated cloud lesson-generation route or change the app's private reconstruction profile.

## Setup

- Python 3.11 or later, the Backend environment, and the lesson-generation dependencies:
  ```bash
  cd Backend
  python -m pip install -e '.[lessons]'
  ```
- `ffmpeg`, `ffprobe` and `rsync` available on the command line.
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) set in the environment of the process running `generate`.
  Keep the key in local secret configuration; do not put it in arguments, lesson files or source
  control. `GEMINI_MODEL` can set the default model when `--model` is omitted.
- Key-based SSH access to a configured, dedicated lesson reconstruction runtime. You need its
  loopback port, runtime folder and data folder. The compatible runtime code lives in
  [`runtime/skill_lab`](../runtime/skill_lab/README.md). A run can occupy the GPU for several minutes;
  use a runtime intended for lessons rather than the app's managed model process.
- The Frontend checkout if you want to install a generated bundle in the app. Xcode is needed only
  to build or verify the app.

The model call uploads the prepared video to Google. Gemini API requests may incur provider charges
and are governed by the key's project configuration. The pipeline does not create a key or change
billing settings. See Google's [video input guide](https://ai.google.dev/gemini-api/docs/video-understanding)
and [Gemini 4 Argon model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon).

## Generate a lesson

Use a work folder outside the repositories, one per video.

1. Reconstruct and measure the video:
   ```bash
   python -m lesson_pipeline run clip.mp4 --work ~/lesson-work/elastico \
     --host you@gpu-host --port PORT --server-root /path/to/Backend/runtime/skill_lab \
     --server-data /path/to/lesson-runtime/data --id elastico --title "Elastico"
   ```
   This prepares a video-only upload, detects cuts, obtains reconstructed motion, computes the
   measurements, and writes evidence and a draft. `--server-data` is needed when `SKILL_LAB_DATA`
   puts the runtime's project files outside `<server-root>/data`.

2. Ask the multimodal API to author the lesson:
   ```bash
   python -m lesson_pipeline generate --work ~/lesson-work/elastico --id elastico \
     --model gemini-4-argon --attempts 3
   ```
   The request includes the video, fixed format, authoring rules, measured evidence and a finished
   example. Video sampling is 5 fps to retain more fast-motion detail than the provider's default.
   The pipeline validates returned JSON and sends checker feedback for a bounded number of repair
   attempts (1–5, default 3). A failed run preserves any existing valid `lesson.json`.

3. Check and review the result:
   ```bash
   python -m lesson_pipeline check --work ~/lesson-work/elastico --strict
   ```
   Read the lesson alongside the source video. Check move names, step timing, coaching claims and
   uncertainty. Measured values remain placeholders filled from `analysis.json`, not model-invented
   measurements. Read [`PLAYBOOK.md`](PLAYBOOK.md) for the content rules and acceptance criteria.

4. Build and install:
   ```bash
   python -m lesson_pipeline build --work ~/lesson-work/elastico \
     --web ~/lesson-work/web --app ~/lesson-work/app
   python -m lesson_pipeline integrate --work ~/lesson-work/elastico --frontend ../Frontend
   ```
   The app discovers `Lesson-<id>.bundle` in `FootyLevels-Frontend/SampleData/Lessons/`. Integrating
   writes that Frontend folder; building alone keeps outputs in the chosen output folders.

`python -m lesson_pipeline <command> --help` lists the options. To repeat the measurement stage,
pass `--reuse PROJECT_ID` to `run` against the same runtime and video. This skips the original
project's inference; any segment that still needs a separate recovery run can use the GPU.

## What owns each step

| Step | Owner |
|---|---|
| Video preparation, scene cuts, reconstruction and motion analysis | Code and the configured GPU runtime |
| Move names, phases, cues, drills, questions and uncertainty | Multimodal API model |
| Lesson structure, placeholder resolution and validation | Code |
| Swift bundle and web packaging | Code |
| Whether the lesson accurately teaches the visible move | Human review |

The pipeline standardizes the expected output; it does not make model wording or judgment
deterministic. No score or skill-correctness classifier is generated.

## Files

| Path | Purpose |
|---|---|
| [`PLAYBOOK.md`](PLAYBOOK.md) | Content rules and the operator procedure |
| [`SPEC.md`](SPEC.md) | Version-1 lesson format, measurements and outputs |
| [`schema/lesson.schema.json`](schema/lesson.schema.json) | Machine-readable lesson structure |
| [`examples/`](examples/) | Five finished lessons and their measurement fixtures |
| `cli.py`, `generation.py`, `prompting.py` | Commands, API generation and the fixed authoring prompt |
| `remote.py` | Dedicated reconstruction runtime access over SSH |
| `analysis.py`, `measures.py` | Motion measurements and numeric placeholders |
| `evidence.py`, `draft.py` | Contact sheets, evidence and automatic draft |
| `validate.py`, `authoring.py` | Lesson checks and loading |
| `build.py` | Swift bundles, web output and integration |
| `refs/`, `web/` | Shared body reference data and the web player |

The app reader is `Frontend/FootyLevels-Frontend/Models/LessonPackage.swift`; its package player
and lesson library already consume this bundle format. API attempts are recorded under
`WORK/generation/run-<uuid>/` for review; the uploaded provider file is deleted on a best-effort
basis after the run. These local audit files contain generated lesson content, not API keys.

## Verification and limits

Run `python -m pytest tests/test_lesson_pipeline.py tests/test_lesson_generation.py -q` for the
offline pipeline and API-client regressions. These tests use fixtures/fake provider responses and
do not spend provider credits. A real key-backed video generation, GPU run and app acceptance are
separate checks; no live provider or GPU verification is implied by a passing offline suite.

One camera leaves depth, contact and ball height uncertain. Hip turns are camera-relative, and
camera panning can invalidate a quoted turn. Fast footage can lose the ball or player track.
Validation checks structure and consistency with the measurements; it cannot judge whether a cue
or drill is correct. Review the generated lesson before adding it to a release.
