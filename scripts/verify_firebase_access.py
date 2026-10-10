"""Opt-in live Development auth probe; temporary accounts/records are cleaned up.

Administration ADC stays on the Mac for disabling/revoking/deleting the temporary
accounts. Application requests use the separate scoped runtime service account.
Passwords and tokens exist in memory only and are never printed or saved.
"""
import base64
import os
from pathlib import Path
import plistlib
import re
import secrets
import time
import uuid

from fastapi.testclient import TestClient
from firebase_admin import auth, credentials, delete_app, initialize_app
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import firestore
import requests

from api.cloud import create_app
from footylevels.authentication import FirebaseIdentityVerifier
from footylevels.repositories import FirestoreRecords


PROJECT = "footylevels-dev-20261009"
IOS_APP = "1:894198668525:ios:7e5fb5c04398b1279140e4"


def main():
    if os.environ.get("FOOTYLEVELS_AUTH_TEST_PROJECT") != PROJECT:
        raise ValueError("Explicit Development opt-in is required")
    key_path = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    runtime_credential = credentials.Certificate(str(key_path))
    if (runtime_credential.project_id != PROJECT or
        runtime_credential.service_account_email != f"footylevels-api-dev@{PROJECT}.iam.gserviceaccount.com"):
        raise ValueError("Use only the scoped Development runtime credential")

    # Do not resolve administration ADC from GOOGLE_APPLICATION_CREDENTIALS.
    saved_key = os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS")
    try:
        google_credential, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"], quota_project_id=PROJECT)
    finally:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = saved_key

    class UserADC(credentials.Base):
        def get_credential(self):
            return google_credential

    admin_credential = UserADC()
    admin = initialize_app(admin_credential, {"projectId": PROJECT}, name="fp14-admin-" + uuid.uuid4().hex)
    runtime = initialize_app(runtime_credential, {"projectId": PROJECT}, name="fp14-runtime-" + uuid.uuid4().hex)
    db = firestore.Client.from_service_account_json(str(key_path), project=PROJECT)
    users, project_ids = [], []
    http = AuthorizedSession(admin_credential.get_credential())
    try:
        # Confirm cleanup privileges before creating accounts.
        try:
            auth.get_user("fp14-nonexistent-" + uuid.uuid4().hex, app=admin)
        except auth.UserNotFoundError:
            pass
        config = http.get(f"https://firebase.googleapis.com/v1beta1/projects/{PROJECT}/iosApps/{IOS_APP}/config", timeout=30)
        config.raise_for_status()
        api_key = plistlib.loads(base64.b64decode(config.json()["configFileContents"]))["API_KEY"]
        client = TestClient(create_app(FirestoreRecords(db), FirebaseIdentityVerifier(runtime)))
        sessions = []
        for suffix in ("a", "b"):
            signup = requests.post("https://identitytoolkit.googleapis.com/v1/accounts:signUp",
                params={"key": api_key}, json={"email": f"fp14-{uuid.uuid4().hex}-{suffix}@example.invalid",
                "password": secrets.token_urlsafe(32), "returnSecureToken": True}, timeout=30)
            if signup.status_code != 200:
                code = signup.json().get("error", {}).get("message", "").split(" : ")[0]
                code = code if re.fullmatch(r"[A-Z_]+", code) else "UNCLASSIFIED"
                raise RuntimeError(f"Temporary Firebase sign-up failed (HTTP {signup.status_code}, {code})")
            body = signup.json()
            users.append(body["localId"])
            sessions.append({"Authorization": "Bearer " + body["idToken"]})
        for i, header in enumerate(sessions):
            for _ in range(2):
                response = client.post("/api/me/bootstrap", headers=header)
                assert response.status_code == 200, f"Bootstrap failed (HTTP {response.status_code})"
                assert response.json()["id"] == users[i]
            response = client.post("/api/projects", headers=header, json={"title": "FP14 temporary test", "source": "gallery"})
            assert response.status_code == 201
            project_ids.append(response.json()["id"])
        for i, header in enumerate(sessions):
            foreign = project_ids[1-i]
            own = project_ids[i]
            assert client.get(f"/api/projects/{own}", headers=header).status_code == 200
            assert client.get(f"/api/projects/{foreign}", headers=header).status_code == 404
            assert client.patch(f"/api/projects/{foreign}", headers=header, json={"title": "foreign"}).status_code == 404
            assert client.delete(f"/api/projects/{foreign}", headers=header).status_code == 404
            assert [p["id"] for p in client.get("/api/projects", headers=header).json()["items"]] == [own]
        print("Real Firebase sessions: repeated bootstrap and two-account project isolation passed")
        time.sleep(2)  # Firebase revocation timestamps have whole-second precision.
        auth.revoke_refresh_tokens(users[0], app=admin)
        assert client.get("/api/projects", headers=sessions[0]).status_code == 401
        auth.update_user(users[1], disabled=True, app=admin)
        assert client.get("/api/projects", headers=sessions[1]).status_code == 401
        print("Real revoked and disabled sessions rejected")
    finally:
        # Exact created IDs only. Never enumerate/delete existing team accounts.
        failures = []
        for identifier in project_ids:
            try:
                db.collection("projects").document(identifier).delete()
            except Exception:
                failures.append("project cleanup")
        for uid in users:
            try:
                db.collection("users").document(uid).delete()
                auth.delete_user(uid, app=admin)
            except Exception:
                failures.append("temporary account cleanup")
        db.close()
        http.close()
        delete_app(runtime)
        delete_app(admin)
        if failures:
            raise RuntimeError("Temporary fixture cleanup requires attention: " + ", ".join(failures))
        print("Temporary account and metadata cleanup complete")


if __name__ == "__main__":
    main()
