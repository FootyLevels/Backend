"""Offline security/behavior proofs execute the real circle repository logic.

The local store serializes transactions and forbids reads after queued writes.
Real Firestore retries, conflicts and indexes are tested separately when opted in.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from functools import cmp_to_key
import hashlib
import threading
import uuid

from fastapi.testclient import TestClient
import pytest

from api.cloud import create_app
from footylevels.authentication import AuthenticationRejected, Identity
from footylevels.circles import FirestoreCircles
from footylevels.records import AssetRef, MediaPreview, PreparedMedia, ProjectRecord, UserRecord, utc_now
from footylevels.repositories import FirestoreRecords, RecordConflict, RecordNotFound


class Snapshot:
    def __init__(self, ref, data):
        self.reference, self.id = ref, ref.id
        self._data = deepcopy(data)
        self.exists = data is not None

    def to_dict(self):
        return deepcopy(self._data)


class Ref:
    def __init__(self, client, collection, identifier):
        self.client, self.collection_name, self.id = client, collection, identifier
        self.path = (collection, identifier)

    def get(self, transaction=None):
        if transaction:
            assert not transaction.writes, 'Firestore forbids reads after writes'
        return Snapshot(self, self.client.data.get(self.path))

    def set(self, value):
        self.client.data[self.path] = deepcopy(value)

    def update(self, value):
        self.client.data[self.path].update(deepcopy(value))

    def delete(self):
        self.client.data.pop(self.path, None)


class Query:
    def __init__(self, client, name, filters=(), orders=(), count=None, after=None):
        self.client, self.name = client, name
        self.filters, self.orders, self.count, self.after = filters, orders, count, after

    def document(self, identifier=None):
        return Ref(self.client, self.name, identifier or uuid.uuid4().hex)

    def where(self, *, filter):
        return Query(self.client, self.name, (*self.filters, filter), self.orders, self.count, self.after)

    def order_by(self, field, direction='ASCENDING'):
        return Query(self.client, self.name, self.filters, (*self.orders, (field, direction)), self.count, self.after)

    def limit(self, count):
        return Query(self.client, self.name, self.filters, self.orders, count, self.after)

    def start_after(self, after):
        return Query(self.client, self.name, self.filters, self.orders, self.count, after)

    def stream(self):
        rows = [Snapshot(Ref(self.client, name, uid), raw) for (name, uid), raw in list(self.client.data.items()) if name == self.name]
        for filter in self.filters:
            assert filter.op_string == '=='
            rows = [row for row in rows if row.to_dict().get(filter.field_path) == filter.value]
        orders = self.orders or (('__name__', 'ASCENDING'),)
        def values(row):
            return [row.id if field == '__name__' else row.to_dict()[field] for field, _ in orders]
        def compare(left, right):
            for a, b, (_, direction) in zip(left, right, orders):
                result = (a > b) - (a < b)
                if result:
                    return -result if direction == 'DESCENDING' else result
            return 0
        rows.sort(key=cmp_to_key(lambda a, b: compare(values(a), values(b))))
        if self.after:
            boundary = [self.after[field].id if field == '__name__' else self.after[field] for field, _ in orders]
            rows = [row for row in rows if compare(values(row), boundary) > 0]
        return iter(rows[:self.count] if self.count is not None else rows)


class MemoryClient:
    def __init__(self):
        self.data, self.lock = {}, threading.RLock()

    def collection(self, name):
        return Query(self, name)


class Transaction:
    def __init__(self):
        self.writes = []

    def create(self, ref, data):
        assert not ref.get().exists
        self.writes.append(lambda: ref.set(data))

    def set(self, ref, data):
        self.writes.append(lambda: ref.set(data))

    def update(self, ref, data):
        self.writes.append(lambda: ref.update(data))

    def delete(self, ref):
        self.writes.append(ref.delete)


class MemoryCircles(FirestoreCircles):
    def _run(self, operation):
        with self.client.lock:
            transaction = Transaction()
            result = operation(transaction)
            for write in transaction.writes:
                write()
            return result


class Verifier:
    def verify(self, token):
        if token in {'alice', 'bob', 'charlie'}:
            return Identity(token, token.title())
        raise AuthenticationRejected()


class Storage:
    def __init__(self):
        self.calls = []
        self.during_sign = None

    def signed_read(self, asset, seconds):
        self.calls.append((asset, seconds))
        if self.during_sign:
            self.during_sign()
        return 'https://storage.test/private-short-lived-capability'


def headers(uid='alice', key=None):
    return {'Authorization': 'Bearer ' + uid, **({'Idempotency-Key': key} if key else {})}


def ready(client, uid='alice', identifier='ready-project', duration=2):
    source = AssetRef(bucket='private-media', object_key=f'users/{uid}/projects/{identifier}/source/recording.mp4',
                      sha256='a'*64, byte_size=100, content_type='video/mp4')
    video = source.model_copy(update={'object_key': f'users/{uid}/projects/{identifier}/media/' + 'b'*64 + '/1/video.mp4', 'sha256': 'b'*64})
    preview = source.model_copy(update={'object_key': f'users/{uid}/projects/{identifier}/media/' + 'b'*64 + '/1/preview-00.jpg', 'content_type': 'image/jpeg'})
    media = PreparedMedia(video=video, width=320, height=240, duration=duration, frame_count=int(duration*30),
                          previews=(MediaPreview(asset=preview, timestamp=0, frame_index=0),))
    project = ProjectRecord(id=identifier, owner_id=uid, title='Practice', source='gallery', state='ready', video=source, prepared_media=media)
    client.collection('projects').document(identifier).set(project.document())
    return project


@pytest.fixture
def setup():
    store = MemoryClient()
    for uid in ['alice', 'bob', 'charlie']:
        store.collection('users').document(uid).set(UserRecord(id=uid, display_name=uid.title()).document())
    circles, assets = MemoryCircles(store), Storage()
    client = TestClient(create_app(FirestoreRecords(store), Verifier(), circles=circles, assets=assets, uploads=object()))
    circle = circles.create_circle('alice', ' Saturday practice ', ' Friends ')
    invite = circles.invite('alice', circle['id'])
    circles.join('bob', invite['code'])
    project = ready(store)
    return client, circles, store, assets, circle['id'], invite['code'], project


def share(setup, uid='alice', key='share-attempt-key-1234', **extra):
    client, _, _, _, cid, _, project = setup
    response = client.post(f'/api/circles/{cid}/posts', headers=headers(uid, key),
        json={'projectId': project.id, 'skill': 'Elastico', 'note': 'Help with the second touch', **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_circle_contract_explicit_share_and_private_membership(setup):
    client, circles, store, _, cid, code, _ = setup
    data = client.get('/api/circles/' + cid, headers=headers('bob')).json()
    assert data['name'] == 'Saturday practice' and data['description'] == 'Friends'
    assert data['memberCount'] == 2
    assert {m['uid'] for m in data['members']} == {'alice', 'bob'}
    assert set(data) == {'id', 'name', 'description', 'ownerId', 'createdAt', 'memberCount', 'members'}
    assert all(set(m) == {'uid', 'displayName', 'joinedAt'} for m in data['members'])
    assert code not in str(store.data) and 'inviteHash' not in str(data) and 'generation' not in str(data)
    assert client.get(f'/api/circles/{cid}/posts', headers=headers()).json() == {'items': [], 'nextCursor': None}
    assert client.get('/api/circles', headers=headers('charlie')).json() == {'items': []}
    for method, path, body in [('get', f'/api/circles/{cid}', None), ('post', f'/api/circles/{cid}/invite', None),
        ('get', f'/api/circles/{cid}/posts', None), ('delete', f'/api/circles/{cid}', None)]:
        response = client.request(method, path, headers=headers('charlie'), json=body)
        assert response.status_code == 404 and response.json() == {'detail': 'Resource not found'}
    assert client.post('/api/circles/join', headers=headers('bob'), json={'code': code}).json()['memberCount'] == 2
    assert client.post(f'/api/circles/{cid}/invite', headers=headers('bob')).status_code == 404
    assert client.delete(f'/api/circles/{cid}/members/alice', headers=headers()).status_code == 409


def test_invite_rotation_expiry_and_revocation(setup):
    client, circles, store, _, cid, code, _ = setup
    fresh = circles.invite('alice', cid)
    assert client.post('/api/circles/join', headers=headers('charlie'), json={'code': code}).status_code == 404
    digest = hashlib.sha256(fresh['code'].encode()).hexdigest()
    store.collection('circleInvites').document(digest).update({'expiresAt': utc_now()-timedelta(seconds=1)})
    assert client.post('/api/circles/join', headers=headers('charlie'), json={'code': fresh['code']}).status_code == 404
    fresh = circles.invite('alice', cid)
    circles.delete_circle('alice', cid)
    assert client.post('/api/circles/join', headers=headers('charlie'), json={'code': fresh['code']}).status_code == 404
    assert client.get('/api/circles', headers=headers()).json()['items'] == []


@pytest.mark.parametrize('body', [{'name': ' '}, {'name': 'x'*81}, {'name': 'Okay', 'description': 'x'*241},
    {'name': 'Okay', 'ownerId': 'bob'}, {'name': 123}])
def test_circle_input_bounds_and_identity_injection(setup, body):
    assert setup[0].post('/api/circles', headers=headers(), json=body).status_code == 422


@pytest.mark.parametrize('extra', [{'skill': ' '}, {'skill': 'x'*61}, {'note': 'x'*501}, {'authorId': 'bob'},
    {'inputRevision': 2}, {'video': {}}, {'previousPostId': '../other'}])
def test_share_rejects_untrusted_or_unbounded_fields(setup, extra):
    client, _, _, _, cid, _, project = setup
    response = client.post(f'/api/circles/{cid}/posts', headers=headers(key='share-attempt-key-1234'),
        json={'projectId': project.id, 'skill': 'Elastico', **extra})
    assert response.status_code == 422


def test_share_pins_prepared_source_idempotent_and_own_projects_only(setup):
    client, circles, store, _, cid, _, project = setup
    post = share(setup)
    assert post['video']['asset'] == project.prepared_media.video.model_dump(mode='json', by_alias=True)
    assert post['authorId'] == 'alice' and post['projectTitle'] == 'Practice'
    assert not {'state', 'inputRevision', 'authorMembership', 'creationHash'} & post.keys()
    assert share(setup)['id'] == post['id']
    assert len(list(store.collection('circlePosts').stream())) == 1
    conflict = client.post(f'/api/circles/{cid}/posts', headers=headers(key='share-attempt-key-1234'),
        json={'projectId': project.id, 'skill': 'Changed'})
    assert conflict.status_code == 409
    assert client.post(f'/api/circles/{cid}/posts', headers=headers('bob', 'share-attempt-key-1234'),
        json={'projectId': project.id, 'skill': 'Elastico'}).status_code == 404
    for key in [None, 'short', 'bad/key-123456789012', 'x'*81]:
        assert client.post(f'/api/circles/{cid}/posts', headers=headers(key=key),
            json={'projectId': project.id, 'skill': 'Elastico'}).status_code == 422
    client.delete(f'/api/circles/{cid}/posts/{post["id"]}', headers=headers())
    assert client.get(f'/api/circles/{cid}/posts/{post["id"]}', headers=headers('bob')).status_code == 404
    assert client.post(f'/api/circles/{cid}/posts', headers=headers(key='share-attempt-key-1234'),
        json={'projectId': project.id, 'skill': 'Elastico', 'note': 'Help with the second touch'}).status_code == 404


def test_source_must_be_ready_prepared_and_within_its_owned_media_path(setup):
    client, _, store, _, cid, _, project = setup
    for change, status in [({'state': 'preparing'}, 409), ({'preparedMedia': None}, 409),
                           ({'state': 'deleting'}, 404), ({'ownerId': 'bob'}, 404)]:
        store.collection('projects').document(project.id).set({**project.document(), **change})
        assert client.post(f'/api/circles/{cid}/posts', headers=headers(key='prepared-requirement-1234'),
            json={'projectId': project.id, 'skill': 'Elastico'}).status_code == status
    invalid = project.document()
    invalid['preparedMedia']['video']['objectKey'] = 'users/bob/foreign.mp4'
    store.collection('projects').document(project.id).set(invalid)
    assert client.post(f'/api/circles/{cid}/posts', headers=headers(key='prepared-requirement-1234'),
        json={'projectId': project.id, 'skill': 'Elastico'}).status_code == 409


def test_timestamped_feedback_retry_and_moderation(setup):
    client, _, _, _, cid, _, project = setup
    post = share(setup)
    path = f'/api/circles/{cid}/posts/{post["id"]}/comments'
    body = {'text': ' Shift your weight here ', 'timestamp': 1.5}
    comment = client.post(path, headers=headers('bob', 'comment-idempotent-1234'), json=body)
    assert comment.status_code == 201
    data = comment.json()
    assert data['text'] == 'Shift your weight here' and data['timestamp'] == 1.5 and data['authorId'] == 'bob'
    assert set(data) == {'id', 'authorId', 'authorName', 'text', 'timestamp', 'createdAt'}
    assert client.post(path, headers=headers('bob', 'comment-idempotent-1234'), json=body).json()['id'] == data['id']
    assert client.post(path, headers=headers('bob', 'comment-idempotent-1234'), json={'text': 'Different'}).status_code == 409
    assert client.get(path, headers=headers()).json()['items'] == [data]
    assert client.delete(path + '/' + data['id'], headers=headers('charlie')).status_code == 404
    assert client.delete(path + '/' + data['id'], headers=headers()).status_code == 200
    assert client.get(path, headers=headers('bob')).json()['items'] == []
    assert client.post(path, headers=headers('bob', 'comment-idempotent-1234'), json=body).status_code == 404


@pytest.mark.parametrize('body', [{'text': ' '}, {'text': 'x'*501}, {'text': 'Okay', 'timestamp': -1},
    {'text': 'Okay', 'timestamp': 2}, {'text': 'Okay', 'timestamp': True}, {'text': 'Okay', 'timestamp': '1'},
    {'text': 'Okay', 'timestamp': float('inf')}, {'text': 'Okay', 'authorId': 'alice'}])
def test_comment_bounds(setup, body):
    client, _, _, _, cid, _, _ = setup
    post = share(setup)
    # Raw JSON allows exercising non-finite values rejected by strict schemas.
    import json
    response = client.post(f'/api/circles/{cid}/posts/{post["id"]}/comments',
        headers={**headers('bob', 'comment-validation-123'), 'Content-Type': 'application/json'}, content=json.dumps(body))
    assert response.status_code == 422


def test_previous_attempt_must_be_visible_same_circle_and_same_author(setup):
    client, circles, store, _, cid, code, project = setup
    first = share(setup)
    retry = share(setup, key='retry-attempt-key-12345', previousPostId=first['id'])
    assert retry['previousPostId'] == first['id']
    second_project = ready(store, 'bob', 'bob-project')
    assert client.post(f'/api/circles/{cid}/posts', headers=headers('bob', 'foreign-retry-key-12345'),
        json={'projectId': second_project.id, 'skill': 'Elastico', 'previousPostId': first['id']}).status_code == 404
    other = circles.create_circle('alice', 'Other')
    assert client.post(f'/api/circles/{other["id"]}/posts', headers=headers('alice', 'foreign-circle-key-1234'),
        json={'projectId': project.id, 'skill': 'Elastico', 'previousPostId': first['id']}).status_code == 404


@pytest.mark.parametrize('revocation', ['author_leaves', 'owner_removes_author', 'project_deleted', 'revision_changed',
    'media_changed', 'author_restricted', 'owner_deleted', 'post_removed', 'circle_deleted'])
def test_all_revocation_fences_cover_posts_comments_and_future_grants(setup, revocation):
    client, circles, store, assets, cid, code, _ = setup
    project = ready(store, 'bob', 'bobs-attempt')
    post = circles.create_post('bob', cid, project.id, 'Elastico', '', 'bob-attempt-key-12345')
    comment = circles.create_comment('alice', cid, post.id, 'Feedback', 0, 'comment-key-12345678')
    if revocation == 'author_leaves':
        circles.remove_member('bob', cid, 'bob')
        circles.join('bob', code)  # A new membership must not revive old posts.
    elif revocation == 'owner_removes_author':
        circles.remove_member('alice', cid, 'bob')
    elif revocation == 'project_deleted':
        store.collection('projects').document(project.id).update({'state': 'deleting'})
    elif revocation == 'revision_changed':
        store.collection('projects').document(project.id).update({'inputRevision': 2})
    elif revocation == 'media_changed':
        raw = project.document()['preparedMedia']; raw['video']['sha256'] = 'c'*64
        store.collection('projects').document(project.id).update({'preparedMedia': raw})
    elif revocation == 'author_restricted':
        store.collection('users').document('bob').update({'accountState': 'restricted'})
    elif revocation == 'owner_deleted':
        store.collection('users').document('alice').update({'accountState': 'deleting'})
    elif revocation == 'post_removed':
        circles.remove_post('bob', cid, post.id)
    else:
        circles.delete_circle('alice', cid)
    # Charlie joins neither; use Alice unless her own active_identity is revoked.
    h = headers('bob' if revocation == 'owner_deleted' else 'alice')
    prefix = f'/api/circles/{cid}/posts/{post.id}'
    for method, suffix in [('get', ''), ('get', '/comments'), ('post', '/assets')]:
        assert client.request(method, prefix + suffix, headers=h).status_code == 404
    assert client.post(prefix + '/comments', headers={**h, 'Idempotency-Key': 'new-comment-key-12345'}, json={'text': 'Late'}).status_code == 404
    assert assets.calls == []


def test_member_leaving_revokes_their_comments_across_rejoin(setup):
    _, circles, _, _, cid, code, _ = setup
    post = share(setup)
    comment = circles.create_comment('bob', cid, post['id'], 'Comment', 0, 'comment-key-12345678')
    circles.remove_member('bob', cid, 'bob'); circles.join('bob', code)
    assert circles.list_comments('alice', cid, post['id'])[0] == []
    with pytest.raises(RecordNotFound):
        circles.create_comment('bob', cid, post['id'], 'Comment', 0, 'comment-key-12345678')


def test_shared_grant_short_lived_rechecked_and_never_persisted(setup):
    client, circles, store, assets, cid, _, project = setup
    post = share(setup)
    path = f'/api/circles/{cid}/posts/{post["id"]}/assets'
    grant = client.post(path, headers=headers('bob'))
    assert grant.status_code == 200
    assert set(grant.json()['assets']) == {'video'}
    assert assets.calls == [(project.prepared_media.video, 60)]
    assert 'private-short-lived-capability' not in str(store.data)
    assert grant.headers['Cache-Control'] == 'no-store' and grant.headers['Vary'] == 'Authorization'
    assets.during_sign = lambda: circles.remove_member('alice', cid, 'bob')
    denied = client.post(path, headers=headers('bob'))
    assert denied.status_code == 404 and 'https://' not in denied.text
    assert len(assets.calls) == 2  # Minted capability withheld; cannot revoke bearer bytes already read.


def test_pagination_does_not_lose_hidden_rows_or_duplicate_pages(setup):
    _, circles, store, _, cid, _, project = setup
    circles.SCAN = 3
    posts = [circles.create_post('alice', cid, project.id, 'Skill', str(i), f'pagination-attempt-{i:04}') for i in range(8)]
    for post in posts[4:]:
        circles.remove_post('alice', cid, post.id)
    items, cursor = circles.list_posts('bob', cid, limit=2)
    assert items == [] and cursor  # Bounded scan returns resumable progress across hidden rows.
    seen = []
    while cursor:
        items, cursor = circles.list_posts('bob', cid, limit=2, cursor=cursor)
        seen.extend(p.id for p in items)
    assert seen == [p.id for p in reversed(posts[:4])]
    first, cursor = circles.list_posts('alice', cid, limit=1)
    assert cursor
    for user, scope, bad in [('bob', cid, cursor), ('alice', cid, 'invalid'), ('alice', 'other', cursor)]:
        with pytest.raises((ValueError, RecordNotFound)):
            circles.list_posts(user, scope, 1, bad)


def test_comment_pagination_and_hidden_author_no_page_loss(setup):
    _, circles, _, _, cid, code, _ = setup
    post = share(setup)
    comments = [circles.create_comment('bob' if i%2 else 'alice', cid, post['id'], str(i), 0,
        f'pagination-comment-{i:04}') for i in range(7)]
    circles.remove_member('bob', cid, 'bob')
    cursor, seen = None, []
    while True:
        items, cursor = circles.list_comments('alice', cid, post['id'], 2, cursor)
        seen.extend(c.id for c in items)
        if not cursor:
            break
    assert seen == [c.id for c in reversed(comments) if c.author_id == 'alice']


def test_transactional_caps_and_concurrent_duplicate_share(setup):
    _, circles, store, _, cid, _, project = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        posts = list(pool.map(lambda _: circles.create_post('alice', cid, project.id, 'Skill', '', 'concurrent-share-1234'), range(8)))
    assert len({p.id for p in posts}) == 1
    circles.MAX_CIRCLES = 2
    circles.create_circle('alice', 'Second')
    with pytest.raises(RecordConflict):
        circles.create_circle('alice', 'Third')
    circles.MAX_MEMBERS = 2
    with pytest.raises(RecordConflict):
        circles.join('charlie', circles.invite('alice', cid)['code'])
    circles.delete_circle('alice', cid)
    assert circles.create_circle('alice', 'Replacement')['memberCount'] == 1


def test_project_account_circle_cleanup_is_bounded_and_erases_shared_metadata(setup):
    _, circles, store, _, cid, code, project = setup
    post = share(setup)
    for i in range(5):
        circles.create_comment('bob', cid, post['id'], 'Private feedback', None, f'cleanup-comment-{i:04}')
    with pytest.raises(RecordConflict):
        circles.cleanup_project('alice', project.id)
    store.collection('projects').document(project.id).update({'state': 'deleting'})
    assert circles.cleanup_project('alice', project.id, 2) is False
    assert len(list(store.collection('circleComments').stream())) == 3
    for _ in range(10):
        if circles.cleanup_project('alice', project.id, 2):
            break
    assert not list(store.collection('circlePosts').stream()) and not list(store.collection('circleComments').stream())
    store.collection('users').document('bob').update({'accountState': 'deleting'})
    for _ in range(10):
        if circles.cleanup_account('bob', 2):
            break
    assert not store.collection('circleMemberships').document('bob').get().exists
    assert circles.get_circle('alice', cid)['memberCount'] == 1
    ready(store)
    post = circles.create_post('alice', cid, project.id, 'Skill', 'Private note', 'new-cleanup-post-1234')
    circles.create_comment('alice', cid, post.id, 'Feedback', None, 'new-cleanup-comment-1234')
    circles.delete_circle('alice', cid)
    for _ in range(10):
        if circles.reconcile_cleanup(2):
            break
    assert not list(store.collection('circlePosts').stream()) and not list(store.collection('circleComments').stream())
    assert not list(store.collection('circleCleanup').stream()) and not list(store.collection('circleInvites').stream())
    assert store.collection('projects').document(project.id).get().to_dict()['state'] == 'ready'


def test_authentication_and_account_state_protect_every_community_route(setup):
    client, _, store, _, cid, _, _ = setup
    for path in ['/api/circles', '/api/circles/'+cid, f'/api/circles/{cid}/posts']:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={'Authorization': 'Bearer expired'}).status_code == 401
    store.collection('users').document('alice').update({'accountState': 'restricted'})
    assert client.get('/api/circles', headers=headers()).status_code == 403
    assert client.post('/api/circles', headers=headers(), json={'name': 'Blocked'}).status_code == 403


def test_owner_kick_invalidates_known_invite_atomically(setup):
    client, circles, store, _, cid, old_code, _ = setup
    assert client.delete(f'/api/circles/{cid}/members/bob', headers=headers()).status_code == 200
    assert circles.get_circle('alice', cid)['memberCount'] == 1
    for user in ['bob', 'charlie']:
        assert client.post('/api/circles/join', headers=headers(user), json={'code': old_code}).status_code == 404
    fresh = circles.invite('alice', cid)['code']
    assert circles.join('bob', fresh)['memberCount'] == 2
    before = set(store.data)
    assert client.delete(f'/api/circles/{cid}/members/unknown', headers=headers()).status_code == 404
    assert set(store.data) == before  # Unknown member IDs cannot create arbitrary ledgers.


def test_lifetime_caps_are_transactional_replay_safe_and_do_not_reopen_after_removal(setup):
    _, circles, store, _, cid, _, project = setup
    circles.MAX_POSTS = circles.MAX_COMMENTS = 2
    first = circles.create_post('alice', cid, project.id, 'Skill', '', 'capped-first-post-123')
    second = circles.create_post('alice', cid, project.id, 'Skill', '', 'capped-next-post-1234')
    assert circles.create_post('alice', cid, project.id, 'Skill', '', 'capped-first-post-123') == first
    with pytest.raises(RecordConflict):
        circles.create_post('alice', cid, project.id, 'Skill', '', 'capped-third-post-123')
    first_comment = circles.create_comment('bob', cid, first.id, 'First', 0, 'capped-comment-first')
    circles.create_comment('bob', cid, first.id, 'Second', 0, 'capped-comment-second')
    assert circles.create_comment('bob', cid, first.id, 'First', 0, 'capped-comment-first') == first_comment
    circles.remove_comment('bob', cid, first.id, first_comment.id)
    with pytest.raises(RecordConflict):
        circles.create_comment('alice', cid, first.id, 'Third', 0, 'capped-comment-third')
    circles.remove_post('alice', cid, second.id)
    with pytest.raises(RecordConflict):
        circles.create_post('alice', cid, project.id, 'Skill', '', 'capped-third-post-123')
    assert store.collection('circles').document(cid).get().to_dict()['postCount'] == 2
    assert store.collection('circlePosts').document(first.id).get().to_dict()['commentCount'] == 2


def test_grant_rechecks_project_and_owner_and_tolerates_new_feedback(setup):
    client, circles, store, assets, cid, _, project = setup
    post = share(setup)
    path = f'/api/circles/{cid}/posts/{post["id"]}/assets'
    assets.during_sign = lambda: circles.create_comment('bob', cid, post['id'], 'New feedback', 0, 'grant-feedback-key-123')
    assert client.post(path, headers=headers('bob')).status_code == 200
    assets.during_sign = lambda: store.collection('projects').document(project.id).update({'state': 'deleting'})
    assert client.post(path, headers=headers('bob')).status_code == 404
    store.collection('projects').document(project.id).set(project.document())
    assets.during_sign = lambda: store.collection('users').document('alice').update({'accountState': 'restricted'})
    assert client.post(path, headers=headers('bob')).status_code == 404
