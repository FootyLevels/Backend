"""
Tests for storage module.

Covers the round trip from BodySequence to disk and back.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels.body_extractor import BodyDetection, BodyFrame, BodySequence
from footylevels.storage import SequenceStorage

NUM_JOINTS = 8


def make_sequence(num_frames: int = 3, empty_frames=(), with_vertices=False) -> BodySequence:
    """Build a sequence with recognizable per-frame values."""
    frames = []
    for i in range(num_frames):
        if i in empty_frames:
            frames.append(BodyFrame(frame_index=i, timestamp_ms=i * 40.0))
            continue

        detection = BodyDetection(
            keypoints_3d=np.full((NUM_JOINTS, 3), float(i)),
            keypoints_2d=np.full((NUM_JOINTS, 2), float(i) * 2),
            cam_t=np.array([0.0, 0.0, float(i)]),
            focal_length=1000.0 + i,
            shape_params=np.full(45, float(i)),
            vertices=np.full((20, 3), float(i)) if with_vertices else None,
        )
        frames.append(BodyFrame(i, i * 40.0, detections=[detection]))

    return BodySequence(
        frames=frames,
        fps=25.0,
        width=1280,
        height=720,
        duration_ms=num_frames * 40.0,
        model_id="facebook/sam-3d-body-dinov3",
        faces=np.zeros((10, 3), dtype=np.int64),
    )


@pytest.fixture
def storage(tmp_path) -> SequenceStorage:
    return SequenceStorage(str(tmp_path))


class TestRoundTrip:
    def test_save_returns_an_id_containing_the_name(self, storage):
        assert "cruyff" in storage.save_sequence(make_sequence(), name="cruyff")

    def test_keypoints_survive_the_round_trip(self, storage):
        sequence_id = storage.save_sequence(make_sequence(3), name="clip")

        restored = storage.load_sequence(sequence_id)

        assert restored.num_joints == NUM_JOINTS
        np.testing.assert_allclose(
            restored.keypoints_3d(), make_sequence(3).keypoints_3d()
        )
        np.testing.assert_allclose(
            restored.keypoints_2d(), make_sequence(3).keypoints_2d()
        )

    def test_video_metadata_survives(self, storage):
        sequence_id = storage.save_sequence(make_sequence(), name="clip")

        restored = storage.load_sequence(sequence_id)

        assert restored.fps == 25.0
        assert restored.width == 1280
        assert restored.height == 720
        assert restored.model_id == "facebook/sam-3d-body-dinov3"

    def test_optional_fields_survive(self, storage):
        sequence_id = storage.save_sequence(make_sequence(2), name="clip")

        detection = storage.load_sequence(sequence_id).primary_detections[0]

        assert detection.focal_length == pytest.approx(1000.0)
        assert detection.shape_params.shape == (45,)
        np.testing.assert_allclose(detection.cam_t, [0.0, 0.0, 0.0])

    def test_faces_survive(self, storage):
        sequence_id = storage.save_sequence(make_sequence(), name="clip")

        assert storage.load_sequence(sequence_id).faces.shape == (10, 3)

    def test_empty_frames_are_preserved_in_order(self, storage):
        """Frames with no detection round trip as empty, keeping their index."""
        sequence_id = storage.save_sequence(
            make_sequence(4, empty_frames=(1, 3)), name="clip"
        )

        restored = storage.load_sequence(sequence_id)

        assert len(restored) == 4
        assert [f.frame_index for f in restored.frames] == [0, 1, 2, 3]
        assert restored.frames[1].primary is None
        assert restored.frames[3].primary is None
        assert len(restored.detected_frames) == 2


class TestVertices:
    def test_vertices_are_stored_when_present(self, storage):
        sequence_id = storage.save_sequence(
            make_sequence(2, with_vertices=True), name="mesh"
        )

        restored = storage.load_sequence(sequence_id)

        assert restored.primary_detections[0].vertices.shape == (20, 3)
        assert storage.load_metadata(sequence_id)["has_vertices"] is True

    def test_vertices_are_absent_when_not_extracted(self, storage):
        sequence_id = storage.save_sequence(make_sequence(2), name="nomesh")

        assert storage.load_sequence(sequence_id).primary_detections[0].vertices is None


class TestListingAndDeletion:
    def test_list_is_empty_initially(self, storage):
        assert storage.list_sequences() == []

    def test_list_reports_saved_sequences(self, storage):
        storage.save_sequence(make_sequence(), name="a")
        storage.save_sequence(make_sequence(), name="b")

        assert len(storage.list_sequences()) == 2

    def test_metadata_records_the_source_video(self, storage):
        sequence_id = storage.save_sequence(
            make_sequence(), name="clip", video_path="data/reference/cruyff_turn.mp4"
        )

        metadata = storage.load_metadata(sequence_id)

        assert metadata["source_video"] == "data/reference/cruyff_turn.mp4"
        assert metadata["frames_with_detection"] == 3

    def test_delete_removes_both_files(self, storage):
        sequence_id = storage.save_sequence(make_sequence(), name="clip")

        storage.delete_sequence(sequence_id)

        assert storage.list_sequences() == []
        with pytest.raises(FileNotFoundError):
            storage.load_metadata(sequence_id)

    def test_missing_sequence_raises(self, storage):
        with pytest.raises(FileNotFoundError):
            storage.load_sequence("does_not_exist")
