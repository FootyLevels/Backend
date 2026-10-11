# Durable reconstruction jobs (FP-53)

Firestore is the durable queue. No paid broker, cloud GPU or additional database
is required. Development and Demo use separate scoped credentials and controls.

## Implemented lifecycle

`queued → running → succeeded / partial / failed / cancelled`, with `retrying`
between transient failures. A project can own only one active reconstruction.
Semantic request identity includes owner, project, source revision, request hash,
job kind and profile version. Repeating a request returns its durable job.

The job pins the canonical prepared video when present; the original recording
remains separately retained. Trusted legacy ready projects can use their source.
Selected frame/bounding-box request data and the model adapter belong to FP-39.

Each claim transaction checks project ownership, revision, active job and source.
It creates a new random lease token and attempt number. A lease lasts five minutes;
the runner must heartbeat every 30–60 seconds, including between model stages.
Stage checkpoints cannot move backwards within an attempt. Heartbeats stop at a
one-hour attempt deadline. Jobs have a 24-hour total deadline including queue time.
Expired attempts can be reclaimed after restart; three attempts is the default.
Transient failures wait two and then four minutes. Exhaustion clears the project's
active operation and leaves a durable failed outcome instead of a spinner.

Cancellation and deletion invalidate the worker's claim. Publication rechecks the
live lease, attempt, current source/revision, deadline and project ownership in the
same transaction that creates the immutable result and marks the job complete.
Results use the job ID, and asset paths include the attempt number. An exact replay
after a lost commit acknowledgement returns the winner. Different output is rejected.
No cancelled, deleting, expired or obsolete job can publish.

`POST /api/jobs/{id}/cancel` requires a verified active Firebase user and ownership.
Cancellation is idempotent; completed outcomes stay complete. Media preparation is
cancelled by deleting the project, rather than leaving a preparing project stalled.
Client status/cancel responses omit lease tokens and worker identifiers.

## Private operator controls

Run on the DGX through the existing isolated DNS wrapper and cloud virtualenv:

```sh
~/.local/share/footylevels/fp50-dns/with_network_dns.sh \
  ~/.local/share/footylevels/cloud-api/venv/bin/python \
  ~/.local/share/footylevels/cloud-api/current/infra/dgx/reconstruction_control.py dev inspect
```

Replace `inspect` with `pause` or `resume` to persist whether new requests are
accepted. Pausing drains existing jobs, and exact retries can still retrieve their
existing job. This is a server-only control, not an unauthenticated admin API.
Inspection pages active jobs using existing single-field indexes, sorts them by age,
and prints the oldest 20 in the scanned set. The default cap is 1,000 active records
(including media jobs); `truncated` explicitly indicates an incomplete scan.
No signed URLs, credentials, worker tokens or personal profile fields are printed.

## Bounded execution boundary

`infra/dgx/run_reconstruction_worker.py` claims one job at a time and holds a
machine-wide user lock across Development/Demo dispatchers. It scans ten active
records per pass and waits two minutes between passes. Each adapter runs in its
own process group with a private temporary workspace and a scrubbed environment.
The parent owns cloud credentials, source verification, lease heartbeats, quota
reservations, verified uploads and fenced publication.

The explicit `--adapter` Python file receives a workspace containing `input.mp4`
and `job.json`, excluding worker/lease capabilities. It writes local files under
`artifacts/` and a bounded `result.json` matching `ReconstructionRecord`. Artifact
object keys must match this job/attempt; local regular files must match every size
and SHA-256 reference. Symlinks, wrong owners, wrong attempts and duplicate paths
are rejected before quota reservation or upload. Partial results must account for
missing frames. The adapter must use the existing runtime and must not load weights
into this CPU client. Its request/result contract is documented by the worker tests.

The parent renews the lease every 30 seconds. Lease loss, cancellation or shutdown
stops and reaps the adapter before removing its workspace. An adapter has a 3,500
second wall limit, a one-hour overall process watchdog and Linux memory/CPU/file
limits. The Linux adapter dies if its parent dies. Restart reclaims durable expired
leases. Old staging directories are removed only after 24 hours under the private
worker root. Unpublished reservations are reconciled after the same writer grace;
immutable winning artifacts stay reserved, including older retained results.

Remote model execution needs attempt-aware cancellation/deduplication in the
FP-39 adapter as well: stopping the CPU client alone cannot cancel work already
submitted to the warm model service. Therefore this runner is not enabled against
the compatibility public API yet.

## Verification and remaining integration

Six real Development Firestore reconstruction tests cover concurrent claims, heartbeat/stage
fences, crash/expired-lease recovery, stale publication, exact completion replay,
retry delays/exhaustion, attempt/total deadlines, owner-scoped cancellation,
tombstones/physical deletion, canonical input and isolated operator pause/resume.
Each test removes its random owner's metadata. Quota/control fixtures are isolated
from real application records. The serial runner test combines real Firestore with
a real adapter child and a fixture storage implementation; it does not upload real
media. Eight local child-process tests verify credential isolation, wrong provenance,
hash/path/symlink rejection, cancellation/timeout cleanup and network retry handling.
These tests generate no GPU work or cloud media.

The lifecycle and bounded runner are implemented but are not yet driving the existing model runtime.
FP-53 remains In Progress until the bounded runner is connected and verified on the
DGX. FP-39 supplies the selected-frame/motion adapter and verified private artifact
publication; it must reuse the single warm runtime rather than load another model.
The public compatibility model API and authenticated local API remain separate.
This checkpoint is not an end-to-end iOS-to-3D verification.

The deployment check exposed an existing shutdown defect: Uvicorn re-raised
SIGTERM before the launcher could reap its media child, leaving the old worker's
lock held during an upgrade. `78a3b37` installs a graceful stop handler and binds
the cloud worker's lifetime to its supervisor on Linux. Regression tests cover
graceful shutdown and a forced parent crash. A live Development restart confirmed
the API and worker both stopped and restarted with one worker and no orphan.

Verified checkpoint: 200 local backend tests passed (16 explicitly skipped),
15 real Firestore tests passed, and 66 candidate tests passed on the DGX, including
both shutdown scenarios and actual FFmpeg decoding. Development runs `78a3b37`;
GPU dispatch is disabled pending the adapter integration. The warm compatibility
model runtime remains `c865730`.

After the shutdown fix, the running deployment automatically prepared a real
private synthetic clip through Firestore/Supabase: six previews and all seven
output hashes passed, source revision stayed unchanged, and exact cleanup verified
the absence of the source and seven prepared objects. The first probe during the
failed upgrade was also cleaned up. No fixture recovery file remained.
The API stayed healthy with one media worker and zero staging directories at
0, 60 and 130 seconds after cleanup, including the subsequent polling cycle.
Machine-readable evidence is in `infra/dgx/reconstruction-evidence.json`.
