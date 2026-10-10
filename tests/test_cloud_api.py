"""HTTP authorization contract; real Firebase sign-in evidence is separate."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from firebase_admin import auth, exceptions
import pytest

from api.cloud import create_app
from footylevels.authentication import (AuthenticationRejected,
    AuthenticationUnavailable, FirebaseIdentityVerifier, Identity)
from footylevels.records import ProjectRecord, UserRecord
from footylevels.repositories import RecordConflict, RecordNotFound


class TestRecords:
    __test__ = False

    def __init__(self):
        self.users, self.projects = {}, {}

    def bootstrap_user(self, uid, name):
        if uid in self.users and self.users[uid].account_state != "active":
            raise RecordConflict()
        return self.users.setdefault(uid, UserRecord(id=uid, display_name=name))

    def get_user(self, uid):
        if uid not in self.users:
            raise RecordNotFound()
        return self.users[uid]

    def create_project(self, uid, title, source):
        project = ProjectRecord(id="project-" + str(len(self.projects)), owner_id=uid,
                                title=title, source=source)
        self.projects[project.id] = project
        return project

    def get_project(self, uid, project_id):
        p = self.projects.get(project_id)
        if not p or p.owner_id != uid or p.state == "deleting":
            raise RecordNotFound()
        return p

    def list_projects(self, uid, limit, cursor):
        return [p for p in self.projects.values() if p.owner_id == uid and p.state != "deleting"][:limit], None

    def rename_project(self, uid, project_id, title):
        self.projects[project_id] = self.get_project(uid, project_id).model_copy(update={"title": title})
        return self.projects[project_id]

    def mark_deleting(self, uid, project_id):
        self.projects[project_id] = self.get_project(uid, project_id).model_copy(update={"state": "deleting"})
        return self.projects[project_id]

    def get_job(self, uid, identifier):
        self.get_project(uid, identifier)
        return SimpleNamespace(model_dump=lambda **kw: {"id": identifier} if kw["exclude"] == {"lease_token", "worker_id", "lease_expires_at"} else pytest.fail("Leaked worker capability"))

    get_reconstruction = get_project
    get_comparison = get_project


class Verifier:
    def verify(self, token):
        if token in ("alice", "bob"):
            return Identity(token, token.title())
        if token == "outage":
            raise AuthenticationUnavailable()
        raise AuthenticationRejected()


@pytest.fixture
def setup():
    records = TestRecords()
    client = TestClient(create_app(records, Verifier()))
    for name in ["alice", "bob"]:
        assert client.post("/api/me/bootstrap", headers={"Authorization": "Bearer " + name}).status_code == 200
    return client, records


def headers(name):
    return {"Authorization": "Bearer " + name}


def test_uid_cannot_be_spoofed_and_bootstrap_is_idempotent(setup):
    client, records = setup
    assert client.post("/api/me/bootstrap", headers=headers("alice"), json={"uid": "bob"}).json()["id"] == "alice"
    assert len(records.users) == 2
    assert client.post("/api/projects", headers=headers("alice"), json={"title": "x", "source": "gallery", "ownerId": "bob"}).status_code == 422


def test_foreign_owner_cannot_read_rename_delete_or_list(setup):
    client, records = setup
    r = client.post("/api/projects", headers=headers("alice"), json={"title": "Clip", "source": "gallery"})
    assert r.status_code == 201
    identifier = r.json()["id"]
    for path in [f"/api/projects/{identifier}", f"/api/jobs/{identifier}",
                 f"/api/reconstructions/{identifier}", f"/api/comparisons/{identifier}"]:
        assert client.get(path, headers=headers("bob")).status_code == 404
    assert client.patch(f"/api/projects/{identifier}", headers=headers("bob"), json={"title": "Stolen"}).status_code == 404
    assert client.delete(f"/api/projects/{identifier}", headers=headers("bob")).status_code == 404
    assert client.get("/api/projects", headers=headers("bob")).json()["items"] == []
    assert client.get(f"/api/jobs/{identifier}", headers=headers("alice")).json() == {"id": identifier}
    assert client.delete(f"/api/projects/{identifier}", headers=headers("alice")).status_code == 202
    assert client.get(f"/api/projects/{identifier}", headers=headers("alice")).status_code == 404


@pytest.mark.parametrize("value", [None, "Basic alice", "Bearer", "Bearer alice extra", "Bearer expired", "Bearer revoked"])
def test_invalid_credentials_fail_before_records(setup, value):
    client, records = setup
    r = client.post("/api/projects", headers={} if value is None else {"Authorization": value},
                    json={"title": "x", "source": "gallery"})
    assert r.status_code == 401
    assert not records.projects
    assert r.headers["Cache-Control"] == "no-store"


def test_account_restriction_and_verifier_outage_fail_closed(setup):
    client, records = setup
    records.users["alice"] = records.users["alice"].model_copy(update={"account_state": "restricted"})
    assert client.get("/api/projects", headers=headers("alice")).status_code == 403
    assert client.post("/api/me/bootstrap", headers=headers("alice")).status_code == 403
    assert client.get("/api/projects", headers=headers("outage")).status_code == 503
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/reconstruct/legacy/model.usdz").status_code == 404
    assert client.get("/assets-data/legacy/video.mp4").status_code == 404


def test_sdk_checks_revocation_and_emulator_is_forbidden(monkeypatch):
    app = object()
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    verifier = FirebaseIdentityVerifier(app)
    def check(token, **kwargs):
        assert kwargs == {"app": app, "check_revoked": True}
        return {"uid": "alice", "name": "Alice"}
    monkeypatch.setattr(auth, "verify_id_token", check)
    assert verifier.verify("test-token") == Identity("alice", "Alice")
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    with pytest.raises(ValueError):
        FirebaseIdentityVerifier(app)


@pytest.mark.parametrize("error,expected", [
    (auth.ExpiredIdTokenError("expired", None), AuthenticationRejected),
    (auth.RevokedIdTokenError("revoked"), AuthenticationRejected),
    (auth.UserDisabledError("disabled"), AuthenticationRejected),
    (auth.CertificateFetchError("offline", None), AuthenticationUnavailable),
    (exceptions.PermissionDeniedError("no access"), AuthenticationUnavailable),
])
def test_sdk_error_classification(monkeypatch, error, expected):
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    def reject(*args, **kwargs):
        raise error
    monkeypatch.setattr(auth, "verify_id_token", reject)
    with pytest.raises(expected):
        FirebaseIdentityVerifier(object()).verify("test-token")
