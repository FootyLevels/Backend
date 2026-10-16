"""One CPU worker invocation. Immutable attempt outputs and fenced publication."""
from pathlib import Path
import tempfile

from .assets import StorageUnavailable
from .media_preparation import MediaRejected, MediaUnavailable, prepare_clip
from .records import AssetRef, MediaPreview, PreparedMedia
from .repositories import RecordConflict, RecordNotFound
from .uploads import StorageQuotaExceeded


def process_job(jobs, storage, job, staging_root: Path):
    """A trusted supervisor supplies the claimed job and owns the private root."""
    try:
        with tempfile.TemporaryDirectory(prefix="media-", dir=staging_root) as folder:
            directory = Path(folder)
            source = directory / "source.mp4"
            jobs.checkpoint(job, "downloadingMedia")
            storage.download_file(job.source_asset, source)
            jobs.checkpoint(job, "decodingMedia")
            clip = prepare_clip(source, directory / "prepared")
            prefix = f"users/{job.owner_id}/projects/{job.project_id}/media/{job.id}/{job.attempt_count}/"
            files = [clip.video, *clip.previews]
            assets = [AssetRef(bucket="private-media", object_key=prefix + f.path.name,
                               sha256=f.sha256, byte_size=f.byte_size, content_type=f.content_type) for f in files]
            jobs.reserve_outputs(job, assets)
            for asset, file in zip(assets, files):
                jobs.checkpoint(job, "publishingMedia")
                storage.put_file(asset, file.path)
            media = PreparedMedia(video=assets[0], width=clip.playback.width, height=clip.playback.height,
                duration=clip.playback.duration, frame_count=clip.playback.frame_count,
                source_rotation=clip.source.rotation,
                previews=tuple(MediaPreview(asset=a, timestamp=f.timestamp, frame_index=f.frame_index)
                               for a, f in zip(assets[1:], clip.previews)))
            jobs.publish(job, media)
            return "ready"
    except MediaRejected as error:
        code, retry = error.code, False
    except (MediaUnavailable, StorageUnavailable, StorageQuotaExceeded, OSError):
        code, retry = "preparation_unavailable", True
    except (RecordConflict, RecordNotFound):
        # A source mismatch is a rejection only if the lease/input still belongs
        # to us. Deleted projects and replaced claims fail the fenced transition.
        code, retry = "invalid_media", False
    try:
        return jobs.fail(job, code, retry=retry).status
    except (RecordConflict, RecordNotFound):
        return "superseded"
