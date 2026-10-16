"""Opt-in real isolated Firestore deletion/retry/account fences; no shared data."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from google.cloud.firestore_v1.base_query import FieldFilter
from tests.test_firestore_records import cloud, media_result, preparing_project, media_jobs_fixture
from footylevels.assets import StorageUnavailable
from footylevels.deletions import FirestoreDeletions, deletion_id
from footylevels.records import utc_now
from footylevels.repositories import RecordConflict, RecordNotFound


class MemoryStorage:
    def __init__(self, assets):
        self.keys = {a.object_key for a in assets}
        self.fail = False
    def delete_exact(self, asset):
        if self.fail:
            self.fail = False
            raise StorageUnavailable()
        self.keys.discard(asset.object_key)
    def verify_absent(self, asset):
        if asset.object_key in self.keys:
            raise StorageUnavailable()


def fixture(cloud, prepared=True):
    repo, client, uid, other = cloud
    repo.bootstrap_user(uid, 'Player')
    jobs = media_jobs_fixture(client, uid)
    project, upload = preparing_project(repo, jobs, uid)
    assets = [upload.asset]
    if prepared:
        claim = jobs.claim(project.id, 'deletion-fixture')
        media = media_result(claim)
        assets += [media.video, *[p.asset for p in media.previews]]
        jobs.reserve_outputs(claim, assets[1:])
        project = jobs.publish(claim, media)
    class ScopedDeletions(FirestoreDeletions):
        def _ledgers(self, owner):
            return self._ref('storageUsage', uid), self._ref('storageUsage', owner+'-usage')
    return repo, client, uid, other, project, upload, ScopedDeletions(client), MemoryStorage(assets)


def mature(client, intent, project, uid):
    old = utc_now()-timedelta(days=4)
    client.collection('deletions').document(intent.id).update({'notBefore':old})
    for collection, field in [('uploads','cleanupAfter'),('assetReservations','createdAt')]:
        for s in client.collection(collection).where(filter=FieldFilter('projectId','==',project.id)).stream():
            s.reference.update({field:old, **({'expiresAt':old-timedelta(days=2)} if collection=='uploads' else {})})


def finish(deletions, intent, storage):
    for _ in range(20):
        result=deletions.process(intent.id,storage)
        if result.state=='completed':return result
    pytest.fail('Bounded cleanup did not finish')


def test_real_firestore_quarantine_progress_owner_fence_and_retry(cloud):
    repo, client, uid, other, project, upload, deletions, store=fixture(cloud)
    with pytest.raises(RecordNotFound):deletions.begin_project(other,project.id)
    intent=deletions.begin_project(uid,project.id)
    assert intent==deletions.begin_project(uid,project.id)
    with pytest.raises(RecordNotFound):repo.get_project(uid,project.id)
    with pytest.raises(RecordNotFound):deletions.progress(other,intent.id)
    assert deletions.process(intent.id,store).state=='pending' and len(store.keys)==3
    client.collection('deletions').document(intent.id).update({'notBefore':utc_now()-timedelta(seconds=1)})
    waiting=deletions.process(intent.id,store)
    assert waiting.not_before>=upload.cleanup_after and len(store.keys)==3
    mature(client,intent,project,uid)
    store.fail=True
    assert deletions.process(intent.id,store).last_error=='cleanup_unavailable'
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes']==42
    result=finish(deletions,intent,store)
    assert result.state=='completed' and result.completed_at and not store.keys
    assert deletions.process(intent.id,store)==result
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes']==0
    erased=client.collection('projects').document(project.id).get().to_dict()
    assert erased['state']=='deleted' and erased['title']=='Deleted recording' and erased['video'] is None
    assert erased['preparedMedia'] is None
    assert not list(client.collection('jobs').where(filter=FieldFilter('projectId','==',project.id)).stream())


def test_cleanup_verifies_absence_before_quota_release(cloud):
    repo, client, uid, other, project, upload, deletions, store=fixture(cloud,False)
    intent=deletions.begin_project(uid,project.id);mature(client,intent,project,uid)
    with patch.object(store,'delete_exact',lambda asset:None):
        assert deletions.process(intent.id,store).last_error=='cleanup_unavailable'
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes']==12
    finish(deletions,intent,store)
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes']==0


def test_idempotency_replay_and_nested_metadata_remain_deleted(cloud):
    repo, client, uid, other=cloud
    repo.bootstrap_user(uid,'Player')
    p=repo.create_project(uid,'Private title','gallery','opaque-project-id-123')
    d=FirestoreDeletions(client);intent=d.begin_project(uid,p.id)
    nested=client.collection('projects').document(p.id).collection('selectedFrames').document('f')
    nested.set({'private':'frame'});nested.collection('children').document('c').set({'private':'data'})
    client.collection('deletions').document(intent.id).update({'notBefore':utc_now()-timedelta(seconds=1)})
    finish(d,intent,MemoryStorage([]))
    assert not nested.get().exists and not list(nested.collection('children').stream())
    with pytest.raises(RecordNotFound):repo.create_project(uid,'Private title','gallery','opaque-project-id-123')
    assert d.begin_project(uid,p.id).state=='completed'


def test_account_data_cleanup_blocks_new_projects_and_bootstrap(cloud):
    repo, client, uid, other=cloud
    repo.bootstrap_user(uid,'Player')
    p=repo.create_project(uid,'Account recording','gallery')
    d=FirestoreDeletions(client);intent=d.begin_account(uid)
    with pytest.raises(RecordConflict):repo.create_project(uid,'New','gallery')
    with pytest.raises(RecordConflict):repo.bootstrap_user(uid,'Restore')
    client.collection('deletions').document(intent.id).update({'notBefore':utc_now()-timedelta(seconds=1)})
    assert d.process(intent.id,MemoryStorage([])).state=='pending'
    child=d.progress(uid,deletion_id(uid,p.id));mature(client,child,p,uid)
    finish(d,child,MemoryStorage([]));done=finish(d,intent,MemoryStorage([]))
    assert done.state=='completed' and repo.get_user(uid).account_state=='deleted'
    assert repo.get_user(uid).display_name=='Deleted account'
    assert d.begin_account(uid)==done
    with pytest.raises(RecordConflict):repo.bootstrap_user(uid,'Restore')


def test_generated_results_comparisons_and_subcollections_are_removed(cloud):
    from footylevels.records import ComparisonRecord
    from tests.test_firestore_records import result_for
    repo, client, uid, other, project, upload, d, store=fixture(cloud)
    job=repo.enqueue_reconstruction(uid,project.id,project.input_revision,'reconstructMotion','e'*64,'fixture-v1')
    result=result_for(project,job)
    client.collection('reconstructions').document(result.id).set(result.document())
    for asset in result.artifacts:
        d.reserve_artifact(uid,project.id,asset);store.keys.add(asset.object_key)
    comparison=ComparisonRecord(id=uid,owner_id=uid,left_reconstruction_id=result.id,right_type='reconstruction',right_id=result.id,mode='overlay')
    repo.create_comparison(comparison,uid)
    nested=client.collection('reconstructions').document(result.id).collection('frames').document('f')
    nested.set({'private':'frame'})
    intent=d.begin_project(uid,project.id);mature(client,intent,project,uid)
    with pytest.raises(RecordNotFound):repo.create_comparison(comparison.model_copy(update={'id':uid+'-late'}),uid)
    finish(d,intent,store)
    assert not client.collection('comparisons').document(uid).get().exists
    assert not client.collection('reconstructions').document(result.id).get().exists
    assert not nested.get().exists and not store.keys
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes']==0


def test_foreign_asset_path_blocks_cleanup_without_deleting_other_data(cloud):
    repo, client, uid, other, p, upload, d, store=fixture(cloud,False)
    intent=d.begin_project(uid,p.id);mature(client,intent,p,uid)
    ref=client.collection('uploads').document(upload.id)
    original=ref.get().to_dict()['asset']
    ref.update({'asset':{**original,'objectKey':f'users/{other}/projects/{p.id}/source/foreign.mp4'}})
    assert d.process(intent.id,store).last_error=='reconciliation_required'
    assert store.keys=={upload.asset.object_key}
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes']==12
    ref.update({'asset':original});finish(d,intent,store)
