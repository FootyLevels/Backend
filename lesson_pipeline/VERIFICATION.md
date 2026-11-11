# API authoring migration — 2 November 2026

Source: `FootyLevels/3d-model-backend`, branch `FP-72-lesson-pipeline`, commit
`0d06b891805ef0f68e9cc0544567ae5124489461`. Destination: Backend branch
`codex/fp-72-lesson-pipeline-api`, implementation checkpoint `7ed3373`.

The version-1 lesson schema and five reference lessons are unchanged. Authoring now uses
the Gemini Developer API with environment-only keys. The app/clipboard handoff commands
were removed. Local schema and semantic checks gate publication of `lesson.json`.

## Completed checks

- Full Backend tests: **504 passed, 29 skipped**. The existing Backend Python 3.11
  environment was used, with the isolated worktree's optional dependency directory
  appended to `sys.path`. The skips are opt-in cloud/platform checks. Two upstream
  Google Python-version warnings were reported.
- Focused lesson/API/CLI tests: **118 passed** in the worktree environment. No API key,
  provider request or GPU was used. Tests include all five examples resolving to their
  original published text, malformed/nonfinite replies, strict checker feedback,
  bounded repair attempts, upload polling/timeout/failure, best-effort deletion,
  sanitized provider errors, and preservation of an existing lesson after failure.
- Independent review led to fixes and regression checks for HTML title/attribute
  injection, unsafe sibling URL schemes, and output-folder traversal.
- Clean wheel built and inspected: 68 entries, including existing `footylevels` code
  and lesson schema, examples, rules, reference meshes and web template. Tests, runtime
  source and the removed chat handoff are excluded. After installing the wheel, the
  CLI, prompt rules and all five example validations/resolutions passed from `/tmp`,
  outside the checkout.
- Google Gen AI SDK 2.34.0 accepted the request/configuration shapes locally, including
  file-based video at 5 fps, JSON response mode and disabled automatic request retries.
- `git diff --check` passed.

## Remaining verification

No Gemini API key was available, so real video authoring quality, account/model access,
provider acceptance and actual provider cleanup remain unverified. No GPU job, cloud
deployment, authenticated lesson API route, Frontend change or iPhone acceptance is
part of this migration. The operator CLI still needs prepared motion analysis; the
current reconstruction runtime limits input to 60-second clips. Human review of move
names, coaching cues, timing and uncertainty remains necessary even after validation.

## Subsequent live context pilot

After a key was supplied, the six independent context-experiment requests completed on
2 November 2026. All passed their common mechanical checks without repair and provider upload
cleanup succeeded. See [EXPERIMENTS.md](EXPERIMENTS.md) and its execution evidence. This pilot
uses qualitative explanations and video timestamps; it does not establish human teaching quality,
Swift acceptance, or live production `generate` placeholder/repair behavior.

## Minimal authoring default — 2 November 2026

At checkpoint `26f3f40`, default generation was changed to video + shared instructions + schema.
It no longer reads examples, cuts, analysis or evidence, and no GPU preparation is needed for
authoring. Validation checks numeric timestamps, JSON shape and playback references rather than
agreement with reconstructed motion. Legacy measured examples and optional 3D packaging remain
compatible; the current exporters still require their separate matching MP4/motion assets.

All 186 focused tests passed. One real Gemini 4 Argon run from a clean Chop and Spin workspace
succeeded on the first response, with zero mechanical errors/warnings and successful provider
file cleanup. The prompt was 11,107 characters. No analysis/evidence sidecars were present.
See [minimal-baseline-evidence.json](minimal-baseline-evidence.json). This verifies the minimal
authoring path, not the correctness of the coaching or a complete app integration/deployment.
