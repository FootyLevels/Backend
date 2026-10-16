"""Opt-in isolated Development Firestore proof for circle transactions and cleanup.

No default collections, real playback files, model jobs, public storage or API
runtime are changed. Root's separate proof also verifies real private media bytes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient
from google.cloud.firestore_v1.base_query import FieldFilter
import pytest

from api.cloud import create_app
from footylevels.authentication import AuthenticationRejected, Identity
from footylevels.circles import FirestoreCircles
from footylevels.deletions import FirestoreDeletions
from footylevels.records import UserRecord, utc_now
from footylevels.repositories import RecordConflict, RecordNotFound
from tests.test_circles import ready, Storage
from tests.test_firestore_records import cloud


@pytest.fixture
def circles_cloud(cloud):
    repo, client, uid, other = cloud
    extra = uid + '-visitor'
    for user in [uid, other, extra]:
        repo.bootstrap_user(user, 'Circle fixture')
    circles = FirestoreCircles(client)
    try:
        yield repo, circles, client, uid, other, extra
    finally:
        # Cleanup is exact to fixture owners/circle IDs, including test failures.
        identifiers = []
        for owner in [uid, other, extra]:
            identifiers.extend(s.id for s in client.collection('circles').where(
                filter=FieldFilter('ownerId', '==', owner)).stream())
        for identifier in identifiers:
            for collection in ['circlePosts', 'circleComments', 'circleInvites', 'circleCleanup']:
                for snapshot in client.collection(collection).where(filter=FieldFilter('circleId', '==', identifier)).stream():
                    snapshot.reference.delete()
            client.collection('circles').document(identifier).delete()
        for owner in [uid, other, extra]:
            client.collection('circleMemberships').document(owner).delete()
        client.collection('users').document(extra).delete()
        for snapshot in client.collection('projects').where(filter=FieldFilter('ownerId', '==', extra)).stream():
            snapshot.reference.delete()


def test_real_circle_api_sharing_feedback_and_foreign_owner_fence(circles_cloud):
    repo, circles, client, uid, other, stranger = circles_cloud
    class Verifier:
        def verify(self, token):
            if token in {uid, other, stranger}:
                return Identity(token, 'Fixture')
            raise AuthenticationRejected()
    def headers(user=uid, key=None):
        return {'Authorization': 'Bearer ' + user, **({'Idempotency-Key': key} if key else {})}
    assets = Storage()
    api = TestClient(create_app(repo, Verifier(), circles=circles, assets=assets))
    circle = api.post('/api/circles', headers=headers(), json={'name': 'Private fixture'}).json()
    cid = circle['id']
    code = api.post(f'/api/circles/{cid}/invite', headers=headers()).json()['code']
    assert api.post('/api/circles/join', headers=headers(other), json={'code': code}).json()['memberCount'] == 2
    project = ready(client, uid, uid)
    assert api.get(f'/api/circles/{cid}/posts', headers=headers()).json()['items'] == []
    response = api.post(f'/api/circles/{cid}/posts', headers=headers(key='real-share-idempotent-123'),
        json={'projectId': project.id, 'skill': 'Elastico', 'note': 'Second touch'})
    assert response.status_code == 201, response.text
    post = response.json()
    path = f'/api/circles/{cid}/posts/{post["id"]}'
    assert api.get(path, headers=headers(other)).json() == post
    assert api.get('/api/projects/' + project.id, headers=headers(other)).status_code == 404
    assert api.get(path, headers=headers(stranger)).status_code == 404
    feedback = api.post(path + '/comments', headers=headers(other, 'real-comment-idempotent'),
                       json={'text': 'Move your weight', 'timestamp': 1.5})
    assert feedback.status_code == 201, feedback.text
    assert api.get(path + '/comments', headers=headers()).json()['items'] == [feedback.json()]
    assert api.get(f'/api/circles/{cid}/posts', headers=headers(other)).json()['items'] == [post]
    assert api.post(path + '/assets', headers=headers(other)).status_code == 200
    assert assets.calls == [(project.prepared_media.video, 60)]
    # Reinstantiation demonstrates durable records rather than an API-local cache.
    restarted = FirestoreCircles(client)
    assert restarted.get_post(other, cid, post['id']).id == post['id']
    restarted.remove_member(uid, cid, other)
    assert api.post(path + '/assets', headers=headers(other)).status_code == 404
    assert api.post('/api/circles/join', headers=headers(other), json={'code': code}).status_code == 404
    fresh = restarted.invite(uid, cid)['code']
    restarted.join(other, fresh)
    assert restarted.list_comments(uid, cid, post['id'])[0] == []


def test_real_concurrent_idempotency_join_capacity_and_membership_generation(circles_cloud):
    _, circles, client, uid, other, stranger = circles_cloud
    circle = circles.create_circle(uid, 'Concurrent')
    cid, code = circle['id'], circles.invite(uid, circle['id'])['code']
    with ThreadPoolExecutor(max_workers=4) as pool:
        members = list(pool.map(lambda _: FirestoreCircles(client).join(other, code), range(4)))
    assert all(member['memberCount'] == 2 for member in members)
    circles.MAX_MEMBERS = 2
    with pytest.raises(RecordConflict):
        circles.join(stranger, code)
    project = ready(client, other, other)
    def share(_):
        return FirestoreCircles(client).create_post(other, cid, project.id, 'Elastico', 'Retry', 'parallel-real-share-1234')
    with ThreadPoolExecutor(max_workers=4) as pool:
        posts = list(pool.map(share, range(4)))
    assert len({post.id for post in posts}) == 1
    post = posts[0]
    assert circles.get_circle(uid, cid)['memberCount'] == 2
    assert len(circles.list_posts(uid, cid)[0]) == 1
    circles.remove_member(other, cid, other)
    circles.join(other, code)
    with pytest.raises(RecordNotFound):
        circles.get_post(uid, cid, post.id)
    with pytest.raises(RecordNotFound):
        share(0)


def test_real_account_and_project_tombstones_revoke_and_worker_erases_metadata(circles_cloud):
    _, circles, client, uid, other, stranger = circles_cloud
    circle = circles.create_circle(uid, 'Cleanup')
    cid = circle['id']
    circles.join(other, circles.invite(uid, cid)['code'])
    project = ready(client, other, other)
    post = circles.create_post(other, cid, project.id, 'Skill', 'Private note', 'cleanup-real-share-1234')
    for i in range(3):
        circles.create_comment(uid, cid, post.id, 'Private feedback', None, f'cleanup-real-comment-{i:04}')
    deletions = FirestoreDeletions(client)
    intent = deletions.begin_project(other, project.id)
    with pytest.raises(RecordNotFound):
        circles.get_post(uid, cid, post.id)
    client.collection('deletions').document(intent.id).update({'notBefore': utc_now()-timedelta(seconds=1)})
    class NoObjects:
        def verify_absent(self, asset):
            pass
    assert deletions.process(intent.id, NoObjects()).state == 'completed'
    assert not client.collection('circlePosts').document(post.id).get().exists
    assert not list(client.collection('circleComments').where(filter=FieldFilter('postId', '==', post.id)).stream())
    # Account cleanup removes foreign-circle membership and all personal comments.
    own = circles.create_circle(other, 'Owned by deleted account')
    intent = deletions.begin_account(other)
    with pytest.raises(RecordNotFound):
        circles.get_circle(uid, own['id'])
    client.collection('deletions').document(intent.id).update({'notBefore': utc_now()-timedelta(seconds=1)})
    for _ in range(10):
        if deletions.process(intent.id, NoObjects()).state == 'completed':
            break
    assert client.collection('users').document(other).get().to_dict()['accountState'] == 'deleted'
    assert circles.get_circle(uid, cid)['memberCount'] == 1
    for _ in range(10):
        if circles.reconcile_cleanup():
            break
    assert not client.collection('circleMemberships').document(other).get().exists
    assert not list(client.collection('circleComments').where(filter=FieldFilter('authorId', '==', other)).stream())
    assert not list(client.collection('circlePosts').where(filter=FieldFilter('authorId', '==', other)).stream())
