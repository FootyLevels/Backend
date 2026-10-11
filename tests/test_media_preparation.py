"""Real FFmpeg fixtures plus resource/cancellation boundaries; no cloud writes."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
from unittest.mock import patch

import pytest

from footylevels.media_preparation import (FILE_LIMIT, MediaRejected, MediaUnavailable,
                                          _run, prepare_clip)

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg and FFprobe are required for actual media decoding")


def ffmpeg(*arguments):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *arguments],
                   check=True, capture_output=True, timeout=30)


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "source.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=2",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
    return path


def probe(path):
    return json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
                                               "-show_format", "-of", "json", str(path)]))


def rotated_copy(source, destination):
    # Write the standard QuickTime track display matrix. Some FFmpeg versions
    # ignore the old rotate tag during stream-copy; this fixture is deterministic.
    data = bytearray(source.read_bytes())
    track = data.index(b"tkhd") - 4
    version = data[track + 8]
    matrix = track + (60 if version == 1 else 48)
    data[matrix:matrix + 36] = struct.pack(">9i", 0, 65536, 0, -65536, 0, 0, 0, 0, 1073741824)
    destination.write_bytes(data)


def test_real_rotation_audio_removal_cfr_and_timestamped_previews(clip, tmp_path):
    rotated = tmp_path / "rotated.mov"
    rotated_copy(clip, rotated)
    result = prepare_clip(rotated, tmp_path / "prepared")
    assert result.source.rotation in (90, 270)
    assert result.playback.height > result.playback.width
    assert result.playback.rotation == 0 and result.playback.fps == 30
    assert abs(result.playback.duration - 2) < 0.04
    streams = probe(result.video.path)["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]
    assert streams[0]["codec_name"] == "h264" and streams[0]["pix_fmt"] == "yuv420p"
    assert len(result.previews) == 6
    assert len({p.frame_index for p in result.previews}) == 6
    for preview in result.previews:
        assert preview.path.read_bytes().startswith(b"\xff\xd8")
        assert preview.timestamp == preview.frame_index / 30
        assert preview.byte_size == preview.path.stat().st_size
        assert 0 <= preview.timestamp < result.playback.duration
    assert (tmp_path / "prepared").stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / "prepared" / "command-output.json").exists()
    assert not (tmp_path / "prepared" / "source.mp4").exists()


def test_output_retry_cannot_overwrite_another_attempt(clip, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_text("owned by another attempt")
    with pytest.raises(FileExistsError):
        prepare_clip(clip, output)
    assert sentinel.read_text() == "owned by another attempt"


@pytest.mark.parametrize("kind", ["garbage", "empty", "playlist", "truncated", "oversize", "symlink"])
def test_invalid_sources_leave_no_staging(clip, tmp_path, kind):
    source = tmp_path / "bad.mp4"
    if kind == "garbage":
        source.write_bytes(b"not a movie")
    elif kind == "empty":
        source.touch()
    elif kind == "playlist":
        source.write_text("#EXTM3U\nhttps://example.invalid/never-fetch.ts\n")
    elif kind == "truncated":
        source.write_bytes(clip.read_bytes()[:100])
    elif kind == "oversize":
        with source.open("wb") as target:
            target.truncate(FILE_LIMIT + 1)
    else:
        source.symlink_to(clip)
    with pytest.raises(MediaRejected) as error:
        prepare_clip(source, tmp_path / "prepared")
    assert error.value.code in {"invalid_media", "unsupported_media", "source_file_limit"}
    assert not (tmp_path / "prepared").exists()
    assert clip.exists()


@pytest.mark.parametrize("duration", [0.5, 61])
def test_server_duration_limits_are_independent_of_client(tmp_path, duration):
    source = tmp_path / "duration.mp4"
    ffmpeg("-f", "lavfi", "-i", f"color=size=32x32:rate=30:duration={duration}",
           "-c:v", "libx264", str(source))
    with pytest.raises(MediaRejected, match="unsupported_media"):
        prepare_clip(source, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_wrong_container_and_extra_video_streams_rejected(clip, tmp_path):
    matroska = tmp_path / "fake.mp4"
    ffmpeg("-i", str(clip), "-c", "copy", "-f", "matroska", str(matroska))
    with pytest.raises(MediaRejected):
        prepare_clip(matroska, tmp_path / "wrong-container")
    multivideo = tmp_path / "multi.mp4"
    ffmpeg("-i", str(clip), "-map", "0:v", "-map", "0:v", "-c", "copy", str(multivideo))
    with pytest.raises(MediaRejected):
        prepare_clip(multivideo, tmp_path / "multi-output")


def test_timeout_reaps_child_and_scrubs_cloud_credentials(tmp_path, monkeypatch):
    helper = tmp_path / "slow"
    helper.write_text("#!/usr/bin/env python3\nimport os,time\n"
                      "assert 'SUPABASE_SECRET_KEY' not in os.environ\n"
                      "assert 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ\n"
                      "open('child.pid','w').write(str(os.getpid()))\n"
                      "time.sleep(20)\n")
    helper.chmod(0o700)
    # Use the interpreter's absolute path because the child PATH is deliberately minimal.
    helper.write_text(helper.read_text().replace("#!/usr/bin/env python3", "#!" + os.sys.executable))
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "must-not-reach-decoder")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "must-not-reach-decoder")
    with patch("footylevels.media_preparation.shutil.which", return_value=str(helper)):
        with pytest.raises(MediaRejected, match="processing_limit"):
            _run("test-tool", [], tmp_path, time.monotonic() + 5)
    pid = int((tmp_path / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (tmp_path / "command-output.json").exists()


def test_missing_tools_are_retryable_not_a_bad_user_clip(clip, tmp_path):
    with patch("footylevels.media_preparation.shutil.which", return_value=None):
        with pytest.raises(MediaUnavailable):
            prepare_clip(clip, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()
