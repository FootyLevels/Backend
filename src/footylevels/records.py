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


class UserRecord(Record):
    display_name: str = Field(min_length=1, max_length=80)
    account_state: Literal["active", "restricted", "deleting", "deleted"] = "active"


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
    deleted_at: AwareDatetime | None = None

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
    kind: Literal["reconstructKeyframes", "reconstructMotion", "prepareMedia"]
    input_revision: int = Field(ge=1)
    input_hash: Sha256
    profile_version: str = Field(min_length=1, max_length=80)
    source_asset: AssetRef
    status: Literal["queued", "running", "retrying", "succeeded", "partial", "failed", "cancelled"] = "queued"
    stage: str = Field(default="queued", min_length=1, max_length=80)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=5)
    lease_token: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: AwareDatetime | None = None
    worker_id: Id | None = None
    reconstruction_id: Id | None = None
    finished_at: AwareDatetime | None = None
    heartbeat_at: AwareDatetime | None = None
    attempt_deadline_at: AwareDatetime | None = None
    deadline_at: AwareDatetime | None = None
    next_attempt_at: AwareDatetime | None = None
    error_code: Literal["invalid_media", "unsupported_media", "source_file_limit", "prepared_file_limit", "processing_limit", "preparation_unavailable", "model_unavailable", "reconstruction_failed", "job_deadline", "input_changed"] | None = None


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
