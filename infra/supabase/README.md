# Supabase Free: private application files

Kunal selected Supabase Free storage on 9 October 2026. Keep Firebase login,
Firestore metadata and DGX generation; replace the deferred GCS asset store with
a Supabase adapter in FP-17. This directory configures buckets only, not that adapter.

## Limits and environment separation

The [Free plan](https://supabase.com/pricing) includes 1 GB file storage, 5 GB egress,
5 GB cached egress and a 50 MB maximum file upload, with up to two active free projects.
Quota accounting is organization-based for applicable usage items; two projects do
not imply twice the organization's included storage/transfer. Use separate dev and
demo projects in the intended Free organization. Do not select Pro, add paid features,
attach payment details or exceed the available free-project allowance. The current
organization already lists three unrelated projects; their existence does not prove
free active capacity is available. If creation reports a limit, stop and reconcile it
with Kunal without pausing or deleting another project.

Keep a small set of short, permitted demo videos and generated assets. SAM weights
are too large for this quota and stay on the allocated DGX/institutional disk. Keep
model chunks below the per-file limit. Free projects pause after a week of inactivity;
prepare the demo by checking actual availability, rather than bypassing inactivity
rules. Quota/paused failures must appear honestly in the app.

The GCP Firestore location is Montreal; Supabase region availability is independent.
The new-project form offers Canada (Central), `ca-central-1`, and that specific region
is verified on the created development project. Confirm any institutional residency rule.

## Deployment record — 9 October 2026

| Environment | Project reference | URL | Region | Status |
| --- | --- | --- | --- | --- |
| Development | `bwzxrvkxfubnmuieljoj` | `https://bwzxrvkxfubnmuieljoj.supabase.co` | `ca-central-1` | Healthy; both private buckets verified |
| Demo | Pending | Pending | Canada Central intended | Not created yet |

Development was created in `bajajku's Org` with the Free plan selected. The user set
the database password privately. `buckets.sql` succeeded: both rows have `public = false`
and `file_size_limit = 52428800`. Live verification found `storage.objects` RLS enabled
and zero client object policies. This proves initial bucket configuration, not the
future Firebase ownership checks or an end-to-end upload/reconstruction.

## Setup

1. Sign into Supabase yourself and review any account terms/permission prompt.
2. Choose a Free organization, then create `FootyLevels Development` and
   `FootyLevels Demo`. Set each project's database password yourself, without
   sending it through chat/Jira. Store it in your password manager. Development is
   created; demo setup depends on available free capacity. This database
   is incidental to Supabase Storage; Firestore remains the application database.
3. In each project's SQL editor, run [buckets.sql](buckets.sql), then verify both
   bucket rows have `public = false` and `file_size_limit = 52428800`.
4. Run [verify.sql](verify.sql) to check existing `storage.objects` policies: there must be no anonymous or
   unrestricted client policy exposing these buckets. New projects start without
   application policies. This script deliberately does not replace unrelated policies.
5. Verify `object_rls_enabled = true` and no client policies for a new project.
6. Record only project URL/reference, selected region, Free plan and bucket names
   in deployment evidence. Do not record a password, API secret or signed URL.

The two buckets are:

- `private-media`: source clips, previews, GLB keyframes, manifest, topology and
  compressed motion chunks at `users/{uid}/projects/{projectId}/...`.
- `reference-assets`: licensed small example assets at `examples/{referenceId}/{version}/...`.

## Runtime access contract for FP-17

Add `SUPABASE_URL`, `SUPABASE_MEDIA_BUCKET=private-media` and
`SUPABASE_REFERENCE_BUCKET=reference-assets` to the future server configuration.
Only the trusted API receives a Supabase server secret, kept outside source/state.
Do not put a server/service-role key in the iOS app, Firestore job document or DGX
worker. Start with API-mediated, scoped upload/download capabilities after Firebase
identity and ownership checks. The server secret bypasses Storage RLS; server checks
are essential. Initial buckets intentionally grant clients no direct access.

The DGX should receive only the permitted input/output asset capabilities for the
leased job, refreshed through an authenticated API if they expire. A public bucket
or broadly readable private media directory is not a substitute for that contract.
Use immutable attempt paths and explicitly scoped deletion through the API/cleanup
worker. Supabase credentials and endpoint reachability on DGX still need validation.

For larger clips, Supabase recommends [TUS resumable uploads](https://supabase.com/docs/guides/storage/uploads/resumable-uploads)
above 6 MB. FP-16 must adapt the current upload contract to the chosen provider; an
existing GCS session URL is not compatible with TUS. The overall 50 MB limit still applies.

Storage alone does not host the Python API or CPU processing. Their free hosting and
durable DGX dispatch remain separate decisions in FP-50/52/53/54/55. No live upload
or reconstruction is implied by creation of a bucket.
