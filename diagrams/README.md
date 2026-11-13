# FootyLevels diagrams

Editable Mermaid sources for every FootyLevels architecture view, with rendered **SVG** (scales for
zooming and slides) and **PNG** (for documents) in [`rendered/`](rendered). The explanations live in
[`docs/architecture`](../docs/architecture/README.md); start with the overview there.

Views are numbered by feature area: 0x overview, 1x accounts, 2x videos, 3x 3D motion, 4x Learn,
5x lessons from your own video, 6x community, 7x overlay, 8x deletion, 9x operations.

| Area | View | Title | Source | Render |
| --- | --- | --- | --- | --- |
| Overview | 01 | FootyLevels system context | [Mermaid](01-system-context.mmd) | [SVG](rendered/01-system-context.svg) · [PNG](rendered/01-system-context.png) |
| Overview | 02 | FootyLevels deployment | [Mermaid](02-deployment.mmd) | [SVG](rendered/02-deployment.svg) · [PNG](rendered/02-deployment.png) |
| Overview | 03 | Backend components | [Mermaid](03-backend-components.mmd) | [SVG](rendered/03-backend-components.svg) · [PNG](rendered/03-backend-components.png) |
| Overview | 04 | iOS app architecture | [Mermaid](04-ios-app-architecture.mmd) | [SVG](rendered/04-ios-app-architecture.svg) · [PNG](rendered/04-ios-app-architecture.png) |
| Overview | 05 | Firestore data model overview | [Mermaid](05-data-model-overview.mmd) | [SVG](rendered/05-data-model-overview.svg) · [PNG](rendered/05-data-model-overview.png) |
| Overview | 06 | Core records - accounts, videos and 3D | [Mermaid](06-data-model-core.mmd) | [SVG](rendered/06-data-model-core.svg) · [PNG](rendered/06-data-model-core.png) |
| Overview | 07 | Where files are stored | [Mermaid](07-storage-layout.mmd) | [SVG](rendered/07-storage-layout.svg) · [PNG](rendered/07-storage-layout.png) |
| Overview | 08 | API map and access checks | [Mermaid](08-api-map.mmd) | [SVG](rendered/08-api-map.svg) · [PNG](rendered/08-api-map.png) |
| Overview | 09 | Security and trust boundaries | [Mermaid](09-security-model.mmd) | [SVG](rendered/09-security-model.svg) · [PNG](rendered/09-security-model.png) |
| Accounts and sign-in | 10 | Sign-in and account connection | [Mermaid](10-sign-in-sequence.mmd) | [SVG](rendered/10-sign-in-sequence.svg) · [PNG](rendered/10-sign-in-sequence.png) |
| Accounts and sign-in | 11 | Account states | [Mermaid](11-account-states.mmd) | [SVG](rendered/11-account-states.svg) · [PNG](rendered/11-account-states.png) |
| Videos | 20 | Upload and video preparation | [Mermaid](20-upload-sequence.mmd) | [SVG](rendered/20-upload-sequence.svg) · [PNG](rendered/20-upload-sequence.png) |
| Videos | 21 | Video project states | [Mermaid](21-video-states.mmd) | [SVG](rendered/21-video-states.svg) · [PNG](rendered/21-video-states.png) |
| 3D motion | 30 | 3D reconstruction and playback | [Mermaid](30-reconstruction-sequence.mmd) | [SVG](rendered/30-reconstruction-sequence.svg) · [PNG](rendered/30-reconstruction-sequence.png) |
| 3D motion | 31 | Background job lifecycle | [Mermaid](31-job-lifecycle.mmd) | [SVG](rendered/31-job-lifecycle.svg) · [PNG](rendered/31-job-lifecycle.png) |
| 3D motion | 32 | Model runtime internals | [Mermaid](32-model-runtime.mmd) | [SVG](rendered/32-model-runtime.svg) · [PNG](rendered/32-model-runtime.png) |
| Learn | 40 | Learn tab and lesson player | [Mermaid](40-learn-lesson-player.mmd) | [SVG](rendered/40-learn-lesson-player.svg) · [PNG](rendered/40-learn-lesson-player.png) |
| Learn | 41 | Lesson package schema version 1 | [Mermaid](41-lesson-package-schema.mmd) | [SVG](rendered/41-lesson-package-schema.svg) · [PNG](rendered/41-lesson-package-schema.png) |
| Learn | 42 | Making the bundled lessons offline | [Mermaid](42-offline-lesson-authoring.mmd) | [SVG](rendered/42-offline-lesson-authoring.svg) · [PNG](rendered/42-offline-lesson-authoring.png) |
| Lessons from your own video | 50 | Create a lesson from your own video | [Mermaid](50-lesson-from-video-sequence.mmd) | [SVG](rendered/50-lesson-from-video-sequence.svg) · [PNG](rendered/50-lesson-from-video-sequence.png) |
| Lessons from your own video | 51 | Grounded multi-pass lesson authoring | [Mermaid](51-grounded-authoring.mmd) | [SVG](rendered/51-grounded-authoring.svg) · [PNG](rendered/51-grounded-authoring.png) |
| Lessons from your own video | 52 | buildLesson job stages | [Mermaid](52-lesson-job-stages.mmd) | [SVG](rendered/52-lesson-job-stages.svg) · [PNG](rendered/52-lesson-job-stages.png) |
| Lessons from your own video | 53 | Lesson records | [Mermaid](53-lesson-data-model.mmd) | [SVG](rendered/53-lesson-data-model.svg) · [PNG](rendered/53-lesson-data-model.png) |
| Lessons from your own video | 54 | App states for Create lesson | [Mermaid](54-app-lesson-states.mmd) | [SVG](rendered/54-app-lesson-states.svg) · [PNG](rendered/54-app-lesson-states.png) |
| Community | 60 | Community circle records | [Mermaid](60-circles-data-model.mmd) | [SVG](rendered/60-circles-data-model.svg) · [PNG](rendered/60-circles-data-model.png) |
| Community | 61 | Circles - join, share and feedback | [Mermaid](61-circle-share-feedback-sequence.mmd) | [SVG](rendered/61-circle-share-feedback-sequence.svg) · [PNG](rendered/61-circle-share-feedback-sequence.png) |
| Community | 62 | Circle access and revocation rules | [Mermaid](62-circle-access-rules.mmd) | [SVG](rendered/62-circle-access-rules.svg) · [PNG](rendered/62-circle-access-rules.png) |
| Overlay | 70 | Overlay comparison | [Mermaid](70-overlay-comparison.mmd) | [SVG](rendered/70-overlay-comparison.svg) · [PNG](rendered/70-overlay-comparison.png) |
| Deletion | 80 | Deletion and retention | [Mermaid](80-deletion-flow.mmd) | [SVG](rendered/80-deletion-flow.svg) · [PNG](rendered/80-deletion-flow.png) |
| Operations | 90 | Operations and recovery | [Mermaid](90-operations.mmd) | [SVG](rendered/90-operations.svg) · [PNG](rendered/90-operations.png) |

## Editing and rendering

Edit a `.mmd` file, then render every view with the pinned Mermaid CLI. Rendering only reads the
checked-in text; it never contacts FootyLevels services or user data. Previews are replaced only
after every view renders.

```sh
cd diagrams
npm ci
npm run render
```

With an existing Chrome, skip the browser download and point Puppeteer at it. On Linux as root,
also turn off Chrome's sandbox:

```sh
npm ci --ignore-scripts
PUPPETEER_EXECUTABLE_PATH=/path/to/chrome PUPPETEER_NO_SANDBOX=1 npm run render
```

Styling lives in `theme.json`. Each source starts with `accTitle` and `accDescr`, which give the
rendered SVG an accessible title and description; keep them accurate when you change a view.
After an architecture change, update the view, the matching page in `docs/architecture`, and
re-render, in the same commit.
