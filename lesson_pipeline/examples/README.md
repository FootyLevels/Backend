# Finished lessons

Five preserved fixtures from `3d-model-backend` branch `FP-72-lesson-pipeline` at commit
`0d06b891805ef0f68e9cc0544567ae5124489461`, one per source video. They establish the fixed format,
tone, detail and uncertainty. API prompting includes one finished lesson as a reference.

| Folder | Video | Skills | Worth reading for | 3D project |
|---|---|---|---|---|
| `hocus-pocus` | the move at full speed at night, then a slow labelled tutorial and a side-on replay | 1 | a tutorial and its demo in **one** skill; 6 phases | `4ae6e537dd534fbe` |
| `chop-and-spin` | a hook clip, then two named moves | 3 | named moves; an unnamed bonus move; a shot where the defender hides the player | `8012c358c2054f82` |
| `three-1v1-moves` | three moves, handheld 60 fps camera | 3 | describing moves the video doesn't name | `fa087525553d4268` |
| `skill-ladder` | five difficulty levels, mostly at night | 5 | rough footage: low confidence, `unsure`, `quality: "low"` | `d0d62b53ff7e4722` |
| `three-levels` | three levels, one segment re-run on its own | 3 | ground moves (`ballLeavesGround: false`) | `34baa5042bc2472f` |

The project IDs identify the original prototype runs. They are provenance, not a guarantee that
those server files remain available or accessible to the current operator. The source videos are
not bundled in this repository. To reuse an original project, obtain the permitted source video
and the correct dedicated runtime from its owner, then use `run --reuse` with that runtime.

Each folder holds:

- `lesson.json`: the authored fixture. Measured numbers are placeholders (`{{drop 2.0 2.6}}`).
  New API generation produces the same format.
- `lesson.resolved.json`: the same lesson after the build filled in the placeholders. Read it to see
  what a placeholder turns into. Don't copy numbers from it: your video's numbers come from your
  own `analysis.json`.
- `analysis.json`: the measurements the placeholders were resolved against.

The tests (`tests/test_lesson_pipeline.py`) check every example and that it still resolves to
`lesson.resolved.json`, so a change to the measurements shows up as a failing test.
