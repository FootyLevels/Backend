# DGX runtime access and selected application deployment

The selected capstone deployment is one modular FastAPI application on DGX, exposed
through ngrok, with Firebase login, Firestore metadata/jobs and private Supabase files.
Model weights stay on institutional disk. No billing account or paid queue/hosting
service is needed. The existing Skill Lab API is publicly reachable; it has not yet
been replaced by this integrated application.

## Responsibility boundaries

| Component | Responsibility |
| --- | --- |
| iOS app | Firebase sign-in, record/import, upload progress, poll owned jobs, render assets and visual comparison |
| API on DGX | Verify Firebase ID tokens, enforce ownership, validate requests, create persistent jobs, authorize short-lived asset access |
| Background runner in the same deployment | Claim jobs transactionally in Firestore, prepare media, run bounded GPU inference, publish complete artifacts, clean up |
| Firestore | Profiles, projects, input revisions, reconstruction/artifact manifests, job leases/status, comparisons and deletion state |
| Supabase private buckets | Source clips/photos, previews, USDZ/keypoints/metadata and motion buffers; never model weights |
| ngrok | Public HTTPS route to the API; not a database, queue or authorization service |

Run blocking FFmpeg/model work outside the request event loop, with one GPU job at
a time initially. A Firestore job is the durable queue: pending jobs survive API
restarts; transactional leases, attempt IDs and heartbeats fence late completions.
An expired lease becomes retryable up to a configured attempt limit. Cancellation,
input revision and deletion must be checked again before publishing. Polling backs
off while idle to preserve free read quotas. No in-memory-only job table, Pub/Sub,
Cloud Tasks, Redis or separate cloud media service is required.

Keep the existing model loaded in the reconstruction process once its GPU allocation
is confirmed. Do not create another unallocated GPU runtime or restart another user's
service. CPU preparation and maintenance remain modules, with bounded subprocesses
and a periodic recovery pass in this deployment.

## Runtime identities and files

Each Firebase project has its own `footylevels-api-dev` or `footylevels-api-demo`
service account with only `roles/datastore.user` in that project. That role permits
application data access; it is not Firebase/project administration. Firestore server
access bypasses client rules, so API ownership checks are mandatory before real
user data is exposed. Current client rules deny direct reads/writes: the app accesses
metadata through the API. Any later direct app reads require separately tested
per-user rules.

Private DGX configuration is under `~/.config/footylevels/{dev|demo}/` (0700):

- `google-service-account.json` (0600): environment-specific runtime key.
- `runtime.json` (0600): environment IDs, credential file path, Supabase URL/key and bucket names.

Keys were created outside Terraform and transferred over the authenticated SSH
connection. No personal ADC or VAC credential was copied. Local backup copies are
under ignored `infra/credentials/`; protect these files. Revoke keys no longer needed,
rotate on exposure, and update the private file before restarting the intended runtime.
No credential is loaded into the currently tunneled API by this foundation work.

Enter each Supabase `sb_secret_...` key privately:

```sh
python3 ~/.local/share/footylevels/fp50-dns/configure_storage.py dev
python3 ~/.local/share/footylevels/fp50-dns/configure_storage.py demo
```

These server keys bypass Supabase RLS and belong only to the trusted backend.
No key, signed URL or private file should be placed in an iOS configuration, job record,
log, Jira comment or committed source. New secret keys use the `apikey` header.

## Disposable access probe

The probe uses Google Auth 2.43.0 and Requests 2.32.5 installed under
`~/.local/share/footylevels/fp50-cloud-libs`. The system Python lacks ensurepip, so
dependencies were installed with pip `--target` in this private directory, with no
sudo/system package update. This is a foundation probe, not the production runtime.

```sh
PYTHONPATH="$HOME/.local/share/footylevels/fp50-cloud-libs" \
  ~/.local/share/footylevels/fp50-dns/with_network_dns.sh \
  python3 ~/.local/share/footylevels/fp50-dns/verify_cloud_access.py dev
# Repeat with demo. --firestore-only checks Firestore without storage configuration.
```

The script creates/reads/deletes a uniquely named Firestore probe document, checks
deletion and cross-environment denial, then uploads/downloads/deletes a tiny JSON
object in each private bucket, checks privacy and deletion. It never overwrites an
application record or deletes a prefix containing existing data. Cleanup runs even
on failure; if cleanup fails, the command fails and needs operator reconciliation.
Diagnostics exclude response bodies, headers, tokens and signed URLs.

This proves runtime permissions and connectivity, not user authorization, resumable
upload, model generation or app playback. Those remain the implementation tickets.

References: [Firestore IAM](https://docs.cloud.google.com/firestore/native/docs/security/iam),
[service account key handling](https://docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys),
[Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys).

## Verified foundation checkpoint — 9 October 2026

Both environments passed the complete probe from DGX: Firestore create/read/delete
and cleanup verification, cross-environment HTTP 403, and both private Supabase
buckets' upload/download/delete and public-read denial. All disposable objects and
documents were removed. The existing public health/project routes also pass.

Terraform validation and three mocked policy tests pass. Each real identity apply
added only IAM API enablement, one service account and its data role; no changes or
destruction. Both refreshed plans report no changes. This completes FP-50 foundation
access, while application integration, identity/ownership, durable jobs and fresh
end-to-end inference remain later tickets.
