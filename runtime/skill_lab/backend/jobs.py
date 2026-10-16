import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import store
from .providers import capabilities, get_provider

EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reconstruct")
LOG = logging.getLogger(__name__)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def start(project_id: str, provider_name: str, frame_ids: list[str] | None = None,
          *, background=True) -> dict:
    capability = next((p for p in capabilities() if p["id"] == provider_name), None)
    if not capability or not capability["ready"]:
        raise ValueError(capability["reason"] if capability else "Unknown provider")
    with store.LOCK:
        project = store.read(project_id)
        if (project.get("job") or {}).get("status") in ("queued", "running") or project.get("motion", {}).get("status") in ("queued", "running"):
            raise ValueError("This project already has a reconstruction job")
        ids = set(frame_ids) if frame_ids is not None else {f["id"] for f in project["frames"] if f["status"] != "ready"}
        if not ids or not ids.issubset({f["id"] for f in project["frames"]}):
            raise ValueError("Choose at least one valid frame that needs reconstruction")
        job = {"id": uuid.uuid4().hex[:16], "status": "queued", "provider": provider_name,
               "frameIds": [f["id"] for f in project["frames"] if f["id"] in ids], "completed": 0, "failed": 0,
               "total": len(ids), "startedAt": timestamp()}
        project["job"] = job
        for frame in project["frames"]:
            if frame["id"] in ids:
                frame.update(status="queued", error=None, meshUrl=None)
        store.save(project)
    if background:
        EXECUTOR.submit(run, project_id, provider_name, job["id"])
    else:
        run(project_id, provider_name, job["id"])
    return job


def run(project_id: str, provider_name: str, job_id: str):
    provider = get_provider(provider_name)
    with store.LOCK:
        project = store.read(project_id)
        project["job"]["status"] = "running"
        store.save(project)
        ids = project["job"]["frameIds"]
    for frame_id in ids:
        with store.LOCK:
            project = store.read(project_id)
            frame = next(f for f in project["frames"] if f["id"] == frame_id)
            frame["status"] = "running"
            store.save(project)
            bbox = frame["bbox"][:]
        directory = store.project_dir(project_id)
        relative = f"models/{frame_id}-{job_id}.glb"
        try:
            info = provider.reconstruct(directory / "frames" / f"{frame_id}.jpg", bbox,
                                        directory / relative)
            with store.LOCK:
                project = store.read(project_id)
                frame = next(f for f in project["frames"] if f["id"] == frame_id)
                frame.update(status="ready", meshUrl=store.asset_url(project_id, relative),
                             metadataUrl=store.asset_url(project_id, relative.replace(".glb", ".json")),
                             provider=provider_name, meshInfo=info, error=None)
                project["job"]["completed"] += 1
                store.save(project)
        except Exception as exc:
            # Do not leak URLs, keys, or upstream request dumps to the browser.
            LOG.error("Reconstruction failed for frame %s (%s)", frame_id, type(exc).__name__)
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f"{type(exc).__name__}: inference failed. Check provider availability and model setup."
            message = message[:400]
            with store.LOCK:
                project = store.read(project_id)
                frame = next(f for f in project["frames"] if f["id"] == frame_id)
                frame.update(status="failed", error=message, meshUrl=None)
                project["job"]["failed"] += 1
                store.save(project)
    with store.LOCK:
        project = store.read(project_id)
        job = project["job"]
        job["status"] = "complete" if job["failed"] == 0 else "partial" if job["completed"] else "failed"
        job["finishedAt"] = timestamp()
        store.save(project)


def recover_interrupted():
    """Explicit retry on restart; never silently duplicate billable remote requests."""
    for project in store.list_projects():
        if project.get("motion", {}).get("status") in ("running", "queued"):
            project["motion"].update(status="interrupted", error="Server restarted. Retry resumes saved work.")
            store.save(project)
        if project.get("job") and project["job"]["status"] in ("running", "queued"):
            project["job"]["status"] = "interrupted"
            for frame in project["frames"]:
                if frame["status"] in ("running", "queued"):
                    frame.update(status="failed", error="Server restarted during reconstruction. Retry this frame.")
            store.save(project)
