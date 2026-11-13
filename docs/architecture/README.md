# FootyLevels architecture

How the FootyLevels app and its backend work, feature by feature. Each page starts with a short
plain-language summary for readers outside the team, then the diagrams, rules and the code that
implements them.

Diagrams are editable Mermaid sources in [`../../diagrams`](../../diagrams/README.md), rendered to
SVG (for zooming) and PNG (for documents). Pages describe the code on Backend `master` and
Frontend `main` as of 13 November 2026 (Backend 6bd43cf, Frontend 340a8de).

| Page | What it covers |
| --- | --- |
| [01 Overview](01-overview.md) | What the system does, its parts, where they run, the API and security model |
| [02 Data model and schema](02-data-model.md) | Every stored record, its fields and rules, file layout and the lesson file format |
| [03 Accounts and sign-in](03-accounts-and-sign-in.md) | Firebase sign-in, profile bootstrap, tokens and account states |
| [04 Videos: upload and preparation](04-videos-upload-and-preparation.md) | Recording, trimming, resumable upload and server-side preparation |
| [05 3D motion](05-3d-motion.md) | Key poses and motion segments on the DGX, jobs, the model runtime, playback |
| [06 Learn: bundled lessons](06-learn-lessons.md) | The Learn tab, the lesson player, the lesson format and how bundled lessons are made |
| [07 Lessons from your own video](07-lessons-from-your-video.md) | Create lesson: consent, the lesson job, grounded AI authoring, download |
| [08 Community: training circles](08-community-circles.md) | Private circles, sharing, timestamped feedback and access rules |
| [09 Overlay comparison](09-overlay.md) | Comparing two motions overlaid or side by side |
| [10 Deletion and retention](10-deletion-and-retention.md) | Deleting a video or all account data, grace periods and verified cleanup |
| [11 Operations](11-operations.md) | Services on the DGX, recovery, configuration, deploy and rollback |

## Conventions

- "Prepared video" means the server's canonical copy of an upload: H.264, 30 fps, at most
  1280×720, no audio. Every 3D result and lesson uses it, so all timestamps share one clock.
- Firestore field names are shown as stored (camelCase). Common fields `schemaVersion`,
  `createdAt` and `updatedAt` exist on every record and are omitted from diagrams.
- No feature scores or grades a player. Comparisons and lessons describe movement only.

## Keeping these pages current

Edit the Markdown here and the `.mmd` sources in `diagrams/`, then re-render (see the diagrams
README). The client Google Docs are generated from these pages, so update them here first.
