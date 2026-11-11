# Playbook: minimal-context video lesson authoring

The default baseline sends **video + shared instructions + schema** to the API. It asks the model
to teach the visible moves through **Watch → Break it down → Practise → Test yourself**, using
qualitative explanations and numeric video timestamps. Examples, camera-cut annotations, drafts
and reconstructed-motion evidence are withheld.

The shared model instructions are implemented in `prompting.py`. This playbook guides the operator;
it is not an extra document attached to the request. Read [`SPEC.md`](SPEC.md) for the output fields.

## 1. Prepare inputs

- Install the Backend `lessons` extra and provide `ffprobe`.
- Set `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the process environment. Keep keys out of arguments,
  prompts, audit files and commits.
- Select the video and a work folder outside both repositories, one per source clip.
- Choose a short lowercase lesson slug, such as `elastico`.

No GPU, SSH connection, analysis file or evidence pack is needed for authoring. The selected video
is uploaded to Google and API calls may incur charges. Its available audio is retained; using an
existing silent prepared copy does not restore the original narration.

## 2. Generate

```bash
python -m lesson_pipeline generate VIDEO.mp4 --work ~/lesson-work/<id> --id <lesson-id> \
  --model gemini-4-carbon --attempts 3
```

Omit `VIDEO.mp4` when the workspace already contains the intended `upload.mp4` or supported `source`
video. Verify that it is the right clip before reusing a workspace. The generator owns the lesson
ID and asset paths. It publishes `lesson.json` only after mechanical checks pass; a failed run
preserves the previous accepted file. Original replies and findings remain under
`generation/run-<uuid>/`.

The API request contains no completed reference lesson, cut list, measured event table or 3D
analysis. Do not add those silently to this baseline, and do not use a chat-app or coding-agent
authoring bypass. Context variants and advisory model judging are separate experiments.

## 3. Check the format

```bash
python -m lesson_pipeline check --work ~/lesson-work/<id> --strict
```

This checks structure and consistency within the video clock. It is useful for malformed fields,
nonfinite numbers, reversed or out-of-video ranges, invalid phase references and similar issues.
It does not decide whether a move was explained correctly or whether someone can learn from it.
If repair attempts fail, inspect the saved findings before intentionally making another API run.
For an existing measured lesson with placeholders, use `check --measured` with its matching analysis.

## 4. Review the teaching content

Watch the video while reading each skill and phase. Inspect:

- Whether the move grouping makes sense, including demonstrations, replays and opening footage.
- Whether the breakdown covers the useful set-up, main action, transition and exit without
  splitting one action into arbitrary phases.
- Whether short cues give an action the learner can understand and practise.
- Whether contact, foot roles, direction changes and timing match what is visible.
- Whether occluded or ambiguous actions are acknowledged instead of asserted confidently.
- Whether drills progress sensibly and questions test the visible sequence.

Do not treat camera edits as mandatory skill boundaries. Use a descriptive move name when the
video does not establish a conventional name. Prefer foot roles in teaching text; `ballFoot` is a
display hint whose left/right identity may be uncertain. Avoid exact distance, force, weight,
speed or angle claims from uncalibrated footage. The model's own confidence is not verification.

Record what needs changing in the shared rules or schema. A Gemini judge can provide timestamped
criticisms as a second opinion, but human review accepts the content. Do not revise lessons or
choose a production context automatically from model ratings.

## 5. Package when matching playback assets are available

Lesson authoring and 3D asset preparation are separate. The retained app/web exporters still need
matching `analysis.json`, `mesh.json` and motion chunks. Reuse saved assets or run the optional
reconstruction procedure in [the README](README.md#optional-reconstruction-and-app-packaging).
Those assets do not become default authoring context merely because they exist in the workspace.

```bash
python -m lesson_pipeline build --work ~/lesson-work/<id> --web ~/lesson-work/web --app ~/lesson-work/app
python -m lesson_pipeline integrate --work ~/lesson-work/<id> --frontend /path/to/Frontend
```

`integrate` changes the selected Frontend checkout. It copies the bundle into
`FootyLevels-Frontend/SampleData/Lessons/`. Build and verify the app separately; generating JSON
does not establish Swift decoding, synchronized playback or physical-device acceptance.

## 6. Report completion precisely

State the video, lesson ID, model, mechanical check outcome, human content-review status and any
uncertainty. Distinguish offline tests, live API generation, GPU processing, packaging and actual
app verification. Do not claim deployment or an authenticated lesson endpoint from this CLI work.

Authoring is complete when the API returns a mechanically valid `lesson.json`. Acceptance for a
release additionally requires human content review and app acceptance of its matching bundle.
