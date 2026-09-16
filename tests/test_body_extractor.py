"""
Tests for body_extractor module.

These cover the data model and the parsing of SAM 3D Body's output, and run
without the model installed or the checkpoint downloaded.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels.body_extractor import (
    BodyDetection,
    BodyFrame,
    BodySequence,
    SAM3DBodyExtractor,
    resolve_device,
)

NUM_JOINTS = 12


def make_detection(scale: float = 1.0, offset: float = 0.0) -> BodyDetection:
    """Build a detection with predictable keypoints."""
    keypoints_3d = np.arange(NUM_JOINTS * 3, dtype=np.float64).reshape(NUM_JOINTS, 3)
    keypoints_2d = (
        np.arange(NUM_JOINTS * 2, dtype=np.float64).reshape(NUM_JOINTS, 2) * scale + offset
    )
    return BodyDetection(
        keypoints_3d=keypoints_3d,
        keypoints_2d=keypoints_2d,
        cam_t=np.array([0.0, 0.0, 3.0]),
        focal_length=1200.0,
    )


def make_sequence(num_frames: int = 4, empty_every: int = 0) -> BodySequence:
    """Build a sequence, optionally leaving every Nth frame undetected."""
    frames = []
    for i in range(num_frames):
        detections = []
        if not (empty_every and i % empty_every == 0):
            detections = [make_detection(offset=float(i))]
        frames.append(
            BodyFrame(frame_index=i, timestamp_ms=i * 33.3, detections=detections)
        )
    return BodySequence(
        frames=frames, fps=30.0, width=1920, height=1080, duration_ms=num_frames * 33.3
    )


class TestBodyDetection:
    def test_num_joints_follows_the_model(self):
        """Joint count is read off the data, not hardcoded."""
        assert make_detection().num_joints == NUM_JOINTS

    def test_bbox_spans_the_keypoints(self):
        """The derived box encloses every 2D keypoint."""
        detection = make_detection()
        x1, y1, x2, y2 = detection.bbox

        assert x1 == detection.keypoints_2d[:, 0].min()
        assert x2 == detection.keypoints_2d[:, 0].max()
        assert y1 == detection.keypoints_2d[:, 1].min()
        assert y2 == detection.keypoints_2d[:, 1].max()

    def test_bbox_area_is_non_negative(self):
        assert make_detection().bbox_area >= 0


class TestBodyFrame:
    def test_primary_is_none_without_detections(self):
        assert BodyFrame(frame_index=0, timestamp_ms=0.0).primary is None

    def test_primary_picks_the_largest_person(self):
        """The bigger box is assumed to be the subject, not a bystander."""
        small = make_detection(scale=1.0)
        large = make_detection(scale=5.0)
        frame = BodyFrame(0, 0.0, detections=[small, large])

        assert frame.primary is large


class TestBodySequence:
    def test_len_counts_all_frames(self):
        assert len(make_sequence(6)) == 6

    def test_detected_frames_skips_empty_ones(self):
        """Frames with nobody in them are excluded from the detected set."""
        sequence = make_sequence(num_frames=4, empty_every=2)

        assert len(sequence) == 4
        assert len(sequence.detected_frames) == 2

    def test_keypoint_stacks_have_expected_shape(self):
        sequence = make_sequence(5)

        assert sequence.keypoints_3d().shape == (5, NUM_JOINTS, 3)
        assert sequence.keypoints_2d().shape == (5, NUM_JOINTS, 2)

    def test_joint_trajectory_tracks_one_joint(self):
        sequence = make_sequence(5)

        assert sequence.joint_trajectory(3).shape == (5, 3)

    def test_num_joints_is_none_when_nothing_detected(self):
        """An all-empty sequence reports no joint count rather than crashing."""
        empty = BodySequence(
            frames=[BodyFrame(0, 0.0)], fps=30.0, width=10, height=10, duration_ms=0.0
        )

        assert empty.num_joints is None
        assert empty.primary_detections == []

    def test_summary_is_serializable(self):
        summary = make_sequence(3).summary()

        assert summary["total_frames"] == 3
        assert summary["frames_with_detection"] == 3
        assert summary["num_joints"] == NUM_JOINTS
        assert summary["has_vertices"] is False


class TestOutputParsing:
    """SAM 3D Body returns one dict per detected person."""

    def setup_method(self):
        self.extractor = SAM3DBodyExtractor()

    def person(self) -> dict:
        return {
            "pred_keypoints_3d": np.zeros((NUM_JOINTS, 3)),
            "pred_keypoints_2d": np.zeros((NUM_JOINTS, 2)),
            "pred_cam_t": np.array([0.0, 0.0, 3.0]),
            "focal_length": np.array([1200.0]),
            "shape_params": np.zeros(45),
        }

    def test_parses_a_list_of_people(self):
        detections = self.extractor._parse_outputs([self.person(), self.person()])

        assert len(detections) == 2
        assert detections[0].num_joints == NUM_JOINTS
        assert detections[0].focal_length == pytest.approx(1200.0)

    def test_parses_a_single_person_dict(self):
        """Some builds return one person directly rather than in a list."""
        assert len(self.extractor._parse_outputs(self.person())) == 1

    def test_handles_empty_and_none(self):
        assert self.extractor._parse_outputs(None) == []
        assert self.extractor._parse_outputs([]) == []

    def test_skips_people_missing_keypoints(self):
        """A malformed entry is dropped instead of raising."""
        assert self.extractor._parse_outputs([{"focal_length": 1200.0}]) == []

    def test_vertices_are_dropped_unless_requested(self):
        """Meshes are ~220 KB per frame, so they are opt-in."""
        person = self.person()
        person["pred_vertices"] = np.zeros((18439, 3))

        assert self.extractor._parse_outputs([person])[0].vertices is None

        self.extractor.include_vertices = True
        assert self.extractor._parse_outputs([person])[0].vertices.shape == (18439, 3)


class TestCheckpointHandling:
    def test_reports_missing_checkpoint_files(self, tmp_path):
        """A bare directory is reported as missing both required files."""
        extractor = SAM3DBodyExtractor(checkpoint_dir=str(tmp_path))

        assert set(extractor.check_checkpoint()) == {"model.ckpt", "assets/mhr_model.pt"}

    def test_reports_complete_checkpoint(self, tmp_path):
        (tmp_path / "model.ckpt").touch()
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "mhr_model.pt").touch()

        assert SAM3DBodyExtractor(checkpoint_dir=str(tmp_path)).check_checkpoint() == []

    def test_incomplete_checkpoint_raises_actionable_error(self, tmp_path):
        """The error names the missing files and the command that fixes it."""
        extractor = SAM3DBodyExtractor(checkpoint_dir=str(tmp_path))

        with pytest.raises(FileNotFoundError, match="hf download"):
            extractor._build_estimator()

    def test_construction_does_not_load_the_model(self, tmp_path):
        """Constructing must stay cheap — weights load on first inference."""
        assert SAM3DBodyExtractor(checkpoint_dir=str(tmp_path))._estimator is None


class TestDeviceResolution:
    def test_explicit_device_wins(self):
        assert resolve_device("cpu") == "cpu"
        assert resolve_device("cuda") == "cuda"

    def test_auto_detect_returns_a_known_device(self):
        assert resolve_device() in {"cuda", "mps", "cpu"}
