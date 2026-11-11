#!/usr/bin/env python3
"""Single DGX GPU runner: reconstructions (FP-39 adapter) and, when configured, lessons.

Lesson jobs (kind buildLesson) run only with --lesson-adapter and a Gemini key in the private
runtime configuration; otherwise they stay queued for a runner that can take them.
"""
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
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # lesson_pipeline
from cloud_config import load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "demo"])
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--adapter-arg", action="append", default=[])
    parser.add_argument("--lesson-adapter", type=Path)
    parser.add_argument("--lesson-adapter-arg", action="append", default=[])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    adapter = args.adapter.resolve(strict=True)
    if not adapter.is_file() or adapter.suffix != ".py":
        parser.error("Adapter must be a local Python file")
    lesson_adapter = args.lesson_adapter.resolve(strict=True) if args.lesson_adapter else None
    if lesson_adapter and (not lesson_adapter.is_file() or lesson_adapter.suffix != ".py"):
        parser.error("Lesson adapter must be a local Python file")
    load(args.environment)
    from google.cloud import firestore
    from footylevels.assets import SupabaseAssets, StorageUnavailable
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.reconstruction_worker import run_job
    from footylevels.lesson_jobs import FirestoreLessonJobs
    from footylevels.lesson_worker import run_job as run_lesson
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
        lesson_jobs = FirestoreLessonJobs(client)
        model = os.environ.get("GEMINI_MODEL") or "gemini-4-carbon"
        lessons_enabled = bool(lesson_adapter and os.environ.get("GEMINI_API_KEY"))
        # Lesson screening, close-ups and bundle checks call ffmpeg/ffprobe by name in this
        # parent; use the same pinned toolchain the adapter is given, not whatever is on PATH.
        tools = [arg.split("=", 1)[1] for arg in args.lesson_adapter_arg if arg.startswith("--ffmpeg=")]
        if lessons_enabled and tools:
            os.environ["PATH"] = str(Path(tools[0]).parent) + os.pathsep + os.environ.get("PATH", os.defpath)
        lesson_cache = root / "lesson-cache"
        lesson_cache.mkdir(exist_ok=True, mode=0o700)
        for path in lesson_cache.iterdir():
            if not path.is_symlink() and path.is_dir() and path.stat().st_mtime < time.time() - 7 * 86400:
                shutil.rmtree(path)
        for path in root.iterdir():
            if (re.fullmatch(r"(reconstruction|lesson)-[a-z0-9_]{8}", path.name) and not path.is_symlink()
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
                            lesson = (snapshot.to_dict() or {}).get("kind") == "buildLesson"
                            if lesson and not lessons_enabled:
                                continue
                            claim = (lesson_jobs if lesson else jobs).claim(snapshot.id, worker)
                            if not claim:
                                continue
                            # Also bound hung cloud calls. On process loss the
                            # adapter dies on Linux and the lease is recoverable.
                            watchdog = threading.Timer(3600, lambda: os._exit(1))
                            watchdog.daemon = True
                            watchdog.start()
                            try:
                                if lesson:
                                    outcome = run_lesson(lesson_jobs, storage, claim,
                                                         [sys.executable, str(lesson_adapter), *args.lesson_adapter_arg],
                                                         root, model=model, cache_root=lesson_cache, stop=stop)
                                else:
                                    outcome = run_job(jobs, storage, claim, [sys.executable, str(adapter), *args.adapter_arg], root, stop=stop)
                            finally:
                                watchdog.cancel()
                            print(json.dumps({"service": "reconstruction-worker", "kind": "buildLesson" if lesson else "reconstruction",
                                              "outcome": outcome}), flush=True)
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
