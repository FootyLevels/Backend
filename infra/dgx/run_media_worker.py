#!/usr/bin/env python3
"""Run the authenticated deployment's bounded CPU work without GPU/model imports."""
import argparse
import fcntl
import json
import os
import re
import shutil
from pathlib import Path
import sys
import threading
import time
import uuid
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cloud_config import load


def reconcile(jobs, storage, cursors):
    """Bounded scans, retaining an audit record and releasing quota after absence."""
    from google.cloud.firestore_v1.base_query import FieldFilter
    from footylevels.records import utc_now
    from footylevels.repositories import RecordConflict, RecordNotFound
    plans = [("uploads", "cleanupAfter", utc_now(), jobs.cleanup_expired),
             ("assetReservations", "createdAt", utc_now() - timedelta(hours=24), jobs.cleanup_orphan_output)]
    cleaned = 0
    for collection, field, before, cleanup in plans:
        query = jobs.client.collection(collection).where(filter=FieldFilter(field, "<=", before)).order_by(field)
        if collection in cursors:
            query = query.start_after(cursors[collection])
        snapshots = list(query.limit(10).stream())
        if not snapshots:
            cursors.pop(collection, None)
        for snapshot in snapshots:
            raw = snapshot.to_dict()
            cursors[collection] = {field: raw[field], "__name__": snapshot.reference}
            eligible = (raw.get("status") != "committed" and raw.get("reservedBytes", 0) > 0) if collection == "uploads" else (raw.get("jobId") and raw.get("bytes", 0) > 0)
            if eligible:
                try:
                    cleanup(snapshot.id, storage)
                    cleaned += 1
                except (RecordConflict, RecordNotFound):
                    pass
    return cleaned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "demo"])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    load(args.environment)
    from google.cloud import firestore
    from footylevels.assets import SupabaseAssets, StorageUnavailable
    from footylevels.media_jobs import FirestoreMediaJobs
    from footylevels.media_worker import process_job
    from footylevels.repositories import RecordConflict, RecordNotFound
    root = Path.home() / ".local/share/footylevels/media-worker" / args.environment
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    with (root / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A media worker already owns this environment") from None
        client = firestore.Client.from_service_account_json(os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
                                                            project=os.environ["FIREBASE_PROJECT_ID"])
        jobs = FirestoreMediaJobs(client)
        storage = SupabaseAssets(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
        for path in root.iterdir():
            if (re.fullmatch(r"media-[a-z0-9_]{8}", path.name) and not path.is_symlink() and path.is_dir()
                    and path.stat().st_mtime < time.time() - 86400):
                shutil.rmtree(path)
        # FFmpeg is already installed in this user's runtime; no sudo or model load.
        os.environ["PATH"] = str(Path.home() / "skill-lab/.media/bin") + os.pathsep + os.environ.get("PATH", os.defpath)
        worker = "media-" + uuid.uuid4().hex
        cursor = None
        maintenance_cursors = {}
        try:
            while True:
                try:
                    candidates = jobs.candidates(limit=10, after=cursor)
                    cursor = candidates[-1] if candidates else None
                    for project in candidates:
                        try:
                            job = jobs.claim(project, worker)
                            if job:
                                # A hard supervisor deadline also bounds stalled
                                # cloud calls. The durable lease outlives this
                                # process, so restart can safely reclaim it.
                                def deadline():
                                    print(json.dumps({"service": "media-worker", "outcome": "worker-deadline"}), flush=True)
                                    os._exit(1)
                                watchdog = threading.Timer(600, deadline)
                                watchdog.daemon = True
                                watchdog.start()
                                try:
                                    result = process_job(jobs, storage, job, root)
                                finally:
                                    watchdog.cancel()
                                print(json.dumps({"service": "media-worker", "outcome": result}), flush=True)
                        except (RecordConflict, RecordNotFound):
                            continue
                    cleaned = reconcile(jobs, storage, maintenance_cursors)
                    if cleaned:
                        print(json.dumps({"service": "media-worker", "cleaned": cleaned}), flush=True)
                except StorageUnavailable:
                    print(json.dumps({"service": "media-worker", "outcome": "temporarily-unavailable"}), flush=True)
                if args.once:
                    break
                # Firestore Spark reads stay bounded; this is not a second API or
                # paid queue. API requests never wait for decoding.
                time.sleep(120)
        finally:
            client.close()


if __name__ == "__main__":
    main()
