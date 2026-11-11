# Lessons from a user's own video

A signed-in user taps **Create lesson** on a prepared upload and gets a full 3D lesson in the
Learn tab, labelled AI-generated, private to them, with "Report a problem". Built on grounded
authoring (`docs/lesson-pipeline-plan.md`, A1). No scoring of the player.

## Flow

`POST /api/projects/{id}/lessons` → job `buildLesson` → GPU runner (`run_reconstruction_worker.py
--lesson-adapter infra/dgx/lesson_adapter.py`) → `lesson_worker.run_job` → `lessons/{jobId}` →
the app polls `/api/jobs/{jobId}`, fetches `/api/lessons/{id}/assets`, downloads the bundle and
opens the existing `PackageLessonView`.

Everything uses the project's **prepared media** (30 fps, ≤ 720p, silent): Gemini timestamps, 3D
frames and playback share one clock.

| Stage | Where | What |
|---|---|---|
| `downloadingInput` | parent | prepared video from private storage |
| `screeningVideo` | parent (holds `GEMINI_API_KEY`) | A1 step 1; `lesson_rejected` stops here, before GPU time |
| `generatingBodies` | child, empty environment | runtime profile `lesson-motion-v1` (ball tracking, `tracking.json`), re-runs of lost segments, `analysis.analyze` |
| `writingLesson` | parent | A1 steps 3–5 (`grounded.author`, cached per job) |
| `checkingLesson` | parent | step 6 + `build_app` + `app_contract.check_bundle` |
| `publishingAssets` | parent | budgeted uploads under `users/{uid}/projects/{pid}/lesson/{jobId}/{attempt}/`, then `publish_lesson` |

## API

- `PUT /api/me/consents/lesson-ai` `{accepted}` — asked once per account (version `lesson-ai-v1`);
  `GET /api/me` returns `consents.lessonAi`.
- `POST /api/projects/{id}/lessons` `{inputRevision}` + `Idempotency-Key` → 202 + `Location`.
  403 `consent_required`; 409 not ready / stale revision / another job active; 422 clip over
  `LESSON_MAX_SECONDS` (default 20); 429 `lesson_quota` over `LESSON_DAILY_LIMIT` (default 3/day).
- `GET /api/lessons`, `GET /api/lessons/{id}`, `POST /api/lessons/{id}/assets` (5-minute signed
  URLs keyed by bundle path, e.g. `motion/sequence.json`), `DELETE /api/lessons/{id}`
  (tombstone; bytes reclaimed by output reconciliation), `POST /api/lessons/{id}/reports`.
- Project and account deletion remove `lessons`, `lessonReports` and quota counters.

## Configuration (private, never in git)

`~/.config/footylevels/<env>/runtime.json` may add `GEMINI_API_KEY`, `GEMINI_MODEL`
(default `gemini-4-carbon`), `LESSON_MAX_SECONDS`, `LESSON_DAILY_LIMIT`. Lesson jobs stay queued
unless the runner has both `--lesson-adapter` and a key. For the CLI, `~/.config/footylevels/lesson.env`
(mode 600) holds the key and `LESSON_GPU_HOST` (an SSH alias); no address is committed.

## Rollout order

New optional fields are written only once set, so older strict readers keep working on existing
records. A `buildLesson` job, however, is unknown to the deployed 367b37a reader: deploy the API,
GPU runner and runtime together before enabling lesson requests (or keep
`runtimeControls/reconstruction.acceptingRequests` false until all three are updated).

## Verification (2026-11-11)

Verified:
- Offline: `pytest tests/` — 626 passed, 29 skipped (cloud/Firestore live tests skip by design).
  New: `test_lesson_passes.py`, `test_lesson_app_contract.py`, `test_lessons_api.py`,
  `test_lesson_worker.py`, lesson-profile test in `test_motion_bridge.py`.
- Live `gemini-4-carbon`: the model ID exists and the key works; a full grounded run on the
  chop-and-spin clip made 10 calls (Teach needed 2 repairs), found the same three moves as the
  hand-made lesson, removed or moved 6 claims to `unsure`, and passed `check --measured --strict`.
- Live GPU (DGX runtime on port 8013, existing body-motion path): whole-clip reconstruction of the
  same clip — 280 frames, 275 with a body, ball in 198, 11 touches, 5 plants; `build --app`
  produced an 8.1 MB bundle that passes `app_contract.check_bundle`. The five shipped app bundles
  also pass it.

Not verified yet:
- The cloud path end to end (Firestore transactions in `lessons.py` / `lesson_jobs.py`, the
  runner dispatch, `lesson_adapter.py` against the runtime's new `lesson-motion-v1` profile): it
  needs the API, runner and runtime deployed to Development. The Firestore code mirrors the
  reconstruction code; its admission logic (`lessons.admit`) is unit-tested.
- Runtime budgets for 20 s clips at the new profile; measure seconds per frame before raising
  `LESSON_MAX_SECONDS`.
- iOS: Frontend branch `codex/lesson-from-video` builds for the simulator (Xcode 26.4) and the app
  launches; the new XCTest cases (`CloudLessonTests.swift`) compile, but the simulator test runner
  hung before connecting on this machine (iOS 18 and 26), so they have not run. The on-device
  create → poll → open flow needs the deployed backend.
