"""Bounded CPU video preparation; no HTTP, cloud credentials or GPU imports.

Reuses the semester runtime's FFprobe → H.264/CFR → timestamped JPEG approach.
The caller owns a private disposable staging directory. Cloud publication and
durable job claims are separate; creating local files never makes a project ready.
"""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

FILE_LIMIT = 52_428_800
PROFILE = "media-720p30-v1"
PREVIEW_COUNT = 6


class MediaRejected(Exception):
    """Only a stable error code may be persisted or returned to a player."""
    def __init__(self, code="invalid_media"):
        self.code = code
        super().__init__(code)


class MediaUnavailable(Exception):
    """Missing tools or transient local failure; retry without disclosing stderr."""


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    duration: float
    frame_count: int
    rotation: int


@dataclass(frozen=True)
class PreparedFile:
    path: Path
    byte_size: int
    sha256: str
    content_type: str
    timestamp: float | None = None
    frame_index: int | None = None


@dataclass(frozen=True)
class PreparedClip:
    source: VideoMetadata
    playback: VideoMetadata
    video: PreparedFile
    previews: tuple[PreparedFile, ...]
    profile: str = PROFILE


def _run(tool: str, arguments: list[str], workspace: Path, deadline: float) -> bytes:
    executable = shutil.which(tool)
    if executable is None:
        raise MediaUnavailable()
    remaining = min(150, deadline - time.monotonic())
    if remaining <= 0:
        raise MediaRejected("processing_limit")
    # Neither Firebase/Supabase credentials nor inherited cloud environment is
    # available to the decoder. File/pipe protocols are explicitly allowlisted.
    environment = {"PATH": os.defpath, "LANG": "C", "HOME": str(workspace),
                   "TMPDIR": str(workspace), "OMP_NUM_THREADS": "2"}
    wrapper = Path(__file__).with_name("media_limits.py")
    output = workspace / "command-output.json"
    try:
        with output.open("wb") as target, open(os.devnull, "wb") as errors:
            process = subprocess.Popen([sys.executable, str(wrapper), executable, *arguments],
                cwd=workspace, env=environment, stdin=subprocess.DEVNULL, stdout=target,
                stderr=errors, start_new_session=True)
            try:
                result = process.wait(timeout=remaining)
            except BaseException:
                # Kill the whole child group on timeout/cancellation, and reap it.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
        if result != 0:
            raise MediaRejected("invalid_media")
        if output.stat().st_size > 256 * 1024:
            raise MediaRejected("invalid_media")
        return output.read_bytes()
    except subprocess.TimeoutExpired:
        raise MediaRejected("processing_limit") from None
    except OSError:
        raise MediaUnavailable() from None
    finally:
        output.unlink(missing_ok=True)


def _metadata(raw: bytes) -> VideoMetadata:
    try:
        value = json.loads(raw)
        streams = [s for s in value["streams"] if s.get("codec_type") == "video"]
        if len(streams) != 1:
            raise ValueError()
        stream = streams[0]
        if stream.get("codec_name") not in {"h264", "hevc", "mpeg4"}:
            raise ValueError()
        if "mov" not in value["format"]["format_name"].split(","):
            raise ValueError()
        width, height = int(stream["width"]), int(stream["height"])
        fps = float(Fraction(stream["avg_frame_rate"]))
        duration = float(stream.get("duration", value["format"].get("duration", 0)))
        if not (math.isfinite(fps) and 0 < fps <= 120 and math.isfinite(duration) and 1 <= duration <= 60.05
                and 0 < width <= 3840 and 0 < height <= 3840 and width * height <= 3840 * 2160):
            raise ValueError()
        rotation = next((float(s["rotation"]) for s in stream.get("side_data_list", [])
                         if "rotation" in s), float(stream.get("tags", {}).get("rotate", 0)))
        if not math.isfinite(rotation) or rotation % 90 != 0:
            raise ValueError()
        return VideoMetadata(width, height, fps, duration,
                             int(stream.get("nb_frames", round(duration * fps))), int(rotation) % 360)
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        raise MediaRejected("unsupported_media") from None


def _probe(path: Path, workspace: Path, deadline: float) -> VideoMetadata:
    return _metadata(_run("ffprobe", ["-v", "error", "-threads", "2", "-protocol_whitelist", "file,pipe",
        "-f", "mov", "-show_streams", "-show_format", "-of", "json", str(path)], workspace, deadline))


def _file(path: Path, content_type: str, timestamp=None, frame_index=None) -> PreparedFile:
    size = path.stat().st_size
    if not 0 < size <= FILE_LIMIT:
        raise MediaRejected("prepared_file_limit")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return PreparedFile(path, size, digest.hexdigest(), content_type, timestamp, frame_index)


def prepare_clip(source: Path, output: Path) -> PreparedClip:
    """Decode the entire bounded input, strip audio, normalize rotation/CFR, preview.

    Inputs must be regular files. Output must not exist, so a failed or repeated
    attempt cannot overwrite files of an earlier worker. Deletes only this newly
    created output directory on failure. A trusted runner supplies both paths.
    """
    source = Path(source)
    output = Path(output)
    if source.is_symlink() or not source.is_file():
        raise MediaRejected("invalid_media")
    if not 0 < source.stat().st_size <= FILE_LIMIT:
        raise MediaRejected("source_file_limit")
    source = source.resolve(strict=True)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    output = output.resolve(strict=True)
    deadline = time.monotonic() + 240
    try:
        original = _probe(source, output, deadline)
        video = output / "video.mp4"
        # Decode slightly beyond the maximum to detect forged duration metadata;
        # the probed result must still satisfy the limit. Never silently trim.
        _run("ffmpeg", ["-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", "-n",
            "-threads", "2", "-protocol_whitelist", "file,pipe", "-f", "mov", "-i", str(source),
            "-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1", "-t", "61",
            "-vf", "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30",
            "-filter_threads", "2", "-c:v", "libx264", "-threads", "2", "-preset", "fast",
            "-crf", "23", "-pix_fmt", "yuv420p", "-metadata:s:v:0", "rotate=0",
            "-movflags", "+faststart", str(video)], output, deadline)
        playback = _probe(video, output, deadline)
        if playback.rotation != 0 or playback.fps != 30 or abs(playback.duration - original.duration) > 0.15:
            raise MediaRejected("invalid_media")
        previews = []
        for index in range(PREVIEW_COUNT):
            frame = min(playback.frame_count - 1, round((index + 0.5) * playback.frame_count / PREVIEW_COUNT))
            timestamp = frame / 30
            filename = output / f"preview-{index:02d}.jpg"
            _run("ffmpeg", ["-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-threads", "2",
                "-protocol_whitelist", "file,pipe", "-ss", f"{timestamp:.9f}", "-i", str(video),
                "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
                "-filter_threads", "2", "-threads", "2", "-q:v", "3", "-update", "1", str(filename)], output, deadline)
            previews.append(_file(filename, "image/jpeg", timestamp, frame))
        return PreparedClip(original, playback, _file(video, "video/mp4"), tuple(previews))
    except BaseException:
        shutil.rmtree(output)
        raise
