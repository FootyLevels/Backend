"""Serial, fenced execution boundary for a local reconstruction adapter.

The adapter receives local files and public provenance only. Firebase/Supabase
credentials and publication rights remain in this parent process.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .assets import StorageUnavailable
from .records import ReconstructionRecord
from .repositories import RecordConflict, RecordNotFound
from .uploads import StorageQuotaExceeded


class AdapterFailed(Exception):
    pass


def validate_output(claim, result, directory):
    expected_mode = "motion" if claim.kind == "reconstructMotion" else "keyframes"
    if (result.id, result.job_id, result.owner_id, result.project_id, result.input_revision,
        result.source_asset, result.profile_version, result.mode) != (
        claim.id, claim.id, claim.owner_id, claim.project_id, claim.input_revision,
        claim.source_asset, claim.profile_version, expected_mode):
        raise AdapterFailed()
    prefix = f"users/{claim.owner_id}/projects/{claim.project_id}/reconstruction/{claim.id}/{claim.attempt_count}/"
    files = []
    seen = set()
    root = (directory / "artifacts").resolve()
    for asset in result.artifacts:
        if asset.bucket != "private-media" or not asset.object_key.startswith(prefix) or asset.object_key in seen:
            raise AdapterFailed()
        seen.add(asset.object_key)
        relative = Path(asset.object_key[len(prefix):])
        path = directory / "artifacts" / relative
        # Do not follow even an in-tree adapter symlink. Parent directories are
        # checked as well; temporary output never aliases shared model weights.
        if (any((directory / "artifacts" / parent).is_symlink() for parent in [relative, *relative.parents])
                or not path.is_file() or not path.resolve().is_relative_to(root)
                or path.stat().st_size != asset.byte_size):
            raise AdapterFailed()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != asset.sha256:
            raise AdapterFailed()
        files.append(path)
    return files


def stop_child(process):
    # The adapter owns a separate process group; stop only that attempt.
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def run_job(jobs, storage, claim, adapter_command, staging_root, *, heartbeat_seconds=30,
            command_timeout=3500, stop=None):
    """One attempt. A caller must serialize calls and enforce a process watchdog."""
    if not adapter_command or heartbeat_seconds <= 0 or not 0 < command_timeout <= 3500:
        raise ValueError("Expected a bounded adapter command")
    finished, lost = threading.Event(), threading.Event()
    failure = []
    process = None

    def heartbeat():
        while not finished.wait(heartbeat_seconds):
            try:
                jobs.checkpoint(claim)
            except Exception as error:
                failure.append(error)
                lost.set()
                return

    def check(stage=None):
        if stop and stop.is_set():
            raise RecordConflict("Worker is stopping")
        if lost.is_set():
            raise failure[0]
        return jobs.checkpoint(claim, stage)

    monitor = threading.Thread(target=heartbeat, daemon=True)
    try:
        check("downloadingInput")
        monitor.start()
        with tempfile.TemporaryDirectory(prefix="reconstruction-", dir=staging_root) as temporary:
            directory = Path(temporary)
            storage.download_file(claim.source_asset, directory / "input.mp4")
            # No lease token, worker identifier, signed URL or cloud credential
            # crosses into the adapter. JSON remains private to this attempt.
            payload = claim.model_dump(mode="json", by_alias=True,
                exclude={"lease_token", "worker_id", "lease_expires_at"})
            (directory / "job.json").write_text(json.dumps(payload))
            (directory / "job.json").chmod(0o600)
            (directory / "artifacts").mkdir(mode=0o700)
            check("generatingBodies")
            started = time.monotonic()
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("reconstruction_limits.py")),
                                       *adapter_command, str(directory)], cwd=directory,
                env={"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": str(directory), "TMPDIR": str(directory)},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            try:
                while process.poll() is None:
                    if lost.is_set() or (stop and stop.is_set()):
                        check()
                    if time.monotonic() - started >= command_timeout:
                        raise AdapterFailed()
                    time.sleep(0.1)
            finally:
                stop_child(process)
            if process.returncode != 0:
                raise AdapterFailed()
            check("packingAssets")
            result_path = directory / "result.json"
            if result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size > 262144:
                raise AdapterFailed()
            try:
                result = ReconstructionRecord.model_validate_json(result_path.read_bytes())
            except ValueError:
                raise AdapterFailed() from None
            files = validate_output(claim, result, directory)
            jobs.reserve_outputs(claim, result.artifacts)
            check("publishingAssets")
            for asset, filename in zip(result.artifacts, files):
                check()
                # SupabaseAssets.put_file verifies the remote size and hash;
                # avoid a second full download against the Free egress quota.
                storage.put_file(asset, filename)
            check()
            jobs.publish_reconstruction(result, claim.lease_token)
            return result.outcome
    except (RecordConflict, RecordNotFound):
        return "superseded"
    except (StorageUnavailable, StorageQuotaExceeded, OSError):
        try:
            jobs.fail(claim, "model_unavailable", retry=True)
        except (RecordConflict, RecordNotFound, StorageUnavailable):
            pass  # A lost connection leaves the durable lease for restart.
        return "retrying"
    except AdapterFailed:
        try:
            jobs.fail(claim, "reconstruction_failed", retry=True)
        except (RecordConflict, RecordNotFound, StorageUnavailable):
            pass
        return "adapter-failed"
    finally:
        finished.set()
        if process is not None:
            stop_child(process)
        if monitor.ident is not None:
            monitor.join(timeout=2)
