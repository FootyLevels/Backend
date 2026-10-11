# FP-17 private files and upload contract

The authenticated API runs on the DGX, Firebase Spark verifies identity and stores metadata, and existing Supabase Free private buckets store files. No billing account, paid hosting, new GPU process or client-side server key is needed. The model compatibility API remains on 8010 until FP-39 merges its routes with the authenticated API.

## Implemented routes

All routes below require a real Firebase session and an active application profile. Foreign and deleted resources return 404. Responses are `Cache-Control: no-store`; access logs are disabled in the launcher.

| Route | Purpose |
| --- | --- |
| POST `/api/projects/{id}/upload-session` | Reserve storage and create/resume one logical source upload |
| GET `/api/uploads/{id}` | Read owner-scoped upload status |
| POST `/api/uploads/{id}/refresh` | Refresh an active upload permission |
| POST `/api/uploads/{id}/finalize` | Verify exact bytes/SHA-256 and atomically pin the source; 202, state `preparing` |
| DELETE `/api/uploads/{id}` | Cancel finalization; 202, delayed object/quota cleanup |
| POST `/api/projects/{id}/assets` | Five-minute signed source read map |
| POST `/api/reconstructions/{id}/assets` | Five-minute allowlisted output map, keyed by relative manifest names |

Create the project first with the existing project route. For upload creation, provide an opaque `Idempotency-Key` of 16–80 URL-safe characters and JSON `{expectedRevision, byteSize, sha256, contentType}`. MP4 and QuickTime are accepted, with a maximum 52,428,800-byte file. A changed payload with the same key returns 409. Only one source upload is active per project; use a new project for a new recording.

The upload response contains `id`, `protocol: tus`, `endpoint`, `headers`, `metadata`, `chunkSize`, `expiresAt`, and `capabilityExpiresAt`. Forward the returned TUS headers/metadata to the returned endpoint. Never forward the Firebase Authorization header to storage. Use 6 MiB chunks; save the provider's Location securely, learn the current offset with HEAD, and continue with PATCH. A stale offset returns 409. Only accept HTTPS Locations on the exact returned storage origin. After a two-hour upload permission expires, the owner refreshes through this API and resumes the same provider Location if it remains valid. The logical operation expires after 24 hours. Keep the original idempotency key, expected input revision and source checksum across retries.

**Signed TUS uses `/storage/v1/upload/resumable/sign` with `x-signature`.** Using the ordinary resumable endpoint without Supabase identity fails. The signed token pins the object path and `upsert=false`; changing `x-upsert` cannot replace it. This was verified with real requests. Provider references: [resumable uploads](https://supabase.com/docs/guides/storage/uploads/resumable-uploads), [signed TUS route implementation](https://github.com/supabase/storage/blob/master/src/http/routes/tus/lifecycle.ts).

Finalization streams the object and independently checks its size/hash. A Firestore transaction fences deletion, expiry, active upload, expected revision and source immutability. Concurrent/repeated finalization returns the same revision; it does not replace a pinned input. `preparing` means byte verification succeeded. Decoding, video bounds, metadata, previews and the transition to `ready` are FP-54; there is no claim that MIME declarations alone prove playable video.

## Records, paths and budgets

- Source: `users/{uid}/projects/{projectId}/source/{uploadId}.mp4|mov`.
- Generated: `users/{uid}/projects/{projectId}/reconstruction/{jobId}/{attempt}/...`. Preserve `body.glb`, sequence manifests, topology and gzip motion chunk formats from the semester implementation.
- Store durable `AssetRef` values (bucket/key/SHA-256/bytes/type), never capability URLs. Output maps include only recorded artifacts from the same project/job/attempt. Clients resolve each manifest name through the exact map; an unknown, external, traversal or cross-attempt name must fail.
- `uploads/{id}` contains owner/project, expected revision, asset declaration, state, expiration, cleanup deadline, committed revision and reserved bytes. IDs derive from owner/project/idempotency key. Project metadata tracks `activeUploadId`.
- `storageUsage/global` and a hashed per-owner ledger count pending reservations and stored application bytes. Reserve the full per-file bucket limit before issuing upload permission, because a client could send more than its declared length. On verified finalization, reduce the reservation to actual immutable bytes. The shared application ceiling is 800 MiB and each owner has 200 MiB. Leave remaining Free capacity for curated/reference material; this ledger does not inventory manual dashboard writes or provider multipart temporary storage.
- Trusted generated-output code must call `FirestoreUploads.reserve_artifact()` before `SupabaseAssets.put_file()`. `assetReservations` retain exact references and charge known output bytes once, including failed/ambiguous uploads. Both helpers are verified with a saved real GLB. FP-39 still must integrate them with worker lease fencing and result publication. Never expose arbitrary object writes as an API route.

## Expiry, deletion and retention

Proposed media retention is 30 days for originals and outputs. Retention execution and committed-project deletion are FP-18/54. Cancelled/abandoned uploads expire logically after 24 hours, but a JSON expiry/cancellation cannot revoke an already issued provider capability. Quarantine remains until 48 hours after the logical expiry (72 hours after creation), covering a permission minted near expiry plus the provider's resumable lifetime. Quota stays reserved during that interval.

The trusted `cleanup_expired(uploadId, storage)` method fences the expired operation, deletes only its exact object, verifies absence, then atomically releases quota once. It supports interruption/retry. It cannot remove a committed source or release quota while outstanding writes may remain. The real probe exercises cancellation, rejection before the quarantine deadline, expired cleanup and concurrent retry. Scheduling this method and releasing committed/derived reservations after fenced deletion remain FP-18/54. Reads already issued can remain valid for up to five minutes after deletion; refresh immediately denies deleted/restricted resources.

## DGX deployment and evidence

Use the isolated `~/.local/share/footylevels/cloud-api/venv`, separate from all model environments. `infra/dgx/cloud_config.py` reads only the owner's private per-environment configuration, validates Firebase/Supabase/scoped service-account pairing and keeps secrets out of arguments/logs. `start_cloud_api.py dev --port 8011` binds loopback and has no model dependencies. Use the existing application-scoped DNS wrapper; no sudo or host resolver change.

`verify_private_assets_mac.py dev|demo` creates two temporary real accounts on the Mac, sends tokens over SSH stdin only and deletes/verifies their absence afterward. DGX runtime keys remain on DGX; administration ADC remains on the Mac. The remote probe checks real ownership, concurrent operation deduplication, >6 MiB interrupted/resumed upload, stale offsets, path/overwrite denial, exact finalization, signed read expiry/refresh, saved GLB delivery, budgets, cancellation/quarantine/cleanup and deleted-resource denial. Test fixtures are removed, including reservations. The saved GLB delivery uses seeded metadata to test storage; authenticated model-job generation is not claimed.

See `private-assets-evidence.json`. FP-16 wires the iOS upload UI, FP-39 connects actual model output, FP-53 provides durable jobs, and FP-54 prepares media/maintenance. Google interactive sign-in and physical-device checks remain separate FP-14 gates.
