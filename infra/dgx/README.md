# DGX access evidence for FP-50

Fresh body and motion inference was subsequently validated through the authorized
shared runtime in FP-52. See [runtime validation and limits](runtime.md); the
earlier observations below describe the initial access checks.

Checked 9 October 2026 using the SSH connection Kunal supplied. SSH authenticated
successfully with `BatchMode=yes` and `StrictHostKeyChecking=yes`. The actual host,
username and credentials are intentionally outside this shared source record.

## Observed environment

- Linux; four NVIDIA A100-SXM4-80GB compute GPUs, each reporting 81,920 MiB, plus
  one NVIDIA DGX Display GPU. Reported driver: 575.57.08.
- Default Python: 3.10.12. Torch, OpenCV and FastAPI were not installed in that
  interpreter; NumPy reports 1.21.5. This does not describe other users' environments
  or containers. The application targets an isolated Python 3.11 runtime in FP-52.
- No model inference, dependency installation or GPU allocation was performed.

## Network diagnosis

Ordinary HTTPS requests failed with `gaierror`, errno -3, "Temporary failure in name
resolution" for Firestore, Firebase public signing keys and both Supabase project hosts.
The resolver service is active, `/etc/resolv.conf` points at the local stub
`127.0.0.53`, and `resolvectl dns` lists no global or per-link upstream DNS servers.

To isolate DNS from outbound connectivity, a temporary diagnostic resolved the Google
Firestore and development Supabase addresses on the Mac, then connected directly to
those addresses from DGX while preserving the original TLS server names and certificate
validation. Both returned an HTTP 404 for `/`. This verifies those outbound TLS paths;
it does not verify authenticated Firestore access or Supabase upload/download permissions.
The temporary addresses are not a runtime configuration and were not installed on DGX.

This was the initial host-environment diagnosis. The application-scoped solution
verified below now enables ordinary hostname-based HTTPS requests without repairing
the shared resolver. An administrator is still needed for any shared resolver repair.
No system DNS, proxy, routing, firewall or certificate settings were modified.

## Read-only follow-up checks

Inside the approved DGX session:

```sh
resolvectl dns
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
python3 -c 'import socket; print(bool(socket.getaddrinfo("firestore.googleapis.com", 443)))'
python3 -c 'import socket; print(bool(socket.getaddrinfo("bwzxrvkxfubnmuieljoj.supabase.co", 443)))'
python3 -c 'import socket; print(bool(socket.getaddrinfo("skzgjwbkadiazfvuhtts.supabase.co", 443)))'
```

Use the institution's allocation/scheduler policy for runtime work. Seeing GPUs in
`nvidia-smi` is not proof that a particular job has an allocation or a working PyTorch
CUDA installation. FP-52 must validate the isolated runtime before inference.

## Cloud credentials and application integration

Keep Google administration ADC on the Mac. The earlier separate-worker design used
scoped API worker endpoints and per-job asset capabilities. Kunal has since proposed
one DGX backend that hosts the API and background generation together. In that design,
the trusted API needs its own scoped Firestore runtime identity and private Supabase
server configuration; administration ADC and VAC credentials must not be reused.
Authentication, ownership checks, durable jobs and the storage adapter still belong
to the implementation tickets. Separate data-only API credentials are now installed privately. Both environments
passed authenticated Firestore and Supabase round trips through the launcher; see
[runtime access evidence](runtime-access.md). The earlier unauthenticated connectivity
checks alone did not establish these permissions.

The existing DGX API compatibility path below is available for local tests through
the Mac. The existing API now responds publicly through the approved ngrok endpoint. The new
monolith and Firebase/Supabase integration have not been deployed by this work.
User-local ngrok installation, approved launch and public verification are recorded in
[ngrok setup](ngrok.md).

## Why VAC works and the verified DNS launcher

Read-only inspection of VAC on 9 October found its API and ngrok in private **user
and mount namespaces**, with the normal host network namespace retained. Their view
of `/etc/resolv.conf` contains Sheridan's resolvers `142.55.100.25`, `142.55.44.25`
and `142.55.136.25`. The ordinary SSH shell sees the broken `127.0.0.53` stub instead.
VAC's `scripts/with_network_dns.sh` creates this environment with `unshare` and a bind
mount. Its ngrok configuration also names those resolvers explicitly. The API has
established MongoDB Atlas connections; a fresh database `ping` through the same wrapper
also succeeded using VAC's own configured runtime. The ping wrote no database data.
No VAC process or configuration was changed, and no credential was copied.

All three resolvers answered queries from the FootyLevels DGX for ngrok, Firestore,
Firebase signing keys and both Supabase hosts. The equivalent versioned launcher is
[with_network_dns.sh](with_network_dns.sh), with network-specific settings in
[resolv.conf.example](resolv.conf.example). It explicitly makes mount propagation
private and snapshots the configuration into a temporary directory. It uses no sudo
and fails if namespace creation or the bind mount fails; it never falls back to editing
the system resolver. UID 0 inside its user namespace is mapped to the caller's host
UID, not host administrator access.

Run a command from a Linux DGX checkout:

```sh
bash infra/dgx/with_network_dns.sh python3 -c \
  'import socket; print(bool(socket.getaddrinfo("firestore.googleapis.com", 443)))'
```

For another approved resolver file, set `FOOTYLEVELS_RESOLV_CONF` to its path. Start
the intended API/runtime through the same wrapper so its child processes inherit
the resolver view. Wrapping a separate check does not change an already-running API.
The helper and example were installed under Kunal's DGX account at
`~/.local/share/footylevels/fp50-dns/`; the existing API was not restarted.

Live verification inside the wrapper used normal URLs and default TLS certificate
validation, with no credentials or manual destination IPs:

| Service | HTTP result | Evidence |
| --- | --- | --- |
| Firestore root | 404 | DNS and HTTPS reached Google |
| Firebase public signing keys | 200 | Public key endpoint reachable |
| Development Supabase bucket route | 400 | DNS and HTTPS reached Supabase; unauthenticated request |
| Demo Supabase bucket route | 400 | DNS and HTTPS reached Supabase; unauthenticated request |

The command ran in a different mount namespace; the host resolver's checksum was
identical before and after. Ordinary host-shell DNS remained broken, confirming the
configuration was confined to the launched command. These checks resolve the
hostname/TLS connectivity blocker for this launch method. The later [runtime access probe](runtime-access.md) verified authenticated Firestore
and Supabase round trips and cleanup. Fresh model generation was later verified
in [FP-52](runtime.md).

## Existing frontend compatibility path

A later read-only check on 9 October found the existing **Skill Lab** API listening
on DGX loopback port `8010`. Its `/api/health` response reports status `ok`, local
CUDA ready and motion ready. This service uses a different runtime from the default
Python interpreter inspected above. These readiness flags do not prove a fresh
inference run. Port `8000` belongs to a different service; the Skill Lab health
response currently advertises that port, so its advertised URL must not be used
to configure this frontend connection.

The frontend's `SkillLabClient` uses `/api/health` and `/api/projects` and lets the
user save a server address in its server settings. An SSH tunnel from the Mac was
verified with HTTP 200 for both routes and a list of three projects:

```sh
ssh -N -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:18010:127.0.0.1:8010 USER@DGX_HOST
```

Use the institution-provided host and account in place of the placeholders. While
the tunnel runs, set the **simulator's** server address to
`http://127.0.0.1:18010`. The simulator reaches the Mac's loopback listener. A
physical iPhone cannot reach this loopback address; its connection requires a
separately verified, permitted route. This tunnel does not expose a public service
or change DGX firewall/network settings.

This confirms the existing API can serve project data to the Mac. It does not
validate a fresh generation job, an app UI session,
the separate legacy image reconstruction client, or the new Firebase/Supabase
integration. New worker architecture and deployment choices remain deferred to
their implementation tickets.

References: [NVIDIA monitoring options](https://docs.nvidia.com/deploy/nvidia-smi/index.html),
[Linux unshare namespaces](https://man7.org/linux/man-pages/man1/unshare.1.html).
