"""Exercise storage boundary behavior, never mint real tokens in unit tests."""
from datetime import timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from api.cloud import create_app
from footylevels.assets import SupabaseAssets, StorageUnavailable
from footylevels.records import AssetRef, UploadRecord, utc_now
from footylevels.repositories import RecordConflict, RecordNotFound
from tests.test_cloud_api import TestRecords, Verifier, headers


class Uploads:
    def __init__(self, records):
        self.records = records
        self.items = {}

    def begin(self, uid, pid, rev, size, digest, mime, key):
        p = self.records.get_project(uid, pid)
        if rev != p.input_revision:
            raise RecordConflict()
        identifier = hashlib.sha256(f'{uid}/{pid}/{key}'.encode()).hexdigest()
        asset = AssetRef(bucket='private-media', object_key=f'users/{uid}/projects/{pid}/source/{identifier}.mp4',
                         byte_size=size, sha256=digest, content_type=mime)
        if identifier in self.items and self.items[identifier].asset != asset:
            raise RecordConflict()
        return self.items.setdefault(identifier, UploadRecord(id=identifier, owner_id=uid, project_id=pid,
            expected_revision=rev, asset=asset, expires_at=utc_now()+timedelta(hours=24), cleanup_after=utc_now()+timedelta(hours=48)))

    def get_upload(self, uid, identifier):
        r = self.items.get(identifier)
        if r is None or r.owner_id != uid:
            raise RecordNotFound()
        self.records.get_project(uid, r.project_id)
        return r

    def grant_upload(self, uid, identifier):
        r = self.get_upload(uid, identifier)
        if r.status != 'uploading' or r.expires_at <= utc_now():
            raise RecordConflict()
        return r

    def commit_upload(self, uid, identifier):
        r = self.get_upload(uid, identifier)
        p = self.records.get_project(uid, r.project_id)
        if r.status != 'committed':
            if p.input_revision != r.expected_revision or r.status != 'uploading':
                raise RecordConflict()
            p = p.model_copy(update={'video': r.asset, 'input_revision': p.input_revision + 1, 'state': 'preparing'})
            self.records.projects[p.id] = p
            self.items[identifier] = r.model_copy(update={'status': 'committed', 'committed_revision': p.input_revision})
        return p

    def cancel_upload(self, uid, identifier):
        r = self.get_upload(uid, identifier)
        if r.status == 'committed':
            raise RecordConflict()
        self.items[identifier] = r.model_copy(update={'status': 'cancelled'})
        return {'id': identifier, 'status': 'cancelled'}


class Assets:
    def __init__(self):
        self.grants, self.reads, self.verified = [], [], []
        self.invalid = False
    def signed_upload(self, asset):
        self.grants.append(asset)
        return {'protocol': 'tus', 'endpoint': 'https://storage.invalid/tus'}
    def signed_read(self, asset):
        self.reads.append(asset)
        return 'https://storage.invalid/signed/test-only'
    def verify(self, asset):
        self.verified.append(asset)
        if self.invalid:
            raise RecordConflict()


@pytest.fixture
def setup():
    records, assets = TestRecords(), Assets()
    uploads = Uploads(records)
    client = TestClient(create_app(records, Verifier(), assets=assets, uploads=uploads))
    client.post('/api/me/bootstrap', headers=headers('alice'))
    client.post('/api/me/bootstrap', headers=headers('bob'))
    p = client.post('/api/projects', json={'title': 'clip', 'source': 'gallery'}, headers=headers('alice')).json()
    return client, records, uploads, assets, p['id']


def begin(client, pid, **changes):
    body = {'expectedRevision': 1, 'byteSize': 12, 'sha256': 'a'*64, 'contentType': 'video/mp4', **changes}
    return client.post(f'/api/projects/{pid}/upload-session', headers={**headers('alice'), 'Idempotency-Key': 'test-upload-key-1234'}, json=body)


def test_upload_boundary_is_private_idempotent_and_preparing(setup):
    c, records, uploads, assets, pid = setup
    r = begin(c, pid)
    assert r.status_code == 200
    uid = r.json()['id']
    assert begin(c, pid).json()['id'] == uid
    assert len(uploads.items) == 1
    assert begin(c, pid, sha256='b'*64).status_code == 409
    for method, path in [('get', f'/api/uploads/{uid}'), ('post', f'/api/uploads/{uid}/refresh'),
                         ('post', f'/api/uploads/{uid}/finalize'), ('delete', f'/api/uploads/{uid}'),
                         ('post', f'/api/projects/{pid}/assets')]:
        assert getattr(c, method)(path, headers=headers('bob')).status_code == 404
        assert getattr(c, method)(path).status_code == 401
    assert not assets.reads and not assets.verified
    finalized = c.post(f'/api/uploads/{uid}/finalize', headers=headers('alice'))
    assert finalized.status_code == 202 and finalized.json()['state'] == 'preparing'
    assert finalized.json()['inputRevision'] == 2
    assert c.post(f'/api/uploads/{uid}/finalize', headers=headers('alice')).json() == finalized.json()
    assert len(assets.verified) == 1
    assert c.post(f'/api/uploads/{uid}/refresh', headers=headers('alice')).status_code == 409
    signed = c.post(f'/api/projects/{pid}/assets', headers=headers('alice'))
    assert signed.status_code == 200 and signed.headers['Cache-Control'] == 'no-store'
    assert set(signed.json()['assets']) == {'source'}
    records.mark_deleting('alice', pid)
    assert c.post(f'/api/projects/{pid}/assets', headers=headers('alice')).status_code == 404


def test_invalid_cancelled_expired_and_changed_uploads_cannot_publish(setup):
    c, records, uploads, assets, pid = setup
    uid = begin(c, pid).json()['id']
    assets.invalid = True
    assert c.post(f'/api/uploads/{uid}/finalize', headers=headers('alice')).status_code == 409
    assert records.projects[pid].video is None
    assets.invalid = False
    uploads.items[uid] = uploads.items[uid].model_copy(update={'expires_at': utc_now()-timedelta(seconds=1)})
    assert c.post(f'/api/uploads/{uid}/finalize', headers=headers('alice')).status_code == 409
    assert c.post(f'/api/uploads/{uid}/refresh', headers=headers('alice')).status_code == 409
    assert c.delete(f'/api/uploads/{uid}', headers=headers('alice')).status_code == 202
    assert c.post(f'/api/uploads/{uid}/finalize', headers=headers('alice')).status_code == 409


@pytest.mark.parametrize('body', [{'byteSize': 52428801}, {'byteSize': 0}, {'contentType': 'text/html'}, {'ownerId': 'bob'}])
def test_upload_input_bounds(setup, body):
    assert begin(setup[0], setup[4], **body).status_code == 422


def test_reconstruction_map_does_not_sign_foreign_or_cross_attempt_paths(setup):
    c, records, uploads, assets, pid = setup
    def result(paths):
        return SimpleNamespace(project_id=pid, job_id='job', artifacts=tuple(AssetRef(bucket='private-media',
            object_key=p, sha256='a'*64, byte_size=12, content_type='application/octet-stream') for p in paths))
    records.get_reconstruction = lambda uid, rid: result([f'users/{uid}/projects/{pid}/reconstruction/job/1/motion/faces.bin',
                                                         f'users/{uid}/projects/{pid}/reconstruction/job/2/sequence.json'])
    assert c.post('/api/reconstructions/r/assets', headers=headers('alice')).status_code == 409
    assert not assets.reads
    records.get_reconstruction = lambda uid, rid: result(['users/bob/projects/p/reconstruction/job/1/model.glb'])
    assert c.post('/api/reconstructions/r/assets', headers=headers('alice')).status_code == 409
    assert not assets.reads
    fixed = result([f'users/alice/projects/{pid}/reconstruction/job/1/motion/faces.bin'])
    records.get_reconstruction = lambda uid, rid: fixed
    assert set(c.post('/api/reconstructions/r/assets', headers=headers('alice')).json()['assets']) == {'motion/faces.bin'}


class Response:
    def __init__(self, status=200, body=None, chunks=()):
        self.status_code, self.body, self.chunks = status, body, chunks
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def json(self): return self.body
    def iter_content(self, size): return iter(self.chunks)


def test_streamed_verification_and_transport_redaction():
    store = SupabaseAssets('https://' + 'a'*20 + '.supabase.co', 'sb_secret_test')
    asset = AssetRef(bucket='private-media', object_key='users/a/projects/p/source/a.mp4', byte_size=3,
                     sha256=hashlib.sha256(b'abc').hexdigest(), content_type='video/mp4')
    store._request = lambda *a, **k: Response(chunks=[b'a', b'bc'])
    store.verify(asset)
    store._request = lambda *a, **k: Response(chunks=[b'abcd'])
    with pytest.raises(RecordConflict): store.verify(asset)
    store._request = lambda *a, **k: Response(chunks=[b'xyz'])
    with pytest.raises(RecordConflict): store.verify(asset)
    store = SupabaseAssets('https://' + 'a'*20 + '.supabase.co', 'sb_secret_test')
    import requests
    with patch('requests.request', side_effect=requests.ConnectionError('SECRET_URL')):
        with pytest.raises(StorageUnavailable) as error:
            store.verify(asset)
        assert 'SECRET' not in str(error.value)


@pytest.mark.parametrize('raw', ['https://evil.invalid/object/sign/a?token=secret', '//evil.invalid/a', '/object/sign/other?token=x'])
def test_signed_response_cannot_redirect_or_sign_another_object(raw):
    store = SupabaseAssets('https://'+'a'*20+'.supabase.co', 'sb_secret_test')
    asset = AssetRef(bucket='private-media', object_key='users/a/projects/p/source/a.mp4', sha256='a'*64,
                     byte_size=12, content_type='video/mp4')
    store._request = lambda *a, **k: Response(body={'signedURL': raw})
    with pytest.raises(StorageUnavailable): store.signed_read(asset)


def test_restricted_account_and_storage_failures_never_issue_capabilities(setup):
    c, records, uploads, assets, pid = setup
    records.users['alice'] = records.users['alice'].model_copy(update={'account_state': 'restricted'})
    assert begin(c, pid).status_code == 403 and not assets.grants
    records.users['alice'] = records.users['alice'].model_copy(update={'account_state': 'active'})
    uid = begin(c, pid).json()['id']
    def unavailable(asset): raise StorageUnavailable()
    assets.verify = unavailable
    assert c.post(f'/api/uploads/{uid}/finalize', headers=headers('alice')).status_code == 503
    assert records.projects[pid].video is None
    from footylevels.uploads import StorageQuotaExceeded
    def full(*args): raise StorageQuotaExceeded()
    uploads.begin = full
    assert begin(c, pid).status_code == 429


def test_deletion_during_verification_cannot_publish(setup):
    c, records, uploads, assets, pid = setup
    uid = begin(c, pid).json()['id']
    assets.verify = lambda asset: records.mark_deleting('alice', pid)
    assert c.post(f'/api/uploads/{uid}/finalize', headers=headers('alice')).status_code == 404
    assert records.projects[pid].video is None


def test_prepared_media_capabilities_are_owned_and_attempt_scoped(setup):
    from footylevels.records import MediaPreview, PreparedMedia
    c, records, uploads, assets, pid = setup
    identifier = begin(c, pid).json()['id']
    c.post(f'/api/uploads/{identifier}/finalize', headers=headers('alice'))
    prefix = f'users/alice/projects/{pid}/media/' + 'a' * 64 + '/1/'
    def file(name, mime):
        return AssetRef(bucket='private-media', object_key=prefix + name, sha256='a'*64, byte_size=12, content_type=mime)
    media = PreparedMedia(video=file('video.mp4', 'video/mp4'), width=320, height=240, duration=2, frame_count=60,
        previews=(MediaPreview(asset=file('preview-00.jpg', 'image/jpeg'), timestamp=0.5, frame_index=15),))
    records.projects[pid] = records.projects[pid].model_copy(update={'state': 'ready', 'prepared_media': media})
    assert c.post(f'/api/projects/{pid}/media-assets', headers=headers('bob')).status_code == 404
    assert not assets.reads
    response = c.post(f'/api/projects/{pid}/media-assets', headers=headers('alice'))
    assert response.status_code == 200 and set(response.json()['assets']) == {'video', 'preview-00'}
    assets.reads.clear()
    foreign = media.video.model_copy(update={'object_key': prefix.replace('/1/', '/2/') + 'video.mp4'})
    records.projects[pid] = records.projects[pid].model_copy(update={'prepared_media': media.model_copy(update={'video': foreign})})
    assert c.post(f'/api/projects/{pid}/media-assets', headers=headers('alice')).status_code == 409
    assert not assets.reads


def test_worker_download_checks_bytes_and_removes_only_its_partial_file(tmp_path):
    store = SupabaseAssets('https://' + 'a'*20 + '.supabase.co', 'sb_secret_test')
    asset = AssetRef(bucket='private-media', object_key='users/a/projects/p/source/a.mp4', byte_size=3,
                     sha256=hashlib.sha256(b'abc').hexdigest(), content_type='video/mp4')
    output = tmp_path / 'source.mp4'
    store._request = lambda *a, **k: Response(chunks=[b'a', b'bc'])
    store.download_file(asset, output)
    assert output.read_bytes() == b'abc'
    with pytest.raises(RecordConflict):
        store.download_file(asset, output)
    assert output.read_bytes() == b'abc'
    output.unlink()
    store._request = lambda *a, **k: Response(chunks=[b'xyz'])
    with pytest.raises(RecordConflict):
        store.download_file(asset, output)
    assert not output.exists()
