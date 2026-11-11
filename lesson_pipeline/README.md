# Lesson pipeline: video → structured learning lesson

The default (`--mode baseline`) authoring input is **video + shared instructions + lesson schema**. Gemini identifies
the moves, breaks them into phases, and writes cues, drills and questions in the fixed
**Watch → Break it down → Practise → Test yourself** structure. It receives no reference lesson,
external camera cuts, draft, motion measurements or reconstruction evidence.

This keeps the first application-integration baseline small enough to assess the model's own video
interpretation. Additional context remains an optional experiment. The earlier context pilot's
`video` arm included an example; it is not the new no-example baseline.

The code was extracted from
[`FootyLevels/3d-model-backend`, `FP-72-lesson-pipeline`](https://github.com/FootyLevels/3d-model-backend/tree/FP-72-lesson-pipeline),
commit `0d06b891805ef0f68e9cc0544567ae5124489461`. The version-1 Swift lesson shape, reference
fixtures and 3D/web exporters are retained. This is a developer CLI in Backend. It does not create
an authenticated cloud lesson endpoint or deploy a change to the Swift app.

## Setup

- Python 3.11 or later and the Backend lesson dependencies:
  ```bash
  cd Backend
  python -m pip install -e '.[lessons]'
  ```
- `ffprobe` on the command line, to read local video metadata.
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the generator's environment. Keep the key in local
  secret configuration, never in arguments, prompts or commits. `GEMINI_MODEL` sets the default
  model when `--model` is omitted; otherwise the default is `gemini-4-carbon`.

Authoring does not require a GPU, SSH, reconstructed motion, `analysis.json` or an evidence pack.
The API uploads the selected video to Google and may incur provider charges. See Google's
[video input guide](https://ai.google.dev/gemini-api/docs/video-understanding) and
[Gemini models documentation](https://ai.google.dev/gemini-api/docs/models).

## Grounded mode (`--mode grounded`)

`generate --mode grounded` needs the 3D reconstruction and `analysis/` (from `run`, or the cloud
lesson worker) and chains narrow calls through one provider session, with the measurements as a
second witness: **screen** (whole clip at 5 fps; rejects clips that are not football or have no
clear player, before GPU time) → **look** (per move, a player-cropped half-speed clip sampled at
10 fps ≈ 20 fps of real motion, with measured touches/plants as fallible hints → phases) →
**teach** (the checked timeline + schema → cues, drills, challenges, numbers only as `{{…}}`
placeholders) → **verify** (each claim supported / unclear / unsupported; unsupported removed,
unclear moved to `unsure`) → deterministic checks (ball foot against measured contacts, keys
snapped to measured events, challenges topped up from the timeline, placeholders resolved,
`check --measured --strict`). `build --app` then runs the Python mirror of the app loader
(`app_contract.py`). Accepted responses are cached by video, prompt, model and step, so a retry
re-bills nothing that already passed. Grounded mode costs about 3 + 2 × moves calls.

Settings may live in `~/.config/footylevels/lesson.env` (mode 600; `GEMINI_API_KEY`,
`GEMINI_MODEL`, `LESSON_GPU_HOST`), loaded by the CLI and never committed; `run` uses
`LESSON_GPU_HOST` when `--host` is omitted.

## Generate and review

Use a work folder outside the repositories, one per video:

```bash
python -m lesson_pipeline generate clip.mp4 --work ~/lesson-work/elastico --id elastico
python -m lesson_pipeline check --work ~/lesson-work/elastico --strict
```

The optional positional video is copied into the workspace as `source` with its original extension.
Omit it to use an existing prepared `upload.mp4` or supported `source` video. The selected file is
what the model sees, including any audio it contains; a previously prepared silent copy remains
silent. Video sampling is 5 fps.

The output is `lesson.json`, with numeric video timestamps and qualitative movement explanations.
The generator checks JSON structure, finite values, in-video times and references. Bounded repair
requests can fix these mechanical failures (`--attempts 1–5`, default 3). A failed run preserves
any existing valid lesson. These checks do not establish correct coaching, contact, physics,
learner understanding or educational effectiveness.

Read the output alongside the video before accepting it. Check move grouping, useful phase
boundaries, actionable cues, unsupported claims, unnecessary detail and uncertainty. The reusable
authoring instructions live in `prompting.py`; [`PLAYBOOK.md`](PLAYBOOK.md) describes the review
procedure, and [`SPEC.md`](SPEC.md) documents the lesson fields.

```bash
python -m lesson_pipeline generate clip.mp4 --work ~/lesson-work/elastico \
  --id elastico --model gemini-4-carbon --attempts 3
```

Audit files under `WORK/generation/run-<uuid>/` retain the prompt, replies, mechanical findings and
execution metadata. They contain video-derived content; review them before sharing. The provider
upload is deleted on a best-effort basis when the run finishes. API keys are never saved there.

## Optional reconstruction and app packaging

The existing app and web exporters include a synchronized 3D viewer. Building those packages still
requires matching `analysis/analysis.json`, `analysis/mesh.json` and `analysis/motion/` assets.
Generating lesson text alone does not create that geometry or a complete runnable 3D lesson bundle.

Reuse matching saved assets when available. To prepare new assets, the retained reconstruction
commands need FFmpeg, rsync, key-based SSH and a dedicated compatible GPU runtime:

```bash
python -m lesson_pipeline run clip.mp4 --work ~/lesson-work/elastico \
  --host you@gpu-host --port PORT --server-root /path/to/Backend/runtime/skill_lab \
  --server-data /path/to/lesson-runtime/data --id elastico
```

This optional path computes cuts, motion, measurements, evidence and a diagnostic draft. Those
files are not added to the default Gemini authoring request. The runtime currently accepts clips
up to 60 seconds. Use a lesson runtime rather than the managed app model process; `--reuse ID`
can reuse the initial reconstruction, while recovery of missing segments may still use the GPU.

After human review and preparation of matching playback assets:

```bash
python -m lesson_pipeline build --work ~/lesson-work/elastico \
  --web ~/lesson-work/web --app ~/lesson-work/app
python -m lesson_pipeline integrate --work ~/lesson-work/elastico --frontend ../Frontend
```

`integrate` writes `FootyLevels-Frontend/SampleData/Lessons/Lesson-<id>.bundle` in the selected
Frontend checkout. Build alone keeps output in the chosen folders. Swift/device acceptance is a
separate check. Neither command adds a cloud lesson-generation route.

## Experiments and compatibility

The [context experiment](EXPERIMENTS.md), advisory judge and five [reference fixtures](examples/)
remain available for later investigation. They are not part of default generation. Existing
measured lessons retain their placeholder syntax and deterministic resolution; use
`check --measured` with matching analysis for those lessons. New baseline lessons use
video-observed timestamps and qualitative descriptions.

With `pip install -e '.[dev,lessons]'`, run:

```bash
python -m pytest tests/test_lesson_pipeline.py tests/test_lesson_generation.py -q
```

Offline checks use fixtures and fake provider responses. Report real API generation, GPU work and
app acceptance separately. A single-camera model can still misread occluded feet, contact and
fast motion even when the JSON is valid. No player score or execution-correctness classifier is
generated.
