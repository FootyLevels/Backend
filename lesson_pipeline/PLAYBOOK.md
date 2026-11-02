# Playbook: football video → lesson in the app

Operator procedure for API-based lesson generation. Follow the steps in order.
The goal: given one football skills video, produce a lesson (web page and app bundle) that teaches
each move in it — **Watch → Break it down → Practise → Test yourself** — grounded only in what the
video and its 3D reconstruction show, and add it to the FootyLevels app.

Read [`SPEC.md`](SPEC.md) for the format. The API prompt includes the authoring rules below,
measured evidence and a finished lesson from [`examples/`](examples/). The model fills the fixed
format; the operator reviews its result against the video.

## 0. What you need

- This Backend checkout with `python -m pip install -e '.[lessons]'` in its environment.
- `ffmpeg`/`ffprobe` and `rsync` on the machine running the pipeline.
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the generator's environment. No key goes into a command
  argument, work file or commit. `GEMINI_MODEL` or `--model` can override `gemini-4-argon`.
- Key-based SSH to a dedicated compatible lesson runtime on the GPU server, with its explicit
  `--port`, `--server-root` and, if data is stored separately, `--server-data`. See
  [Setup](README.md#setup). The authenticated application API is not this runtime.
- For step 7: the FootyLevels Frontend repository and Xcode.

Use a work folder outside both repositories, one per video, e.g. `~/lesson-work/<lesson-id>`.
The Gemini request uploads the prepared video to the provider and may incur API charges. Do not
use the managed app model process for lesson reconstruction without operational authorization.

## 1. Run the pipeline

```bash
python -m lesson_pipeline run VIDEO.mp4 --work ~/lesson-work/<id> --host USER@HOST \
  --port PORT --server-root SERVER_FOLDER --server-data DATA_FOLDER \
  --title "<Working Title>" [--reuse PROJECT_ID]
```

This finds the scene cuts, reconstructs the 3D on the GPU (5–20 minutes per video; segments where
the tracker lost the player are re-run on their own automatically), analyses it, writes the
evidence pack and an automatic draft. Add `--reuse PROJECT_ID` to reuse a finished backend project.
If it stops with an SSH or GPU error, report it and stop: do not invent data. A `partial` status must be inspected: it can indicate missing body frames or a failed optional
ball mesh. The lessons do not use the ball mesh, but they must retain uncertainty and missing-data
labels for incomplete tracking or reconstruction.

## 2. Read the evidence

Open `evidence/evidence.md` and look at every image it links:

- `shot-N.jpg`: the whole shot, timestamped. Read on-screen text (titles, labels such as
  "Roll back", "Level 3") and note when each label appears.
- `shot-N-burst-K.jpg`: close-ups around each burst of ball touches. This is where the move happens.
- The timeline table: body lowering, hip direction, which foot is planted, foot-to-ball gap.
- Each shot's 3D quality: pitch fit under 5 cm is good; a ball marked "not trusted" is hidden.

The API model watches the prepared video and receives the measurements as text. During review,
check whether its selected skills distinguish a move, title card or replay, and whether the phase
times cover the set-up, key touch, direction change and exit actually visible in the video.

## 3. Decide the skills

- One skill per move. Usually one per shot; a slow tutorial and its full-speed demo of the same
  move belong in **one** skill (use the demo as `watch`, break down the tutorial).
- Skip shots that are only titles, hooks, replays or reactions: leave them out of every skill's
  `range` (they stay in `shots`, so the timeline still labels them), or attach them to the nearest
  skill's range when they show the same move.
- Name a move only if the video names it (an on-screen label) or it is unmistakable. Otherwise
  describe what it does ("Feint and burst", "Stop and cut back") and say in `unsure` that the video
  doesn't name it.

## 4. Generate the lesson through the API

```bash
python -m lesson_pipeline generate --work ~/lesson-work/<id> --id <lesson-id> \
  --model gemini-4-argon --attempts 3
```

The model receives the video, measured evidence, version-1 format and a finished example. The code
owns the lesson ID and asset paths. Responses are validated; checker feedback drives bounded repair
attempts. A failed run leaves any prior valid lesson untouched and keeps its audit files under
`generation/run-<uuid>/`. Do not paste requests or replies into the Gemini app.

The draft is a diagnostic starting point. The model should delete, merge, split, rename and renumber
skills and phases to match the actual moves, using meaningful skill IDs. It must set
`ballLeavesGround` from visible evidence, not blindly copy tracker estimates.

The following model-facing rules are embedded in the API prompt. Then edit `lesson.json`. Rules:

1. **Ground everything in the evidence.** Every cue and every "video" line must describe something
   visible in the video. If you can't see it, don't claim it; add it to `unsure` instead.
2. **Numbers only via placeholders** (SPEC.md). Never type a measured value. Check that each
   placeholder's time window covers the moment you describe (`check` warns when one misses its phase).
3. **Feet by job, not side.** Say "ball foot" and "standing foot". Do not write "left foot" or
   "right foot": one camera can swap the legs. Keep `ballFoot` as the draft set it unless the
   touches clearly belong to the other measured foot.
4. **Phases:** 3–6 per skill, each one idea, in order, touching end to start, inside the skill's
   range. They needn't cover all of it: a full-speed demo used as `watch` can stay without phases.
   Put `key` on the clearest frame of the phase. Give each a short imperative `cue` (≤ 10 words),
   a `video` line (what to look at), a `shows` line (what the 3D measured, with placeholders),
   `conf` + `why`, the overlays that help (`ov`), a `focus` reading and a `view`.
   Use `{{touchesText …}}` rather than "`{{touches …}}` touches" so "1 touch" reads right. A reading
   that comes out as 0 ("about 0 cm lower") is better said in words ("he stays upright"); `check`
   warns when one does. Hip turns are measured against the camera: where the camera pans or turns
   with the player, describe the turn ("he spins all the way round") instead of quoting `{{turn …}}`.
5. **Confidence:** `high` only for slow, clearly visible moments; `medium` for most; `low` for fast,
   blurred, night, occluded or wide-angle footage, and for anything about the ball's height or speed.
6. **Watch stage:** `watch` is the full-speed part; 3–5 `notice` moments with what to look for.
7. **Drills:** 4–5, progressive: footwork or walking pace → ball at walking pace → jogging/cone →
   passive defender at full speed. Each with a `model` clip from this video and 3 concrete self-checks.
8. **Challenges:** 3–4 per skill: a `tap` on the key touch (target from a placeholder or a measured
   touch time), a `choice` that predicts the next movement or explains why, and an `order` of the
   phases. Wrong options must be plausible.
9. **Honesty:** list in `unsure` what the clip can't show (unnamed move, hidden contact, rough 3D,
   untracked ball). Set `ballLeavesGround: false` for ground moves. Mark rough shots with
   `"quality": "low"` in `shots`.
10. **Tone:** plain, short sentences; second person in cues; no ratings or judgements of the player.
    Credit the video's creator in `sourceNote`.
11. Remove `"draft": true` and `reviewNotes` when finished.

## 5. Check and review

```bash
python -m lesson_pipeline check --work ~/lesson-work/<id> --strict
```

Generation already validates before publishing `lesson.json`. This independent check also works
for an existing lesson or after a code change. Read warnings and inspect the video: structural
validation does not establish correct coaching, skill names, contact or physics. If all API attempts
fail, inspect `generation/run-<uuid>/validation-N.json` and correct the underlying evidence or
configuration before intentionally starting another generation run.

## 6. Build

```bash
python -m lesson_pipeline build --work ~/lesson-work/<id> --web ~/lesson-work/web --app ~/lesson-work/app
```

Optional: serve the web lesson with a server that supports HTTP range requests (video seeking
needs them) and click through every stage of every skill.

## 7. Add it to the app

```bash
python -m lesson_pipeline integrate --work ~/lesson-work/<id> --frontend /path/to/Frontend
```

This copies `Lesson-<id>.bundle` into `FootyLevels-Frontend/SampleData/Lessons/`. Xcode's
synchronized folders include it automatically and the Learn tab lists every lesson bundle, so no
project or code change is needed. Build and run the tests:

```bash
xcodebuild test -project FootyLevels-Frontend.xcodeproj -scheme FootyLevels-Frontend \
  -destination 'platform=iOS Simulator,name=iPhone 17' -only-testing:FootyLevels-FrontendTests/LessonPackageTests
```

`LessonPackageTests` decodes every bundled lesson and fails on a broken one.

## 8. Report

Say what you produced (lesson id, skills, phases), the provider/model, anything in `unsure`, the
check result, and whether app acceptance was performed. Distinguish offline fixture checks from a
real API call, GPU generation and app verification. Do not commit or push unless asked.

## Definition of done

- API generation succeeded and `check --strict` reports 0 errors.
- Every skill has phases, 4–5 drills and 3–4 challenges, all inside its range.
- No typed measurements, no left/right foot claims, no draft text left.
- The bundle is in the app and `LessonPackageTests` passes.
