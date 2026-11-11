"""Small, versioned metadata records. Media bytes and credentials belong elsewhere."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field,
                      model_validator)
from pydantic.alias_generators import to_camel


def document_id(value: str) -> str:
    if value in {".", ".."} or "/" in value or value.startswith("__"):
        raise ValueError("Expected a single non-reserved document ID")
    return value


Id = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(document_id)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True,
                              alias_generator=to_camel)

    def document(self) -> dict:
        return self.model_dump(mode="python", by_alias=True)


class Record(Metadata):
    id: Id
    schema_version: Literal[1] = 1
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class AssetRef(Metadata):
    bucket: Literal["private-media", "reference-assets"]
    object_key: str = Field(min_length=1, max_length=1024)
    sha256: Sha256
    byte_size: int = Field(gt=0, le=52428800)
    content_type: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_path(self):
        if any(ord(c) < 32 or ord(c) == 127 for c in self.object_key) or "%" in self.object_key or self.object_key.startswith("/") or any(c in self.object_key for c in [":", "?", "#", "\\"]):
            raise ValueError("Store an object key, never a URL or capability")
        if any(part in {"", ".", ".."} for part in self.object_key.split("/")):
            raise ValueError("Invalid object path")
        return self


LESSON_AI_CONSENT = "lesson-ai-v1"


def _without_unset(payload: dict, *fields: str) -> dict:
    # Rolling upgrades: older strict readers forbid unknown fields, so optional
    # new fields are written only once they hold a value.
    for field in fields:
        if payload.get(field) is None:
            payload.pop(field, None)
    return payload


class UserRecord(Record):
    display_name: str = Field(min_length=1, max_length=80)
    account_state: Literal["active", "restricted", "deleting", "deleted"] = "active"
    # Asked once per account before any video is sent to an AI model for a lesson.
    lesson_ai_consent_at: AwareDatetime | None = None
    lesson_ai_consent_version: Literal["lesson-ai-v1"] | None = None

    def document(self):
        return _without_unset(super().document(), "lessonAiConsentAt", "lessonAiConsentVersion")

    @property
    def lesson_ai_consented(self) -> bool:
        return self.lesson_ai_consent_version == LESSON_AI_CONSENT and self.lesson_ai_consent_at is not None


class MediaPreview(Metadata):
    asset: AssetRef
    timestamp: float = Field(ge=0, lt=61, allow_inf_nan=False)
    frame_index: int = Field(ge=0, lt=1830)


class PreparedMedia(Metadata):
    profile_version: Literal["media-720p30-v1"] = "media-720p30-v1"
    video: AssetRef
    width: int = Field(gt=0, le=1280)
    height: int = Field(gt=0, le=720)
    fps: Literal[30] = 30
    duration: float = Field(ge=1, le=60.05, allow_inf_nan=False)
    frame_count: int = Field(ge=30, le=1802)
    source_rotation: Literal[0, 90, 180, 270] = 0
    previews: tuple[MediaPreview, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def valid_previews(self):
        if self.video.bucket != "private-media" or self.video.content_type != "video/mp4":
            raise ValueError("Prepared playback must remain private MP4")
        if len({p.frame_index for p in self.previews}) != len(self.previews):
            raise ValueError("Previews must select distinct frames")
        for preview in self.previews:
            if (preview.asset.bucket != "private-media" or preview.asset.content_type != "image/jpeg"
                    or preview.frame_index >= self.frame_count or preview.timestamp >= self.duration
                    or abs(preview.timestamp - preview.frame_index / self.fps) > 0.000001):
                raise ValueError("Preview timestamp must refer to prepared playback")
        return self


class ProjectRecord(Record):
    owner_id: Id
    title: str = Field(min_length=1, max_length=120)
    source: Literal["capture", "gallery"]
    label: str | None = Field(default=None, min_length=1, max_length=80)
    # Original creation payload stays immutable when a project is renamed.
    creation_hash: Sha256 | None = None
    state: Literal["uploading", "preparing", "ready", "rejected", "deleting", "deleted"] = "uploading"
    skill_id: Id | None = None
    input_revision: int = Field(default=1, ge=1)
    video: AssetRef | None = None
    prepared_media: PreparedMedia | None = None
    media_error: Literal["invalid_media", "unsupported_media", "source_file_limit", "prepared_file_limit", "processing_limit", "preparation_unavailable"] | None = None
    active_job_id: Id | None = None
    active_upload_id: Id | None = None
    latest_reconstruction_id: Id | None = None
    latest_lesson_id: Id | None = None
    deleted_at: AwareDatetime | None = None

    def document(self):
        return _without_unset(super().document(), "latestLessonId")

    @model_validator(mode="after")
    def private_source(self):
        if self.video and self.video.bucket != "private-media":
            raise ValueError("User recordings belong in private-media")
        if self.state == "ready" and self.video is None:
            raise ValueError("A ready project needs a verified source asset")
        return self


class JobRecord(Record):
    owner_id: Id
    project_id: Id
    kind: Literal["reconstructKeyframes", "reconstructMotion", "prepareMedia", "buildLesson"]
    input_revision: int = Field(ge=1)
    input_hash: Sha256
    profile_version: str = Field(min_length=1, max_length=80)
    source_asset: AssetRef
    keyframes: tuple["KeyframeInput", ...] | None = Field(default=None, min_length=1, max_length=24)
    motion_segment: "MotionSegment | None" = None
    status: Literal["queued", "running", "retrying", "succeeded", "partial", "failed", "cancelled"] = "queued"
    stage: str = Field(default="queued", min_length=1, max_length=80)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=5)
    lease_token: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: AwareDatetime | None = None
    worker_id: Id | None = None
    reconstruction_id: Id | None = None
    lesson_id: Id | None = None
    finished_at: AwareDatetime | None = None
    heartbeat_at: AwareDatetime | None = None
    attempt_deadline_at: AwareDatetime | None = None
    deadline_at: AwareDatetime | None = None
    next_attempt_at: AwareDatetime | None = None
    error_code: Literal["invalid_media", "unsupported_media", "source_file_limit", "prepared_file_limit", "processing_limit", "preparation_unavailable", "model_unavailable", "reconstruction_failed", "job_deadline", "input_changed", "lesson_unavailable", "lesson_rejected"] | None = None

    def document(self):
        payload = super().document()
        # During a rolling upgrade, old strict readers must still understand
        # prepareMedia/legacy jobs. Only the new body profile needs this field.
        if self.keyframes is None:
            payload.pop("keyframes", None)
        if self.motion_segment is None:
            payload.pop("motionSegment", None)
        return _without_unset(payload, "lessonId")


class KeyframeInput(Metadata):
    frame_index: int = Field(ge=0, lt=1802)
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def normalized_box(self):
        import math
        left, top, right, bottom = self.bbox
        if not all(math.isfinite(n) and 0 <= n <= 1 for n in self.bbox) or right-left < .03 or bottom-top < .03:
            raise ValueError("Use normalized left/top/right/bottom with positive area")
        return self


class MotionSegment(Metadata):
    start_frame: int = Field(ge=0, lt=1802, strict=True)
    end_frame: int = Field(gt=0, le=1802, strict=True)

    @model_validator(mode="after")
    def bounded_segment(self):
        # End is exclusive: 1–10 seconds of canonical 30 fps playback.
        if not 30 <= self.end_frame - self.start_frame <= 300:
            raise ValueError("Select a motion segment between one and ten seconds")
        return self


JobRecord.model_rebuild()


class Engine(Metadata):
    model_name: str = Field(min_length=1, max_length=120)
    upstream_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    checkpoint_hash: Sha256
    config_hash: Sha256


class PlaybackContract(Metadata):
    schema_version: Literal[1] = 1
    topology_version: str = Field(min_length=1, max_length=80)
    coordinate_system: Literal["rightHandedYUp"] = "rightHandedYUp"
    units: Literal["meters"] = "meters"
    encoding: Literal["usdz", "glb", "motionBuffers"]


class ReconstructionRecord(Record):
    owner_id: Id
    project_id: Id
    job_id: Id
    input_revision: int = Field(ge=1)
    profile_version: str = Field(min_length=1, max_length=80)
    source_asset: AssetRef
    mode: Literal["keyframes", "motion"]
    outcome: Literal["ready", "partial"]
    engine: Engine
    contract: PlaybackContract
    artifacts: tuple[AssetRef, ...] = Field(min_length=1, max_length=32)
    requested_frame_count: int = Field(gt=0)
    reconstructed_frame_count: int = Field(gt=0)
    missing_frame_count: int = Field(ge=0)
    completed_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def consistent_frames(self):
        if self.reconstructed_frame_count + self.missing_frame_count != self.requested_frame_count:
            raise ValueError("Frame counts must account for missing frames")
        if (self.outcome == "ready") != (self.missing_frame_count == 0):
            raise ValueError("Missing frames must be published as partial")
        return self


class LessonSkill(Metadata):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    name: str = Field(min_length=1, max_length=120)


class LessonAuthoring(Metadata):
    model: str = Field(min_length=1, max_length=120)
    mode: Literal["grounded", "baseline"]
    prompt_sha256: Sha256
    claims_removed: int = Field(ge=0, le=1000)
    calls: int = Field(ge=0, le=200)


class LessonRecord(Record):
    """A private, AI-generated lesson built from one prepared project video."""
    owner_id: Id
    project_id: Id
    job_id: Id
    input_revision: int = Field(ge=1)
    profile_version: Literal["lesson-3d-v1"] = "lesson-3d-v1"
    source_asset: AssetRef
    title: str = Field(min_length=1, max_length=120)
    skills: tuple[LessonSkill, ...] = Field(min_length=1, max_length=8)
    authoring: LessonAuthoring
    engine: Engine
    artifacts: tuple[AssetRef, ...] = Field(min_length=4, max_length=64)
    ai_generated: Literal[True] = True
    state: Literal["ready", "deleted"] = "ready"
    completed_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def private_bundle(self):
        prefix = f"users/{self.owner_id}/projects/{self.project_id}/lesson/{self.job_id}/"
        names = [a.object_key.rsplit("/", 1)[-1] for a in self.artifacts]
        if self.id != self.job_id or any(a.bucket != "private-media" or not a.object_key.startswith(prefix)
                                         for a in self.artifacts):
            raise ValueError("Lesson files belong to their own job prefix")
        if not {"lesson.json", "analysis.json", "mesh.json", "video.mp4"} <= set(names):
            raise ValueError("A lesson bundle needs lesson, analysis, mesh and video files")
        return self


class LessonReport(Record):
    owner_id: Id
    lesson_id: Id
    project_id: Id
    reason: Literal["wrong_move", "wrong_timing", "unsafe", "inaccurate", "other"]
    note: str | None = Field(default=None, max_length=500)


class ReferenceRecord(Record):
    title: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    video_asset: AssetRef
    license_reference: str = Field(min_length=1, max_length=500)
    publication_state: Literal["draft", "published", "retired"] = "draft"
    manifest_asset: AssetRef | None = None

    @model_validator(mode="after")
    def reference_bucket(self):
        if any(asset.bucket != "reference-assets" for asset in [self.video_asset, self.manifest_asset] if asset):
            raise ValueError("Reference files belong in reference-assets")
        return self


class ComparisonRecord(Record):
    owner_id: Id
    left_reconstruction_id: Id
    right_type: Literal["reconstruction", "reference"]
    right_id: Id
    right_reference_version: str | None = Field(default=None, min_length=1, max_length=80)
    mode: Literal["overlay", "sideBySide"]
    offset_ms: int = Field(default=0, ge=-600000, le=600000)
    state: Literal["ready", "invalidated"] = "ready"

    @model_validator(mode="after")
    def pinned_reference(self):
        if (self.right_type == "reference") != (self.right_reference_version is not None):
            raise ValueError("Pin a version only when comparing with a reference")
        return self


class UploadRecord(Record):
    owner_id: Id
    project_id: Id
    expected_revision: int = Field(ge=1)
    asset: AssetRef
    status: Literal["uploading", "committed", "cancelled"] = "uploading"
    expires_at: AwareDatetime
    cleanup_after: AwareDatetime
    committed_revision: int | None = Field(default=None, ge=2)
    reserved_bytes: int = Field(default=52428800, ge=0, le=52428800)


class DeletionRecord(Record):
    owner_id: Id
    scope: Literal["project", "account"]
    project_id: Id | None = None
    state: Literal["pending", "completed"] = "pending"
    not_before: AwareDatetime = Field(default_factory=utc_now)
    completed_at: AwareDatetime | None = None
    last_error: Literal["cleanup_unavailable", "reconciliation_required"] | None = None
