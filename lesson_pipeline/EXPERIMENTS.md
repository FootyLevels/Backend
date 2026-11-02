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

## First pilot execution

All six live Gemini 4 Argon requests completed, with no repairs and no mechanical findings.
The provider file was deleted successfully. The recorded model version was `gemini-4-argon`
for every response. Inputs were 11,382 tokens for video, 11,492 for shots, and 14,122 for
reconstruction; total reported usage across the six calls was 113,241 tokens. One response
reported implicit cached tokens, so elapsed times are descriptive rather than a latency benchmark.
All six outputs contained three skills and twelve drills, with ten to twelve phases in total.
These counts do not establish lesson quality or which context is preferable.

The neutral-label review page and complete replies are kept in the local experiment output,
outside the repository. Human content review is pending. Portable, credential-free execution
metadata is in [context-pilot-evidence.json](context-pilot-evidence.json). The focused lesson and
experiment suite passed 139 checks; the final review-rendering check passed all 21 experiment tests.

## Optional advisory model judge

To obtain a second opinion, the judge receives the same source video and the same learner-facing
content from all six lessons, using stable random aliases. Generation prompts, conditions, model
metadata, reference answers and reconstructed-motion evidence are withheld. Each candidate retains
its own uncertainty statements; the judge could still infer aspects of a process from the wording.

```sh
python -m lesson_pipeline.judging prepare --results /path/to/experiment
python -m lesson_pipeline.judging run --results /path/to/experiment
```

The two independent calls reverse presentation order, with no previous judge response supplied.
The rubric distinguishes visible-video fidelity, useful breakdown, actionable clarity, uncertainty,
practice progression and appropriate detail. Ties and abstention are allowed; extra length or phases
alone should earn no credit. Major criticisms require lesson excerpts and video timestamps.
The judge is not told a preferred skill/phase count or asked to infer the authoring method.

The model's scores and rankings concern lessons, never player performance. They do not establish
actual learner comprehension or expert coaching correctness. Using the same model family can retain
shared misunderstandings and stylistic preferences. Two orders can expose instability but cannot
separate order effects from sampling variation. Both assessments are preserved separately, with
provider usage and cleanup status, in `judge/`. No automatic lesson revision follows a judgment.

The first advisory review completed both live passes. Pass 1 preferred Lesson 02; the reversed
order pass preferred Lessons 05 and 01 jointly. The reviewer also contradicted itself about which
foot works the ball in the final spin. This is a concrete reason to inspect its criticisms rather
than treat the rankings as acceptance. All 24 cited lesson excerpts were found in the supplied
candidate text; that checks excerpt presence, not the accuracy of its video interpretation.
Both reviews and their raw responses remain in the local `judge/` output. Provider cleanup
succeeded. No lesson was automatically changed. Human review remains pending.

Known LLM-judge limitations include position, verbosity and self-preference effects; see
[Zheng et al.](https://arxiv.org/abs/2306.05685) and
[Panickssery et al.](https://arxiv.org/abs/2404.13076). These studies do not establish a specific
Gemini error rate for football coaching. In this pilot, order and sampling effects are confounded,
and using the same model family may preserve shared misunderstandings.
