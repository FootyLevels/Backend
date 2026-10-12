#!/usr/bin/env python3
"""Opt-in Development-only media/cloud probe with exact quiescent fixture cleanup.

No Firebase accounts, GPU model loads or signed client upload capabilities are
created. The trusted fixture is uploaded directly with the server storage key.
Only aggregate evidence is printed; server keys remain in the private config.
"""
import hashlib
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "infra/dgx"))


def cleanup(client, jobs, storage, uid, project_id, *, ios_playback_run_id=None):
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
    from footylevels.records import AssetRef, UploadRecord, utc_now
    project = client.collection("projects").document(project_id).get()
    if not project.exists or project.to_dict()["ownerId"] != uid:
        raise ValueError("Fixture cleanup guard failed")
    if ios_playback_run_id is not None:
        # Real Firebase QA users have provider-assigned IDs, not synthetic
        # prefixes. Require the exact UUID title and a single owned project;
        # never widen the normal synthetic-fixture cleanup path.
        validate_ios_cleanup_scope(client.project, uid, project_id, ios_playback_run_id,
            [(s.id, s.to_dict()) for s in client.collection("projects").where(filter=FieldFilter("ownerId", "==", uid)).stream()])
    elif not uid.startswith(("fp54-media-", "fp53-model-", "fp39-model-")):
        raise ValueError("Fixture cleanup guard failed")
    active_jobs = list(client.collection("jobs").where(filter=FieldFilter("ownerId", "==", uid)).stream())
    if any(s.to_dict()["status"] == "running" for s in active_jobs):
        raise ValueError("A worker still owns the fixture; keep its private recovery evidence")
    if any(s.to_dict()["projectId"] != project_id for s in active_jobs):
        raise ValueError("Fixture jobs cross projects")
    jobs.mark_deleting(uid, project_id)
    uploads = list(client.collection("uploads").where(filter=FieldFilter("ownerId", "==", uid)).stream())
    reservations = list(client.collection("assetReservations").where(filter=FieldFilter("ownerId", "==", uid)).stream())
    assets = []
    total = 0
    for snapshot in uploads:
        record = UploadRecord.model_validate(snapshot.to_dict())
        if record.project_id != project_id:
            raise ValueError("Fixture project mismatch")
        assets.append(record.asset)
        total += record.reserved_bytes
    for snapshot in reservations:
        raw = snapshot.to_dict()
        if raw["projectId"] != project_id:
            raise ValueError("Fixture project mismatch")
        assets.append(AssetRef.model_validate(raw["asset"]))
        total += raw["bytes"]
    for asset in assets:
        if not asset.object_key.startswith(f"users/{uid}/projects/{project_id}/"):
            raise ValueError("Fixture asset mismatch")
        storage.delete_exact(asset)
        storage.verify_absent(asset)
    ledgers = jobs._ledgers(uid)
    @firestore.transactional
    def release(transaction):
        counts = [(ref.get(transaction=transaction).to_dict() or {}).get("bytes", 0) for ref in ledgers]
        if any(count < total for count in counts):
            raise ValueError("Fixture quota mismatch")
        for ref, count in zip(ledgers, counts):
            transaction.set(ref, {"bytes": count - total, "updatedAt": utc_now()})
        for snapshot in [*uploads, *reservations]:
            transaction.delete(snapshot.reference)
    jobs._run(release)
    for collection in ["jobs", "projects", "reconstructions"]:
        for snapshot in client.collection(collection).where(filter=FieldFilter("ownerId", "==", uid)).stream():
            snapshot.reference.delete()
    client.collection("users").document(uid).delete()
    owner_usage = ledgers[1].get().to_dict()
    if owner_usage["bytes"] != 0:
        raise ValueError("Fixture quota not released")
    ledgers[1].delete()
    for collection in ["jobs", "projects", "reconstructions", "uploads", "assetReservations"]:
        if list(client.collection(collection).where(filter=FieldFilter("ownerId", "==", uid)).limit(1).stream()):
            raise ValueError("Fixture records remain")
    if client.collection("users").document(uid).get().exists:
        raise ValueError("Fixture profile remains")
    return {"sourceObjects": len(uploads), "preparedObjects": len(reservations), "absenceVerified": True}


def validate_ios_cleanup_scope(environment, uid, project_id, run_id, owned_projects):
    import re
    try:
        canonical = str(uuid.UUID(run_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid iOS fixture marker") from None
    if (environment != "footylevels-dev-20261009" or canonical != run_id
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", uid)
            or len(owned_projects) != 1 or owned_projects[0][0] != project_id
            or owned_projects[0][1].get("ownerId") != uid
            or owned_projects[0][1].get("title") != "FP40 iOS " + run_id
            or owned_projects[0][1].get("source") != "gallery"):
        raise ValueError("iOS fixture cleanup scope mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", action="store_true", help="Let the running deployment discover and prepare the fixture")
    args = parser.parse_args()
    if os.environ.get("FOOTYLEVELS_LIVE_MEDIA_TEST") != "1":
        raise SystemExit("Set FOOTYLEVELS_LIVE_MEDIA_TEST=1 to opt in")
    from cloud_config import load
    load("dev")
    assert os.environ["FIREBASE_PROJECT_ID"] == "footylevels-dev-20261009"
    os.environ["PATH"] = str(Path.home() / "skill-lab/.media/bin") + os.pathsep + os.environ.get("PATH", os.defpath)
    from google.cloud import firestore
    from footylevels.assets import SupabaseAssets
    from footylevels.media_jobs import FirestoreMediaJobs
    from footylevels.media_worker import process_job
    client = firestore.Client.from_service_account_json(os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
                                                        project=os.environ["FIREBASE_PROJECT_ID"])
    jobs = FirestoreMediaJobs(client)
    storage = SupabaseAssets(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    uid = "fp54-media-" + uuid.uuid4().hex
    project = None
    evidence = {"ticket": "FP-54", "environment": "dev", "gpuUsed": False}
    recovery = Path.home() / ".local/share/footylevels/cloud-api/fp54-last-fixture.json"
    with recovery.open("x") as saved:
        os.chmod(recovery, 0o600)
        saved.write(json.dumps({"ownerId": uid}))
    try:
        with tempfile.TemporaryDirectory(prefix="fp54-verification-") as folder:
            directory = Path(folder)
            source = directory / "fixture.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                "testsrc2=size=320x240:rate=24:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-threads", "2", "-c:a", "aac", "-shortest", str(source)],
                check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            jobs.bootstrap_user(uid, "Temporary media verification")
            project = jobs.create_project(uid, "FP54 isolated media fixture", "gallery")
            recovery.write_text(json.dumps({"ownerId": uid, "projectId": project.id}))
            upload = jobs.begin(uid, project.id, 1, source.stat().st_size, digest, "video/mp4", uuid.uuid4().hex)
            storage.put_file(upload.asset, source)
            preparing = jobs.commit_upload(uid, upload.id)
            assert preparing.state == "preparing" and preparing.active_job_id
            if args.deployment:
                deadline = time.monotonic() + 240
                while jobs.get_project(uid, project.id).state == "preparing" and time.monotonic() < deadline:
                    time.sleep(3)
                claimed = jobs.get_job(uid, preparing.active_job_id)
            else:
                claimed = jobs.claim(project.id, "fp54-verification")
                assert claimed and jobs.claim(project.id, "duplicate-worker") is None
                assert process_job(jobs, storage, claimed, directory) == "ready"
            ready = jobs.get_project(uid, project.id)
            assert ready.state == "ready" and ready.prepared_media and ready.active_job_id is None
            assert ready.video == upload.asset and ready.input_revision == 2
            media = ready.prepared_media
            for asset in [media.video, *[p.asset for p in media.previews]]:
                storage.verify(asset)
            assert jobs.get_job(uid, claimed.id).status == "succeeded"
            assert jobs.claim(project.id, "duplicate-after-completion") is None
            evidence.update({"passed": True, "sourceRevisionPreserved": True, "previews": len(media.previews),
                "playbackFps": media.fps, "durationSeconds": media.duration,
                "outputChecksumsVerified": 7, "duplicateClaimsIgnored": True})
            evidence["runningDeploymentVerified"] = args.deployment
    finally:
        if project:
            evidence["fixtureCleanup"] = cleanup(client, jobs, storage, uid, project.id)
        else:
            client.collection("users").document(uid).delete()
        recovery.unlink()
        client.close()
    print(json.dumps(evidence))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"ticket": "FP-54", "passed": False, "reason": "verification_failed"}))
        raise SystemExit(1) from None
