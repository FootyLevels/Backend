# FP-54: DGX media preparation and maintenance

Upload finalization verifies source bytes and atomically sets the project to
`preparing` with a durable `prepareMedia` job. It does not decode inside the HTTP
request. The selected DGX deployment runs one serial CPU worker alongside its
API, using the same environment's scoped Firestore identity and private Supabase
storage. The API launcher stops if that worker exits. No GPU/model imports,
sudo, billing, Cloud Run or paid queue are needed.

## Processing and reuse

The processor reuses the semester runtime's FFprobe → H.264/CFR normalization →
timestamped JPEG approach. It is separated from the legacy OpenCV/GPU runtime so
the authenticated service can use its small cloud environment. New clips must
have one H.264/HEVC/MPEG-4 video stream in an MP4/QuickTime container, be 1–60
seconds, at most 50 MiB, at most 4K and at most 120 fps. The server independently
probes and decodes them rather than trusting client type/duration claims.

Playback is a video-only H.264 MP4, with rotation baked in, square pixels, 30 fps
and a 1280×720 bounding box (no upscaling). Six temporal preview JPEGs provide
coverage for later user frame selection; this is not skill recognition or AQA.
Each preview stores its canonical frame index and exact `frameIndex / 30`
timestamp. FP-39 must use this canonical playback/timebase for new reconstruction
requests, while retaining the immutable uploaded source for provenance.

Decoders run in disposable process groups with two codec/filter threads, no
cloud credentials, file/pipe protocols only, an overall 240-second processing
deadline and a per-command wall limit. On Linux they also have 2 GiB address
space, 150 CPU seconds, 50 MiB output-file, 64-descriptor and zero-core limits.
Timeout/cancellation kills and reaps the group. Parent-death signaling prevents
an orphan decoder after a killed supervisor. Staging is private and removed on
success/failure; startup removes abandoned staging older than 24 hours.
The worker also has a hard ten-minute per-job watchdog, including cloud calls;
its 15-minute durable lease allows a safe reclaim after deployment restart.

## Durable records and publication

`jobs/{id}` adds `kind: prepareMedia`, a pinned source/revision/checksum/profile,
stage, attempt count, lease and sanitized error code. The semantic job ID is
derived from owner, project, revision, source checksum and profile. Claims use
Firestore transactions and 15-minute leases; only one claim wins. A restart can
reclaim an expired lease, up to three attempts. Old leases, deleted projects,
changed sources and conflicting jobs cannot reserve or publish outputs.

Generated objects use immutable attempt paths:

`private-media/users/{uid}/projects/{projectId}/media/{jobId}/{attempt}/video.mp4`

`private-media/users/{uid}/projects/{projectId}/media/{jobId}/{attempt}/preview-00.jpg`

All seven outputs are budgeted together against existing owner/global free-plan
quotas before storage writes. The worker verifies stored checksums before one
transaction sets `state: ready`, clears the active job and publishes
`preparedMedia` (video reference, dimensions, fps, duration, frame count, original
rotation and timestamped preview references). The uploaded `video` reference and
input revision remain unchanged. Malformed input becomes `rejected`; temporary
storage/tool/quota failures retry and become a sanitized terminal error after
the attempt bound. HTTP responses never expose leases or worker identifiers.

Owned clients can request `POST /api/projects/{id}/media-assets` for a five-minute
map containing `video` and `preview-00` through `preview-05`. The API validates the
entire owner/project/attempt map before signing and rereads the project afterward
to fence concurrent deletion/change. This route does not accept client paths.

## Maintenance and operating limits

The same CPU worker performs bounded reconciliation every 120 seconds. Its
rotating scans inspect at most ten preparing projects, ten old upload records
and ten old output reservations per cycle, rather than repeatedly reading every
job. Metadata cursors are an optimization; durable job/cleanup state remains in
Firestore. The polling interval can add up to two minutes before preparation.

Uncommitted uploads retain quota until their 72-hour capability quarantine has
expired. Only exact deletion plus verified absence permits an atomic quota
release. Failed-attempt media outputs have a 24-hour writer grace; reconciliation
fences expired workers, preserves all published assets and releases each exact
reservation once. Deleted committed recordings and published results still use
FP-18's deletion/retention workflow; this maintenance does not claim that ticket
is complete or erase a player's committed source automatically.

Run `infra/dgx/start_cloud_api.py dev --port 8011` through the existing private
DNS wrapper and cloud venv to start the selected deployment. It owns the CPU
child `run_media_worker.py dev`; its process lock prevents duplicate local workers
for that environment. A trusted one-shot maintenance check uses that worker's
`--once` option only when the deployment worker is stopped. Demo remains isolated.

## Verification

Actual FFmpeg fixtures cover rotation, audio removal, CFR previews, invalid or
unsupported media, duration/file limits, timeout reaping, credential scrubbing
and staging cleanup. These 13 tests passed both locally and on DGX with Linux
limits. Default backend checks passed; opt-in Firestore tests additionally cover
concurrent claim, restart/reclaim, stale publication, retries/rejection, deletion,
quota idempotency, orphan cleanup and capability quarantine.

The Development-only `scripts/verify_media_worker_dgx.py` probe passed with real
DGX/Firestore/Supabase: a three-second synthetic clip became ready with six
previews and all seven output hashes verified. It removed the exact source,
prepared objects, metadata and fixture quota, verifying absence. No GPU was used.
It tests server preparation, not authenticated iOS model generation. Aggregate
evidence belongs in `infra/dgx/media-evidence.json`; no credentials/capabilities.

The older FP-16 trusted early fixture-cleanup helper deliberately refuses owners
with job/output records. With this worker enabled, a future SDK probe must use
the normal quarantined cleanup or a separately verified quiescent fixture
procedure. Do not remove that guard or claim immediate deletion of running work.
