# Reconstruction runtime — FP-52

## Current Development integration — 12 October 2026

The approved public endpoint now serves the **authenticated Development API** on
loopback **8010**. The owned model runtime listens privately on loopback **8013**;
its owner-only Unix inference bridge is unchanged. Cloud source `0148340` supervises
one CPU media worker and one durable reconstruction dispatcher; model source
`4017f11` supplies selected poses and whole-motion output through that bridge.
The body model is warm, and no second GPU model process was added. The CPU worker
also performs FP-18 durable deletion and retention maintenance. The launcher
source defaults now match these ports; the deployed releases use explicit ports.

See [public route and recovery](public-api.md) for launch commands and boundaries.
The owned [systemd services](services.md) now supervise API, model and ngrok;
use those units for restarts instead of launching a duplicate detached process.
The original sections below are historical observations, including previous ports
and releases. They must not be used as the current deployment map. Real private
simulator motion and pose proofs passed before this public migration; their
recorded loopback evidence remains valid for those separate runs. Physical iPhone,
interactive Google sign-in and the complete app acceptance remain open.

## Owned runtime takeover — 10 October 2026

Kunal authorized replacement after Digaant removed his listener. The replacement
runs under `bajajkun`, on loopback 8010, through the same approved ngrok endpoint.
The model files and environments were already under Kunal's `~/skill-lab`; the
former teammate checkout used symlinks to them. No reinstall/download, sudo or
other users' process eviction was needed.

Verified source checkpoint: Backend `c865730`, copied from model service commit
`01cff082cfff00ac323511cb81024f33717095b9`. Runtime code is now tracked in
`runtime/skill_lab` in Backend. The cloud API remains a separate integration
boundary; this restored compatibility service has no cloud credentials attached.

The deployed immutable source directory is
`~/.local/share/footylevels/runtime/releases/c865730`; `runtime/current` points to
it. Persistent runtime data lives separately in `runtime/data`, private logs in
`runtime/logs`, and `runtime/api.pid` records the owned launcher. The observed
server process is 3847072 (historical, always recheck before using it).

### Launch and recovery

Select the single assigned/authorized A100 UUID. The verified deployment uses
`GPU-bf91a965-c736-7317-95ff-469e3d182eeb`, the A100 at physical index 0. Other users'
vLLM jobs remain running; about 20.57 GiB CUDA headroom was available at preflight.
Startup requires at least 12 GiB, checks body/MHR checkpoint hashes and pinned body
source, verifies media tools and port availability, and preloads the body model
before reporting startup complete. GPU availability is transient; a UUID pin does
not reserve capacity or evict anyone else.

```sh
export FOOTYLEVELS_GPU_UUID=GPU-bf91a965-c736-7317-95ff-469e3d182eeb
export FOOTYLEVELS_RUNTIME_REVISION=c865730
release="$HOME/.local/share/footylevels/runtime/current"
bash "$release/infra/dgx/with_network_dns.sh"   bash "$release/infra/dgx/start_runtime.sh"
```

For a detached session redirect output into an owner-only runtime log and save
its specific launcher PID. Stop only a verified owned process/launch group whose
working directory matches this release, after draining active jobs. Do not kill
all Python/ngrok processes. The existing ngrok agent is independent and need not
restart when this API restarts. A reconnecting API reports interrupted in-memory
jobs for explicit retry; durable Firestore leases remain FP-53.

The launcher uses existing GPU/segmentation/object interpreters and media tools
without modifying their environments. Pytest 8.4.2 plus its dependencies were
installed into a separate `runtime/test-dependencies` directory only for the 19
passing tests. No server credentials or old `.env` were copied/loaded; the launcher
unsets the paid provider key and cloud secret environment variables.

Body weights stay loaded for keyframes. The serial motion runner releases the
parent's warm body model before segmentation/body subprocesses, then rewarms it.
Optional object/ground stages need 32 GiB free on that same selected GPU. With
less memory, body output survives and the project explicitly reports partial
completion. Segmentation and motion inference currently load once per job stage;
this is not a claim that all models stay permanently resident. The threshold is
a conservative admission check, not a guarantee that every larger request fits.

### Verification

The reused compatibility suite and two added low/high-memory runner cases passed
19 tests in the DGX GPU environment. Public health reports `owner=FootyLevels`,
`revision=c865730` and `bodyModelLoaded=true`. Final public smoke produced two valid 18,439-vertex / 36,874-face GLBs with 70
finite keypoints, with observed wall times 4.25 s and 2.17 s (including polling).
All 3 motion frames decoded; motion finished partial in 40.49 s with explicit
optional football/ground omission, ballReady=false and groundAligned=false. Body
weights rewarmed after motion. Idle GPU process allocation was 4,076 MiB, not a
peak measurement. The exact final test project was deleted (204/read 404), and
the original smoke fixture was likewise removed after the cleanup route was restored.
Fresh artifact evidence is in `owned-runtime-evidence.json`; test media/results remain outside Git. Use
`scripts/verify_owned_runtime.py` with a permitted bounded fixture to reproduce
public upload/keyframe/motion downloads, independent decoding, warmup recovery
and exact test-project deletion. The bundled semester example is used only as
an existing internal fixture; redistribution remains FP-21.

## Historical shared runtime validation

Validated 10 October 2026. Kunal authorized reuse of the team's existing Skill Lab
service on loopback port **8010**. It runs under Digaant's account. No additional
model server was installed or started, and no teammate process was restarted.
This is the compatibility runtime for local testing; consolidation into this
Backend is FP-39. Firestore/Supabase integration is not yet attached to it.

## Startup and dependency contract

The team checkout is `~/skill-lab` under the service owner's account. Its
`scripts/serve_gpu.sh` invokes `.venv-gpu/bin/python -m uvicorn backend.app:app`,
binds loopback, supplies the body repository/checkpoint and optionally loads the
owner's private `.env`. Its default port is 8000; this instance uses 8010.
Do not run the script against the existing listener or copy the owner's secrets.
The actual process environment and assigned CUDA device were not readable from
our account. An independent redeployment requires the owner's launch configuration
and GPU allocation, rather than assuming the script defaults match this process.

Read-only package inventory of the existing team environments:

| Environment | Python | torch | torchvision | NumPy | Other observed packages |
| --- | --- | --- | --- | --- | --- |
| `.venv-gpu` | 3.12.13 | 2.10.0+cu128 | 0.25.0+cu128 | 2.5.3 | FastAPI 0.141.1, trimesh 4.12.2 |
| `.venv-seg` | 3.12.13 | 2.10.0+cu128 | 0.25.0+cu128 | 1.26.4 | segmentation subprocess environment |
| `.venv-objects` | 3.11.15 | 2.5.1+cu121 | 0.20.1+cu121 | 1.26.4 | OpenCV 4.11.0.86, usd-core 26.8, trimesh 5.1.0 |

These describe installed environments, not a verified process executable. The
source caches the body estimator for key-frame requests; the motion pipeline
starts separate segmentation/body/object subprocesses and loads their models per
stage. Fully warm motion workers are not implemented by this validation.

The machine reports driver 575.57.08 and four 80 GB A100 compute GPUs. All had
substantial memory in use before our request. Snapshot usage ranged from 59,674
to 78,402 MiB of 81,920 MiB. These are machine-wide observations, not our job's
peak consumption or proof of an assigned GPU. Shared-service requests use its
existing queue; do not launch competing processes or clear other users' memory.

## Fresh output verification

We trimmed the frontend's bundled `OverFake.bundle/video.mp4` to one second,
640 × 1138 at 3 fps (203,121 bytes). This is an existing internal test fixture;
its redistribution rights have not been audited by this ticket. We uploaded it
into a separate test project and selected the visible foreground player.

* Body key frame: complete in 17.53 seconds, valid GLB v2, 18,439 vertices,
  36,874 triangular faces and 70 finite 3D keypoints.
* Body motion: all three frames succeeded, with matching shared topology, finite
  decoded coordinates, increasing timestamps and complete chunk coverage.
* Motion request: finished in 86.74 seconds with **partial** status. The optional
  football mesh stage failed with `cuda failed with error 2 out of memory` in
  the object reconstruction subprocess. `ballReady` and `groundAligned` are false.
  Estimated ball positions do not constitute a reconstructed football mesh.

The saved artifacts were independently decoded by
[`verify_dgx_artifacts.py`](../../scripts/verify_dgx_artifacts.py), rather than
trusting the health/status flags. Fingerprints are in
[`runtime-evidence.json`](runtime-evidence.json). The test project's remote
video/models/motion files are removed after verification; local evidence is kept
outside the repository, without committing large assets.

Body coordinates are camera relative, Y-up, in estimated metres with a fixed
shot origin. Motion uses gzip-compressed little-endian uint16 XYZ chunks plus
shared uint32 triangle indices. Smoothing uses a three-frame 0.2/0.6/0.2 window
without bridging cuts or missing frames. This does not establish accurate metric
scale, skill correctness, or scoring.

## API compatibility and recovery

The approved public ngrok endpoint forwards to port 8010 with the approved
localhost Host rewrite. See [ngrok.md](ngrok.md). Health and project requests
return application JSON. Compatibility routes used here are:

| Request | Purpose |
| --- | --- |
| `POST /api/projects` (multipart video) | Prepare a video and key frames |
| `PATCH /api/projects/{id}/frames/{frameId}` | Select the person bounding box |
| `POST /api/projects/{id}/generate` | Queue selected key-frame models |
| `POST /api/projects/{id}/motion` | Queue body/football motion stages |
| `GET /api/projects/{id}` | Read progress and relative asset paths |
| `GET /assets-data/{id}/…` | Fetch this project's current local artifacts |
| `DELETE /api/projects/{id}` | Remove an idle test project and its files |

The legacy service stores project data locally and has no implemented Firebase
ownership boundary. It is a team testing service. FP-14/17/53/39 introduce the
authenticated, privately stored, durable application contract. Never load cloud
credentials into the iOS client. Runtime cloud access is independently verified
in [runtime-access.md](runtime-access.md).

If DGX/ngrok is unavailable, the client must show an unavailable/retry state and
keep its source video. There is no guaranteed uptime, watchdog or failover here.
The service owner coordinates restart/allocation during maintenance; our ngrok
launcher belongs to Kunal. Check health after recovery before submitting work.
Legacy in-process jobs are not durable leases; uncertain interrupted jobs need
project inspection before retrying. FP-53 implements durable claim/recovery.

For the football memory failure, preserve completed body outputs and display a
partial result. A retry needs sufficient available memory in the assigned device
and coordination with the service owner. Blind retries, eviction of teammates'
models and automatic switches to another GPU are not a recovery policy.
