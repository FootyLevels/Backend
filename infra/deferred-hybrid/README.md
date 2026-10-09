# FP-50: Cloud foundation and DGX access

**Deferred, do not apply.** On 9 October 2026 Kunal specified free services only,
with no budget. This configuration needs billing-enabled Google Cloud resources.
It is retained as a validated future deployment design. `acknowledge_billable_services`
defaults to false and blocks planning/applying it. Use [../README.md](../README.md)
for the active Spark setup. Do not link billing or upgrade Firebase as part of FP-50.

The iOS app uses Firebase login. Cloud Run hosts the API and CPU media/maintenance
worker. Firestore stores application records; private Cloud Storage holds videos
and 3D assets. The on-premises DGX pulls reconstruction jobs from Pub/Sub using
outbound connections. No cloud GPU is provisioned. Scoring/AQA remains out of scope.

This directory contains infrastructure configuration, not the cloud application
adapters. The current local Backend API does not yet use these environment variables.
FP-51/14/17/52/53/54 implement persistence, authorization, storage and worker behavior;
FP-55 implements automated releases and operational monitoring.

## Resources and deployment defaults

Apply once per separate existing GCP project, with separate state for `dev` and `demo`.
Project creation and billing linkage are bootstrap steps rather than Terraform-managed
resources, so this configuration cannot accidentally delete an entire project.

| Resource | Configuration |
| --- | --- |
| Firebase project | Adopt existing project or enable Firebase; preserve the existing iOS registration |
| Firestore | Native `(default)` database in selected region; deletion protection; deny all direct client access |
| User media bucket | Private, uniform IAM; objects expire after 30 days by default; no versioning/soft-delete retention |
| Reference bucket | Private; permitted examples/weights only; no automatic expiry |
| Artifact Registry | Regional Docker repository for future CPU and DGX images |
| Reconstruction dispatch | One regional-storage Pub/Sub topic and durable pull subscription; 7-day backlog |
| Media dispatch | One Cloud Tasks queue; 2 concurrent dispatches, 1/second, bounded retries |
| Cloud Run API | Optional reviewed image; 1 CPU/1 GiB; concurrency 20; min 0/max 2; IAM-private by default |
| Cloud Run media | Optional reviewed image; 2 CPU/2 GiB; concurrency 1; min 0/max 2; internal ingress and IAM |
| Reconciliation | Optional, initially paused Scheduler job; every 5 minutes; authenticated media endpoint |
| Secret Manager | API enabled; no secrets/keys created or stored in Terraform |

No containers or schedules deploy until image digests are supplied. Do not deploy
the existing unauthenticated local API as the public cloud API. `enable_public_api`
is a separate deliberate switch after FP-14 verifies Firebase tokens and ownership.
Cloud Run IAM allows internet invocation of that future service; Firebase identity
and application authorization must still be enforced by the application itself.

Use `northamerica-northeast1` (Montreal) as the proposed Canadian location. Toronto
is absent from the current [Cloud Tasks region list](https://docs.cloud.google.com/tasks/docs/locations).
Keeping the services in Montreal avoids a second queue region. Confirm the
institution's data policy before database creation; database location cannot simply
be changed in place. Regional media/database/queue settings do not imply that every
Firebase authentication, logging or IAM operation is confined to that region.

## Local checks without Google credentials

Install Terraform (tested with the version in `.terraform-version`) from its official
distribution. From this directory:

```sh
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

Tests use mocked providers: they check configuration/privacy defaults, bounded
services, environment names and rejection of unsafe/invalid inputs. They do not
authenticate to Google, provision resources, prove effective IAM or run reconstruction.
Commit `.terraform.lock.hcl`; never commit `.terraform/`, state, plans, credentials
or real `.tfvars`/backend files. Do not treat a passing mock test as a successful deployment.

## Cloud bootstrap

1. Select two projects under the intended account/organization, with approved billing
   or capstone credits. Preserve last semester's `footylevels-de8aa` project referenced
   by the current iOS configuration until an explicit migration is ready.
2. Install the [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk).
   Sign in yourself using `gcloud auth login --update-adc`.
   The CLI login is for administration; ADC is for Terraform. Never paste tokens/keys in chat or Jira.
3. The operator needs API enablement, resource creation, IAM administration and
   `iam.serviceAccounts.actAs` for the runtime and dispatch accounts. Bootstrap
   administrators are distinct from application identities. The configuration uses
   additive IAM members; it does not replace project IAM policies or grant runtime
   identities Owner/Editor. If policy forbids these operations, use the institution's administrator.
4. Create one private state bucket per project. Fill these non-secret variables first:

```sh
export FOOTYLEVELS_PROJECT_ID='YOUR_DEV_PROJECT_ID'
export FOOTYLEVELS_REGION='northamerica-northeast1'
export FOOTYLEVELS_STATE_BUCKET="${FOOTYLEVELS_PROJECT_ID}-terraform-state"
gcloud storage buckets create "gs://${FOOTYLEVELS_STATE_BUCKET}" \
  --project="$FOOTYLEVELS_PROJECT_ID" --location="$FOOTYLEVELS_REGION" \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://${FOOTYLEVELS_STATE_BUCKET}" --versioning
```

State buckets are separate from user media and should allow only infrastructure
operators. Their version history supports state recovery. No runtime identity is
granted state-bucket access. Apply an approved state-version retention policy; do
not place videos or credentials in state. Terraform can store sensitive metadata even
when outputs are marked sensitive.

## Plan and apply one environment

```sh
cp dev.tfvars.example dev.tfvars
cp dev.backend.hcl.example dev.backend.hcl
# Verify project ID, approved region and state bucket in these two local files.
terraform init -reconfigure -backend-config=dev.backend.hcl
terraform plan -var-file=dev.tfvars -out=dev.tfplan
terraform show dev.tfplan
# After reviewing the exact project, location, IAM and resource changes:
terraform apply dev.tfplan
terraform output application_environment
```

Use the demo examples and a different project/state bucket for demo. Reinitialize
with its backend before every switch; never apply demo variables against dev state.
Use separate directories/checkouts for simultaneous operators. Saved plans contain
state details and must remain private. Re-plan after any configuration change.

### Adopting resources that already exist

Inventory the selected project before applying. Import Firebase, an existing database
and its rules release rather than attempting to create them again:

```sh
terraform import -var-file=dev.tfvars google_firebase_project.app "$FOOTYLEVELS_PROJECT_ID"
terraform import -var-file=dev.tfvars google_firestore_database.app \
  "projects/${FOOTYLEVELS_PROJECT_ID}/databases/(default)"
terraform import -var-file=dev.tfvars google_firebaserules_release.firestore \
  "projects/${FOOTYLEVELS_PROJECT_ID}/releases/cloud.firestore"
```

Run only imports for resources that actually exist. Import any matching buckets,
queues/accounts/repository using the IDs in the pinned provider documentation.
Review existing clients before replacing rules with deny-all; this is correct for
new API-only environments but can break a legacy client that accesses Firestore directly.
Stop if the plan proposes replacing a database, overwriting an existing location or
changing unrelated data. `prevent_destroy` guards managed resources, not arbitrary
manual deletion. This directory intentionally has no blanket destroy procedure.

### Firebase sign-in setup and reuse

After Firebase is enabled, configure Authentication in the Firebase console using
the existing Google sign-in approach from `GoogleService.swift`. Register the
existing bundle ID `ca.sheridan.FootyLevels-Frontend` in each new environment and
download its matching iOS configuration. Keep the existing file until FP-14/16
adds environment selection; tokens from the old project are not valid in the new one.
Verify one real Google sign-in and the token's project audience before opening API access.

Provider OAuth clients/consent screens require account-specific setup. This foundation
does not upgrade existing Authentication to Identity Platform or embed OAuth secrets
in Terraform. [Firebase's Terraform guide](https://firebase.google.com/docs/projects/terraform/get-started)
explains that automated Authentication configuration requires that upgrade and billing.
Record the enabled sign-in provider and iOS app ID as deployment evidence, not secret values.

## Identity access

| Identity | Granted access |
| --- | --- |
| `footylevels-{env}-api` | Firestore records; view/create user media; read references; publish reconstruction; enqueue media; attach task identity; sign its own short-lived asset URLs |
| `footylevels-{env}-media` | Firestore records; read/create/update/delete user media for preparation/cleanup; publish/enqueue recovery dispatch; attach task identity |
| `footylevels-{env}-dgx` | Firestore records; pull/ack its subscription; read/create immutable user media artifacts; read references/images; write operational logs |
| `footylevels-{env}-tasks` | Invoke media service only; no database/storage access |
| `footylevels-{env}-scheduler` | Invoke media service only; no database/storage access |
| Google-managed Tasks/Scheduler agents | Their own service-agent roles needed to mint invocation tokens; never assign these roles to application accounts |

Server Firestore IAM is project-wide in this small design. It cannot enforce per-player
ownership: the API and workers must validate UID, project state and leases. Bucket
grants likewise trust server code within the environment. The DGX can read other media
in that environment, so only approved footage may be uploaded until its allocation,
credential handling and privacy process are verified. It cannot delete/overwrite
existing objects with the supplied viewer/creator grants; write unique attempt paths.

Task creation uses the dedicated Tasks account in an OIDC token, with audience equal
to the media service's base `run.app` URL. Use an HTTP dispatch deadline no longer than
1800 seconds and bounded/idempotent work. The Scheduler uses its separate identity.
Handlers must distinguish the two principals so Scheduler cannot request arbitrary
media processing. Internal ingress accepts [same-project Tasks/Scheduler traffic](https://docs.cloud.google.com/run/docs/securing/ingress)
at the default URL; no VPC connector is needed for those calls. The API enqueues tasks
and does not directly call the internal service.

## DGX connection in plain terms

The DGX opens the connection, asks Pub/Sub for a job, downloads its input from private
storage, generates the model, uploads the files and updates Firestore. There is no
inbound firewall port, public DGX API, VPN to Cloud Run or cloud GPU required for this
workflow. Start with one allocated GPU and one reconstruction at a time; FP-52 will
check the actual institution scheduler/runtime.

Kunal confirmed on 9 October 2026 that outbound cloud access is allowed. The actual
DGX connection and credential method have not yet been tested. On an allocated DGX
session, check DNS/TLS access to:

```sh
for endpoint in pubsub.googleapis.com storage.googleapis.com firestore.googleapis.com \
  sts.googleapis.com iamcredentials.googleapis.com logging.googleapis.com; do
  curl --connect-timeout 10 --max-time 20 --silent --show-error \
    --output /dev/null --write-out "${endpoint}: HTTP %{http_code}\n" "https://${endpoint}/"
done
```

HTTP 400/401/403/404 can establish endpoint reachability; they do not prove identity
or authorization. Container pulls also need `${REGION}-docker.pkg.dev` connectivity.
StreamingPull requires outbound TLS/HTTP2 for gRPC; a successful curl is insufficient
if a proxy blocks HTTP2. Test the actual Pub/Sub client in FP-52/53.

Prefer [Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-providers):
the institution supplies an approved OIDC/SAML identity or equivalent credential
source, and a narrow pool/provider mapping identifies this worker. Supply only that
principal in `dgx_federated_principal`. Generate the non-secret external account
configuration through the approved WIF workflow and point `GOOGLE_APPLICATION_CREDENTIALS`
to its file. The provider's issuer, audience, subject mapping and token source cannot
be guessed from the hardware's name. Until configured, no external DGX access is granted.

For a supervised smoke test, an authorized operator can use short-lived service-account
impersonation following institutional policy. This is a testing credential, not an
unattended deployment. Do not copy personal ADC files to a shared DGX or create a
long-lived service-account JSON key as a convenience. If no institution identity is
available, settle an approved credential method with the administrator before live deployment.

## Environment and secret contract

`terraform output application_environment` supplies project/environment, database,
media/reference buckets, full topic/subscription/queue IDs, task account and retention.
These are resource names, not secrets. The Cloud Run API additionally receives
`MEDIA_WORKER_URL`. Future DGX code uses the same storage/database names plus
`RECONSTRUCTION_SUBSCRIPTION`; start with a worker concurrency of one.

Managed Cloud Run identities use ambient ADC, with no credential environment variable.
Local administration uses user ADC. Only the external DGX flow needs an approved
`GOOGLE_APPLICATION_CREDENTIALS` external-account configuration path.
No cloud credentials belong in the iOS bundle.

No runtime secret is required for this infrastructure foundation. If FP-52 requires
a gated-model download token, create a Secret Manager secret separately, add its
version outside Terraform and grant the downloader access to that one secret only.
Reference form: `projects/{project}/secrets/{name}/versions/{number}`. Prefer staging
permitted weights in the reference bucket so the runtime does not repeatedly download
them with a personal token. Do not provision dummy secret versions or pass secret
payloads through Terraform variables/state.

## Retention and completion evidence

Bucket lifecycle expiry is asynchronous. Application history must represent expired
media honestly; FP-54 handles abandoned uploads after 24 hours and metadata reconciliation.
FP-18 handles user-requested deletion in bounded jobs, including racing DGX workers.
No versioning or soft-delete is enabled for these new media/reference buckets, so
deleted objects are not held for recovery. State history is separate. Firestore PITR
is explicitly disabled at this foundation stage; backups/restore validation and
operational log retention are release work in FP-55. No backup capability is claimed.

Before closing FP-50, retain actual evidence of:

- Separate projects/state, billing/approved location and successful reviewed apply.
- Firebase provider/iOS registrations and deny-all client rules.
- Private bucket policies, effective API/media/DGX permissions and expected access denials.
- Named DGX session reaching the endpoints, and the approved credential flow.
- Resource outputs, local checks and a recorded implementation branch/commit.

No cloud apply or DGX execution is implied by committing these files. Monitoring,
budgets, CI/deployer federation and hybrid end-to-end proof remain FP-55/41 work.
Budget alerts do not cap spending; min-zero Cloud Run and bounded concurrency are
starting settings, not a monthly cost guarantee.

## Historical configuration record — 9 October 2026

Branch: `codex/fp-50-cloud-dgx-foundation`, based on the latest application Backend
branch `digaant-sam-meta-proto` at `1e34090`.

| Environment | Created GCP project | Proposed data region |
| --- | --- | --- |
| Development | `footylevels-dev-20261009` (number `894198668525`) | Montreal, `northamerica-northeast1` |
| Demo | `footylevels-demo-20261009` | Montreal, `northamerica-northeast1` |

These project IDs are shared with the active Spark configuration. Local CLI authorization
and the Spark Firebase/Firestore applies have succeeded; billing remains disabled.
Nothing in this deferred directory has been applied. Its state buckets, Cloud Run,
GCS, queues and runtime IAM bindings have not been created. Kunal selected Supabase
Free storage instead of GCS. See [the active setup record](../README.md) for current
status and remaining Auth/storage/hosting/DGX checks. FP-50 remains In Progress.
