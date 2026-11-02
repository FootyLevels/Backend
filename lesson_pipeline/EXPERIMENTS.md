# How much context helps lesson authoring?

This pilot compares first responses from three nested prompt variants. People review the
breakdowns; the code reports mechanical properties without assigning teaching-quality scores.

| Context | Inputs |
|---|---|
| `video` | Video, common rules, lesson schema and unrelated reference example |
| `shots` | The same inputs plus advisory camera-cut ranges |
| `reconstruction` | The same inputs and cuts plus explicitly fallible reconstructed-motion estimates |

The shared rules are neutral: numeric timestamps from the video, qualitative movement explanations,
and no required measurement placeholders. The production prompt's hidden 3D assumptions are removed
from all arms. The Hocus Pocus example is converted to qualitative observations and is identical
across arms. Camera cuts do not dictate skills or phases. Estimated contacts, foot plants, ball
positions and camera-relative turns are suggestions rather than ground truth.

Every arm retains the same schema, 3–6 phase / 4–5 drill / 3–4 challenge instructions, reference,
video bytes, 5 fps sampling, model, temperature 1, and output limit. This tests extra metadata
within that lesson structure; it does not test whether the structure or example is too restrictive.

## Run

Prepare an existing lesson workspace with `upload.mp4`, `analysis/analysis.json` and
`evidence/evidence.md`. Do not include a finished lesson for the target video in the prompt.
Prepare the randomized plan without spending API credits:

```sh
python -m lesson_pipeline.experiments prepare --work /path/to/work \
  --output /path/to/experiment --id context-pilot --model gemini-4-argon --repeats 2
```

Set `GEMINI_API_KEY` in the process environment, then execute the six independent requests:

```sh
python -m lesson_pipeline.experiments run --output /path/to/experiment
```

There is one provider upload, removed on a best-effort basis after the run. The API calls have no
conversation history, repair feedback or automatic transport retries. Invalid JSON, refusals and
mechanical failures are retained as outcomes. Known completed/failed calls and calls with an
uncertain lost response are not automatically repeated on resume. Auth/model/quota rejection stops
the remaining requests. After an interrupted process, inspect the manifest and confirm the process
has stopped before removing its `.running` lock. Original video/prompt hashes are checked on resume.

The output folder contains matched prompts, raw replies, mechanical diagnostics, input hashes,
model version and token usage, plus `review.html` and the video. The HTML shows neutral lesson
labels; an expandable section reveals the context mapping. Prefer reviewing the breakdowns before
revealing it. The manifest/logs retain the mapping for reproducibility, so this is not an enforced
blinded study. Regenerate the HTML without API calls using:

```sh
python -m lesson_pipeline.experiments report --output /path/to/experiment
```

Review move grouping, useful phase boundaries, actionable cues, unsupported claims, unnecessary
detail and overall usefulness. Skill/phase counts, latency and tokens describe the outputs;
none establishes educational quality. Two samples per arm on one clip are a pilot. A preference
needs confirmation on other videos before choosing the default context.

## First pilot inputs

The 2 November 2026 pilot uses the saved Chop and Spin video: 9.333333 seconds, 280 frames at
30 fps, 360×640, no audio. SHA-256:
`be1a00c9bfd618a71595559e81f27fd6d49bfe8ce5ec33a53b8954312f3c1e03`.
The matching existing reconstruction has 275 valid body frames and 162 ball frames. The finished
Chop and Spin lesson is withheld from the model. All arms receive the same silent video;
results do not establish behavior on clips with tutorial narration. No new GPU run is needed.
