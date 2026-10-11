# FP-51: Application records

The current implementation adds typed metadata and a Firestore repository beside the
existing local `SequenceStorage` and image/video reconstruction pipeline. It does not
switch existing API routes, import legacy data or restart the DGX service. Firebase
request authentication/DTO integration is FP-14; private file operations FP-17;
runner leases/recovery FP-53.

## Core schema

| Collection | Important fields |
| --- | --- |
| users | Firebase UID as ID, displayName, accountState |
| projects | ownerId, title/source, state, inputRevision, video AssetRef, activeJobId, latestReconstructionId, deletedAt |
| jobs | owner/project, kind, inputRevision/hash/profileVersion, pinned source, selected keyframes/boxes when applicable, status/stage, attempts and lease, result link |
| reconstructions | owner/project/job/revision, source, engine/checkpoint/config hashes, playback contract, bounded artifact refs, ready/partial and frame counts |
| references | title, immutable version, asset refs, permission/license reference, publicationState |
| comparisons | owner, exact left result, right result or versioned reference, overlay/sideBySide, manual time offset, state |

Records have schemaVersion 1 and timezone-aware timestamps; Firestore fields use
camelCase and Python fields snake_case. Unknown fields are rejected. Assets contain
only Supabase bucket/objectKey, SHA256, byteSize and contentType, with a 50 MB bound.
They never contain media bytes, signed URLs or keys. Immutable source/result paths
and checksums replace GCS-specific generation identifiers. The adapter must forbid
replacement of published bytes under those paths. Model arrays stay in file assets;
only bounded manifest references go in Firestore. No assessment/scoring fields.

## Repository behavior

The caller supplies UID from a verified token. Every owned read/write validates it;
missing, foreign and deleted records share a not-found outcome. Library queries
filter owner and visible state on the server, order by creation time with a document
ID tie breaker and use owner-bound cursors (maximum page size 50). Cursor values do
not confer authorization. The only composite index currently required is versioned
in `infra/spark/record_indexes.tf` and applied separately in dev/demo.

UID-keyed profile bootstrap is transactional and idempotent. Project title changes
do not change the input revision. Replacing the verified source increments it and
clears the latest-result pointer; active jobs must be cancelled/completed first.
The source must be inside the owner's project source path.

A deterministic job ID hashes owner/project/input revision/kind/input hash/profile.
Concurrent identical requests return the same durable job; different requests are
rejected while a job is active. Input hash is computed by the trusted service from
canonical frame/crop/segment settings, never taken as authorization from client JSON.
The runner supplies the fenced lease. The `body-keyframes-v1` profile persists
1–24 distinct `keyframes` containing `frameIndex` and normalized `[left, top, right,
bottom]` `bbox`. Frame indices refer to the pinned canonical 30 fps video. The
request hash includes its SHA256, profile and exact selection order/boxes, so
recovery repeats the same input. Ordinary media/legacy job documents omit this
new field for compatibility with older strict workers during a rolling upgrade.
Old services must be upgraded before consuming the new body profile.

Publication atomically creates the immutable result, completes the job and updates
the project pointer. It requires current source/revision, a nonexpired matching lease,
active job/attempt and matching owner/project/source/mode/attempt artifact paths.
Concurrent identical publication returns the same result; changed provenance cannot
overwrite it. Deletion tombstones immediately and cancels active work; it is not
recursive asset/document cleanup (FP-18). Comparison reads recheck both inputs and
reference publication/version, so deletion prevents later access.

Reference creation is an internal trusted-curator operation, never an ordinary
player API. Real coach/guardian/notes/challenge flows remain outside these core records.
No new client Firestore permissions were opened.

## Verification

Install the cloud extra with the existing environment:

```sh
uv pip install --python .venv/bin/python '.[cloud]'
.venv/bin/python -m pytest tests/test_records.py tests/test_firestore_records.py -q
```

Default tests skip real cloud writes. The opt-in integration suite accepts only the
Development project and its scoped API identity, and removes its random fixture
records even on assertion failure:

```sh
FOOTYLEVELS_FIRESTORE_TEST_PROJECT=footylevels-dev-20261009 \
GOOGLE_APPLICATION_CREDENTIALS="$PWD/infra/credentials/dev/google-service-account.json" \
  .venv/bin/python -m pytest tests/test_firestore_records.py -q
```

Tests exercise real concurrent transactions, owner isolation, cursor pagination,
revision/lease/tombstone rejection, immutable publication and reference comparison
access. They use synthetic metadata, generate no model/video and consume a small
amount of development Firestore quota. This is repository verification, not a real
app login/upload/generation/playback test.

SDK reference: [Firestore transactions](https://docs.cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.transaction.Transaction).

Verified checkpoint: all four live Development scenarios pass (including concurrent
bootstrap/enqueue/publication, tied-timestamp pagination, foreign-owner isolation,
profile/revision/lease/tombstone conflicts and immutable output). The selected local
record/storage/catalog/reconstruction suite passes 73 tests. Its pre-existing fake
model fixture was corrected to bypass filesystem checkpoint inspection, with a
separate 503 test preserving the production missing-checkpoint guard.

Both development/demo owned-project indexes are applied and READY. Development
CRUD/pagination tests and a read-only Demo library query pass using the matching
scoped identities.

## FP-16 additions

Projects now also allow `label` (optional practice text, up to 80 characters) and
`creationHash` (optional SHA-256 of the original title/source/label). Existing
records remain valid without them. The immutable creation hash supports durable
project creation retries even after renaming; it is not an assessment or a score.
The API supplies no client-controlled owner/document ID. See `authentication.md`
for the owner-scoped optional `Idempotency-Key` contract and verification.

`UploadRecord` plus the project's `activeUploadId` continue to track the private
source transfer and its quarantine/quota reservation. Uploaded and independently
verified sources advance inputRevision exactly once and enter `preparing`; ready
requires FP-54's trusted decoding and preparation, not an iOS declaration.
