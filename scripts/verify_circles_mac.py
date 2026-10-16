"""Opt-in community proof with disposable identities and isolated DGX TestClient.

Archives reviewed tracked source only, starts no listener and changes no deployed
release. Firebase passwords/tokens stay in memory and travel only on SSH stdin.
"""
import io
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import subprocess
import sys
import tarfile
import uuid

from firebase_admin import auth, credentials, delete_app, initialize_app
import google.auth
from google.cloud import firestore_admin_v1
import requests


PROJECT = "footylevels-dev-20261009"
HOST = "bajajkun@142.55.34.202"


def ensure_indexes(credential):
    """Only two isolated Development test indexes; never apply infrastructure state."""
    client = firestore_admin_v1.FirestoreAdminClient(credentials=credential, transport="rest")
    Index = firestore_admin_v1.Index
    for collection, scope in [("integration-circlePosts", "circleId"),
                              ("integration-circleComments", "postId")]:
        parent = f"projects/{PROJECT}/databases/(default)/collectionGroups/{collection}"
        fields = [(scope, Index.IndexField.Order.ASCENDING),
                  ("createdAt", Index.IndexField.Order.DESCENDING),
                  ("__name__", Index.IndexField.Order.ASCENDING)]
        match = next((index for index in client.list_indexes(parent=parent)
                      if index.name.startswith(parent + "/indexes/")
                      and [(f.field_path, f.order) for f in index.fields] == fields), None)
        if match is None:
            operation = client.create_index(parent=parent, index=Index(
                query_scope=Index.QueryScope.COLLECTION,
                fields=[Index.IndexField(field_path=name, order=order) for name, order in fields]))
            print("Preparing isolated community test index: " + collection, flush=True)
            operation.result(timeout=600)
        elif match.state != Index.State.READY:
            raise RuntimeError("Isolated community index is still building; retry later")
    client.transport.close()


def main():
    if os.environ.get("FOOTYLEVELS_COMMUNITY_TEST_PROJECT") != PROJECT:
        raise ValueError("Explicit Development community proof opt-in required")
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Expected a committed verification checkpoint")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root):
        raise ValueError("Commit the complete verification checkpoint before archiving")
    public = plistlib.loads((root.parent / "Frontend/FootyLevels-Frontend/GoogleService-Info-Dev.plist").read_bytes())
    if public["PROJECT_ID"] != PROJECT:
        raise ValueError("Development Firebase configuration mismatch")
    saved = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    try:
        adc, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"],
                                     quota_project_id=PROJECT)
    finally:
        if saved:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = saved

    class AdminADC(credentials.Base):
        def get_credential(self):
            return adc

    admin = initialize_app(AdminADC(), {"projectId": PROJECT}, name="fp69-proof-" + uuid.uuid4().hex)
    owners, tokens, emails = [], [], []
    try:
        try:
            auth.get_user("fp69-missing-" + uuid.uuid4().hex, app=admin)
        except auth.UserNotFoundError:
            pass
        ensure_indexes(adc)
        archive = subprocess.check_output(["git", "archive", "--format=tar", revision,
                                           "api", "src", "scripts", "infra/dgx"], cwd=root)
        # The allowlist above excludes private credentials, dependencies and data.
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            if any(member.name.startswith("/") or ".." in Path(member.name).parts
                   or member.issym() or member.islnk() for member in bundle.getmembers()):
                raise ValueError("Unexpected source archive entry")
        remote = "~/.local/share/footylevels/community-proof/" + revision
        ssh = ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=15", HOST]
        transfer = subprocess.run([*ssh, "umask 077; mkdir -p " + remote + "; tar -xf - -C " + remote],
                                  input=archive, capture_output=True, timeout=60)
        if transfer.returncode:
            raise RuntimeError("Trusted proof source transfer failed")
        for _ in range(3):
            email = "fp69-" + uuid.uuid4().hex + "@example.invalid"
            response = requests.post("https://identitytoolkit.googleapis.com/v1/accounts:signUp",
                params={"key": public["API_KEY"]}, json={"email": email,
                "password": secrets.token_urlsafe(32), "returnSecureToken": True}, timeout=30)
            if response.status_code != 200:
                raise RuntimeError("Temporary Firebase signup failed")
            body = response.json()
            owners.append(body["localId"]); tokens.append(body["idToken"]); emails.append(email)
        command = ("cd " + remote + " && PYTHONPATH=src:. bash "
            "~/.local/share/footylevels/fp50-dns/with_network_dns.sh "
            "~/.local/share/footylevels/cloud-api/venv/bin/python scripts/verify_circles_dgx.py")
        result = subprocess.run([*ssh, command], input=json.dumps({"environment": "dev",
            "owners": owners, "tokens": tokens, "emails": emails}).encode(),
            capture_output=True, timeout=600)
        for line in result.stdout.decode().splitlines():
            try:
                evidence = json.loads(line)
                if evidence.get("ticket") == "FP-69" and evidence.get("environment") == "dev":
                    print(json.dumps(evidence), flush=True)
            except (ValueError, TypeError):
                pass
        if result.returncode:
            raise RuntimeError("Isolated community proof failed; inspect bounded evidence")
    finally:
        failures = []
        for uid, email in zip(owners, emails):
            try:
                user = auth.get_user(uid, app=admin)
                if user.email != email or not email.startswith("fp69-"):
                    raise ValueError("Fixture account identity mismatch")
                auth.delete_user(uid, app=admin)
                try:
                    auth.get_user(uid, app=admin)
                except auth.UserNotFoundError:
                    continue
                failures.append(uid)
            except Exception:
                failures.append(uid)
        delete_app(admin)
        if failures:
            raise RuntimeError("Exact temporary account cleanup requires attention")
        print(json.dumps({"ticket": "FP-69", "environment": "dev",
                          "temporaryAccountsAbsentVerified": len(owners)}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Community proof failed (" + type(error).__name__ + "); credential diagnostics suppressed", file=sys.stderr)
        sys.exit(1)
