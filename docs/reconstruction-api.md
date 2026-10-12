# Reconstruction requests (FP-39)

The authenticated application API accepts keyframe and bounded motion requests
through the existing durable reconstruction worker. Actual Development DGX motion
generation, private publication and cancellation have passed the opt-in probe;
local fake-inference checks remain separate evidence.
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

## Request body motion

Submit `POST /api/projects/{id}/reconstructions/motion` with the same verified
identity and current prepared input revision:

```json
{"inputRevision": 2, "segment": {"startFrame": 15, "endFrame": 45}}
```

The start is inclusive, end exclusive, and both use canonical 30 fps frame indices.
Choose 30–300 frames (1–10 seconds) inside the prepared video. This bounds model
time, temporary disk use and the free-storage output count. The server derives
`body-motion-30fps-v1` and an immutable source/segment hash. The response, polling,
cancellation, retry identity, ownership and publication fences match keyframes.

The private adapter trims the exact segment and sends its verified local file to
the owned model bridge. Motion and keyframes share one serial executor. The warm
body estimator is released before SAM 3 tracking and SAM 3D Body subprocesses run
sequentially on the same selected GPU, then restored afterward. No model process
receives cloud credentials. Each stage has a wall limit, a 2 GiB workspace limit
and a supervisor that stops its process group on cancellation or runtime parent loss.
The segment's private cache is keyed by job/input/model provenance, allowing raw
checkpoints or completed output to survive a retry without acquiring publication rights.
Raw pose checkpoints are committed atomically so a crash cannot expose a partial NPZ
as a valid cached pose. Completed raw/mask files are removed after a replayable result
is committed; at most two inactive motion entries are retained (24-hour expiry on new work).

This profile reuses the existing dominant-subject SAM tracking and compressed
Swift playback format. It produces `sequence.json`, shared `faces.bin`, independent
one-second gzip body chunks, six JPEG previews and a trimmed playback `video.mp4`.
The manifest keeps local segment times for the existing player and adds exact
original `sourceFrameIndex`/`sourceTimestamp` values plus `sourceTimeOffset`.
It pins engine, body/segmentation checkpoint hashes, topology, coordinates and
estimated metric units. Each frame declares validity and shot ID; smoothing never
crosses a shot cut or missing pose. Missing body frames are `partial`, and zero
valid bodies fails. The adapter verifies topology indices/hash, complete chunk
coverage, bounded decompression, timestamps and validity before publication.

The person is the largest foreground candidate at each shot seed, followed by a
persistent SAM track within that shot. This is declared selection behavior, not
verified identity in crowded footage. Ball processing is optional and not requested
by this body-only profile; no ground calibration or measured scale is claimed.
`optionalAssets` declares these omissions explicitly. No execution correctness,
score or AQA is generated.

## Verification scope

HTTP tests exercise authorization, account restrictions, source derivation, input
bounds and private responses. A real Development Firestore HTTP test uses synthetic
verified identities to check concurrent duplicate requests, competing work, ownership,
lease redaction, cancellation, paused intake and deletion fences; its exact random
fixtures are removed. This does not verify real Firebase sign-in or iOS playback.
Actual GPU/private-storage generation and recovery are separately verified in
`reconstruction-jobs.md` and `../infra/dgx/reconstruction-api-evidence.json`.
FP-19/40 still supply the durable mobile flow and device verification. Motion IPC
tests use fake inference with real decoding/packing, check cancellation and serial
execution, and verify stage-group cleanup after timeout, termination and parent loss.
The opt-in `scripts/verify_motion_dgx.py` is the separate actual-GPU/private-storage
probe, using a permitted source and synthetic verified HTTP identity; it never
creates a real Firebase account or claims physical-device end-to-end coverage.

The final API checkpoint `e43109d` runs on Development loopback 8011 with model
runtime `4017f11` on 8010. Verification passed 284 local backend tests (20 explicit
opt-in/platform skips), 18 isolated real Development Firestore tests and 119 DGX
cloud-service tests. The model release passed 46 runtime/IPC tests; five CPU-adapter
cases needing cloud dependencies were checked separately locally and by real probes.
The actual motion probe generated 30/30 poses, six previews and ten private output
files, then verified exact replay without additional inference, expired-claim
recovery and cancellation after real SAM seed inference. Its source, seven prepared
objects and ten result objects were removed, together with fixture metadata/quota,
staging and private motion cache. Selection identity in crowded clips and measured
scale/ground calibration remain unverified. These checks are server/component
verification; real Firebase sign-in and the full iPhone flow remain open.

After motion processing and the final API restart, the separate actual keyframe
regression generated two independently decoded GLBs and a manifest. Expired-claim
recovery used attempt 2; exact replay added no inference, and real in-flight
cancellation published no result. Its one source, seven prepared objects, three
result files, fixture metadata/quota, staging and cache were removed. This confirms
that restoring the warm estimator after motion preserves keyframe generation.
