# FP-18: recording deletion and retention

The existing owner tombstone/cancellation fence now creates a durable deletion
intent. `DELETE /api/projects/{id}` accepts removal (202), returns `deletionId`,
and immediately hides the project and denies its jobs, reconstructions and new
asset grants. Repeated removal returns the same intent. Confirmation is offered
in the iOS private library; Cancel sends no request. The bundled local example
and legacy compatibility deletion remain separate.

`GET /api/deletions/{id}` returns only an owner's progress. `GET /api/deletions`
lists up to 50 pending owned intents. Fields are `id`, `ownerId`, `scope`,
`projectId`, `state` (`pending`/`completed`), `notBefore`, `completedAt` and a
sanitized `lastError`. No paths, capabilities, credentials or worker leases.
The library shows that a hidden recording can still be waiting for stored-file
removal and provides a refresh action; a 202 is never shown as physical erasure.

## Cleanup and retry

The existing supervised CPU worker processes at most ten pending intents per
120-second cycle, with a rotating cursor; no extra service or paid queue. Each
intent processes bounded batches of exact upload/output reservations and metadata.
Storage deletion and an independent absence check precede a transaction that
releases that object's owner/global quota once. A failed deletion/absence check
preserves its reservation. Crashes and duplicate runs resume from surviving
records, without decrementing a global counter blindly.

A removed source remains quarantined until at least its recorded `cleanupAfter`
and 48 hours beyond its logical upload expiry: normally 72 hours from reservation.
This covers existing signed/TUS writes. Derived outputs keep the existing 24-hour
writer grace from reservation; read capabilities also have a five-minute grace
from tombstoning. Links already issued cannot be instantly revoked. New reads,
mutations and publication are fenced immediately; physical removal waits for all
applicable grace periods. Pending status reports the next eligible time. This can
hold free-plan quota for up to three days; removing from the library does not
immediately free that allowance.

After every recorded file is absent, cleanup removes reconstruction/job records,
dependent owned comparisons and explicitly traverses their scoped subcollections.
It also removes project/user subcollections. Published references are checked for
absence so a missing reservation does not silently claim completion. Provenance
or accounting inconsistencies remain pending with `reconciliation_required`;
transient storage failures report `cleanup_unavailable` and retry. No prefix/bucket
wipe or client-supplied object path is used. Curated references and another user's
assets/comparisons are preserved. Comparison creation transactionally rereads both
project fences, preventing a late comparison after deletion.

A minimal project tombstone remains with its ID/owner, deletion timestamps and
`deleted` state. Title, label, source/assets, prepared media, source-derived
creation hash, model/selection references are erased. Keeping the ID ensures a
replayed idempotent create cannot recreate the removed project. Completion is
written only after exact cleanup; project roots are not physically removed.

## Account data and retention

`DELETE /api/me/data` requests removal of application data. It immediately marks
the profile unavailable, rejects bootstrap and transactionally blocks new project
creation. The same worker tombstones/cleans owned projects in batches and removes
owned comparisons. Unexpected orphan records prevent completion and require
reconciliation. A minimal unavailable UID/profile tombstone remains to prevent
reactivation/replay. Cleanup progress remains readable with the same verified
Firebase identity even while ordinary application access is disabled.

This endpoint does **not** delete the Firebase login identity or implement an
account-settings UI. Those are separate account lifecycle work; it never claims
the provider account was deleted. The operator has no public cleanup bypass.

Active recordings have no automatic age-based erasure: they remain until owner
removal. Failed/abandoned attempts and uncommitted uploads continue the existing
bounded maintenance policy. No application backup or restore workflow is
configured by this ticket; provider-internal retention is not promised to be
instant. Any future restore must retain/apply deletion tombstones before granting
access or dispatching jobs. Do not restore a database snapshot alone over them.

## Verification boundaries

Real Development database tests use isolated `integration-*` collections and
random fixture owners. They verify quarantine, retries/absence, exact quota
release, comparisons/subcollections, idempotency replay and account-data fences.
Synthetic fixture timestamps are aged to exercise eligibility; this is not a
claim that real signed/TUS capabilities were left to expire for 72 hours.
Default tests and iOS client tests cover auth/ownership and truthful progress.
Actual DGX/private storage and deployed-worker evidence are recorded separately;
no physical-device or complete interactive-app acceptance is inferred.

## Deployed proof — 12 October 2026

Development API/CPU source 0148340 passed 82 targeted checks on DGX and was deployed
on the existing public upstream 8010. Model 4017f11 stayed warm on private 8013;
no model restart, extra GPU process, Demo change, ngrok policy or billing linkage.

The scoped real Supabase/DGX probe passed: one committed synthetic video and two
opaque derived fixture objects were independently verified, then tombstoned. A
first injected storage failure retained quota and pending progress. With only
this signature-free fixture's eligibility timestamps aged, the ordinary deployed
CPU worker automatically retried, removed all three registered objects, verified
absence, released owner quota once and removed the dependent comparison, nested
metadata, jobs and synthetic reconstruction record. Completed replay was
idempotent. The trusted final fixture teardown removed only its retained
tombstone/profile/intent and zero-byte owner ledger, verifying metadata absence.
This is real storage/maintenance evidence, not new GPU output or Firebase SDK
login. Actual 72-hour expiry and physical UI interaction were not exercised.

Final local defaults: 287 passes, 26 cloud opt-in skips. Real isolated database
checks: 24 passes; all six deletion cases passed again after final changes.
All 38 iOS upload/auth/playback component checks passed. Evidence is in
[deployed deletion evidence](../infra/dgx/deletion-evidence.json). Confirmation/
Cancel on a device and complete interactive application acceptance remain separate.
