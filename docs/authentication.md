# Authenticated application API — FP-14

`api.cloud` is the new application entry point. It uses FP-51 records and does not
mount the older `api.main` local image/sequence routes or the shared Skill Lab
file server. The public DGX compatibility service remains unchanged during this
implementation. Debug frontend testing still uses that legacy service. The new authenticated
transport is verified locally; model/upload UI migration, private asset capability
checks and physical-device release checks are pending.

From a Backend checkout with the package and cloud dependencies installed:

```sh
uv pip install -e '.[cloud]'
export FIREBASE_PROJECT_ID=footylevels-dev-20261009
export GOOGLE_APPLICATION_CREDENTIALS=/private/path/google-service-account.json
uvicorn api.cloud:app_factory --factory --host 127.0.0.1 --port 8011
```

Port 8011 is an example for a separate local API, not a change to the team's DGX
listener or ngrok upstream. On DGX use the [DNS launcher](../infra/dgx/README.md)
when eventually deploying the authenticated service. Do not use personal ADC or
another application's service account for runtime work. The explicitly configured
Firebase project must match the credential's project. Emulator authentication is
rejected by this entry point; no development identity bypass exists.

Except for `/api/health`, routes require `Authorization: Bearer <Firebase ID
token>`. The Firebase Admin SDK verifies signature, project audience/issuer,
expiry and revocation/disabled-user status. The runtime identity receives
`firebaseauth.users.get` in a custom project role for account-state reads only;
it cannot administer Auth users. See [Firebase ID token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
and [Firebase Auth permissions](https://docs.cloud.google.com/iam/docs/roles-permissions/firebaseauth).

`POST /api/me/bootstrap` derives UID and display name from the verified token
and transactionally creates one profile. Other routes also require an active
application profile. Ownership always comes from that UID, never a submitted
`ownerId`. Foreign/missing resources both return 404. Job responses omit runner
lease tokens, expiry and worker identities. Responses use `Cache-Control: no-store`.

Implemented routes: profile bootstrap/read, project create/list/read/rename/delete,
owned job/reconstruction/comparison reads. Project deletion returns 202 and creates
a tombstone; actual file cleanup remains FP-17/54. Asset signing and new model job
submission will attach to this authenticated boundary in their implementation
tickets. Do not proxy unauthenticated legacy file routes through this API.

Errors: 401 means missing/expired/revoked/disabled identity and requires sign-in;
403 means application setup/account restriction; 404 conceals inaccessible data;
409 requires refreshing stale state; 503 means identity verification is unavailable
and should offer retry. Do not log bearer tokens, passwords or upstream responses.

## Verification

Local HTTP contract tests cover identity spoofing, separate owners, deletion,
invalid sessions, disabled/restricted accounts, dependency outages and worker
capability redaction. They inject a verifier and do not prove Firebase cryptography.

The opt-in Development probe uses real temporary Firebase email/password accounts,
real ID tokens and the scoped runtime identity against real Firestore. Mac user
ADC is used only to revoke/disable/delete the created test accounts. Passwords
and tokens remain in memory and are not printed. No email is sent. Exact created
account/project/profile IDs are cleaned up in `finally`; unrelated users are never
enumerated for deletion.

```sh
FOOTYLEVELS_AUTH_TEST_PROJECT=footylevels-dev-20261009 \
GOOGLE_APPLICATION_CREDENTIALS=/private/path/dev/google-service-account.json \
PYTHONPATH=src python -m scripts.verify_firebase_access
```

Live auth checks have Development opt-in guards. Do not run test account mutations
against Demo. Private asset URL ownership checks are required when FP-17 attaches
storage signing; no legacy asset routes are mounted on this API.

On 10 October the probe passed two-account project read/list/rename/delete
isolation, repeated bootstrap, real revocation and real disabled-account rejection,
then removed its temporary accounts/profiles/projects. The application used its
scoped Development identity, including the new user-read permission. The combined
local HTTP/records/storage/catalog/reconstruction regression run passed 88 tests;
Terraform validation and all three foundation tests passed.

Email/password sign-in was initially disabled in both new projects. It was enabled
using an explicit two-field update mask (`signIn.email.enabled` and
`signIn.email.passwordRequired`); no anonymous signup, SMS, paid upgrade or provider
replacement was requested. This provider setting is outside the current Terraform
resources, like the previously configured Google provider. Runtime account-state
permissions are managed by Terraform in both environments.

## iOS checkpoint

The simulator's real Firebase SDK passed two-account profile/project ownership,
repeated bootstrap, sign-in/sign-out and switching on 10 October. Both test Auth
accounts and exact Firestore records were removed and absence verified. The
client refreshes a 401 once, rejects redirects/foreign destinations and discards
responses from old account generations. Debug retains the requested bundled
example entry and team service; Release blocks the unauthenticated legacy service
until the owned upload/model migration. See the Frontend `docs/authentication.md`
for configuration and opt-in SDK test instructions.
