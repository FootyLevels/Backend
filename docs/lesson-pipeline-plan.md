# Lesson pipeline: improvement plan

The lesson pipeline (`lesson_pipeline/`, FP-72) turns a football video into a
Watch → Break it down → Practise → Test yourself lesson. Until this change it was **one Gemini
call**: the model watched the whole clip at 5 fps and returned `lesson.json`; mechanical checks
passed it through; the app rendered it. Nothing checked the text against the 3D measurements the
pipeline also produces, the lesson never changed once displayed, and lessons reached users only as
five bundles compiled into the app.

The weakness is not the lesson format. It is that the format is filled **once, by one model pass,
unverified, and then frozen**. Four directions, in recommended order. Product rule throughout: no
scoring of the player.

## A1. Grounded multi-pass authoring — built (`generate --mode grounded`)

Each step has a narrow job; the 3D measurements act as a second witness.

| Step | Input | Output |
|---|---|---|
| 1. Screen | whole clip, 5 fps | moves with rough ranges, or a rejection (`not_football`, `no_clear_player`, `unclear`); runs **before** GPU time |
| 2. Reconstruct + measure | clip | existing `analysis.json` (touches, plants, turn, drop, ball) |
| 3. Look closely | per move: clip cropped to the player (SAM `tracking.json` boxes), half speed, 10 fps sampling (≈ 20 fps of real motion vs 5) + measured touch/plant times as fallible hints | phases with `start` / `end` / `key` |
| 4. Teach | step-3 timeline + schema | cues, drills, challenges; numbers only as `{{touches A B ball}}`-style placeholders, filled from measurement |
| 5. Verify | each move's close-up + its claims | supported / unclear / unsupported; unsupported removed, unclear moved to `unsure` |
| 6. Deterministic checks | lesson + analysis | ball foot vs measured contacts, keys snapped to measured events, challenges topped up from the timeline, placeholders resolved, `validate(strict=True)`, app contract (`app_contract.py`) at build |

Code: `generation.Session` (shared upload, bounded repair, audit, cache), `passes.py`,
`clips.py`, `grounded.py`, `app_contract.py`. The single-call path stays as `--mode baseline`, so
both can be compared on the same clips with `experiments.py`. Cost: about 3 + 2 × moves calls
instead of 1, bounded by the authoring cache and (in the cloud) a daily quota.

## A2. Make the lesson respond to the learner (later)

- **Ask the coach**: a question box in a lesson, answered from that lesson's text, video and
  measurements, seeking the video to the moment referred to.
- **Adaptive practice**: quiz answers and drill self-checks live only on the phone
  (`DrillProgressStore`, UserDefaults). Sync them; then reorder drills, re-explain the phase a
  learner keeps missing, and resurface challenges on a spaced schedule.
- **Practise with your own attempt**: record a drill attempt, reconstruct it, show it beside the
  model clip (the Overlay tab already aligns two motions) with descriptive differences. Needs a
  product decision: it sits close to the no-execution-correctness rule.

## A3. Stop generating what should be curated (later)

- **Vetted move library**: identify the move first, then pull coach-approved cues, common mistakes
  and drill progressions; generate from scratch only for unknown moves. Consistent lessons and a
  real progression ("levels") across them.
- **Lesson format v2**: common mistakes, when to use the move in a game, prerequisites,
  difficulty; spoken cues for hands-free practice; rhythm cues from measured touch timing.

## A4. Know whether lessons are any good (later, cheap)

- A fixed evaluation set (the five reference clips + ~20 user-style clips) with a human rubric
  reusing `judging.CRITERIA`. Per run record first-pass validity, repairs, claims removed by Verify
  (`authoring.json`), calls/cache hits, latency. A model judge stays advisory: the pilot showed it
  contradicting itself.
- In-app signals: reports, phases replayed, challenge accuracy, drill completion.

## A5. Engineering debt found on the way

- Two validators with different rules (`validate_structure` vs Swift `LessonPackage.check`):
  `app_contract.py` now mirrors the Swift loader and runs in `build_app` — **done**.
- Authoring cache by `(videoSha256, promptSha256, model, step)` — **done** (`Session`).
- Throughput: measure seconds per frame on the DGX; evaluate the `3d-model-backend` `perf/*`
  branches; worker polling is 120 s.
- Service logs go to `/dev/null` (`infra/dgx/services.py`); no CI; no linter.

## Verification status

See `docs/lesson-from-video.md` for the cloud feature and what was verified live versus offline.
