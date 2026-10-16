# Owned startup and recovery — FP-55

The Development deployment uses the DGX's existing **systemd user manager**.
No sudo, cron replacement, paid hosting or second GPU process is needed. Run all
operator commands as `bajajkun`, outside the DNS namespace. User lingering must
report `Linger=yes` for startup before an SSH login:

```sh
loginctl --no-ask-password enable-linger
loginctl show-user "$(id -u)" -p Linger
```

This host permits the account to enable its own lingering. If another host denies
it, an administrator must enable it; do not claim unattended boot is configured.

## Install from a reviewed commit

Archive tracked source from the selected Backend commit into a new immutable
`~/.local/share/footylevels/services/releases/<commit>` directory. Point
`services/current` at that release using an atomic symlink replacement. Do not
copy the working tree, ignored credentials or model weights. Keep the existing
cloud/model releases and private config in their documented locations.

```sh
python3 ~/.local/share/footylevels/services/current/infra/dgx/services.py install
systemctl --user daemon-reload
systemd-analyze --user verify ~/.config/systemd/user/footylevels*.service \
  ~/.config/systemd/user/footylevels.target ~/.config/systemd/user/footylevels-health.timer
```

Installation changes definitions only; it refuses unmanaged files/symlinks and
does not start, stop or enable services. Before the initial cutover, verify zero
queued/running/retrying jobs, empty media/reconstruction staging and zero active
model requests. Identify each old listener and its real host UID/argv/cwd, then
gracefully stop only those owned processes. Wait for children to exit and ports
to close before starting units. Never use historical PIDs or broad `pkill`.

```sh
systemctl --user enable footylevels.target
systemctl --user start footylevels.target
python3 ~/.local/share/footylevels/services/current/infra/dgx/services.py status
```

The target owns model, API, tunnel and a local health timer. Services may start
independently: the authenticated API remains available to show durable job status
while the model is warming or waiting for GPU capacity. The model validates the
existing checkpoint/source/GPU contract on every attempt; lack of 12 GiB free
headroom retries after 60 seconds and never clears somebody else's GPU process.
API restarts after 15 seconds, ngrok after 30. There is no start-limit lockout;
manual `stop` suppresses retries. `Restart=always` also handles a worker exit that
causes the current API supervisor to exit normally. Singleton locks and existing
worker/IPC locks guard against overlapping owned processes.

The service cgroup stops all namespace descendants and detached inference process
groups before restart, with 45 seconds for graceful shutdown before forced cleanup.
Durable leases, stale-attempt fences and idempotent outputs still govern recovery
of interrupted jobs; service restart is not a promise of instant job completion.

`services.py` builds a minimal environment: cloud keys load only inside the API,
ngrok reads its already-private configuration, and the model receives no cloud
credentials. The approved ngrok port, endpoint and Host rewrite are unchanged.
No authentication/traffic policy or notification delivery is added.

## Health and maintenance

The timer atomically replaces owner-only `services/health.json` every minute.
It contains service states/restart counts/PIDs, local API health and model warmup
booleans, with a timestamp. Check freshness; a stopped timer leaves the last file.
It does **not** probe public ngrok, cloud connectivity, queue progress, storage
quotas or actual inference. This avoids consuming free public tunnel traffic on
every timer tick. Inspect those separately using the existing scoped verifiers.

Raw SDK/model stdout and stderr are discarded by these units, so they cannot put
tokens, signed URLs, media or SDK exception bodies in the service journal. Systemd
retains process lifecycle/exit metadata. The health snapshot is bounded to one
small file. A model process being `active` does not mean weights are warm. If a
failure persists, stop the affected service and investigate its configuration and
preflight privately; do not paste raw logs or secrets into tickets.

```sh
systemctl --user show footylevels-api.service -p ActiveState -p SubState -p NRestarts -p Result
systemctl --user list-timers footylevels-health.timer --no-pager
```

For a planned release: drain owned work, `stop` the affected service, verify its
cgroup is empty, install the immutable release and atomically replace that
component's `current` link, then `start` and verify readiness plus public auth
smokes. If readiness fails, stop it, restore the previous link and start again.
Use `systemctl --user stop footylevels.target` to stop the whole stack; `restart`
restarts the whole stack including the model. Never run old detached launchers
alongside enabled services. Disable the target before intentionally returning to
manual startup. Keep the supervisor source link separate from model/cloud links.

## Scope of evidence

Controlled service restart/crash recovery can be verified without rebooting the
shared DGX. Lingering and an enabled target establish the boot configuration;
only a future permitted host reboot proves boot behavior. Free ngrok, institutional
power/network availability, GPU contention and provider quotas remain limits.
This step does not activate Demo, complete physical iPhone/Google-login testing,
provide release CI, exercise backup restore/deletion replay or deliver external
alerts. Those remaining FP-55 requirements are tracked explicitly.
