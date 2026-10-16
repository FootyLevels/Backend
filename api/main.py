"""
FootyLevels API
FastAPI backend for 3D body extraction from soccer skill video.
"""

import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels import SAM3DBodyExtractor, SequenceStorage, SkillCatalog, resolve_device
from footylevels.reconstruct import ImageReconstructor, ReconstructionError

app = FastAPI(
    title="FootyLevels API",
    description="3D body extraction for soccer skill video, powered by SAM 3D Body",
    version="0.2.0",
)

# CORS for the frontend.
# Note: allow_credentials must stay False while origins is "*" — browsers
# reject that combination. Set explicit origins if cookies are ever needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path(__file__).parent.parent / "data"
REFERENCE_DIR = DATA_DIR / "reference"
SEQUENCE_DIR = DATA_DIR / "sequences"
RECONSTRUCTION_DIR = DATA_DIR / "output" / "reconstructions"

IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"}

# In-memory job table. Extraction runs per frame through an 840M-parameter
# model, so it is far too slow to answer inline. Swap for Redis or a database
# before this runs anywhere real.
extraction_jobs: Dict[str, dict] = {}


# -- models ---------------------------------------------------------------


class SkillInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    difficulty: str = "intermediate"
    player_id: Optional[str] = None
    player_name: Optional[str] = None
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    origin_story: Optional[str] = None


class PlayerInfo(BaseModel):
    id: str
    name: str
    nickname: str = ""
    nationality: str = ""
    era: str = ""
    category: str = "legend"
    image_url: str = ""
    bio: str = ""
    skills: List[str] = []


class CategoryInfo(BaseModel):
    id: str
    name: str
    skills: List[str]


class ProgressionInfo(BaseModel):
    beginner: List[str] = []
    intermediate: List[str] = []
    advanced: List[str] = []


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued | running | done | failed
    created_at: str
    sequence_id: Optional[str] = None
    summary: Optional[dict] = None
    error: Optional[str] = None


# -- shared state ---------------------------------------------------------

_catalog: Optional[SkillCatalog] = None
_extractor: Optional[SAM3DBodyExtractor] = None


def get_catalog() -> SkillCatalog:
    """The skill/player catalog, loaded once."""
    global _catalog
    if _catalog is None:
        _catalog = SkillCatalog()
    return _catalog


def get_extractor() -> SAM3DBodyExtractor:
    """The extractor, constructed once. Model weights load on first inference."""
    global _extractor
    if _extractor is None:
        _extractor = SAM3DBodyExtractor()
    return _extractor


def get_storage() -> SequenceStorage:
    return SequenceStorage(str(SEQUENCE_DIR))


_reconstructor: Optional[ImageReconstructor] = None


def get_reconstructor() -> ImageReconstructor:
    """The image reconstructor, sharing the API's single loaded model."""
    global _reconstructor
    if _reconstructor is None:
        _reconstructor = ImageReconstructor(
            output_dir=str(RECONSTRUCTION_DIR), extractor=get_extractor()
        )
    return _reconstructor


def _require_model_ready() -> None:
    """Refuse work with a clear reason when the checkpoint is incomplete."""
    missing = get_extractor().check_checkpoint()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                f"SAM 3D Body checkpoint incomplete. Missing: {', '.join(missing)}. "
                f"See /api/model/status."
            ),
        )


def _skill_info(skill: dict, catalog: SkillCatalog) -> SkillInfo:
    """Build a SkillInfo, resolving the player's display name."""
    player_id = skill.get("player_id")
    player = catalog.players.get(player_id, {}) if player_id else {}
    return SkillInfo(
        id=skill["id"],
        name=skill.get("name", skill["id"]),
        description=skill.get("description", ""),
        difficulty=skill.get("difficulty", "intermediate"),
        player_id=player_id,
        player_name=player.get("name"),
        video_url=skill.get("video_url"),
        thumbnail_url=skill.get("thumbnail_url"),
        origin_story=skill.get("origin_story"),
    )


# -- health and model status ---------------------------------------------


@app.get("/")
async def root():
    """Health check."""
    return {"status": "ok", "service": "FootyLevels API", "version": "0.2.0"}


@app.get("/api/model/status")
async def model_status():
    """Report whether the SAM 3D Body checkpoint is present and usable.

    Weights are gated on Hugging Face and total roughly 2.8 GB, so this is the
    quickest way to confirm a deployment can actually run inference.
    """
    extractor = get_extractor()
    missing = extractor.check_checkpoint()
    return {
        "model_id": extractor.hf_repo_id or "facebook/sam-3d-body-dinov3",
        "checkpoint_dir": str(extractor.checkpoint_dir),
        "ready": not missing,
        "missing_files": missing,
        "device": resolve_device(),
        "loaded": extractor._estimator is not None,
    }


# -- catalog --------------------------------------------------------------


@app.get("/api/skills", response_model=List[SkillInfo])
async def list_skills():
    """All supported skills."""
    catalog = get_catalog()
    return [_skill_info(s, catalog) for s in catalog.list_skills()]


@app.get("/api/skills/difficulty/{difficulty}", response_model=List[SkillInfo])
async def list_skills_by_difficulty(difficulty: str):
    """Skills at one difficulty level."""
    if difficulty not in ("beginner", "intermediate", "advanced"):
        raise HTTPException(
            status_code=400,
            detail="Invalid difficulty. Use: beginner, intermediate, advanced",
        )
    catalog = get_catalog()
    return [_skill_info(s, catalog) for s in catalog.skills_by_difficulty(difficulty)]


@app.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str):
    """One skill, including its player and phase breakdown."""
    catalog = get_catalog()
    try:
        skill = catalog.get_skill(skill_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    player_id = skill.get("player_id")
    player = catalog.players.get(player_id) if player_id else None

    return {
        **skill,
        "player": {"id": player_id, **player} if player else None,
        "has_reference_video": (REFERENCE_DIR / f"{skill_id}.mp4").exists(),
    }


@app.get("/api/players", response_model=List[PlayerInfo])
async def list_players():
    """All players."""
    return [PlayerInfo(**p) for p in get_catalog().list_players()]


@app.get("/api/players/category/{category}", response_model=List[PlayerInfo])
async def list_players_by_category(category: str):
    """Players in one category, e.g. 'legend' or 'current'."""
    return [PlayerInfo(**p) for p in get_catalog().players_by_category(category)]


@app.get("/api/players/{player_id}")
async def get_player(player_id: str):
    """One player and the skills attributed to them."""
    catalog = get_catalog()
    try:
        player = catalog.get_player(player_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}")

    return {**player, "skill_details": catalog.skills_for_player(player_id)}


@app.get("/api/categories", response_model=List[CategoryInfo])
async def list_categories():
    """Skill categories for browsing."""
    return [CategoryInfo(**c) for c in get_catalog().categories]


@app.get("/api/progression", response_model=ProgressionInfo)
async def get_progression():
    """Skill progression by difficulty."""
    return ProgressionInfo(**get_catalog().progression)


# -- image reconstruction (the iOS path) ----------------------------------


@app.post("/api/reconstruct")
async def reconstruct_image(
    image: UploadFile = File(...),
    y_up: bool = Form(True),
    preview: bool = Form(True),
    raw: bool = Form(False),
):
    """
    Reconstruct a 3D body from one photo and return a renderable USDZ.

    This runs inline: a single image is one forward pass, so there is no job to
    poll. Expect a few seconds on GPU/MPS and longer on CPU — set a generous
    client timeout.

    The response carries URLs rather than the model bytes, because RealityKit
    needs a local file: download `model_url`, write it to disk, then hand that
    file URL to `ModelEntity(contentsOf:)`.

    - **image**: the photo (JPEG, PNG, HEIC, WebP)
    - **y_up**: convert the camera frame to USD's Y-up convention
    - **preview**: also render a 2D keypoint overlay for debugging
    - **raw**: also emit positions/normals as float32 buffers, for clients
      building a RealityKit MeshDescriptor or LowLevelMesh directly
    """
    _require_model_ready()

    if image.content_type and image.content_type not in IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image type: {image.content_type}",
        )

    temp_dir = Path(tempfile.mkdtemp())
    suffix = Path(image.filename or "upload.jpg").suffix or ".jpg"
    upload_path = temp_dir / f"upload_{uuid.uuid4().hex}{suffix}"

    try:
        with open(upload_path, "wb") as f:
            shutil.copyfileobj(image.file, f)

        metadata = get_reconstructor().reconstruct(
            str(upload_path), y_up=y_up, write_preview=preview, write_raw=raw
        )
        metadata["source_image"] = image.filename or upload_path.name
    except ReconstructionError as exc:
        # The image was fine but yielded nothing to render.
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return _with_urls(metadata)


def _with_urls(metadata: dict) -> dict:
    """Attach the client-facing download URLs to a reconstruction's metadata."""
    base = f"/api/reconstruct/{metadata['id']}"
    enriched = {
        **metadata,
        "model_url": f"{base}/model.usdz",
        "keypoints_url": f"{base}/keypoints",
        "preview_url": f"{base}/preview.jpg" if metadata.get("has_preview") else None,
    }

    if metadata.get("raw"):
        enriched["raw"] = {
            **metadata["raw"],
            "positions_url": f"{base}/positions.bin",
            "normals_url": f"{base}/normals.bin",
            # Topology is shared by every reconstruction, so it is not per-id.
            "indices_url": "/api/topology",
        }
    return enriched


def _raw_buffer_response(reconstruction_id: str, filename: str):
    """Serve one raw vertex buffer as octet-stream."""
    path = get_reconstructor().bundle_dir(reconstruction_id) / filename
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No {filename} for {reconstruction_id}. "
                f"Reconstruct with raw=true to generate it."
            ),
        )
    return FileResponse(str(path), media_type="application/octet-stream")


@app.get("/api/topology")
async def get_topology(response: Response):
    """The MHR triangle indices, as a flat little-endian uint32 buffer.

    Identical for every reconstruction — the model's vertex count and
    connectivity never change — so a client should fetch this once and cache
    it, then download only positions per photo. That is the entire reason the
    raw path is smaller than USDZ: topology is ~432 KB and would otherwise be
    re-sent every time.

    Served with a long max-age and a strong ETag so revalidation is cheap.
    """
    _require_model_ready()

    faces = get_extractor().faces
    if faces is None:
        raise HTTPException(status_code=503, detail="Model exposed no topology")

    import hashlib

    import numpy as np

    payload = np.ascontiguousarray(faces, dtype="<u4").tobytes()
    etag = hashlib.sha256(payload).hexdigest()[:32]

    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["ETag"] = f'"{etag}"'
    response.headers["X-Triangle-Count"] = str(len(faces))
    response.headers["X-Index-Dtype"] = "uint32-le"
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers=dict(response.headers),
    )


@app.get("/api/reconstruct/{reconstruction_id}/positions.bin")
async def get_positions(reconstruction_id: str):
    """Vertex positions: float32 xyz interleaved, little-endian, metres."""
    return _raw_buffer_response(reconstruction_id, "positions.bin")


@app.get("/api/reconstruct/{reconstruction_id}/normals.bin")
async def get_normals(reconstruction_id: str):
    """Smooth vertex normals: float32 xyz interleaved, little-endian."""
    return _raw_buffer_response(reconstruction_id, "normals.bin")


@app.get("/api/reconstruct/{reconstruction_id}/model.usdz")
async def get_reconstruction_model(reconstruction_id: str):
    """Download the USDZ. Feed this to RealityKit's ModelEntity(contentsOf:)."""
    reconstructor = get_reconstructor()
    path = reconstructor.bundle_dir(reconstruction_id) / "model.usdz"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {reconstruction_id}")

    return FileResponse(
        str(path),
        media_type="model/vnd.usdz+zip",
        filename=f"{reconstruction_id}.usdz",
    )


@app.get("/api/reconstruct/{reconstruction_id}/keypoints")
async def get_reconstruction_keypoints(reconstruction_id: str):
    """The subject's 3D and 2D joints — a few KB, versus ~0.7 MB for the mesh."""
    path = get_reconstructor().bundle_dir(reconstruction_id) / "keypoints.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {reconstruction_id}")
    return FileResponse(str(path), media_type="application/json")


@app.get("/api/reconstruct/{reconstruction_id}/preview.jpg")
async def get_reconstruction_preview(reconstruction_id: str):
    """The 2D keypoint overlay, for checking the model latched onto the player."""
    path = get_reconstructor().bundle_dir(reconstruction_id) / "preview.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {reconstruction_id}")
    return FileResponse(str(path), media_type="image/jpeg")


@app.get("/api/reconstruct/{reconstruction_id}")
async def get_reconstruction(reconstruction_id: str):
    """One reconstruction's metadata."""
    try:
        return _with_urls(get_reconstructor().load_metadata(reconstruction_id))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Not found: {reconstruction_id}")


@app.get("/api/reconstructions")
async def list_reconstructions():
    """Every stored reconstruction, newest first."""
    return [_with_urls(m) for m in get_reconstructor().list_reconstructions()]


@app.delete("/api/reconstruct/{reconstruction_id}")
async def delete_reconstruction(reconstruction_id: str):
    """Delete a reconstruction bundle."""
    if not get_reconstructor().delete(reconstruction_id):
        raise HTTPException(status_code=404, detail=f"Not found: {reconstruction_id}")
    return {"deleted": reconstruction_id}


# -- extraction -----------------------------------------------------------


def _run_extraction(
    job_id: str,
    video_path: Path,
    temp_dir: Path,
    name: str,
    stride: int,
    max_frames: Optional[int],
    include_vertices: bool,
) -> None:
    """Background worker: extract a video, store the result, update the job."""
    job = extraction_jobs[job_id]
    job["status"] = "running"

    try:
        extractor = get_extractor()
        extractor.include_vertices = include_vertices

        sequence = extractor.extract_from_video(
            str(video_path),
            max_frames=max_frames,
            stride=stride,
            progress=False,
        )
        sequence_id = get_storage().save_sequence(
            sequence, name=name, video_path=str(video_path)
        )

        job["status"] = "done"
        job["sequence_id"] = sequence_id
        job["summary"] = sequence.summary()
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/extract", response_model=JobStatus)
async def extract_video(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    name: str = Form("upload"),
    stride: int = Form(1),
    max_frames: Optional[int] = Form(None),
    include_vertices: bool = Form(False),
):
    """
    Queue 3D body extraction for an uploaded video.

    Returns immediately with a job id — inference runs one forward pass per
    frame, so a short clip can still take minutes. Poll /api/extract/{job_id}.

    - **video**: the clip to process
    - **name**: base name for the stored sequence
    - **stride**: process every Nth frame (raise this to go faster)
    - **max_frames**: stop after this many processed frames
    - **include_vertices**: keep full meshes (~220 KB per frame)
    """
    extractor = get_extractor()
    missing = extractor.check_checkpoint()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                f"SAM 3D Body checkpoint incomplete. Missing: {', '.join(missing)}. "
                f"See /api/model/status."
            ),
        )

    temp_dir = Path(tempfile.mkdtemp())
    video_path = temp_dir / f"upload_{uuid.uuid4().hex}.mp4"
    with open(video_path, "wb") as f:
        shutil.copyfileobj(video.file, f)

    job_id = uuid.uuid4().hex
    extraction_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "sequence_id": None,
        "summary": None,
        "error": None,
    }

    background_tasks.add_task(
        _run_extraction,
        job_id, video_path, temp_dir, name, stride, max_frames, include_vertices,
    )

    return JobStatus(**extraction_jobs[job_id])


@app.get("/api/extract/{job_id}", response_model=JobStatus)
async def get_extraction_job(job_id: str):
    """Check on a queued extraction."""
    if job_id not in extraction_jobs:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return JobStatus(**extraction_jobs[job_id])


# -- stored sequences -----------------------------------------------------


@app.get("/api/sequences")
async def list_sequences():
    """Every stored body sequence, newest first."""
    return get_storage().list_sequences()


@app.get("/api/sequences/{sequence_id}")
async def get_sequence(sequence_id: str):
    """One stored sequence's metadata."""
    try:
        return get_storage().load_metadata(sequence_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Sequence not found: {sequence_id}")


@app.get("/api/sequences/{sequence_id}/keypoints")
async def get_sequence_keypoints(sequence_id: str):
    """One stored sequence's 3D and 2D keypoints, as nested lists."""
    try:
        sequence = get_storage().load_sequence(sequence_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Sequence not found: {sequence_id}")

    return {
        "id": sequence_id,
        "num_joints": sequence.num_joints,
        "frame_indices": [f.frame_index for f in sequence.detected_frames],
        "timestamps_ms": [f.timestamp_ms for f in sequence.detected_frames],
        "keypoints_3d": sequence.keypoints_3d().tolist(),
        "keypoints_2d": sequence.keypoints_2d().tolist(),
    }


@app.delete("/api/sequences/{sequence_id}")
async def delete_sequence(sequence_id: str):
    """Delete a stored sequence."""
    get_storage().delete_sequence(sequence_id)
    return {"deleted": sequence_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
