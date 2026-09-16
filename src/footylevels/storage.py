"""
Storage Module
Save and load extracted body sequences.

Keypoint arrays go to a compressed .npz; metadata goes to a sibling .json so it
can be listed and inspected without loading the arrays.

Only the primary subject of each frame is persisted — these videos are of one
player, and keeping every detection would bloat the payload for no gain.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np

from .body_extractor import BodyDetection, BodyFrame, BodySequence

# Per-detection fields persisted alongside the keypoints, when the model
# supplies them and their shape is consistent across frames.
OPTIONAL_FIELDS = (
    "cam_t",
    "shape_params",
    "body_pose_params",
    "hand_pose_params",
    "vertices",
)


def _stack_optional(detections: List[BodyDetection], field: str) -> Optional[np.ndarray]:
    """Stack one optional field across detections, or None if unusable.

    Returns None when any frame is missing the field or the shapes disagree,
    so a partially-populated field never produces a misleading ragged array.
    """
    values = [getattr(d, field, None) for d in detections]
    if not values or any(v is None for v in values):
        return None
    shapes = {np.asarray(v).shape for v in values}
    if len(shapes) != 1:
        return None
    return np.stack([np.asarray(v) for v in values])


class SequenceStorage:
    """
    Save and retrieve extracted body sequences.

    Usage:
        storage = SequenceStorage()
        sequence_id = storage.save_sequence(sequence, name="cruyff_turn")
        restored = storage.load_sequence(sequence_id)
    """

    def __init__(self, storage_dir: str = "data/sequences"):
        """
        Args:
            storage_dir: Directory to store sequences in.
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _json_path(self, sequence_id: str) -> Path:
        return self.storage_dir / f"{sequence_id}.json"

    def _npz_path(self, sequence_id: str) -> Path:
        return self.storage_dir / f"{sequence_id}.npz"

    def save_sequence(
        self,
        sequence: BodySequence,
        name: Optional[str] = None,
        video_path: Optional[str] = None,
    ) -> str:
        """
        Save a body sequence.

        Args:
            sequence: BodySequence to persist.
            name: Base name for the saved files. Defaults to the video stem.
            video_path: Source video, recorded in the metadata.

        Returns:
            The id the sequence was saved under.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if name is None:
            name = Path(video_path).stem if video_path else "sequence"
        sequence_id = f"{name}_{timestamp}"

        detections = sequence.primary_detections
        detected = sequence.detected_frames

        arrays = {
            "frame_index": np.array([f.frame_index for f in detected], dtype=np.int64),
            "timestamp_ms": np.array([f.timestamp_ms for f in detected], dtype=np.float64),
        }
        if detections:
            arrays["keypoints_3d"] = sequence.keypoints_3d()
            arrays["keypoints_2d"] = sequence.keypoints_2d()
            arrays["focal_length"] = np.array(
                [d.focal_length if d.focal_length is not None else np.nan
                 for d in detections],
                dtype=np.float64,
            )
            for field in OPTIONAL_FIELDS:
                stacked = _stack_optional(detections, field)
                if stacked is not None:
                    arrays[field] = stacked
        if sequence.faces is not None:
            arrays["faces"] = sequence.faces

        np.savez_compressed(self._npz_path(sequence_id), **arrays)

        metadata = {
            "id": sequence_id,
            "created_at": datetime.now().isoformat(),
            "source_video": str(video_path) if video_path else None,
            "arrays": sorted(arrays.keys()),
            "empty_frames": [
                f.frame_index for f in sequence.frames if f.primary is None
            ],
            **sequence.summary(),
        }
        with open(self._json_path(sequence_id), "w") as f:
            json.dump(metadata, f, indent=2)

        return sequence_id

    def load_metadata(self, sequence_id: str) -> dict:
        """
        Load just the metadata for a sequence.

        Raises:
            FileNotFoundError: If the sequence does not exist.
        """
        path = self._json_path(sequence_id)
        if not path.exists():
            raise FileNotFoundError(f"Sequence not found: {sequence_id}")
        with open(path, "r") as f:
            return json.load(f)

    def load_sequence(self, sequence_id: str) -> BodySequence:
        """
        Load a full sequence back into BodySequence form.

        Frames that had no detection are restored as empty frames.

        Raises:
            FileNotFoundError: If the sequence does not exist.
        """
        metadata = self.load_metadata(sequence_id)

        npz_path = self._npz_path(sequence_id)
        if not npz_path.exists():
            raise FileNotFoundError(f"Sequence arrays not found: {sequence_id}")

        with np.load(npz_path) as data:
            arrays = {key: data[key] for key in data.files}

        frames: List[BodyFrame] = []
        frame_indices = arrays.get("frame_index", np.array([], dtype=np.int64))
        timestamps = arrays.get("timestamp_ms", np.array([], dtype=np.float64))

        for i, frame_index in enumerate(frame_indices):
            focal = arrays.get("focal_length")
            focal_value = float(focal[i]) if focal is not None else None
            if focal_value is not None and np.isnan(focal_value):
                focal_value = None

            detection = BodyDetection(
                keypoints_3d=arrays["keypoints_3d"][i],
                keypoints_2d=arrays["keypoints_2d"][i],
                cam_t=arrays["cam_t"][i] if "cam_t" in arrays else None,
                focal_length=focal_value,
                shape_params=arrays["shape_params"][i] if "shape_params" in arrays else None,
                body_pose_params=(
                    arrays["body_pose_params"][i] if "body_pose_params" in arrays else None
                ),
                hand_pose_params=(
                    arrays["hand_pose_params"][i] if "hand_pose_params" in arrays else None
                ),
                vertices=arrays["vertices"][i] if "vertices" in arrays else None,
            )
            frames.append(
                BodyFrame(
                    frame_index=int(frame_index),
                    timestamp_ms=float(timestamps[i]),
                    detections=[detection],
                )
            )

        for frame_index in metadata.get("empty_frames", []):
            frames.append(BodyFrame(frame_index=int(frame_index), timestamp_ms=0.0))
        frames.sort(key=lambda f: f.frame_index)

        return BodySequence(
            frames=frames,
            fps=metadata.get("fps", 0.0),
            width=metadata.get("width", 0),
            height=metadata.get("height", 0),
            duration_ms=metadata.get("duration_ms", 0.0),
            model_id=metadata.get("model_id", ""),
            faces=arrays.get("faces"),
        )

    def list_sequences(self) -> List[dict]:
        """Every saved sequence's metadata, newest first."""
        sequences = []
        for path in sorted(self.storage_dir.glob("*.json"), reverse=True):
            with open(path, "r") as f:
                sequences.append(json.load(f))
        return sequences

    def delete_sequence(self, sequence_id: str) -> None:
        """Delete a sequence's metadata and arrays."""
        for path in (self._json_path(sequence_id), self._npz_path(sequence_id)):
            if path.exists():
                path.unlink()


def save_sequence(
    sequence: BodySequence,
    name: Optional[str] = None,
    video_path: Optional[str] = None,
    storage_dir: str = "data/sequences",
) -> str:
    """
    Save a body sequence in one call.

    Args:
        sequence: BodySequence to persist.
        name: Base name for the saved files.
        video_path: Source video, recorded in the metadata.
        storage_dir: Directory to store the sequence in.

    Returns:
        The id the sequence was saved under.
    """
    return SequenceStorage(storage_dir).save_sequence(sequence, name, video_path)
