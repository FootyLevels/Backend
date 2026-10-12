"""HTTP authorization contract; real Firebase sign-in evidence is separate."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from firebase_admin import auth, exceptions
import pytest

from api.cloud import create_app
from footylevels.authentication import (AuthenticationRejected,
    AuthenticationUnavailable, FirebaseIdentityVerifier, Identity)
from footylevels.records import ProjectRecord, UserRecord
from footylevels.repositories import RecordConflict, RecordNotFound, RecordUnavailable


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

    def create_project(self, uid, title, source, key=None, label=None):
        import hashlib
        import json
        digest = hashlib.sha256(json.dumps([title, source, label]).encode()).hexdigest()
        identifier = hashlib.sha256(f'{uid}/{key}'.encode()).hexdigest() if key else 'project-' + str(len(self.projects))
        if identifier in self.projects:
            existing = self.get_project(uid, identifier)
            if existing.creation_hash != digest:
                raise RecordConflict()
            return existing
        project = ProjectRecord(id=identifier, owner_id=uid,
                                title=title, source=source, label=label, creation_hash=digest if key else None)
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

    def cancel_job(self, uid, identifier):
        return self.get_job(uid, identifier)


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
    assert client.post(f"/api/jobs/{identifier}/cancel", headers=headers("bob")).status_code == 404
    assert client.post(f"/api/jobs/{identifier}/cancel", headers=headers("alice")).json() == {"id": identifier}
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


def test_project_retry_is_scoped_validated_and_does_not_resurrect(setup):
    client, records = setup
    payload = {'title': 'First recording', 'source': 'capture', 'label': 'Practice'}
    h = {**headers('alice'), 'Idempotency-Key': 'draft-project-key-1234'}
    first = client.post('/api/projects', headers=h, json=payload)
    assert first.status_code == 201
    pid = first.json()['id']
    assert client.post('/api/projects', headers=h, json=payload).json()['id'] == pid
    client.patch(f'/api/projects/{pid}', headers=headers('alice'), json={'title': 'Renamed'})
    assert client.post('/api/projects', headers=h, json=payload).json()['title'] == 'Renamed'
    assert client.post('/api/projects', headers=h, json={**payload, 'label': 'Different'}).status_code == 409
    other = client.post('/api/projects', headers={**headers('bob'), 'Idempotency-Key': h['Idempotency-Key']}, json=payload)
    assert other.status_code == 201 and other.json()['id'] != pid
    client.delete(f'/api/projects/{pid}', headers=headers('alice'))
    assert client.post('/api/projects', headers=h, json=payload).status_code == 404
    assert len(records.projects) == 2
    for key in ['short', 'x'*81, 'bad/key-1234567890']:
        assert client.post('/api/projects', headers={**h, 'Idempotency-Key': key}, json=payload).status_code == 422


@pytest.fixture
def reconstruction_request(setup):
    from unittest.mock import Mock
    from footylevels.records import AssetRef, JobRecord, MediaPreview, PreparedMedia
    client, records = setup
    project = records.create_project('alice', 'Permitted practice', 'gallery')
    original = AssetRef(bucket='private-media', object_key='users/alice/source.mp4',
                        sha256='a'*64, byte_size=10, content_type='video/mp4')
    canonical = original.model_copy(update={'object_key': 'users/alice/prepared.mp4', 'sha256': 'b'*64})
    preview = original.model_copy(update={'object_key': 'users/alice/preview.jpg', 'content_type': 'image/jpeg'})
    media = PreparedMedia(video=canonical, width=320, height=240, duration=2, frame_count=60,
                          previews=(MediaPreview(asset=preview, timestamp=0, frame_index=0),))
    records.projects[project.id] = project.model_copy(update={'state': 'ready', 'video': original,
                                                            'prepared_media': media})
    def enqueue(uid, pid, revision, kind, digest, profile, *, keyframes=None, motion_segment=None):
        return JobRecord(id='f'*64, owner_id=uid, project_id=pid, input_revision=revision,
            kind=kind, input_hash=digest, profile_version=profile, source_asset=canonical,
            keyframes=keyframes, motion_segment=motion_segment, lease_token='private-token', worker_id='private-worker')
    records.enqueue_reconstruction = Mock(side_effect=enqueue)
    body = {'inputRevision': 1, 'keyframes': [{'frameIndex': 15, 'bbox': [.1, .1, .9, .9]}]}
    return client, records, project.id, body


def test_reconstruction_request_derives_identity_and_pins_prepared_source(reconstruction_request):
    from footylevels.model_requests import BODY_PROFILE, body_input_hash
    from footylevels.records import KeyframeInput
    client, records, pid, body = reconstruction_request
    response = client.post(f'/api/projects/{pid}/reconstructions/keyframes', headers=headers('alice'), json=body)
    assert response.status_code == 202
    job = response.json()
    assert response.headers['Location'] == '/api/jobs/' + job['id']
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Vary'] == 'Authorization'
    assert not {'leaseToken', 'workerId', 'leaseExpiresAt'} & job.keys()
    selected = tuple(KeyframeInput.model_validate(f) for f in body['keyframes'])
    source = records.projects[pid].prepared_media.video
    records.enqueue_reconstruction.assert_called_once_with('alice', pid, 1, 'reconstructKeyframes',
        body_input_hash(source, selected), BODY_PROFILE, keyframes=selected)
    assert job['sourceAsset']['sha256'] == source.sha256


@pytest.mark.parametrize('token,status', [(None, 401), ('expired', 401), ('bob', 404), ('outage', 503)])
def test_reconstruction_request_checks_auth_and_ownership_before_enqueue(reconstruction_request, token, status):
    client, records, pid, body = reconstruction_request
    response = client.post(f'/api/projects/{pid}/reconstructions/keyframes',
                           headers=headers(token) if token else {}, json=body)
    assert response.status_code == status
    records.enqueue_reconstruction.assert_not_called()


def test_restricted_user_cannot_request_reconstruction(reconstruction_request):
    client, records, pid, body = reconstruction_request
    records.users['alice'] = records.users['alice'].model_copy(update={'account_state': 'restricted'})
    assert client.post(f'/api/projects/{pid}/reconstructions/keyframes', headers=headers('alice'), json=body).status_code == 403
    records.enqueue_reconstruction.assert_not_called()


@pytest.mark.parametrize('change', [
    {'ownerId': 'bob'}, {'inputHash': 'f'*64}, {'profileVersion': 'arbitrary'},
    {'inputRevision': 0}, {'inputRevision': '1'}, {'inputRevision': True},
    {'keyframes': []}, {'keyframes': [{'frameIndex': -1, 'bbox': [0, 0, 1, 1]}]},
    {'keyframes': [{'frameIndex': 60, 'bbox': [0, 0, 1, 1]}]},
    {'keyframes': [{'frameIndex': 1, 'bbox': [0, 0, .01, 1]}]},
    {'keyframes': [{'frameIndex': 1, 'bbox': [0, 0, 2, 1]}]},
    {'keyframes': [{'frameIndex': 1, 'bbox': [0, 0, 1, 1]}]*2},
    {'keyframes': [{'frameIndex': i, 'bbox': [0, 0, 1, 1]} for i in range(25)]},
])
def test_reconstruction_rejects_untrusted_or_unbounded_inputs(reconstruction_request, change):
    client, records, pid, body = reconstruction_request
    assert client.post(f'/api/projects/{pid}/reconstructions/keyframes', headers=headers('alice'),
                       json={**body, **change}).status_code == 422
    records.enqueue_reconstruction.assert_not_called()


@pytest.mark.parametrize('change', [{'input_revision': 2}, {'prepared_media': None}, {'state': 'preparing'}])
def test_reconstruction_requires_current_prepared_input(reconstruction_request, change):
    client, records, pid, body = reconstruction_request
    records.projects[pid] = records.projects[pid].model_copy(update=change)
    assert client.post(f'/api/projects/{pid}/reconstructions/keyframes', headers=headers('alice'), json=body).status_code == 409
    records.enqueue_reconstruction.assert_not_called()


@pytest.mark.parametrize('error,status', [(RecordConflict, 409), (RecordNotFound, 404), (RecordUnavailable, 503)])
def test_reconstruction_surfaces_transaction_race_without_leaking_details(reconstruction_request, error, status):
    client, records, pid, body = reconstruction_request
    records.enqueue_reconstruction.side_effect = error('private repository detail')
    response = client.post(f'/api/projects/{pid}/reconstructions/keyframes', headers=headers('alice'), json=body)
    assert response.status_code == status
    assert 'private repository detail' not in response.text


def test_reconstruction_accepts_limit_and_last_canonical_frame(reconstruction_request):
    client, records, pid, body = reconstruction_request
    body['keyframes'] = [{'frameIndex': i, 'bbox': [0, 0, 1, 1]} for i in [*range(23), 59]]
    response = client.post(f'/api/projects/{pid}/reconstructions/keyframes', headers=headers('alice'), json=body)
    assert response.status_code == 202
    assert len(response.json()['keyframes']) == 24


def test_motion_request_pins_segment_source_and_server_profile(reconstruction_request):
    from footylevels.model_requests import MOTION_PROFILE, motion_input_hash
    from footylevels.records import MotionSegment
    client, records, pid, _ = reconstruction_request
    body = {'inputRevision': 1, 'segment': {'startFrame': 15, 'endFrame': 60}}
    response = client.post(f'/api/projects/{pid}/reconstructions/motion', headers=headers('alice'), json=body)
    assert response.status_code == 202
    segment = MotionSegment(start_frame=15, end_frame=60)
    source = records.projects[pid].prepared_media.video
    records.enqueue_reconstruction.assert_called_once_with('alice', pid, 1, 'reconstructMotion',
        motion_input_hash(source, segment), MOTION_PROFILE, motion_segment=segment)
    assert response.json()['motionSegment'] == body['segment']
    assert not {'leaseToken', 'workerId', 'leaseExpiresAt'} & response.json().keys()


@pytest.mark.parametrize('segment', [
    {'startFrame': -1, 'endFrame': 30}, {'startFrame': 0, 'endFrame': 29},
    {'startFrame': 0, 'endFrame': 301}, {'startFrame': 15, 'endFrame': 61},
    {'startFrame': True, 'endFrame': 31}, {'startFrame': '0', 'endFrame': 30},
    {'startFrame': 30, 'endFrame': 30}, {'startFrame': 0, 'endFrame': 30, 'fps': 60},
])
def test_motion_request_rejects_unbounded_or_out_of_video_segment(reconstruction_request, segment):
    client, records, pid, _ = reconstruction_request
    assert client.post(f'/api/projects/{pid}/reconstructions/motion', headers=headers('alice'),
        json={'inputRevision': 1, 'segment': segment}).status_code == 422
    records.enqueue_reconstruction.assert_not_called()


@pytest.mark.parametrize('token,status', [(None, 401), ('expired', 401), ('bob', 404), ('outage', 503)])
def test_motion_request_checks_identity_and_ownership(reconstruction_request, token, status):
    client, records, pid, _ = reconstruction_request
    assert client.post(f'/api/projects/{pid}/reconstructions/motion', headers=headers(token) if token else {},
        json={'inputRevision': 1, 'segment': {'startFrame': 0, 'endFrame': 30}}).status_code == status
    records.enqueue_reconstruction.assert_not_called()


@pytest.mark.parametrize('error', ['ServiceUnavailable', 'Aborted', 'ResourceExhausted', 'DeadlineExceeded', 'PermissionDenied', 'FailedPrecondition'])
def test_metadata_sdk_read_failure_is_private_retryable_response(reconstruction_request, error):
    from google.api_core import exceptions as cloud_errors
    from unittest.mock import Mock
    client, records, pid, body = reconstruction_request
    records.get_project = Mock(side_effect=getattr(cloud_errors, error)('private SDK diagnostic'))
    response = client.post(f'/api/projects/{pid}/reconstructions/keyframes', headers=headers('alice'), json=body)
    assert response.status_code == 503
    assert response.json() == {'detail': 'Service temporarily unavailable'}
    assert response.headers['Cache-Control'] == 'no-store'
    records.enqueue_reconstruction.assert_not_called()
