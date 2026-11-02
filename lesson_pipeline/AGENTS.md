# For AI agents

- **Asked to make a lesson from a video?** Follow [`PLAYBOOK.md`](PLAYBOOK.md). The default API
  authoring input is video + shared instructions + schema only. Do not attach examples, external
  cut annotations, drafts, motion measurements or evidence. Use the API-key `generate` command;
  do not substitute chat-app replies or a coding agent's authored lesson.
- Authoring requires the selected video, a work folder, `ffprobe` and the API key. GPU/SSH and
  analysis assets are not prerequisites. The existing app/web 3D exporters separately require
  matching motion assets; resolve their runtime/Frontend inputs only when that work is requested.
- **Asked to change the pipeline?** Keep `SPEC.md`, `schema/lesson.schema.json`, `validate.py` and
  the app's `Models/LessonPackage.swift` compatible. Run
  `python -m pytest tests/test_lesson_pipeline.py tests/test_lesson_generation.py -q`; existing
  measured example lessons must still resolve to their `lesson.resolved.json`.
- Credentials are environment-only (`GEMINI_API_KEY` or `GOOGLE_API_KEY`). Never put keys in source,
  shell arguments, prompts or audit output. Offline verification uses fake API responses. State
  separately whether live provider generation, human content review, GPU work or app acceptance
  was performed.
- Preserve prior experiments as historical evidence. Their `video` condition included a reference
  example; it is distinct from the current no-example default. Keep experiments and model judging
  out of the default authoring path unless explicitly requested.
