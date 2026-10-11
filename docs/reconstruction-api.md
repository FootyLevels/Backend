# Reconstruction requests (FP-39)

The authenticated application API accepts keyframe requests and uses the existing
durable reconstruction worker. Whole-motion integration is still in progress.
The legacy model API on port 8010 is a separate compatibility service; the new
authenticated API currently runs on loopback port 8011.

## Select and submit keyframes

1. Sign in with Firebase and bootstrap the profile using `/api/me/bootstrap`.
2. Upload/finalize a permitted clip using the existing upload-session endpoints.
3. Poll `/api/projects/{id}` until `state` is `ready`. Use `preparedMedia.video`
   for selection, with its canonical 30 fps frame indices and normalized boxes.
   Obtain temporary private playback/preview URLs from
   `POST /api/projects/{id}/prepared-media-assets`.
4. Submit `POST /api/projects/{id}/reconstructions/keyframes` with the current
   project `inputRevision`. Every request requires `Authorization: Bearer <Firebase ID token>`.

```json
{
  "inputRevision": 2,
  "keyframes": [
    {"frameIndex": 0, "bbox": [0.1, 0.1, 0.9, 0.9]},
    {"frameIndex": 15, "bbox": [0.1, 0.1, 0.9, 0.9]}
  ]
}
```

Select 1–24 distinct frames inside the prepared video. Boxes use normalized
`[left, top, right, bottom]`, each in `[0, 1]`, with width/height at least 0.03.
Frame time is exactly `frameIndex / 30`, relative to prepared playback. The server
derives owner, source hash, profile and job identity; clients cannot set them.
Selection order is part of the immutable request identity.

The response is **202 Accepted**, containing the durable job and a `Location`
header pointing to `/api/jobs/{id}`. It does not imply generation has completed.
Repeating the same request returns the same job, including an existing terminal
outcome; it never silently resets cancelled/failed work. A changed selection is a
new request and is accepted after the project's active job finishes or is cancelled.
Concurrent duplicate submissions converge on one Firestore job. Pausing intake
rejects new work but allows exact retries to retrieve existing jobs.

## Follow progress and open results

* `GET /api/jobs/{id}`: durable status/stage; poll while queued/running/retrying.
* `POST /api/jobs/{id}/cancel`: owner-scoped cancellation, idempotent for terminal jobs.
* `GET /api/reconstructions/{id}`: immutable metadata once the job links a result.
* `POST /api/reconstructions/{id}/assets`: five-minute private URLs for only the
  result's allowlisted files. Resolve manifest relative names against this map.

Keyframe output contains `keyframes.json` and one GLB per successful selection.
The manifest declares engine/checkpoint/config hashes, topology, encoding,
coordinates, estimated metric units, canonical timestamps and missing frames.
Missing frames produce `partial`; no successful frames fails the attempt.
No scoring or skill-correctness classification is performed.

All client job responses omit worker identifiers and publication leases. Foreign,
deleted and inaccessible resources return **404**. Missing/invalid sign-in returns
**401**, restricted/unbootstrapped accounts **403**, malformed/out-of-video selections
**422**, stale/unprepared input or competing active work **409**, and paused intake
or unavailable metadata/auth services **503**. Responses are private and not cached.
The transaction rechecks revision/source/ownership after the API reads the project.

## Verification scope

HTTP tests exercise authorization, account restrictions, source derivation, input
bounds and private responses. A real Development Firestore HTTP test uses synthetic
verified identities to check concurrent duplicate requests, competing work, ownership,
lease redaction, cancellation, paused intake and deletion fences; its exact random
fixtures are removed. This does not verify real Firebase sign-in or iOS playback.
Actual GPU/private-storage generation and recovery are separately verified in
`reconstruction-jobs.md`. FP-39 remains open for motion artifacts and integration;
FP-19/40 still supply the durable mobile flow and device verification.
