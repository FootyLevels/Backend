# FP-50: Free cloud foundation

Current requirement: **free services only, no budget, no billing linkage or Blaze upgrade**.

| Setup | Location | Status |
| --- | --- | --- |
| Firebase social login | Spark projects | Google provider enabled in both; app migration/login test in FP-14 |
| Firestore application records | Spark, Montreal | One protected default database per environment |
| API | Kunal's Mac, local testing | Selected by Kunal; deployment hosting deferred |
| CPU media worker | Local testing first; DGX role planned | Packaging/integration pending |
| 3D generation | On-premises DGX | SSH/GPU access verified; missing DNS configuration blocks normal cloud access |
| Private videos and generated assets | Supabase Free, Canada Central | Both environments' buckets created and verified. See [storage guide](supabase/README.md) |
| Durable job dispatch | Local testing contract pending implementation | Pub/Sub/Tasks deferred |

The full cloud design cannot be run on Firebase Spark: Cloud Run and Pub/Sub require
billing, and current Cloud Storage for Firebase also requires Blaze.
[Firebase plans](https://firebase.google.com/docs/projects/billing/firebase-pricing-plans),
[Storage changes](https://firebase.google.com/docs/storage/faqs-storage-changes-announced-sept-2024)
explain the limits. Google Cloud's free usage allowances require a billing account
and are not a promise of zero charges. We do not enable them under this requirement.

`spark/` is the active configuration. It enables Firebase/Firestore and denies direct
client database access. It has no Cloud Run, buckets, queues, registry, secrets,
Identity Platform upgrade, billing-account resource, PITR, backups or TTL policy.
`deferred-hybrid/` preserves the full design for future funding; a false-by-default
acknowledgment variable blocks its plan/apply. Do not opt into it without new authorization.

The existing iOS app and local reconstruction code are preserved. Neither has been
connected to these new projects yet. Environment outputs define a contract for future
adapters; they do not implement FP-51, API authentication or a live DGX worker.

## Projects and state

Created and verified in the Google Cloud console on 9 October 2026:

| Environment | Project ID | Project number |
| --- | --- | --- |
| Development | `footylevels-dev-20261009` | `894198668525` |
| Demo | `footylevels-demo-20261009` | `554230919588` |

Both are under No organization. CLI checks confirm `billingEnabled: false` in both.
Last semester's `footylevels-de8aa`, referenced by the iOS app, remains unchanged.
Both Spark configurations have been applied: Firebase enabled, protected default
Firestore database in Montreal (`northamerica-northeast1`), deny-all direct client
rules deployed, and the existing iOS bundle registered separately. Google sign-in
is enabled in both consoles, with environment-specific public project names and the
Kunal-approved support contact. No Identity Platform or billing upgrade was made.
The existing Frontend still uses its legacy Firebase configuration; migration and a
real app login test remain FP-14 work.

| Environment | Registered iOS app ID |
| --- | --- |
| Development | `1:894198668525:ios:7e5fb5c04398b1279140e4` |
| Demo | `1:554230919588:ios:d6dcb9342a122579dc1131` |

State is local, in separate Terraform `dev` and `demo` workspaces. A GCS state bucket
would require the deferred billing setup. State and real configuration files are
ignored by Git; keep the local directory private and back it up securely yourself.
Only the pinned provider lock files, source and examples are committed.

## Local checks

Use Terraform from the official distribution, tested with `.terraform-version`:

```sh
terraform -chdir=infra/spark init
terraform -chdir=infra/spark fmt -check -recursive
terraform -chdir=infra/spark validate
terraform -chdir=infra/spark test
```

The tests use mocked providers, with no Google calls or provisioning. They verify
free-mode configuration and default denial of client access. The deferred configuration
has its own separate format/validation/mock tests; it is not deployed by these commands.

## Provision one environment

Use an approved administrator login. Never put private credentials in this repository,
the iOS app, chat or Jira. The Google Cloud CLI is available locally on Kunal's machine
at `~/.cache/footylevels-tools/google-cloud-sdk/bin/gcloud`; it was downloaded from
Google and its published checksum verified. `gcloud auth login --update-adc` supplies
the CLI and Terraform credentials after the user approves Google's permission screen.
Kunal completed this authorization. The providers' `billing_project` and
`user_project_override` settings identify the API quota project for those user
credentials; they do not link a billing account or upgrade Firebase.

First verify `gcloud billing projects describe PROJECT_ID` reports `billingEnabled: false`.
Do not link a billing account or start a paid/free-trial upgrade. The operator requires
API enablement, Firebase project administration, database creation and Rules deployment.
No application runtime IAM access is granted by this Spark foundation.

```sh
cp infra/spark/dev.tfvars.example infra/spark/dev.tfvars
terraform -chdir=infra/spark init
terraform -chdir=infra/spark workspace new dev
# For an existing workspace use: terraform -chdir=infra/spark workspace select dev
terraform -chdir=infra/spark plan -var-file=dev.tfvars -out=dev.tfplan
terraform -chdir=infra/spark show dev.tfplan
terraform -chdir=infra/spark apply dev.tfplan
```

Repeat with the demo file and demo workspace. Match project/environment/workspace
before every plan. If resources already exist, import them using the pinned provider's
documented resource IDs rather than replacing them. Never change a legacy database's
rules/location blindly. Database destruction is protected; location changes require
a deliberate migration. Saved plans can contain sensitive state and remain local.

## Firebase login and free quotas

Both Firebase projects' Google sign-in providers were enabled manually in the console.
On reprovisioning, enable that provider on Spark and select the owner's approved support
contact; do not assume permission to publish another address. Terraform has registered
the existing iOS bundle `ca.sheridan.FootyLevels-Frontend`
in each project; do not create duplicates.
Use that project's matching iOS configuration when FP-14/16 implements environment
selection. Google OAuth setup needs the account-specific clients/consent configuration;
no OAuth secret or dummy credential is managed in Terraform. Do not enable phone
authentication or upgrade to Identity Platform. Verify a real social login before release.

Firestore Spark allows one free-quota database per project: 1 GiB, 50,000 reads/day,
20,000 writes/day, 20,000 deletes/day and 10 GiB outbound/month. Quota exhaustion can
make requests unavailable. No TTL, PITR or backups/restore are enabled because those
need billing. [Firestore quotas](https://firebase.google.com/docs/firestore/quotas)
The application must handle unavailable/quota responses truthfully.

Server SDKs bypass Firebase client rules. Keep API ownership/authorization enforcement
in FP-14; deny-all client rules alone are not proof of a secure application. The free
database is for metadata, never video/model blobs or downloadable private media links.

## DGX and the remaining hosting decision

Kunal supplied the DGX SSH connection, which succeeded with host-key checking enabled.
The node reports four A100 80 GB compute GPUs plus a display GPU. Direct HTTPS to
Google and Supabase succeeded with certificate validation, but ordinary hostname
requests fail: the active system resolver has no upstream DNS servers configured.
See [DGX access evidence](dgx/README.md). This needs a Sheridan-approved network repair
before normal cloud integration can be verified. No shared network settings were changed.
FP-52 will check the allocation, scheduler, isolated GPU/runtime and worker identity.
Do not expose a public DGX endpoint or place personal cloud credentials on a shared node.
The deferred guide documents outbound endpoint/WIF requirements for the original plan.

Kunal selected [Supabase Free](supabase/README.md) for private video/model files.
Its 1 GB storage quota and 50 MB per-file limit require bounded demo clips and chunks.
Both environments' `private-media` and `reference-assets` buckets are verified private,
limited to 52,428,800 bytes, with object RLS enabled and no client object policies.
The application adapter still needs implementation and end-to-end verification.
It does not host the Python API or CPU processing. Kunal selected local API testing
on his Mac for now and explicitly deferred choosing a deployment host. Do not create
a Render service or another external API deployment. Reuse repository interfaces with the
Supabase asset adapter in FP-17 and a durable dispatch implementation. Firestore-backed jobs may replace Pub/Sub
for a small deployment, with bounded polling and transactional leases, if that design
is chosen. This alternative has not been implemented or approved as the final hosting
architecture. Existing local playback can continue while that decision is settled.

For the simulator, use the local API address documented in the application's setup.
A physical iPhone needs the Mac's LAN address on the same permitted network;
`localhost` on the phone refers to the phone. Keep any development login bypass
limited to debug builds. Local user ADC is for development only and must not be
copied to DGX or committed. New Firestore/Supabase environment names do not make the
existing API use those providers; that integration belongs to the implementation tickets.

FP-50 remains In Progress until its actual free resources, Authentication and access
evidence are recorded. The original full-cloud acceptance and later hosting tickets
need reconciliation with the free-only constraint; no live end-to-end generation is claimed.
