# DGX access evidence for FP-50

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

Sheridan-approved DNS settings and an authorized administrator are required to repair
the shared resolver. No system DNS, proxy, routing, firewall or certificate settings
were modified. After repair, rerun ordinary hostname-based checks from the intended
worker environment; direct-IP success alone cannot close the network acceptance check.

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

## Worker access contract

Keep Google administration ADC on the Mac. Keep the Supabase server secret with the
trusted API. Do not copy either onto a shared DGX node. The worker will authenticate
to scoped API worker endpoints and receive only the leased job's temporary input/output
asset capabilities, with refresh and expiry handled by the API. Worker identity,
ownership/lease checks and the asset adapter are FP-14/17/52/53 implementation work;
no live worker credential or cloud data permission is claimed here.

The API is local on the Mac for now. `localhost` on DGX cannot reach it. An approved
route between the local API and worker still needs integration validation; DNS repair
alone does not provide that route. No public DGX/API endpoint was created.

Reference: [NVIDIA monitoring options](https://docs.nvidia.com/deploy/nvidia-smi/index.html).
