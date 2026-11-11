"""Lesson routes: consent, create, read, assets, delete and reports, with in-memory records."""
import hashlib

from fastapi.testclient import TestClient
import pytest

from api.cloud import create_app
from footylevels.authentication import Identity
from footylevels.lessons import admit, lesson_job_id
from footylevels.model_requests import lesson_input_hash
from footylevels.records import (AssetRef, Engine, LessonAuthoring, LessonRecord, LessonReport, LessonSkill,
                                 MediaPreview, PreparedMedia, ProjectRecord, UserRecord, utc_now)
from footylevels.repositories import RecordConflict, RecordNotFound

MODEL = 'gemini-4-carbon'
KEY = 'lesson-request-0001'


def asset(key, content='video/mp4', size=1000):
    return AssetRef(bucket='private-media', object_key=key, sha256=hashlib.sha256(key.encode()).hexdigest(),
                    byte_size=size, content_type=content)


def project(uid, pid='p1', duration=12.0, **update):
    media_prefix = f'users/{uid}/projects/{pid}/media/{"a" * 64}/1/'
    media = PreparedMedia(video=asset(media_prefix + 'video.mp4'), width=720, height=720,
                          duration=duration, frame_count=int(duration * 30),
                          previews=[MediaPreview(asset=asset(media_prefix + 'preview-00.jpg', 'image/jpeg'),
                                                 timestamp=0, frame_index=0)])
    return ProjectRecord(id=pid, owner_id=uid, title='Clip', source='gallery', state='ready',
                         video=asset(f'users/{uid}/projects/{pid}/source/a.mp4'), prepared_media=media).model_copy(update=update)


class Records:
    def __init__(self):
        self.users, self.projects, self.jobs = {}, {}, {}

    def bootstrap_user(self, uid, name):
        return self.users.setdefault(uid, UserRecord(id=uid, display_name=name))

    def get_user(self, uid):
        if uid not in self.users:
            raise RecordNotFound()
        return self.users[uid]

    def get_project(self, uid, pid):
        p = self.projects.get(pid)
        if not p or p.owner_id != uid or p.state == 'deleting':
            raise RecordNotFound()
        return p

    def get_job(self, uid, job_id):
        job = self.jobs.get(job_id)
        if not job or job.owner_id != uid:
            raise RecordNotFound()
        return job


class Lessons:
    """Same decisions as FirestoreLessons (``admit``), stored in dictionaries."""

    max_seconds = 20

    def __init__(self, records, daily_limit=3):
        self.records, self.daily_limit = records, daily_limit
        self.lessons, self.reports, self.quota = {}, {}, {}

    def set_consent(self, uid, accepted):
        user = self.records.get_user(uid)
        self.records.users[uid] = user.model_copy(update={
            'lesson_ai_consent_at': utc_now() if accepted else None,
            'lesson_ai_consent_version': 'lesson-ai-v1' if accepted else None})
        return self.records.users[uid]

    def enqueue_lesson(self, uid, pid, revision, key):
        project = self.records.get_project(uid, pid)
        job_id = lesson_job_id(uid, key)
        existing = self.records.jobs.get(job_id)
        record = admit(self.records.get_user(uid), project, revision, existing, self.quota.get(uid, 0), True,
                       job_id=job_id, model=MODEL, max_seconds=self.max_seconds, daily_limit=self.daily_limit)
        if record is not existing:
            self.records.jobs[job_id] = record
            self.records.projects[pid] = project.model_copy(update={'active_job_id': job_id})
            self.quota[uid] = self.quota.get(uid, 0) + 1
        return record

    def get_lesson(self, uid, lesson_id):
        lesson = self.lessons.get(lesson_id)
        if not lesson or lesson.owner_id != uid or lesson.state != 'ready':
            raise RecordNotFound()
        self.records.get_project(uid, lesson.project_id)
        return lesson

    def list_lessons(self, uid):
        out = []
        for lesson in self.lessons.values():
            try:
                out.append(self.get_lesson(uid, lesson.id))
            except RecordNotFound:
                pass
        return out

    def delete_lesson(self, uid, lesson_id):
        lesson = self.get_lesson(uid, lesson_id)
        self.lessons[lesson_id] = lesson.model_copy(update={'state': 'deleted'})
        return self.lessons[lesson_id]

    def report(self, uid, lesson_id, reason, note):
        lesson = self.get_lesson(uid, lesson_id)
        key = (uid, lesson_id, reason)
        return self.reports.setdefault(key, LessonReport(id='r' + str(len(self.reports)), owner_id=uid, lesson_id=lesson.id,
                                                         project_id=lesson.project_id, reason=reason, note=note))


class Assets:
    def __init__(self):
        self.signed = []

    def signed_read(self, a):
        self.signed.append(a.object_key)
        return 'https://storage.invalid/' + a.sha256


class Verifier:
    def verify(self, token):
        return Identity(token, token.title())


def lesson_record(uid, pid='p1', job='j' * 64, attempt=1, extra=()):
    prefix = f'users/{uid}/projects/{pid}/lesson/{job}/{attempt}/'
    names = ['lesson.json', 'analysis.json', 'mesh.json', 'video.mp4', 'motion/sequence.json', *extra]
    return LessonRecord(id=job, owner_id=uid, project_id=pid, job_id=job, input_revision=1,
                        source_asset=project(uid, pid).prepared_media.video, title='Chop and spin',
                        skills=[LessonSkill(id='chop', name='Chop')],
                        authoring=LessonAuthoring(model=MODEL, mode='grounded', prompt_sha256='a' * 64, claims_removed=1, calls=8),
                        engine=Engine(model_name='SAM 3D Body', upstream_commit='b' * 40, checkpoint_hash='c' * 64, config_hash='d' * 64),
                        artifacts=[asset(prefix + n, 'application/json') for n in names])


@pytest.fixture
def setup():
    records = Records()
    lessons, assets = Lessons(records), Assets()
    client = TestClient(create_app(records, Verifier(), assets=None, lessons=lessons))
    client.app.dependency_overrides = {}
    for name in ('alice', 'bob'):
        client.post('/api/me/bootstrap', headers=auth(name))
    records.projects['p1'] = project('alice')
    lessons.assets = assets
    return client, records, lessons


def auth(name, key=None):
    return {'Authorization': 'Bearer ' + name, **({'Idempotency-Key': key} if key else {})}


def create(client, name='alice', key=KEY, pid='p1', revision=1):
    return client.post(f'/api/projects/{pid}/lessons', headers=auth(name, key), json={'inputRevision': revision})


def test_consent_is_required_once_and_reported_by_me(setup):
    client, records, _ = setup
    assert client.get('/api/me', headers=auth('alice')).json()['consents']['lessonAi']['accepted'] is False
    refused = create(client)
    assert refused.status_code == 403 and refused.json()['code'] == 'consent_required'
    assert records.jobs == {}
    put = client.put('/api/me/consents/lesson-ai', headers=auth('alice'), json={'accepted': True})
    assert put.status_code == 200 and put.json()['lessonAi']['version'] == 'lesson-ai-v1'
    me = client.get('/api/me', headers=auth('alice')).json()
    assert me['consents']['lessonAi']['accepted'] is True and me['lessonAiConsentVersion'] == 'lesson-ai-v1'


def test_create_is_idempotent_and_returns_the_job_location(setup):
    client, records, _ = setup
    client.put('/api/me/consents/lesson-ai', headers=auth('alice'), json={'accepted': True})
    first = create(client)
    assert first.status_code == 202 and first.headers['Location'] == f"/api/jobs/{first.json()['id']}"
    body = first.json()
    assert body['kind'] == 'buildLesson' and body['profileVersion'] == 'lesson-3d-v1'
    assert 'leaseToken' not in body and 'workerId' not in body
    assert create(client).json()['id'] == body['id'] and len(records.jobs) == 1
    assert body['inputHash'] == lesson_input_hash(records.projects['p1'].prepared_media.video, MODEL)
    assert client.get(f"/api/jobs/{body['id']}", headers=auth('alice')).status_code == 200
    assert client.get(f"/api/jobs/{body['id']}", headers=auth('bob')).status_code == 404


def test_conflicts_limits_and_missing_key(setup):
    client, records, lessons = setup
    client.put('/api/me/consents/lesson-ai', headers=auth('alice'), json={'accepted': True})
    assert client.post('/api/projects/p1/lessons', headers=auth('alice'), json={'inputRevision': 1}).status_code == 422
    assert create(client, revision=2).status_code == 409                                # stale revision
    records.projects['p1'] = project('alice', active_job_id='other-job')
    assert create(client).status_code == 409                                            # another job active
    records.projects['p1'] = project('alice', state='preparing', prepared_media=None, video=None)
    assert create(client).status_code == 409                                            # not ready
    records.projects['p1'] = project('alice', duration=40)
    assert create(client).status_code == 422                                            # clip too long
    records.projects['p1'] = project('alice')
    assert create(client, name='bob').status_code == 404                                # not the owner


def test_daily_quota(setup):
    client, records, lessons = setup
    client.put('/api/me/consents/lesson-ai', headers=auth('alice'), json={'accepted': True})
    for i in range(3):
        records.projects[f'q{i}'] = project('alice', f'q{i}')
        assert create(client, key=f'quota-request-000{i}', pid=f'q{i}').status_code == 202
    records.projects['q9'] = project('alice', 'q9')
    over = create(client, key='quota-request-0009', pid='q9')
    assert over.status_code == 429 and over.json()['code'] == 'lesson_quota'
    assert create(client, key='quota-request-0000', pid='q0').status_code == 202       # a repeat is not a new lesson


def test_reading_assets_reports_and_deletion_are_owner_only(setup):
    client, records, lessons = setup
    assets = Assets()
    client = TestClient(create_app(records, Verifier(), assets=assets, uploads=object(), lessons=lessons))
    lesson = lesson_record('alice', extra=['motion/body-000000.bin.gz'])
    lessons.lessons[lesson.id] = lesson
    listed = client.get('/api/lessons', headers=auth('alice')).json()['items']
    assert [item['id'] for item in listed] == [lesson.id] and listed[0]['aiGenerated'] is True
    assert 'artifacts' not in listed[0] and 'sourceAsset' not in listed[0]
    assert client.get('/api/lessons', headers=auth('bob')).json()['items'] == []
    assert client.get(f'/api/lessons/{lesson.id}', headers=auth('bob')).status_code == 404
    signed = client.post(f'/api/lessons/{lesson.id}/assets', headers=auth('alice')).json()
    assert sorted(signed['assets']) == sorted(['lesson.json', 'analysis.json', 'mesh.json', 'video.mp4',
                                               'motion/sequence.json', 'motion/body-000000.bin.gz'])
    assert client.post(f'/api/lessons/{lesson.id}/assets', headers=auth('bob')).status_code == 404
    report = client.post(f'/api/lessons/{lesson.id}/reports', headers=auth('alice'), json={'reason': 'wrong_move', 'note': 'Not a chop'})
    assert report.status_code == 201
    assert client.post(f'/api/lessons/{lesson.id}/reports', headers=auth('alice'), json={'reason': 'spam'}).status_code == 422
    assert client.post(f'/api/lessons/{lesson.id}/reports', headers=auth('bob'), json={'reason': 'other'}).status_code == 404
    assert client.delete(f'/api/lessons/{lesson.id}', headers=auth('bob')).status_code == 404
    assert client.delete(f'/api/lessons/{lesson.id}', headers=auth('alice')).json()['state'] == 'deleted'
    assert client.get(f'/api/lessons/{lesson.id}', headers=auth('alice')).status_code == 404
    assert client.post(f'/api/lessons/{lesson.id}/assets', headers=auth('alice')).status_code == 404


def test_lessons_disappear_with_their_project(setup):
    client, records, lessons = setup
    lesson = lesson_record('alice')
    lessons.lessons[lesson.id] = lesson
    records.projects['p1'] = records.projects['p1'].model_copy(update={'state': 'deleting'})
    assert client.get('/api/lessons', headers=auth('alice')).json()['items'] == []
    assert client.get(f'/api/lessons/{lesson.id}', headers=auth('alice')).status_code == 404


def test_signed_assets_refuse_paths_from_two_attempts(setup):
    from api.lessons import bundle_paths
    lesson = lesson_record('alice')
    mixed = lesson.model_copy(update={'artifacts': (*lesson.artifacts, asset(
        f'users/alice/projects/p1/lesson/{"j" * 64}/2/extra.json'))})
    with pytest.raises(RecordConflict):
        bundle_paths(mixed)


def test_lesson_record_requires_its_own_private_prefix():
    lesson = lesson_record('alice')
    with pytest.raises(ValueError):
        LessonRecord.model_validate({**lesson.document(), 'artifacts': [
            a.document() | {'objectKey': a.object_key.replace('/lesson/', '/reconstruction/')} for a in lesson.artifacts]})
    with pytest.raises(ValueError):
        LessonRecord.model_validate({**lesson.document(), 'artifacts': [a.document() for a in lesson.artifacts[1:]]})


def test_new_optional_fields_are_not_written_until_set():
    # Older strict readers forbid unknown fields during a rolling upgrade.
    assert 'lessonAiConsentAt' not in UserRecord(id='u', display_name='U').document()
    assert 'latestLessonId' not in project('u').document()
    assert 'lessonId' not in admit(UserRecord(id='u', display_name='U', lesson_ai_consent_at=utc_now(),
                                              lesson_ai_consent_version='lesson-ai-v1'), project('u'), 1, None, 0, True,
                                   job_id='j', model=MODEL, max_seconds=20, daily_limit=3).document()
