# Shared reconstruction runtime — FP-52

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
