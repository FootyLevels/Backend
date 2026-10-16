# Private training circles prototype (FP-68 / FP-69)

The first community feature is a private circle of friends, teammates or a coach.
Players deliberately share an existing, prepared recording, receive feedback at a
video moment, and can link their next attempt. A player may also deliberately
attach one already-generated 3D body-motion result. Creating, uploading or
generating a recording never shares it automatically. No public feed, direct
messages, cross-player 3D comparison or scoring is added.

## API contract

All routes require a verified Firebase identity and an active account, using the
same dependency as private projects. Circle IDs never authorize access by
themselves. Non-members and unrelated IDs receive the same sanitized 404. Every
response is `Cache-Control: no-store` and `Vary: Authorization`.

| Method | Path | Request / response |
| --- | --- | --- |
| GET | `/api/circles` | `{items: [Circle]}` |
| POST | `/api/circles` | `{name, description?}` → `Circle` (201) |
| GET | `/api/circles/{id}` | `Circle`, with its current active roster |
| POST | `/api/circles/{id}/invite` | Owner rotates code → `{code, expiresAt}` |
| POST | `/api/circles/join` | `{code}` → `Circle`; repeated joins are idempotent |
| DELETE | `/api/circles/{id}/members/{uid}` | Non-owner self-leave or owner removal → `{state: "removed"}` |
| DELETE | `/api/circles/{id}` | Owner deletes circle → `{state: "deleted"}` |
| GET | `/api/circles/{id}/posts?limit=20&cursor=…` | `{items: [Post], nextCursor}` |
| POST | `/api/circles/{id}/posts` | `{projectId, skill, note?, previousPostId?, reconstructionId?}` → `Post` (201) |
| GET | `/api/circles/{id}/posts/{postId}` | `Post` |
| DELETE | `/api/circles/{id}/posts/{postId}` | Author or circle owner removes share |
| POST | `/api/circles/{id}/posts/{postId}/assets` | `PrivateReadGrant`, canonical video only |
| POST | `/api/circles/{id}/posts/{postId}/motion-assets` | `PrivateReadGrant`, explicitly attached body-motion files only |
| GET | `/api/circles/{id}/posts/{postId}/comments?limit=30&cursor=…` | `{items: [Comment], nextCursor}` |
| POST | `/api/circles/{id}/posts/{postId}/comments` | `{text, timestamp?}` → `Comment` (201) |
| DELETE | `/api/circles/{id}/posts/{postId}/comments/{commentId}` | Author or circle owner removes comment |

Sharing and commenting require a 16–80 character URL-safe `Idempotency-Key`.
Identity, author name, original title, prepared media, input revision and creation
time come from server records. Unexpected body fields are rejected. Text is
trimmed; limits are name 80, description 240, skill 60, note 500 and comment 500
characters. Names, skills and comments cannot be blank. Timestamps must be finite
numbers in `[0, duration)` measured in seconds of prepared playback.

`Circle` contains `id`, `name`, `description`, `ownerId`, `createdAt`, `memberCount`
and `members: [{uid, displayName, joinedAt}]`. It never contains invite hashes or
membership generations. Restricted/deleted profiles are omitted from the public
roster while durable cleanup catches up.

`Post` contains `id`, `circleId`, `authorId`, `authorName`, `projectId`,
`projectTitle`, `skill`, `note`, `createdAt`, `previousPostId` (nullable), and
`video: {duration, width, height, fps, frameCount, asset: AssetRef}`. The asset is
metadata; it contains no URL. A previous attempt must be visible in the same
circle and belong to the same author. `motion` is nullable. When explicitly
selected, it contains `{reconstruction, job}`: the immutable `ReconstructionRecord`
JSON and only the mobile `CloudJob` playback fields (`id`, `ownerId`, `projectId`,
`inputRevision`, `kind`, `status`, `stage`, `attemptCount`, `inputHash`,
`profileVersion`, `sourceAsset`, `motionSegment`, `keyframes`, `reconstructionId`,
`errorCode`). Worker IDs, leases, scheduling details and an internal full-job
fingerprint are never returned or copied into the public job snapshot.

Only a completed `body-motion-30fps-v1` result may be selected: successful/ready
or partial/partial, a 30–300 frame segment inside the current prepared video,
the exact current owner/project/revision/source, and matching job ID, attempt,
source/segment hash and supported playback contract. Selected keyframe-pose
results and unfinished jobs cannot be shared. The complete result and the full
job fingerprint are pinned when sharing. Every read rechecks both actual
records. Changing or deleting either hides the attached post and its feedback;
there is no silent fallback to different motion. A newer generated result does
not replace the selection. Existing video-only shares never gain motion unless
the author creates a separate share with an explicit selection. Omitting
`reconstructionId` or sending null preserves legacy video-only idempotency hashes.

`Comment` contains `id`, `authorId`, `authorName`, `text`, `timestamp` (nullable)
and `createdAt`. List order is newest first, with document ID as a stable tie
breaker. Pages accept limits 1–50. A scan inspects at most 100 rows, returning a
continuation even when all 100 rows are hidden. Consumers must follow
`nextCursor` even after an empty page; no hidden rows are silently dropped.
Cursors are bound to the caller and circle/post scope. Deleted rows do not need
to remain present for a cursor to work.

## Privacy and revocation

Invites use 32 random bytes (43 URL-safe characters), expire after seven days and
are stored only under SHA-256 hashes. Rotation deletes the old code. Owner
removal of a member atomically invalidates the current invitation; self-leaving
permits later rejoining. The owner cannot leave; they must delete the circle.

All circle reads and mutations check the active caller, circle owner profile and
current membership in a Firestore transaction. Sharing and comments reread the
same membership inside their write transaction. Shares pin the author's current
membership generation and the source project's input revision and canonical
private video reference. A deleted/restricted author, removed membership,
replaced source, non-ready/deleted project, removed post, or deleted circle makes
the post and feedback inaccessible. Rejoining never revives posts or comments
from an earlier membership. This preserves deliberate consent after a leave.

The video grant signs only the pinned prepared `video.mp4` inside that author's
exact project/media/job/attempt prefix. The optional motion grant signs only the
selected result's exact reconstruction/job/attempt prefix: `sequence.json`,
`faces.bin`, contiguous `body-000000.bin.gz` one-second chunks (six-digit start
frame indices), optional in-segment `preview-####.jpg`, and trimmed `video.mp4`.
The entire set, uniqueness and content types are checked before any signing;
arbitrary files, nested paths, mixed attempts and incomplete chunk coverage are
rejected. Grant keys are the exact relative filenames. Gzip chunks use
`application/gzip`, topology uses `application/octet-stream`, and manifest,
previews and video use `application/json`, `image/jpeg` and `video/mp4`.
Original recordings and unrelated 3D outputs remain private. Existing owner-only
reconstruction endpoints remain owner-only. No new bucket, storage copy, public
object or stored signed URL is created. The server mints at most 60 seconds of
bearer access, then rechecks all consent/source fences and the selected motion
records after signing before returning it.
Already-issued bearer reads cannot be revoked immediately; they can remain usable
for at most 60 seconds. A recipient can retain bytes they already received.

Account/project deletion immediately revokes access through existing durable
profile/project tombstones. The existing deletion worker also erases scoped
community metadata in bounded batches before completing cleanup. Circle deletion
erases its roster/text and creates a durable `circleCleanup` marker; worker
reconciliation removes its posts/comments without touching anyone's original
projects. Removing a share/comment erases its text and playback metadata while
retaining a minimal idempotency tombstone. Removing a motion share erases both
the nested result/job snapshot and its internal fingerprint. Membership removal immediately hides
that member's posts/comments; their old metadata is retained until circle,
project or account cleanup. Existing private-owner project routes remain private.

## Storage, limits and deployment

Server-only Firestore collections are `circles`, `circleMemberships`,
`circleInvites`, `circlePosts`, `circleComments`, and `circleCleanup`. All access
still goes through the authenticated API; deny-all client rules are unchanged.
Queries and cleanup are scoped by ledger, circle ID, post ID, project ID or
author ID. There is no unfiltered user-content scan.

This prototype permits 20 circles per player, 20 members per circle, 200 lifetime
posts per circle and 200 lifetime comments per post. Counters are transactional;
idempotent replay succeeds before the cap is checked. Deleting a post/comment
does not reopen its lifetime quota or erase its replay tombstone. Limits bound
free-tier metadata use without introducing a paid service. They are explicit
prototype limits, not a complete anti-abuse or moderation system.

`create_app(..., circles=FirestoreCircles(client))` enables routes; omitting the
optional dependency preserves existing API test doubles. `app_factory` injects
the real repository. Runtime deployment remains separate from source changes.
The scoped feed indexes are declared in `infra/spark/record_indexes.tf`:

- `circlePosts`: `circleId ASC`, `createdAt DESC`, `__name__ ASC`.
- `circleComments`: `postId ASC`, `createdAt DESC`, `__name__ ASC`.
- Development integration tests use the same indexes on `integration-` names.

Default verification:

```sh
.venv/bin/python -m pytest tests/test_circles.py tests/test_cloud_api.py tests/test_deletion_api.py
```

Opt-in Firestore tests use the existing scoped Development service account and
isolated collection wrapper. They check durable API behavior, parallel join/share
idempotency, membership generations, owner fences, and deletion-worker cleanup:

```sh
FOOTYLEVELS_FIRESTORE_TEST_PROJECT=footylevels-dev-20261009 \
GOOGLE_APPLICATION_CREDENTIALS=/path/to/scoped-development-api-key.json \
.venv/bin/python -m pytest tests/test_firestore_circles.py
```

These metadata tests do not prove a deployed frontend or actual storage bytes.
The separate cross-stack proof uses a real small prepared MP4 and real private
storage. iPhone acceptance remains an additional product check.


## Verified local checkpoint — 16 October 2026

The full Backend suite passed **339 checks**, with 29 explicit cloud/platform skips.
The three opted-in real isolated Firestore circle tests passed separately. A
separate committed TestClient proof on DGX passed with three real temporary
Firebase accounts and actual private Supabase bytes: invitation/join, sharing,
outsider and foreign-project denial, timestamped feedback, retry links,
pagination/idempotency, removal/rejoin generations, unsharing and deletion.
It seeded synthetic prepared media and ran no new GPU job or API listener.
All three fixture objects, exact scoped metadata and three temporary login
accounts were removed, with absence verified. See
[aggregate evidence](training-circles-evidence.json).

The proof helpers are `scripts/verify_circles_mac.py` and
`scripts/verify_circles_dgx.py`. The Mac helper requires
`FOOTYLEVELS_COMMUNITY_TEST_PROJECT=footylevels-dev-20261009`, a clean committed
checkout, the existing trusted DGX SSH route and local operator ADC for disposable
identity cleanup. It transfers only allowlisted tracked source, passes temporary
tokens on SSH stdin, starts no listener and leaves deployed release links unchanged.
The server's existing private runtime config supplies the scoped application
identity and storage key; these never enter the source archive or output.

Only the two integration-prefixed indexes were created for this proof. Their
verified resource names are in the aggregate evidence. Before applying this
Terraform branch, import them into
`google_firestore_index.circle_feeds["integration_posts"]` and
`google_firestore_index.circle_feeds["integration_comments"]` respectively.
Default feature indexes and the updated API runtime are not deployed. Native iOS
component/UI checks and physical-device acceptance are separate evidence.
