"""Isolated real Auth/Firestore/Supabase community proof; never starts a server.

Only synthetic prepared-video fixtures are seeded. This proves shared playback
authorization/storage, not another upload/media/GPU run or an iOS UI workflow.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import struct
import sys
import tempfile
import uuid

from fastapi.testclient import TestClient
from firebase_admin import credentials, delete_app, initialize_app
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
import requests

from api.cloud import create_app
from footylevels.assets import SupabaseAssets
from footylevels.authentication import FirebaseIdentityVerifier
from footylevels.circles import FirestoreCircles
from footylevels.model_requests import motion_input_hash
from footylevels.records import (AssetRef, Engine, JobRecord, MediaPreview, MotionSegment,
                                PlaybackContract, PreparedMedia, ReconstructionRecord, utc_now)
from footylevels.repositories import FirestoreRecords
from infra.dgx.cloud_config import load


def main():
    body = json.load(sys.stdin)
    if (body.get("environment") != "dev" or len(body.get("owners", [])) != 3
            or len(body.get("tokens", [])) != 3 or len(body.get("emails", [])) != 3
            or any(not re.fullmatch(r"fp69-[a-f0-9]{32}@example.invalid", email) for email in body["emails"])):
        raise ValueError("Expected three disposable Development identities")
    config = load("dev")
    raw = firestore.Client.from_service_account_json(os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
                                                    project=config["FIREBASE_PROJECT_ID"])

    class IsolatedClient:
        def collection(self, name):
            return raw.collection("integration-" + name)
        def __getattr__(self, name):
            return getattr(raw, name)

    db = IsolatedClient()
    runtime = initialize_app(credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]),
        {"projectId": config["FIREBASE_PROJECT_ID"]}, name="fp69-runtime-" + uuid.uuid4().hex)
    records = FirestoreRecords(db)
    circles = FirestoreCircles(db)
    storage = SupabaseAssets(config["SUPABASE_URL"], config["SUPABASE_SECRET_KEY"])
    client = TestClient(create_app(records, FirebaseIdentityVerifier(runtime), circles=circles, assets=storage))
    headers = [{"Authorization": "Bearer " + token} for token in body["tokens"]]
    uid, member, outsider = body["owners"]
    created_circles, projects, motions, objects, tests = [], [], [], [], []
    cleanup_verified = False

    def request(method, path, actor=0, expected=200, payload=None, key=None):
        h = dict(headers[actor])
        if key:
            h["Idempotency-Key"] = key
        response = client.request(method, path, headers=h, json=payload)
        if response.status_code != expected:
            raise AssertionError("Unexpected community HTTP status: " + str(response.status_code))
        return response.json()

    try:
        for actor, expected_uid in enumerate(body["owners"]):
            assert request("POST", "/api/me/bootstrap", actor=actor)["id"] == expected_uid
        circle = request("POST", "/api/circles", expected=201,
                         payload={"name": "FP69 disposable circle", "description": "Exact isolated service proof"})
        cid = circle["id"]; created_circles.append(cid); base = "/api/circles/" + cid
        assert client.get(base).status_code == 401
        request("GET", base, actor=2, expected=404)
        invite = request("POST", base + "/invite")
        second = request("POST", base + "/invite")
        request("POST", "/api/circles/join", actor=1, expected=404, payload={"code": invite["code"]})
        assert request("POST", "/api/circles/join", actor=1, payload={"code": second["code"]})["memberCount"] == 2
        assert request("POST", "/api/circles/join", actor=1, payload={"code": second["code"]})["memberCount"] == 2
        tests.append("real-firebase-invite-rotation-idempotent-join-and-outsider-denial")
        project = request("POST", "/api/projects", expected=201,
                          payload={"title": "FP69 synthetic prepared clip", "source": "gallery"})
        pid = project["id"]; projects.append(pid)
        with tempfile.TemporaryDirectory(prefix="fp69-circle-proof-") as folder:
            root = Path(folder); video = root / "video.mp4"; preview = root / "preview.jpg"
            ffmpeg = str(Path.home() / "skill-lab/.media/bin/ffmpeg")
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                "testsrc2=size=320x240:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-an", str(video)], check=True, capture_output=True, timeout=30)
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
                "-frames:v", "1", str(preview)], check=True, capture_output=True, timeout=30)
            job = uuid.uuid4().hex * 2
            prefix = f"users/{uid}/projects/{pid}/"

            def put(file, suffix, content_type):
                asset = AssetRef(bucket="private-media", object_key=prefix + suffix,
                    sha256=hashlib.sha256(file.read_bytes()).hexdigest(), byte_size=file.stat().st_size,
                    content_type=content_type)
                objects.append(asset)
                storage.put_file(asset, file)
                return asset

            source = put(video, "source/fixture.mp4", "video/mp4")
            canonical = put(video, f"media/{job}/1/video.mp4", "video/mp4")
            image = put(preview, f"media/{job}/1/preview-00.jpg", "image/jpeg")
            media = PreparedMedia(video=canonical, width=320, height=240, duration=2, frame_count=60,
                                  previews=(MediaPreview(asset=image, timestamp=0, frame_index=0),))
            db.collection("projects").document(pid).update({"state": "ready", "inputRevision": 2,
                "video": source.document(), "preparedMedia": media.document()})
            share_body = {"projectId": pid, "skill": "Ball control", "note": "Feedback at the second touch?"}
            post = request("POST", base + "/posts", expected=201, payload=share_body, key="fp69-share-idempotency")
            post_path = base + "/posts/" + post["id"]
            assert request("POST", base + "/posts", expected=201, payload=share_body,
                           key="fp69-share-idempotency")["id"] == post["id"]
            assert request("GET", base + "/posts", actor=1)["items"][0]["id"] == post["id"]
            request("GET", "/api/projects/" + pid, actor=1, expected=404)
            request("POST", post_path + "/assets", actor=2, expected=404)
            grant = request("POST", post_path + "/assets", actor=1)
            assert set(grant["assets"]) == {"video"}
            response = requests.get(grant["assets"]["video"]["url"], timeout=30, allow_redirects=False)
            assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == canonical.sha256
            tests.append("explicit-private-share-real-signed-video-bytes-and-own-api-isolation")
            # A trusted, tiny motion fixture proves new sharing authorization
            # and actual artifact bytes without invoking the production GPU.
            motion_id = uuid.uuid4().hex * 2
            motions.append(motion_id)
            segment = MotionSegment(start_frame=15, end_frame=45)
            input_hash = motion_input_hash(canonical, segment)
            motion_job = JobRecord(id=motion_id, owner_id=uid, project_id=pid, kind="reconstructMotion",
                input_revision=2, input_hash=input_hash, profile_version="body-motion-30fps-v1",
                source_asset=canonical, motion_segment=segment, status="succeeded", stage="ready",
                attempt_count=1, reconstruction_id=motion_id, finished_at=utc_now())
            engine = Engine(model_name="synthetic-proof", upstream_commit="c" * 40,
                            checkpoint_hash="d" * 64, config_hash="e" * 64)
            topology = struct.pack("<3I", 0, 1, 2)
            contract = PlaybackContract(topology_version="mhr-" + hashlib.sha256(topology).hexdigest()[:24] + "-v1",
                                        encoding="motionBuffers")
            motion_root = root / "motion"; motion_root.mkdir()
            (motion_root / "faces.bin").write_bytes(topology)
            # One static triangle repeated for 30 frames, quantized xyz uint16.
            chunk = gzip.compress(struct.pack("<9H", 0, 0, 0, 65535, 0, 0, 0, 65535, 0) * 30, mtime=0)
            (motion_root / "body-000000.bin.gz").write_bytes(chunk)
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
                "-ss", "0.5", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                str(motion_root / "video.mp4")], check=True, capture_output=True, timeout=30)
            manifest = {"schemaVersion": 1, "version": 1, "profileVersion": "body-motion-30fps-v1",
                "jobId": motion_id, "attempt": 1, "inputRevision": 2, "inputHash": input_hash,
                "sourceSha256": canonical.sha256, "sourceStartFrame": 15, "sourceEndFrame": 45,
                "sourceTimeOffset": 0.5, "playbackVideo": "video.mp4", "engine": engine.document(),
                "contract": contract.document(), "vertexCount": 3, "faceCount": 1, "frameCount": 30,
                "min": [0, 0, 0], "span": [1, 1, 1], "encoding": "uint16-le-xyz-gzip",
                "topologyFile": "faces.bin", "fps": 30, "duration": 1,
                "validBodyFrames": 30, "validBallFrames": 0, "ball": {"diameter": 0.22},
                "chunks": [{"start": 0, "count": 30, "file": "body-000000.bin.gz", "bytes": len(chunk)}],
                "frames": [{"index": i, "time": i / 30, "shot": 0, "valid": True,
                            "sourceFrameIndex": 15 + i, "sourceTimestamp": (15 + i) / 30} for i in range(30)]}
            (motion_root / "sequence.json").write_text(json.dumps(manifest))
            motion_assets = tuple(put(motion_root / name, f"reconstruction/{motion_id}/1/{name}", content_type)
                for name, content_type in [("sequence.json", "application/json"), ("faces.bin", "application/octet-stream"),
                                           ("body-000000.bin.gz", "application/gzip"), ("video.mp4", "video/mp4")])
            motion_result = ReconstructionRecord(id=motion_id, owner_id=uid, project_id=pid, job_id=motion_id,
                input_revision=2, profile_version="body-motion-30fps-v1", source_asset=canonical,
                mode="motion", outcome="ready", engine=engine, contract=contract, artifacts=motion_assets,
                requested_frame_count=30, reconstructed_frame_count=30, missing_frame_count=0)
            db.collection("jobs").document(motion_id).set(motion_job.document())
            db.collection("reconstructions").document(motion_id).set(motion_result.document())
            db.collection("projects").document(pid).update({"latestReconstructionId": motion_id})
            assert request("GET", post_path, actor=1).get("motion") is None
            request("POST", post_path + "/motion-assets", actor=1, expected=404)
            motion_body = {**share_body, "reconstructionId": motion_id}
            motion_post = request("POST", base + "/posts", expected=201, payload=motion_body,
                                  key="fp69-motion-share-idempotency")
            motion_path = base + "/posts/" + motion_post["id"]
            assert motion_post["motion"]["reconstruction"]["id"] == motion_id
            assert motion_post["motion"]["job"]["motionSegment"] == {"startFrame": 15, "endFrame": 45}
            assert not {"leaseToken", "leaseExpiresAt", "workerId"}.intersection(motion_post["motion"]["job"])
            assert request("POST", base + "/posts", expected=201, payload=motion_body,
                           key="fp69-motion-share-idempotency")["id"] == motion_post["id"]
            motion_feedback = request("POST", motion_path + "/comments", actor=1, expected=201,
                payload={"text": "Feedback uses the original video clock", "timestamp": 1.75},
                key="fp69-motion-canonical-feedback")
            assert motion_feedback["timestamp"] == 1.75
            motion_retry = request("POST", base + "/posts", expected=201,
                payload={**motion_body, "previousPostId": motion_post["id"]}, key="fp69-motion-linked-retry")
            assert motion_retry["previousPostId"] == motion_post["id"]
            request("GET", "/api/reconstructions/" + motion_id, actor=1, expected=404)
            request("GET", "/api/jobs/" + motion_id, actor=1, expected=404)
            request("POST", motion_path + "/motion-assets", actor=2, expected=404)
            motion_grant = request("POST", motion_path + "/motion-assets", actor=1)
            assert set(motion_grant["assets"]) == {"sequence.json", "faces.bin", "body-000000.bin.gz", "video.mp4"}
            for item in motion_grant["assets"].values():
                fetched = requests.get(item["url"], timeout=30, allow_redirects=False)
                assert fetched.status_code == 200 and len(fetched.content) == item["byteSize"]
                assert hashlib.sha256(fetched.content).hexdigest() == item["sha256"]
            tests.append("explicit-pinned-motion-share-real-artifact-bytes-and-owner-api-isolation")
            # Newer generation metadata never silently changes a published share.
            db.collection("projects").document(pid).update({"latestReconstructionId": "unshared-newer-result"})
            assert request("GET", motion_path, actor=1)["motion"]["reconstruction"]["id"] == motion_id
            db.collection("jobs").document(motion_id).update({"inputHash": "f" * 64})
            request("POST", motion_path + "/motion-assets", actor=1, expected=404)
            db.collection("jobs").document(motion_id).set(motion_job.document())
            assert request("GET", motion_path, actor=1)["motion"]["reconstruction"]["id"] == motion_id
            tests.append("motion-provenance-mutation-revokes-and-new-results-do-not-upgrade-shares")
            comment = request("POST", post_path + "/comments", actor=1, expected=201,
                payload={"text": "Watch the second touch", "timestamp": 0.5}, key="fp69-comment-idempotent")
            assert request("POST", post_path + "/comments", actor=1, expected=201,
                payload={"text": "Watch the second touch", "timestamp": 0.5}, key="fp69-comment-idempotent")["id"] == comment["id"]
            assert request("GET", post_path + "/comments")["items"][0]["timestamp"] == 0.5
            request("POST", post_path + "/comments", actor=1, expected=422,
                    payload={"text": "Outside clip", "timestamp": 2}, key="fp69-invalid-timestamp")
            retry = request("POST", base + "/posts", expected=201, key="fp69-linked-retry-key",
                payload={**share_body, "previousPostId": post["id"], "note": "Another attempt"})
            assert retry["previousPostId"] == post["id"]
            page = request("GET", base + "/posts?limit=1", actor=1)
            assert len(page["items"]) == 1 and page["nextCursor"]
            assert request("GET", base + "/posts?limit=1&cursor=" + page["nextCursor"], actor=1)["items"][0]["id"] != page["items"][0]["id"]
            tests.append("timestamp-feedback-retry-link-pagination-and-idempotency")
            request("DELETE", base + "/members/" + member)
            request("POST", post_path + "/assets", actor=1, expected=404)
            request("POST", motion_path + "/motion-assets", actor=1, expected=404)
            request("POST", "/api/circles/join", actor=1, expected=404, payload={"code": second["code"]})
            fresh_invite = request("POST", base + "/invite")
            request("POST", "/api/circles/join", actor=1, payload={"code": fresh_invite["code"]})
            assert request("GET", post_path + "/comments")["items"] == []
            request("DELETE", post_path)
            request("POST", post_path + "/assets", actor=1, expected=404)
            request("DELETE", motion_path)
            request("POST", motion_path + "/motion-assets", actor=1, expected=404)
            tests.append("member-removal-rejoin-generation-and-unshare-revocation")
            db.collection("projects").document(pid).update({"state": "deleting"})
            request("GET", base + "/posts/" + retry["id"], actor=1, expected=404)
            request("POST", base + "/posts/" + motion_retry["id"] + "/motion-assets", actor=1, expected=404)
            tests.append("source-project-tombstone-revokes-shared-access")
        request("DELETE", base)
        request("GET", base, actor=1, expected=404)
        tests.append("circle-deletion-revokes-members")
    finally:
        failures = []
        removed_objects = 0
        for asset in objects:
            try:
                storage.delete_exact(asset); storage.verify_absent(asset)
                removed_objects += 1
            except Exception:
                failures.append("object")
        for cid in created_circles:
            for collection in ["circleComments", "circlePosts", "circleInvites", "circleCleanup"]:
                for snap in db.collection(collection).where(filter=FieldFilter("circleId", "==", cid)).stream():
                    snap.reference.delete()
                    if snap.reference.get().exists:
                        failures.append("metadata")
                if list(db.collection(collection).where(filter=FieldFilter("circleId", "==", cid)).limit(1).stream()):
                    failures.append("metadata")
            db.collection("circles").document(cid).delete()
            if db.collection("circles").document(cid).get().exists:
                failures.append("circle")
        for pid in projects:
            db.collection("projects").document(pid).delete()
            if db.collection("projects").document(pid).get().exists:
                failures.append("project")
        for identifier in motions:
            for collection in ["jobs", "reconstructions"]:
                db.collection(collection).document(identifier).delete()
                if db.collection(collection).document(identifier).get().exists:
                    failures.append("motion-metadata")
        for owner in body["owners"]:
            for collection in ["circleMemberships", "users"]:
                db.collection(collection).document(owner).delete()
                if db.collection(collection).document(owner).get().exists:
                    failures.append("profile")
        cleanup_verified = not failures
        raw.close(); client.close(); delete_app(runtime)
        print(json.dumps({"ticket": "FP-69", "environment": "dev", "tests": tests,
            "passed": len(tests) == 8 and cleanup_verified,
            "storageObjectsRemoved": removed_objects, "fixtureCleanupVerified": cleanup_verified,
            "fixtureIds": {"owners": body["owners"], "circles": created_circles, "projects": projects, "motions": motions},
            "seededPreparedMedia": True, "seededMotion": True, "newGPUInference": False, "deploymentChanged": False}), flush=True)
        if failures:
            raise RuntimeError("Exact synthetic object cleanup requires attention")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Community DGX proof failed (" + type(error).__name__ + "); credential diagnostics suppressed", file=sys.stderr)
        sys.exit(1)
