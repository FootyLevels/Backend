# For AI agents

- **Asked to make a lesson from a video?** Follow [`PLAYBOOK.md`](PLAYBOOK.md). Use the API-key
  `generate` command for lesson authoring; do not use the Gemini app, clipboard replies or a
  coding-agent authoring bypass. Reuse an existing work folder when analysis is already present.
  Reconstruction needs the supplied GPU host, dedicated runtime port/folder and optional data
  folder; app installation needs the Frontend path. Resolve missing inputs before dependent work.
- **Asked to change the pipeline?** Keep `SPEC.md`, `schema/lesson.schema.json`, `validate.py` and
  the app's `Models/LessonPackage.swift` in agreement. Run
  `python -m pytest tests/test_lesson_pipeline.py tests/test_lesson_generation.py -q`; the examples
  must still resolve to their `lesson.resolved.json`.
- Credentials are environment-only (`GEMINI_API_KEY` or `GOOGLE_API_KEY`). Never put keys in source,
  shell arguments, prompts or audit output. Offline verification uses fake API responses. State
  separately whether a live provider request, GPU run or app verification was actually performed.
