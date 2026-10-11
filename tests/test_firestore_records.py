"""Opt-in integration tests against Development; all fixture records are removed.

No cloud model/media is generated. Credentials must be the scoped development API
identity, never implicit personal ADC. Default test runs skip cloud writes.
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
    client = firestore.Client(project=project, credentials=credentials)
    uid = "fp51-" + uuid.uuid4().hex
    other = uid + "-other"
    repo = FirestoreRecords(client)
    try:
        yield repo, client, uid, other
    finally:
        # Only this invocation's random fixture owners and exact reference ID.
        for owner in [uid, other]:
            for collection in ["comparisons", "reconstructions", "jobs", "projects"]:
                for snapshot in client.collection(collection).where(filter=FieldFilter("ownerId", "==", owner)).stream():
                    snapshot.reference.delete()
            client.collection("users").document(owner).delete()
        client.collection("references").document(uid).delete()
        client.close()


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
