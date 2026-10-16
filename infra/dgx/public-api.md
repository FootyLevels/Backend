# Development public API — 12 October 2026

The app connects to `https://ranting-pesticide-thespian.ngrok-free.dev` over HTTPS.
The existing ngrok agent forwards to **127.0.0.1:8010** with the approved localhost
Host rewrite. That listener now runs the authenticated application API. Firebase
Development login identifies the owner; Firestore stores records/jobs; Supabase
Free stores private video and results. No extra ngrok policy or paid service.

The GPU runtime's HTTP listener moved to **127.0.0.1:8013**. Cloud workers invoke
its existing owner-only `model-bridge/inference.sock`; the app never calls this
private listener. One model process shares the serial body/motion executor.

## Recovery

The current deployment is managed by the account's systemd services. Follow
[owned startup and recovery](services.md) for status, restart and release changes.
Do not launch a second detached copy while the services are enabled. The commands
below describe the underlying processes and are only a manual fallback after
stopping/disabling the corresponding services.

These commands use the existing private configuration, interpreters, checkpoints
and selected GPU. First inspect owned PID/command/cwd and active jobs/staging,
drain work, then gracefully stop only the identified deployment. Do not launch
duplicates, kill other users' processes, or use historical PIDs from documentation.
Start the model first and wait for private health `runtime.bodyModelLoaded=true`:

```sh
release="$HOME/.local/share/footylevels/runtime/current"
export PORT=8013
export FOOTYLEVELS_GPU_UUID=GPU-bf91a965-c736-7317-95ff-469e3d182eeb
export FOOTYLEVELS_RUNTIME_REVISION=4017f11
bash ~/.local/share/footylevels/fp50-dns/with_network_dns.sh \
  bash "$release/infra/dgx/start_runtime.sh"
```

Then start the application API and its two owned workers:

```sh
release="$HOME/.local/share/footylevels/cloud-api/current"
bash ~/.local/share/footylevels/fp50-dns/with_network_dns.sh \
  ~/.local/share/footylevels/cloud-api/venv/bin/python \
  "$release/infra/dgx/start_cloud_api.py" dev --port 8010 \
  --reconstruction-adapter "$release/infra/dgx/reconstruction_adapter.py"
```

For detached launches use owner-only logs and record the specific launcher PID.
Current releases remain model `4017f11` and cloud `0148340`; these explicit port
arguments work with those immutable releases. No cloud keys go into the model
environment. The private DNS wrapper preserves the real HOME and allows the API
and workers to reach Firebase/Supabase without sudo.

The running ngrok agent needs no restart for application restarts. If it exits,
use the previously approved command and its already-private configuration:

```sh
ngrok http 8010 --url https://ranting-pesticide-thespian.ngrok-free.dev \
  --host-header=localhost
```

Since FP-18, the existing CPU worker also finishes durable recording deletion and
quota/metadata cleanup; see [retention policy](../../docs/deletion.md).

## Verification and limits

Public `/api/health` returns the application service/version, not GPU readiness.
`/api/projects` without login and `/api/me` with an invalid bearer return 401;
legacy `/assets-data/...` is unavailable. Inspect GPU readiness privately, and
verify a real owned job through its durable status and private artifacts.

The cutover checked zero queued/running/retrying jobs, empty worker staging and
zero active model requests before graceful restarts. Body weights rewarmed on the
same assigned GPU, with no additional model process or other-user process changes.
The operator prepared rollback to the previous model 8010/cloud 8011 mapping; it
was not needed. Returning to that mapping also re-exposes the legacy shared API,
so the Development app must not use it as its authenticated account origin.

This is a Development deployment. Demo Firebase/storage stay configured but have
no public application runtime. User lingering and an enabled systemd target now
configure startup without SSH login. Controlled service recovery is recorded in
[service evidence](service-recovery-evidence.json); a real shared-host reboot has
not been performed. The phone can now reach the
API directly; physical-device acceptance and interactive Google login remain
separate checks. No full application acceptance is inferred from smoke tests.
