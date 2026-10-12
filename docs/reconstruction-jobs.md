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
The `body-keyframes-v1` profile persists up to 24 distinct canonical frame indices
and normalized subject boxes. Enqueue verifies bounds and the source/request hash.
The private adapter can repeat this immutable selection after a process restart.

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

### Owned model bridge

`body_keyframes_adapter.py` is a credential-free CPU client of the already loaded
SAM 3D Body estimator. It decodes exact frames from canonical playback, sends JPEGs
and subject boxes over `~/.local/share/footylevels/model-bridge/inference.sock`, and
writes one GLB per successful frame plus `keyframes.json`. The manifest pins source,
selection, attempt, engine hashes, topology, timestamps, boxes and camera metadata.
Missing frames produce an explicit partial outcome; zero bodies fail the attempt.
No scoring or correctness classification is produced.

The private bridge uses the existing single-thread executor, also shared with
compatibility body/motion work. It loads no second estimator. Its directory is
0700, socket 0600, and a file lock excludes competing owned bridges. Request identity
includes job, attempt, frame, selection/input/image hashes and engine provenance.
Completed responses replay from immutable disk cache after a lost acknowledgement
or runtime restart. Configuration/attempt changes cannot reuse a stale identity.
The cache retains at most 100 entries and removes expired entries on new work after
24 hours; cached outputs have no publication rights and remain separate from user
cloud assets. Connections, JPEG dimensions/size and response packets are bounded.

Disconnect cancels queued inference. In-flight inference finishes only into private
cache, while the cloud parent fences publication. It cannot overlap new work because
the same serial executor owns both. An inference watchdog exits our owned model
process after 180 seconds of hung execution. The owned Development systemd service
now restarts that process under the same GPU preflight; see
`infra/dgx/services.md`. Startup can wait for GPU capacity, and no uptime guarantee
is implied. A controlled process-exit test is separate from inducing a CUDA hang.
Cloud attempt/retry deadlines still provide a durable failure when the model stays
unavailable. Unsupported profiles fail within the bounded retry policy.

The cloud launcher enables the serial dispatcher only when explicitly given
`--reconstruction-adapter /absolute/release/infra/dgx/body_keyframes_adapter.py`.
It supervises both workers and reaps them on shutdown. This adds no public model
route or ngrok traffic policy. Whole-motion adapters and authenticated request/UI
integration are documented in `reconstruction-api.md`; the FP-39 deployment uses
`reconstruction_adapter.py` to dispatch keyframes or bounded body-motion segments.
Its owned model launch explicitly enables `FOOTYLEVELS_MOTION_BRIDGE=1`.
Actual motion generation, private storage, recovery and cancellation passed the
Development probe with model runtime `4017f11` and the modular adapter. Final API
checkpoint `e43109d` adds metadata conflict retries and isolated integration tests.
Current evidence is in `infra/dgx/reconstruction-api-evidence.json`; the counts and
revisions below describe the earlier FP-53 checkpoint. Mobile/public-origin
integration and physical-device verification remain FP-19/FP-40 work.

The launcher passes explicit absolute socket/FFmpeg paths via `--adapter-arg`.
This matters inside the isolated DNS user namespace: the account appears as uid 0
and passwd reports `/root`, while the adapter's HOME intentionally points to its
temporary workspace. Neither is a reliable path to the real DGX tools. Standalone
adapter invocations require `--socket` and `--ffmpeg` as well as the workspace.
No cloud credentials are passed in arguments or environment to that child.

## Verification and remaining integration

Seven real Development Firestore reconstruction tests cover concurrent claims, heartbeat/stage
fences, crash/expired-lease recovery, stale publication, exact completion replay,
retry delays/exhaustion, attempt/total deadlines, owner-scoped cancellation,
tombstones/physical deletion, canonical input and isolated operator pause/resume.
Each test removes its random owner's metadata. Quota/control fixtures are isolated
from real application records. The serial runner test combines real Firestore with
a real adapter child and a fixture storage implementation; it does not upload real
media. Eight local child-process tests verify credential isolation, wrong provenance,
hash/path/symlink rejection, cancellation/timeout cleanup and network retry handling.
These tests generate no GPU work or cloud media.

Development's bounded runner drives the existing warm model through the private
keyframe adapter. The actual GPU/cloud probe passed on `8abf8d9` with model runtime
`429deeb`: after an owned API/worker restart the dispatcher reclaimed an abandoned
expired claim as attempt 2, generated two GLBs and a manifest, and published their
verified bytes privately in Supabase with immutable Firestore provenance. Both GLBs
independently decoded to 18,439 finite vertices and 36,874 triangular faces. Exact
adapter replay added no inference; duplicate claims were ignored, old lease rights
were rejected and a lost-completion acknowledgement replayed the same result.

The probe then cancelled a real in-flight model request. The CPU adapter stopped
and was reaped, staging disappeared, the model quiesced and no cancelled result
published. Exact fixture cleanup verified absence of the source, seven prepared
outputs, three result files, private model-cache files and metadata/quota records.
The earlier path-failure fixture was also cancelled and cleaned before retrying.
The original source and its revision remained unchanged throughout generation.

Final checks: 216 local backend tests passed (18 explicit opt-in/platform skips),
16 real Development Firestore tests passed, 69 current DGX cloud-service tests and
30 GPU-environment runtime/IPC tests passed. The latter excludes three CPU adapter
cases requiring the cloud SDK; those pass locally and the actual cloud-environment
adapter is exercised by the real GPU/storage probe. Model tests use fake inference
except the separate opted-in live probe. Runtime and cloud environments remain
separate; no cloud SDK/credentials or second model were added to the GPU service.

FP-39 supplies authenticated frame selection/request APIs and the whole-motion
adapter; FP-19/40 integrate durable progress, private assets and iOS playback.
The public compatibility model API and authenticated local API remain separate.
This is server-path verification, not an authenticated iOS-to-new-3D verification.

The deployment check exposed an existing shutdown defect: Uvicorn re-raised
SIGTERM before the launcher could reap its media child, leaving the old worker's
lock held during an upgrade. `78a3b37` installs a graceful stop handler and binds
the cloud worker's lifetime to its supervisor on Linux. Regression tests cover
graceful shutdown and a forced parent crash. A live Development restart confirmed
the API and worker both stopped and restarted with one worker and no orphan.

Initial foundation checkpoint (`470b2d2`): 200 local backend tests passed (16 explicitly skipped),
15 real Firestore tests passed, and 66 candidate tests passed on the DGX, including
both shutdown scenarios and actual FFmpeg decoding. Development then ran `78a3b37`
with GPU dispatch disabled, and the warm compatibility runtime was `c865730`.

After the shutdown fix, the running deployment automatically prepared a real
private synthetic clip through Firestore/Supabase: six previews and all seven
output hashes passed, source revision stayed unchanged, and exact cleanup verified
the absence of the source and seven prepared objects. The first probe during the
failed upgrade was also cleaned up. No fixture recovery file remained.
The API stayed healthy with one media worker and zero staging directories at
0, 60 and 130 seconds after cleanup, including the subsequent polling cycle.
Machine-readable evidence is in `infra/dgx/reconstruction-evidence.json`.
