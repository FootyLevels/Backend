"""Opt-in integration tests against Development; all fixture records are removed.

No cloud model/media is generated. Credentials must be the scoped development API
identity, never implicit personal ADC. Default test runs skip cloud writes.
Each invocation uses isolated collection names so a live dispatcher cannot claim
synthetic metadata jobs. The actual default-collection dispatcher is tested by the
separate opted-in GPU/storage probes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from footylevels.records import (AssetRef, ComparisonRecord, Engine, PlaybackContract,
                                ReconstructionRecord, ReferenceRecord, utc_now)


@pytest.fixture
def cloud():
    project = os.environ.get("FOOTYLEVELS_FIRESTORE_TEST_PROJECT")
    if not project:
        pytest.skip("Set the development project and scoped key to run real Firestore tests")
    if project != "footylevels-dev-20261009":
        pytest.fail("Integration writes are restricted to the FootyLevels development project")
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
    from google.oauth2 import service_account
    from footylevels.repositories import FirestoreRecords
    credentials = service_account.Credentials.from_service_account_file(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    assert credentials.service_account_email == f"footylevels-api-dev@{project}.iam.gserviceaccount.com"
    real_client = firestore.Client(project=project, credentials=credentials)
    class IsolatedClient:
        def __init__(self):
            self.prefix = 'integration-'

        def collection(self, name):
            return real_client.collection(self.prefix + name)

        def __getattr__(self, name):
            return getattr(real_client, name)

    client = IsolatedClient()
    uid = "fp51-" + uuid.uuid4().hex
    other = uid + "-other"
    repo = FirestoreRecords(client)
    try:
        yield repo, client, uid, other
    finally:
        # Only this invocation's random fixture owners and exact reference ID.
        for owner in [uid, other]:
            for collection in ["comparisons", "reconstructions", "jobs", "projects", "uploads", "assetReservations"]:
                for snapshot in client.collection(collection).where(filter=FieldFilter("ownerId", "==", owner)).stream():
                    snapshot.reference.delete()
            client.collection("users").document(owner).delete()
            client.collection("storageUsage").document(owner).delete()
            client.collection("storageUsage").document(owner + "-usage").delete()
        client.collection("references").document(uid).delete()
        client.collection("runtimeControls").document(uid).delete()
        client.close()


def media_jobs_fixture(client, fixture_uid):
    from footylevels.media_jobs import FirestoreMediaJobs
    class ScopedMediaJobs(FirestoreMediaJobs):
        def _ledgers(self, owner):
            # Metadata-only integration tests never touch the actual global quota.
            return self._ref("storageUsage", fixture_uid), self._ref("storageUsage", owner + "-usage")
    return ScopedMediaJobs(client)


def media_result(job):
    from footylevels.records import MediaPreview, PreparedMedia
    prefix = f"users/{job.owner_id}/projects/{job.project_id}/media/{job.id}/{job.attempt_count}/"
    video = AssetRef(bucket="private-media", object_key=prefix + "video.mp4", sha256="a" * 64,
                     byte_size=20, content_type="video/mp4")
    jpeg = AssetRef(bucket="private-media", object_key=prefix + "preview-00.jpg", sha256="b" * 64,
                    byte_size=10, content_type="image/jpeg")
    return PreparedMedia(video=video, width=320, height=240, duration=2, frame_count=60,
                         previews=(MediaPreview(asset=jpeg, timestamp=0.5, frame_index=15),))


def preparing_project(repo, jobs, uid):
    project = repo.create_project(uid, "Media fixture", "gallery")
    upload = jobs.begin(uid, project.id, 1, 12, "a" * 64, "video/mp4", "media-fixture-key-1234")
    return jobs.commit_upload(uid, upload.id), upload


def test_media_dispatch_claim_restart_and_stale_publication(cloud):
    from footylevels.repositories import RecordConflict
    repo, client, uid, _ = cloud
    jobs = media_jobs_fixture(client, uid)
    project, upload = preparing_project(repo, jobs, uid)
    assert project.state == "preparing" and project.active_job_id
    assert project.id in jobs.candidates()
    assert project.id not in jobs.candidates(after=project.id)
    assert jobs.commit_upload(uid, upload.id) == project
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda worker: jobs.claim(project.id, worker), ["worker-a", "worker-b"]))
    claim = next(j for j in claims if j is not None)
    assert sum(j is not None for j in claims) == 1 and claim.attempt_count == 1
    jobs.checkpoint(claim, "decodingMedia")
    assert repo.get_job(uid, claim.id).stage == "decodingMedia"
    media = media_result(claim)
    assets = [media.video, *[p.asset for p in media.previews]]
    jobs.reserve_outputs(claim, assets)
    jobs.reserve_outputs(claim, assets)
    used = client.collection("storageUsage").document(uid).get().to_dict()["bytes"]
    assert used == 12 + 30
    client.collection("jobs").document(claim.id).update({"leaseExpiresAt": utc_now() - timedelta(seconds=1)})
    restarted = media_jobs_fixture(client, uid)
    replacement = restarted.claim(project.id, "worker-after-restart")
    assert replacement.id == claim.id and replacement.attempt_count == 2
    for action in [lambda: jobs.checkpoint(claim, "publishingMedia"),
                   lambda: jobs.reserve_outputs(claim, assets), lambda: jobs.publish(claim, media)]:
        with pytest.raises(RecordConflict):
            action()
    assert repo.get_project(uid, project.id).state == "preparing"
    latest = media_result(replacement)
    restarted.reserve_outputs(replacement, [latest.video, *[p.asset for p in latest.previews]])
    published = restarted.publish(replacement, latest)
    assert published.state == "ready" and published.prepared_media == latest
    assert published.video == project.video and published.input_revision == project.input_revision
    assert published.active_job_id is None
    assert repo.get_job(uid, replacement.id).status == "succeeded"
    assert restarted.claim(project.id, "late-worker") is None
    # The cursor remains valid after the previous project leaves preparing or
    # is deleted. This is the deployment's second and subsequent polling cycle.
    assert project.id not in restarted.candidates(after=project.id)
    repo.mark_deleting(uid, project.id)
    assert project.id not in restarted.candidates(after=project.id)
    client.collection("projects").document(project.id).delete()
    assert project.id not in restarted.candidates(after=project.id)


def test_media_rejection_retry_bound_and_tombstone_fence(cloud):
    from footylevels.repositories import RecordNotFound
    repo, client, uid, _ = cloud
    jobs = media_jobs_fixture(client, uid)
    project, _ = preparing_project(repo, jobs, uid)
    claim = jobs.claim(project.id, "worker")
    failed = jobs.fail(claim, "invalid_media")
    assert failed.status == "failed" and failed.error_code == "invalid_media"
    assert repo.get_project(uid, project.id).state == "rejected"
    assert jobs.claim(project.id, "another") is None
    retry_project, _ = preparing_project(repo, jobs, uid)
    for attempt in range(1, 4):
        retry = jobs.claim(retry_project.id, "worker")
        assert retry.attempt_count == attempt
        status = jobs.fail(retry, "preparation_unavailable", retry=True).status
        assert status == ("retrying" if attempt < 3 else "failed")
    assert jobs.claim(retry_project.id, "another") is None
    deleted_project, _ = preparing_project(repo, jobs, uid)
    late = jobs.claim(deleted_project.id, "worker")
    repo.mark_deleting(uid, deleted_project.id)
    assert client.collection("jobs").document(late.id).get().to_dict()["status"] == "cancelled"
    with pytest.raises(RecordNotFound):
        jobs.checkpoint(late, "publishingMedia")


def test_media_orphan_cleanup_keeps_winners_and_releases_exact_quota(cloud):
    import hashlib
    from footylevels.repositories import RecordConflict
    repo, client, uid, _ = cloud
    jobs = media_jobs_fixture(client, uid)
    project, _ = preparing_project(repo, jobs, uid)
    first = jobs.claim(project.id, "worker")
    old_media = media_result(first)
    old_assets = [old_media.video, old_media.previews[0].asset]
    jobs.reserve_outputs(first, old_assets)
    jobs.fail(first, "preparation_unavailable", retry=True)
    second = jobs.claim(project.id, "replacement")
    latest = media_result(second)
    jobs.reserve_outputs(second, [latest.video, latest.previews[0].asset])
    jobs.publish(second, latest)
    class Store:
        deleted = []
        absent = []
        def delete_exact(self, asset): self.deleted.append(asset)
        def verify_absent(self, asset): self.absent.append(asset)
    store = Store()
    for asset in old_assets:
        identifier = hashlib.sha256(asset.object_key.encode()).hexdigest()
        with pytest.raises(RecordConflict):
            jobs.cleanup_orphan_output(identifier, store)
        client.collection("assetReservations").document(identifier).update({"createdAt": utc_now() - timedelta(hours=25)})
        jobs.cleanup_orphan_output(identifier, store)
        with pytest.raises(RecordConflict):
            jobs.cleanup_orphan_output(identifier, store)
    assert store.deleted == store.absent == old_assets
    assert client.collection("storageUsage").document(uid).get().to_dict()["bytes"] == 12 + 30
    winning_id = hashlib.sha256(latest.video.object_key.encode()).hexdigest()
    client.collection("assetReservations").document(winning_id).update({"createdAt": utc_now() - timedelta(hours=25)})
    with pytest.raises(RecordConflict):
        jobs.cleanup_orphan_output(winning_id, store)
    assert store.deleted == old_assets


def test_upload_maintenance_enforces_quarantine_and_releases_once(cloud):
    from google.cloud.firestore_v1.base_query import FieldFilter
    from footylevels.repositories import RecordConflict
    repo, client, uid, _ = cloud
    jobs = media_jobs_fixture(client, uid)
    project = repo.create_project(uid, "Expired upload fixture", "gallery")
    upload = jobs.begin(uid, project.id, 1, 12, "a" * 64, "video/mp4", "maintenance-fixture-key")
    class Store:
        deleted = []
        absent = []
        def delete_exact(self, asset): self.deleted.append(asset)
        def verify_absent(self, asset): self.absent.append(asset)
    store = Store()
    with pytest.raises(RecordConflict):
        jobs.cleanup_expired(upload.id, store)
    assert not store.deleted
    client.collection("uploads").document(upload.id).update({"expiresAt": utc_now() - timedelta(hours=49),
                                                            "cleanupAfter": utc_now() - timedelta(hours=1)})
    cleaned = jobs.cleanup_expired(upload.id, store)
    assert cleaned.status == "cancelled" and cleaned.reserved_bytes == 0
    assert jobs.cleanup_expired(upload.id, store) == cleaned
    assert store.deleted == store.absent == [upload.asset]
    assert client.collection("storageUsage").document(uid).get().to_dict()["bytes"] == 0
    assert repo.get_project(uid, project.id).active_upload_id is None
    # Reconciliation keeps a timestamp + document cursor after cleanup. Verify
    # the actual provider query, including its document-name tie breaker.
    snapshot = client.collection("uploads").document(upload.id).get()
    query = client.collection("uploads").where(filter=FieldFilter("cleanupAfter", "<=", utc_now())).order_by("cleanupAfter").order_by("__name__")
    later = list(query.start_after({"cleanupAfter": snapshot.to_dict()["cleanupAfter"], "__name__": snapshot.reference}).limit(1).stream())
    assert all(item.id != upload.id for item in later)


def ready_project(repo, uid):
    project = repo.create_project(uid, "Fixture clip", "gallery")
    source = AssetRef(bucket="private-media", object_key=f"users/{uid}/projects/{project.id}/source/fixture.mp4",
                      sha256="a" * 64, byte_size=12, content_type="video/mp4")
    return repo.set_source(uid, project.id, source, project.input_revision)


def enqueue(repo, uid, project, input_hash="b" * 64):
    return repo.enqueue_reconstruction(uid, project.id, project.input_revision, "reconstructMotion", input_hash, "fixture-v1")


def running_job(client, job, *, token="fixture-lease", expired=False):
    # Runner transitions are FP-53. Set a controlled lease fixture to test publication.
    client.collection("jobs").document(job.id).update({"status": "running", "attemptCount": 1,
        "leaseToken": token, "leaseExpiresAt": utc_now() + timedelta(minutes=-1 if expired else 5)})


def result_for(project, job):
    return ReconstructionRecord(id=job.id, owner_id=job.owner_id, project_id=project.id, job_id=job.id,
        input_revision=job.input_revision, profile_version=job.profile_version, source_asset=job.source_asset, mode="motion", outcome="ready",
        engine=Engine(model_name="fixture-model", upstream_commit="c" * 40, checkpoint_hash="d" * 64, config_hash="e" * 64),
        contract=PlaybackContract(topology_version="fixture-v1", encoding="usdz"),
        artifacts=(AssetRef(bucket="private-media", object_key=f"users/{job.owner_id}/projects/{project.id}/reconstruction/{job.id}/1/model.usdz",
                           sha256="f" * 64, byte_size=12, content_type="model/vnd.usdz+zip"),),
        requested_frame_count=1, reconstructed_frame_count=1, missing_frame_count=0)


def test_idempotent_profile_and_owned_crud_pagination(cloud):
    from footylevels.repositories import RecordNotFound
    repo, client, uid, other = cloud
    with ThreadPoolExecutor(max_workers=2) as pool:
        users = list(pool.map(lambda _: repo.bootstrap_user(uid, "Player"), range(2)))
    assert users[0] == users[1]
    assert repo.bootstrap_user(uid, "Changed name") == users[0]
    records = [repo.create_project(uid, f"Clip {i}", "gallery") for i in range(3)]
    tied_timestamp = utc_now()
    for record in records:
        client.collection("projects").document(record.id).update({"createdAt": tied_timestamp})
    repo.create_project(other, "Other person's clip", "capture")
    first, cursor = repo.list_projects(uid, limit=2)
    second, end = repo.list_projects(uid, limit=2, cursor=cursor)
    assert len(first) == 2 and len(second) == 1 and end is None
    assert {p.id for p in first + second} == {p.id for p in records}
    assert [p.id for p in first + second] == sorted(p.id for p in records)
    with pytest.raises(ValueError):
        repo.list_projects(other, cursor=cursor)
    for action in [lambda: repo.get_project(other, records[0].id),
                   lambda: repo.rename_project(other, records[0].id, "Wrong owner"),
                   lambda: repo.mark_deleting(other, records[0].id)]:
        with pytest.raises(RecordNotFound):
            action()
    assert repo.rename_project(uid, records[0].id, "New title").input_revision == 1
    repo.mark_deleting(uid, records[0].id)
    assert len(repo.list_projects(uid)[0]) == 2
    with pytest.raises(RecordNotFound):
        repo.get_project(uid, records[0].id)


def test_concurrent_enqueue_and_immutable_publication(cloud):
    from footylevels.repositories import RecordConflict, RecordNotFound
    repo, client, uid, other = cloud
    project = ready_project(repo, uid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: enqueue(repo, uid, project), range(2)))
    assert jobs[0] == jobs[1]
    job = jobs[0]
    with pytest.raises(RecordConflict):
        enqueue(repo, uid, project, "c" * 64)
    with pytest.raises(RecordNotFound):
        repo.get_job(other, job.id)
    running_job(client, job)
    result = result_for(project, job)
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result.model_copy(update={"profile_version": "wrong-profile"}), "fixture-lease")
    with ThreadPoolExecutor(max_workers=2) as pool:
        published = list(pool.map(lambda _: repo.publish_reconstruction(result, "fixture-lease"), range(2)))
    assert published == [result, result]
    assert repo.publish_reconstruction(result, "fixture-lease") == result
    assert repo.get_job(uid, job.id).status == "succeeded"
    assert repo.get_project(uid, project.id).latest_reconstruction_id == result.id
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result.model_copy(update={"engine": result.engine.model_copy(update={"config_hash": "a" * 64})}), "fixture-lease")
    assert repo.get_reconstruction(uid, result.id).engine.config_hash == "e" * 64


def test_stale_revision_lease_and_deleted_project_cannot_publish(cloud):
    from footylevels.repositories import RecordConflict, RecordNotFound
    repo, client, uid, other = cloud
    project = ready_project(repo, uid)
    with pytest.raises(RecordConflict):
        repo.enqueue_reconstruction(uid, project.id, project.input_revision - 1, "reconstructMotion", "b" * 64, "fixture-v1")
    job = enqueue(repo, uid, project)
    result = result_for(project, job)
    running_job(client, job, expired=True)
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result, "fixture-lease")
    running_job(client, job)
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result, "old-worker-lease")
    client.collection("projects").document(project.id).update({"inputRevision": project.input_revision + 1})
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result, "fixture-lease")
    client.collection("projects").document(project.id).update({"inputRevision": project.input_revision})
    repo.mark_deleting(uid, project.id)
    assert repo.mark_deleting(uid, project.id).state == "deleting"
    with pytest.raises(RecordNotFound):
        repo.publish_reconstruction(result, "fixture-lease")
    assert client.collection("reconstructions").document(job.id).get().exists is False


def test_reference_version_and_comparison_access_rechecked(cloud):
    from footylevels.repositories import RecordNotFound
    repo, client, uid, other = cloud
    project = ready_project(repo, uid)
    job = enqueue(repo, uid, project)
    running_job(client, job)
    result = repo.publish_reconstruction(result_for(project, job), "fixture-lease")
    reference = ReferenceRecord(id=uid, title="Permitted fixture", version="v1", license_reference="Test-only synthetic metadata",
        publication_state="published", video_asset=AssetRef(bucket="reference-assets", object_key=f"examples/{uid}/v1/fixture.mp4",
            sha256="a" * 64, byte_size=12, content_type="video/mp4"))
    repo.create_reference(reference)
    comparison = ComparisonRecord(id=uid, owner_id=uid, left_reconstruction_id=result.id, right_type="reference",
        right_id=reference.id, right_reference_version="v1", mode="sideBySide")
    repo.create_comparison(comparison, uid)
    assert repo.get_comparison(uid, uid) == comparison
    with pytest.raises(RecordNotFound):
        repo.get_comparison(other, uid)
    with pytest.raises(RecordNotFound):
        repo.get_reference(uid, "v2")
    repo.mark_deleting(uid, project.id)
    with pytest.raises(RecordNotFound):
        repo.get_comparison(uid, uid)


def test_durable_project_creation_retries_are_owner_scoped(cloud):
    from footylevels.repositories import RecordConflict, RecordNotFound
    repo, client, uid, other = cloud
    key = 'draft-' + uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=3) as pool:
        projects = list(pool.map(lambda _: repo.create_project(uid, 'Original', 'capture', key, 'Practice'), range(3)))
    assert len({p.id for p in projects}) == 1
    pid = projects[0].id
    repo.rename_project(uid, pid, 'Renamed')
    assert repo.create_project(uid, 'Original', 'capture', key, 'Practice').title == 'Renamed'
    with pytest.raises(RecordConflict):
        repo.create_project(uid, 'Changed', 'capture', key, 'Practice')
    assert repo.create_project(other, 'Original', 'capture', key, 'Practice').id != pid
    repo.mark_deleting(uid, pid)
    with pytest.raises(RecordNotFound):
        repo.create_project(uid, 'Original', 'capture', key, 'Practice')


def test_reconstruction_claim_heartbeat_restart_and_missed_ack(cloud):
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.repositories import RecordConflict
    repo, client, uid, _ = cloud
    project = ready_project(repo, uid)
    job = enqueue(repo, uid, project)
    jobs = FirestoreReconstructionJobs(client)
    assert job.id in [s.id for s in jobs.candidates()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda name: jobs.claim(job.id, name), ['first', 'second']))
    claimed = next(j for j in claims if j)
    assert sum(j is not None for j in claims) == 1
    heartbeat = jobs.checkpoint(claimed, 'generatingBodies')
    assert heartbeat.heartbeat_at >= claimed.heartbeat_at
    assert heartbeat.lease_expires_at >= claimed.lease_expires_at
    with pytest.raises(RecordConflict):
        jobs.checkpoint(claimed, 'downloadingInput')
    client.collection('jobs').document(job.id).update({'leaseExpiresAt': utc_now() - timedelta(seconds=1)})
    restarted = FirestoreReconstructionJobs(client)
    replacement = restarted.claim(job.id, 'restarted')
    assert replacement.attempt_count == 2 and replacement.lease_token != claimed.lease_token
    with pytest.raises(RecordConflict):
        jobs.checkpoint(claimed)
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result_for(project, job), claimed.lease_token)
    result = result_for(project, job)
    asset = result.artifacts[0].model_copy(update={'object_key': result.artifacts[0].object_key.replace('/1/', '/2/')})
    result = result.model_copy(update={'artifacts': (asset,)})
    restarted.checkpoint(replacement, 'publishingAssets')
    repo.publish_reconstruction(result, replacement.lease_token)
    # The transaction committed but the worker lost its acknowledgement. An
    # exact replay returns the immutable winner, even after its lease is cleared.
    assert repo.publish_reconstruction(result, replacement.lease_token) == result
    assert repo.get_job(uid, job.id).lease_token is None
    assert restarted.claim(job.id, 'late') is None
    assert all(s.id != job.id for s in restarted.candidates(after=job.id))
    client.collection('jobs').document(job.id).delete()
    assert all(s.id != job.id for s in restarted.candidates(after=job.id))


def test_reconstruction_retry_backoff_exhaustion_and_deadlines(cloud):
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.repositories import RecordConflict
    repo, client, uid, _ = cloud
    project = ready_project(repo, uid)
    job = enqueue(repo, uid, project)
    jobs = FirestoreReconstructionJobs(client)
    for attempt in range(1, 4):
        claim = jobs.claim(job.id, 'worker')
        assert claim.attempt_count == attempt
        failed = jobs.fail(claim)
        if attempt < 3:
            assert failed.status == 'retrying' and failed.next_attempt_at > utc_now()
            assert jobs.claim(job.id, 'too-soon') is None
            client.collection('jobs').document(job.id).update({'nextAttemptAt': utc_now() - timedelta(seconds=1)})
        else:
            assert failed.status == 'failed' and failed.finished_at and failed.lease_token is None
    assert repo.get_project(uid, project.id).active_job_id is None
    assert jobs.claim(job.id, 'fourth') is None
    overdue_project = ready_project(repo, uid)
    overdue_job = enqueue(repo, uid, overdue_project)
    overdue_claim = jobs.claim(overdue_job.id, 'worker')
    client.collection('jobs').document(overdue_job.id).update({'attemptDeadlineAt': utc_now() - timedelta(seconds=1)})
    with pytest.raises(RecordConflict):
        jobs.checkpoint(overdue_claim)
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result_for(overdue_project, overdue_job), overdue_claim.lease_token)
    next_claim = jobs.claim(overdue_job.id, 'replacement')
    assert next_claim.attempt_count == 2
    client.collection('jobs').document(overdue_job.id).update({'deadlineAt': utc_now() - timedelta(seconds=1)})
    assert jobs.claim(overdue_job.id, 'deadline') is None
    assert repo.get_job(uid, overdue_job.id).error_code == 'job_deadline'
    assert repo.get_project(uid, overdue_project.id).active_job_id is None


def test_reconstruction_cancel_delete_and_changed_input_fences(cloud):
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.repositories import RecordConflict, RecordNotFound
    repo, client, uid, other = cloud
    jobs = FirestoreReconstructionJobs(client)
    project = ready_project(repo, uid)
    job = enqueue(repo, uid, project)
    claim = jobs.claim(job.id, 'worker')
    with pytest.raises(RecordNotFound):
        repo.cancel_job(other, job.id)
    cancelled = repo.cancel_job(uid, job.id)
    assert cancelled.status == 'cancelled' and cancelled.lease_token is None
    assert repo.cancel_job(uid, job.id) == cancelled
    assert repo.get_project(uid, project.id).active_job_id is None
    with pytest.raises(RecordConflict):
        jobs.checkpoint(claim)
    with pytest.raises(RecordConflict):
        repo.publish_reconstruction(result_for(project, job), claim.lease_token)
    assert jobs.claim(job.id, 'late') is None
    for mutation in ['delete', 'revision', 'physical-delete']:
        p = ready_project(repo, uid)
        j = enqueue(repo, uid, p)
        c = jobs.claim(j.id, 'worker')
        if mutation == 'delete':
            repo.mark_deleting(uid, p.id)
        elif mutation == 'revision':
            client.collection('projects').document(p.id).update({'inputRevision': p.input_revision + 1})
        else:
            client.collection('projects').document(p.id).delete()
        assert jobs.claim(j.id, 'late') is None
        assert client.collection('jobs').document(j.id).get().to_dict()['status'] == 'cancelled'
        with pytest.raises((RecordConflict, RecordNotFound)):
            jobs.checkpoint(c)
        assert not client.collection('reconstructions').document(j.id).get().exists


def test_reconstruction_pins_canonical_media_and_operator_pause(cloud):
    from footylevels.repositories import FirestoreRecords, RecordUnavailable
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    repo, client, uid, _ = cloud
    media_jobs = media_jobs_fixture(client, uid)
    p, _ = preparing_project(repo, media_jobs, uid)
    media_claim = media_jobs.claim(p.id, 'media-worker')
    media = media_result(media_claim)
    media_jobs.reserve_outputs(media_claim, [media.video, media.previews[0].asset])
    project = media_jobs.publish(media_claim, media)
    class ScopedRecords(FirestoreRecords):
        def _reconstruction_control(self):
            return self._ref('runtimeControls', uid)
    records = ScopedRecords(client)
    job = enqueue(records, uid, project)
    assert job.source_asset == media.video and job.source_asset != project.video
    control = client.collection('runtimeControls').document(uid)
    control.set({'acceptingRequests': False})
    assert enqueue(records, uid, project).id == job.id
    another = ready_project(repo, uid)
    with pytest.raises(RecordUnavailable):
        enqueue(records, uid, another)
    claim = FirestoreReconstructionJobs(client).claim(job.id, 'worker')
    assert claim is not None  # pause rejects new requests, drains existing jobs
    published = records.publish_reconstruction(result_for(project, job), claim.lease_token)
    assert published.source_asset == media.video
    control.set({'acceptingRequests': True})
    assert enqueue(records, uid, another).status == 'queued'


def test_body_selection_is_persisted_and_pinned_to_canonical_input(cloud):
    from footylevels.records import KeyframeInput
    from footylevels.model_requests import BODY_PROFILE, body_input_hash
    from footylevels.repositories import RecordConflict
    repo, client, uid, _ = cloud
    jobs = media_jobs_fixture(client, uid)
    project, _ = preparing_project(repo, jobs, uid)
    claim = jobs.claim(project.id, 'media-worker')
    media = media_result(claim)
    jobs.reserve_outputs(claim, [media.video, media.previews[0].asset])
    project = jobs.publish(claim, media)
    selections = [KeyframeInput(frame_index=15, bbox=(.1, .1, .9, .9))]
    digest = body_input_hash(media.video, selections)
    for frames, input_hash in [(None, digest), (selections, 'f'*64),
            ([*selections, *selections], digest), ([KeyframeInput(frame_index=60, bbox=(0,0,1,1))], digest)]:
        with pytest.raises(RecordConflict):
            repo.enqueue_reconstruction(uid, project.id, project.input_revision, 'reconstructKeyframes',
                input_hash, BODY_PROFILE, keyframes=frames)
    record = repo.enqueue_reconstruction(uid, project.id, project.input_revision, 'reconstructKeyframes',
        digest, BODY_PROFILE, keyframes=selections)
    assert record.keyframes == tuple(selections)
    assert repo.get_job(uid, record.id).keyframes == tuple(selections)
    assert repo.enqueue_reconstruction(uid, project.id, project.input_revision, 'reconstructKeyframes',
        digest, BODY_PROFILE, keyframes=selections).id == record.id
    assert record.source_asset == media.video


@pytest.mark.parametrize('mode', ['keyframes', 'motion'])
def test_authenticated_http_reconstruction_retries_cancel_and_tombstone(cloud, mode):
    """Real transactions; synthetic verified identities, no sign-in/GPU claims."""
    from fastapi.testclient import TestClient
    from api.cloud import create_app
    from footylevels.authentication import Identity
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.repositories import FirestoreRecords
    repo, client, uid, other = cloud
    repo.bootstrap_user(uid, 'HTTP fixture')
    repo.bootstrap_user(other, 'Other fixture')
    jobs = media_jobs_fixture(client, uid)
    project, _ = preparing_project(repo, jobs, uid)
    media_claim = jobs.claim(project.id, 'fixture-media-worker')
    media = media_result(media_claim)
    jobs.reserve_outputs(media_claim, [media.video, media.previews[0].asset])
    project = jobs.publish(media_claim, media)
    class ScopedRecords(FirestoreRecords):
        def _reconstruction_control(self):
            return self._ref('runtimeControls', uid)
    class FixtureVerifier:
        def verify(self, token):
            assert token in {uid, other}
            return Identity(token, 'Fixture')
    api = TestClient(create_app(ScopedRecords(client), FixtureVerifier()))
    own = {'Authorization': 'Bearer ' + uid}
    foreign = {'Authorization': 'Bearer ' + other}
    path = f'/api/projects/{project.id}/reconstructions/{mode}'
    body = {'inputRevision': project.input_revision, **({'keyframes': [{'frameIndex': 15, 'bbox': [.1, .1, .9, .9]}]}
        if mode == 'keyframes' else {'segment': {'startFrame': 0, 'endFrame': 30}})}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api.post(path, headers=own, json=body), range(2)))
    assert [r.status_code for r in responses] == [202, 202]
    identifier = responses[0].json()['id']
    assert responses[1].json()['id'] == identifier
    assert responses[0].headers['Location'] == '/api/jobs/' + identifier
    durable = repo.get_job(uid, identifier)
    assert durable.source_asset == media.video
    if mode == 'keyframes':
        assert durable.keyframes[0].frame_index == 15
    else:
        assert durable.motion_segment.start_frame == 0 and durable.motion_segment.end_frame == 30
    changed = {**body, **({'keyframes': [{'frameIndex': 16, 'bbox': [.1, .1, .9, .9]}]}
        if mode == 'keyframes' else {'segment': {'startFrame': 15, 'endFrame': 45}})}
    assert api.post(path, headers=own, json=changed).status_code == 409
    assert api.post(path, headers=foreign, json=body).status_code == 404
    assert api.get('/api/jobs/' + identifier, headers=foreign).status_code == 404
    assert api.post('/api/jobs/' + identifier + '/cancel', headers=foreign).status_code == 404
    claim = FirestoreReconstructionJobs(client).claim(identifier, 'fixture-reconstruction-worker')
    assert claim is not None
    response = api.get('/api/jobs/' + identifier, headers=own)
    assert response.json()['status'] == 'running'
    assert not {'leaseToken', 'leaseExpiresAt', 'workerId'} & response.json().keys()
    assert api.post('/api/jobs/' + identifier + '/cancel', headers=own).json()['status'] == 'cancelled'
    assert api.post(path, headers=own, json=body).json()['id'] == identifier
    assert repo.get_project(uid, project.id).active_job_id is None
    control = client.collection('runtimeControls').document(uid)
    control.set({'acceptingRequests': False})
    assert api.post(path, headers=own, json=changed).status_code == 503
    assert api.post(path, headers=own, json=body).status_code == 202
    control.set({'acceptingRequests': True})
    assert api.post(path, headers=own, json=changed).status_code == 202
    assert api.delete(f'/api/projects/{project.id}', headers=own).status_code == 202
    assert api.post(path, headers=own, json=body).status_code == 404
    assert api.get('/api/jobs/' + identifier, headers=own).status_code == 404


def test_reconstruction_serial_runner_with_real_firestore_and_isolated_quota(cloud, tmp_path):
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.reconstruction_worker import run_job
    from tests.test_reconstruction_worker import adapter, Storage
    repo, client, uid, _ = cloud
    class ScopedJobs(FirestoreReconstructionJobs):
        def _ledgers(self, owner):
            return self._ref('storageUsage', uid), self._ref('storageUsage', uid + '-usage')
    jobs = ScopedJobs(client)
    project = ready_project(repo, uid)
    job = enqueue(repo, uid, project)
    claim = jobs.claim(job.id, 'runner-fixture')
    command, root = adapter(tmp_path)
    storage = Storage()
    assert run_job(jobs, storage, claim, command, root) == 'ready'
    persisted = repo.get_job(uid, job.id)
    assert persisted.status == 'succeeded' and persisted.stage == 'ready' and persisted.lease_token is None
    result = repo.get_reconstruction(uid, job.id)
    assert result.source_asset == project.video
    assert tuple(storage.uploaded) == tuple(storage.verified) == result.artifacts
    assert repo.get_project(uid, project.id).latest_reconstruction_id == job.id
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes'] == len(b'fixture-mesh')
    assert not jobs.claim(job.id, 'duplicate')
    assert list(root.iterdir()) == []


def test_reconstruction_orphan_outputs_keep_immutable_winner(cloud):
    import hashlib
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.repositories import RecordConflict
    repo, client, uid, _ = cloud
    class ScopedJobs(FirestoreReconstructionJobs):
        def _ledgers(self, owner):
            return self._ref('storageUsage', uid), self._ref('storageUsage', uid + '-usage')
    jobs = ScopedJobs(client)
    project = ready_project(repo, uid)
    job = enqueue(repo, uid, project)
    first = jobs.claim(job.id, 'interrupted')
    old_result = result_for(project, job)
    jobs.reserve_outputs(first, old_result.artifacts)
    client.collection('jobs').document(job.id).update({'leaseExpiresAt': utc_now() - timedelta(seconds=1)})
    second = jobs.claim(job.id, 'replacement')
    winner_asset = old_result.artifacts[0].model_copy(update={'object_key': old_result.artifacts[0].object_key.replace('/1/', '/2/')})
    winner = old_result.model_copy(update={'artifacts': (winner_asset,)})
    jobs.reserve_outputs(second, winner.artifacts)
    jobs.publish_reconstruction(winner, second.lease_token)
    class Store:
        deleted = []
        def delete_exact(self, asset): self.deleted.append(asset)
        def verify_absent(self, asset): assert asset in self.deleted
    store = Store()
    for asset in [old_result.artifacts[0], winner_asset]:
        identifier = hashlib.sha256(asset.object_key.encode()).hexdigest()
        client.collection('assetReservations').document(identifier).update({'createdAt': utc_now() - timedelta(hours=25)})
        if asset == winner_asset:
            with pytest.raises(RecordConflict): jobs.cleanup_orphan_output(identifier, store)
        else:
            jobs.cleanup_orphan_output(identifier, store)
    assert store.deleted == list(old_result.artifacts)
    assert client.collection('storageUsage').document(uid).get().to_dict()['bytes'] == winner_asset.byte_size
