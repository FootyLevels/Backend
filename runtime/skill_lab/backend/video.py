"""Decode source video, propose diverse sharp frames, and retain exact timestamps."""
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from . import store


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, text=True,
                            timeout=30, check=True)
    obj = json.loads(result.stdout)
    stream = next((s for s in obj["streams"] if s["codec_type"] == "video"), None)
    if not stream:
        raise ValueError("This file does not contain a video stream")
    numerator, denominator = map(float, stream["avg_frame_rate"].split("/"))
    fps = numerator / denominator if denominator else 0
    duration = float(stream.get("duration", obj["format"].get("duration", 0)))
    if not (fps > 0 and 0 < duration <= 300):
        raise ValueError("Use a video between 0 and 300 seconds with a valid frame rate")
    if stream["width"] * stream["height"] > 3840 * 2160:
        raise ValueError("Maximum video resolution is 4K")
    return {"width": stream["width"], "height": stream["height"], "fps": fps,
            "duration": duration, "frameCount": int(stream.get("nb_frames", round(duration * fps)))}


def extract_frame(path: Path, timestamp: float) -> tuple[np.ndarray, float, int]:
    cap = cv2.VideoCapture(str(path))
    try:
        fps, count = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        index = min(count - 1, max(0, round(timestamp * fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if not ok:
            raise ValueError(f"Cannot decode frame at {timestamp:.2f}s")
        # OpenCV reports the actual presentation timestamp after decoding (also for VFR).
        pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        return frame, pts if pts > 0 else index / fps, index
    finally:
        cap.release()


def propose_timestamps(path: Path, count: int = 6) -> list[float]:
    """A transparent heuristic, not semantic pose recognition.

    Sample at up to 8fps. Partition time to guarantee coverage, then prefer sharp
    frames with moderate visual change in each partition. Penalize edit flashes.
    Users can replace/add exact frames and choose a person before inference.
    """
    cap = cv2.VideoCapture(str(path))
    candidates = []
    previous = None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        stride = max(1, round(fps / 8))
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if index % stride == 0:
                gray = cv2.cvtColor(cv2.resize(frame, (144, 192)), cv2.COLOR_BGR2GRAY)
                sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                motion = float(np.abs(gray.astype(float) - previous).mean()) if previous is not None else 0
                pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                candidates.append((pts if pts > 0 else index / fps, sharpness, motion))
                previous = gray.astype(float)
            index += 1
    finally:
        cap.release()
    if not candidates:
        raise ValueError("No frames could be decoded")
    rows = np.asarray(candidates)
    sharp = rows[:, 1] / max(float(np.percentile(rows[:, 1], 90)), 1)
    motion = rows[:, 2] / max(float(np.percentile(rows[:, 2], 90)), 1)
    scores = sharp.clip(0, 1) + 0.35 * motion.clip(0, 1) - 0.8 * (motion > 1.8)
    chosen = [int(group[np.argmax(scores[group])]) for group in
              np.array_split(np.arange(len(rows)), min(count, len(rows))) if len(group)]
    return [round(float(rows[i, 0]), 6) for i in chosen]


def add_frame(project: dict, timestamp: float, label: str = "", note: str = "",
              bbox: list[float] | None = None) -> dict:
    if not 0 <= timestamp < project["video"]["duration"]:
        raise ValueError("Timestamp must lie inside the video")
    directory = store.project_dir(project["id"])
    image, actual, index = extract_frame(directory / "video.mp4", timestamp)
    existing = next((f for f in project["frames"] if f["frameIndex"] == index), None)
    if existing:
        return existing
    frame_id = uuid.uuid4().hex[:12]
    (directory / "frames").mkdir(exist_ok=True)
    filename = f"frames/{frame_id}.jpg"
    if not cv2.imwrite(str(directory / filename), image, [cv2.IMWRITE_JPEG_QUALITY, 96]):
        raise ValueError("Cannot save extracted frame")
    frame = {"id": frame_id, "timestamp": round(actual, 6), "frameIndex": index,
             "label": label or f"Moment at {actual:.2f}s", "note": note,
             "imageUrl": store.asset_url(project["id"], filename),
             "bbox": bbox or [0, 0, 1, 1], "status": "pending", "meshUrl": None,
             "error": None, "provider": None}
    project["frames"].append(frame)
    project["frames"].sort(key=lambda f: f["timestamp"])
    return frame


def prepare(path: Path, title: str, count: int = 6, project_id: str | None = None,
            curated: list[dict] | None = None) -> dict:
    metadata = probe(path)
    project_id = project_id or uuid.uuid4().hex[:16]
    directory = store.project_dir(project_id)
    if (directory / "project.json").exists():
        raise ValueError("Project already exists; use a new ID")
    directory.mkdir(parents=True, exist_ok=True)
    # Browser-compatible H.264 also normalizes rotation metadata and variable fps.
    # All downstream timestamps refer to this canonical, constant-frame-rate copy.
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                    "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast",
                    "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(metadata["fps"]),
                    "-c:a", "aac", "-movflags", "+faststart", str(directory / "video.mp4")],
                   check=True, capture_output=True, timeout=180)
    metadata = probe(directory / "video.mp4")
    project = {"id": project_id, "title": title[:120],
               "createdAt": datetime.now(timezone.utc).isoformat(),
               "video": {**metadata, "url": store.asset_url(project_id, "video.mp4")},
               "selectionMethod": "curated" if curated else "temporal-sharpness-heuristic",
               "frames": [], "job": None}
    selections = curated or [{"timestamp": t} for t in propose_timestamps(directory / "video.mp4", count)]
    for selection in selections:
        add_frame(project, **selection)
    store.save(project)
    return project
