#!/usr/bin/env python3
"""Single DGX reconstruction runner; requires an explicit verified FP-39 adapter."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cloud_config import load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "demo"])
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    adapter = args.adapter.resolve(strict=True)
    if not adapter.is_file() or adapter.suffix != ".py":
        parser.error("Adapter must be a local Python file")
    load(args.environment)
    from google.cloud import firestore
    from footylevels.assets import SupabaseAssets, StorageUnavailable
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.reconstruction_worker import run_job
    from footylevels.repositories import RecordConflict, RecordNotFound
    root = Path.home() / ".local/share/footylevels/reconstruction-worker" / args.environment
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    # One GPU runtime is shared between environments. A machine-wide lock under
    # our user's private directory forbids overlapping Dev and Demo dispatchers.
    with (root.parent / "gpu-worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A reconstruction runner already owns this GPU runtime") from None
        client = firestore.Client.from_service_account_json(os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
                                                            project=os.environ["FIREBASE_PROJECT_ID"])
        storage = SupabaseAssets(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
        jobs = FirestoreReconstructionJobs(client)
        for path in root.iterdir():
            if (re.fullmatch(r"reconstruction-[a-z0-9_]{8}", path.name) and not path.is_symlink()
                    and path.is_dir() and path.stat().st_mtime < time.time() - 86400):
                shutil.rmtree(path)
        cursor, worker = None, "gpu-" + uuid.uuid4().hex
        try:
            while not stop.is_set():
                try:
                    snapshots = jobs.candidates(limit=10, after=cursor)
                    cursor = snapshots[-1].id if snapshots else None
                    for snapshot in snapshots:
                        if stop.is_set():
                            break
                        try:
                            claim = jobs.claim(snapshot.id, worker)
                            if not claim:
                                continue
                            # Also bound hung cloud calls. On process loss the
                            # adapter dies on Linux and the lease is recoverable.
                            watchdog = threading.Timer(3600, lambda: os._exit(1))
                            watchdog.daemon = True
                            watchdog.start()
                            try:
                                outcome = run_job(jobs, storage, claim, [sys.executable, str(adapter)], root, stop=stop)
                            finally:
                                watchdog.cancel()
                            print(json.dumps({"service": "reconstruction-worker", "outcome": outcome}), flush=True)
                        except (RecordConflict, RecordNotFound):
                            pass
                except StorageUnavailable:
                    print(json.dumps({"service": "reconstruction-worker", "outcome": "temporarily-unavailable"}), flush=True)
                if args.once:
                    break
                stop.wait(120)
        finally:
            client.close()


if __name__ == "__main__":
    main()
