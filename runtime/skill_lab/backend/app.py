import re
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import jobs, store, video, motion
from .providers import capabilities


@asynccontextmanager
async def lifespan(app):
    jobs.recover_interrupted()
    if os.getenv("FOOTYLEVELS_PRELOAD_BODY") == "1":
        from .providers.local import LocalProvider
        LocalProvider().load()
    yield


app = FastAPI(title="Skill Lab", lifespan=lifespan)
# Local workstation tool; remote use goes through an SSH tunnel.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
store.DATA.mkdir(parents=True, exist_ok=True)


@app.middleware("http")
async def local_mutations(request, call_next):
    # Prevent cross-origin browser requests from triggering uploads or paid inference.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
    return await call_next(request)


def get_project(project_id):
    try:
        return store.read(project_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Project not found")


def require_idle(project):
    if (project.get("job") or {}).get("status") in ("running", "queued") or project.get("motion", {}).get("status") in ("running", "queued"):
        raise HTTPException(409, "Wait for reconstruction to finish before editing frames")


@app.get("/api/health")
def health():
    from .providers.local import LocalProvider
    return {"status": "ok", "providers": capabilities(), "motion": motion.capability(),
            "runtime": {"owner": "FootyLevels", "bodyModelLoaded": LocalProvider._estimator is not None,
                        "revision": os.getenv("FOOTYLEVELS_RUNTIME_REVISION", "development")}}


@app.get("/api/projects")
def projects():
    return store.list_projects()


@app.get("/api/projects/{project_id}")
def project(project_id: str):
    return get_project(project_id)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    with store.LOCK:
        require_idle(get_project(project_id))
        shutil.rmtree(store.project_dir(project_id))
    return Response(status_code=204)


@app.post("/api/projects", status_code=201)
def upload(file: UploadFile = File(...)):
    extension = Path(file.filename or "clip.mp4").suffix.lower()
    if extension not in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
        raise HTTPException(422, "Upload an MP4, MOV, WebM, MKV, or M4V video")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / ("input" + extension)
        size = 0
        with source.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > 200 * 1024 * 1024:
                    raise HTTPException(413, "Maximum upload size is 200 MB")
                output.write(chunk)
        try:
            return video.prepare(source, Path(file.filename or "My skill").stem)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))
        except Exception:
            raise HTTPException(422, "Video could not be decoded. Check the file and FFmpeg installation.")


class FrameCreate(BaseModel):
    timestamp: float = Field(ge=0, allow_inf_nan=False)
    label: str = Field(default="", max_length=80)


@app.post("/api/projects/{project_id}/frames", status_code=201)
def create_frame(project_id: str, body: FrameCreate):
    with store.LOCK:
        project = get_project(project_id)
        require_idle(project)
        if len(project["frames"]) >= 24:
            raise HTTPException(422, "Maximum 24 key frames per project")
        try:
            video.add_frame(project, body.timestamp, body.label)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        store.save(project)
        return project


class FramePatch(BaseModel):
    bbox: list[float] | None = None
    label: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_bbox(self):
        if self.bbox is not None:
            b = self.bbox
            if (len(b) != 4 or not all(0 <= n <= 1 for n in b)
                    or b[2] - b[0] < .03 or b[3] - b[1] < .03):
                raise ValueError("Use a normalized [left, top, right, bottom] box with positive area")
        return self


@app.patch("/api/projects/{project_id}/frames/{frame_id}")
def edit_frame(project_id: str, frame_id: str, body: FramePatch):
    with store.LOCK:
        project = get_project(project_id)
        require_idle(project)
        frame = next((f for f in project["frames"] if f["id"] == frame_id), None)
        if frame is None:
            raise HTTPException(404, "Frame not found")
        values = body.model_dump(exclude_none=True)
        if body.bbox is not None and body.bbox != frame["bbox"]:
            frame.update(status="pending", meshUrl=None, metadataUrl=None, meshInfo=None,
                         error=None, provider=None)
        frame.update(values)
        store.save(project)
        return project


@app.delete("/api/projects/{project_id}/frames/{frame_id}")
def delete_frame(project_id: str, frame_id: str):
    with store.LOCK:
        project = get_project(project_id)
        require_idle(project)
        if len(project["frames"]) < 2:
            raise HTTPException(422, "Keep at least one key frame")
        if not any(f["id"] == frame_id for f in project["frames"]):
            raise HTTPException(404, "Frame not found")
        project["frames"] = [f for f in project["frames"] if f["id"] != frame_id]
        store.save(project)
        return project


class GenerateBody(BaseModel):
    provider: Literal["local", "fal"]
    frameIds: list[str] | None = None


@app.post("/api/projects/{project_id}/generate", status_code=202)
def generate(project_id: str, body: GenerateBody):
    get_project(project_id)
    try:
        return jobs.start(project_id, body.provider, body.frameIds)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/projects/{project_id}/motion", status_code=202)
def generate_motion(project_id: str):
    get_project(project_id)
    try:
        return motion.start(project_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.get("/assets-data/{project_id}/{kind}/{filename}")
def data_asset(project_id: str, kind: str, filename: str):
    if kind not in ("frames", "models", "motion") or Path(filename).name != filename:
        raise HTTPException(404)
    if kind == "motion" and not (filename in ("sequence.json", "faces.bin", "ball.glb", "ball.json") or re.fullmatch(r"(?:body-[a-z0-9-]+\.bin\.gz|faces-[a-z0-9-]+\.bin)", filename)):
        raise HTTPException(404)
    get_project(project_id)
    path = store.project_dir(project_id) / kind / filename
    if not path.is_file() or path.suffix not in (".jpg", ".glb", ".json", ".bin", ".gz"):
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/assets-data/{project_id}/video.mp4")
def video_asset(project_id: str):
    get_project(project_id)
    return FileResponse(store.project_dir(project_id) / "video.mp4", media_type="video/mp4")


dist = store.ROOT / "web/dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")
