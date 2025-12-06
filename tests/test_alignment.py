"""
Tests for alignment module.
"""

import pytest
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from footylevels.alignment import (
    align_sequences,
    extract_features_for_alignment,
    compute_aligned_deviations,
    get_deviation_summary,
    SequenceAligner
)


def create_synthetic_poses(num_frames, phase_offset=0):
    """Create synthetic pose sequence for testing."""
    poses = np.zeros((num_frames, 33, 4))
    t = np.linspace(phase_offset, 2*np.pi + phase_offset, num_frames)

    for i in range(33):
        poses[:, i, 0] = 0.5 + 0.1 * np.sin(t + i * 0.1)  # x
        poses[:, i, 1] = 0.5 + 0.1 * np.cos(t + i * 0.1)  # y
        poses[:, i, 2] = 0.0  # z
        poses[:, i, 3] = 1.0  # visibility

    return poses


class TestAlignment:
    def test_feature_extraction(self):
        """Test feature extraction from poses."""
        poses = create_synthetic_poses(30)
        features = extract_features_for_alignment(poses)

        assert features.shape[0] == 30  # num frames
        assert features.shape[1] > 0  # has features
        assert features.dtype == np.float64

    def test_align_sequences_same_length(self):
        """Test alignment with same-length sequences."""
        ref_poses = create_synthetic_poses(60)
        user_poses = create_synthetic_poses(60, phase_offset=0.1)

        result = align_sequences(ref_poses, user_poses)

        assert result.distance >= 0
        assert len(result.ref_indices) == len(result.user_indices)
        assert result.normalized_distance >= 0

    def test_align_sequences_different_length(self):
        """Test alignment with different-length sequences."""
        ref_poses = create_synthetic_poses(60)
        user_poses = create_synthetic_poses(45)  # shorter

        result = align_sequences(ref_poses, user_poses)

        assert result.distance >= 0
        assert len(result.ref_indices) > 0
        assert len(result.user_indices) > 0

    def test_compute_deviations(self):
        """Test deviation computation."""
        ref_poses = create_synthetic_poses(30)
        user_poses = create_synthetic_poses(30, phase_offset=0.2)

        alignment = align_sequences(ref_poses, user_poses)
        deviations = compute_aligned_deviations(ref_poses, user_poses, alignment)

        assert len(deviations) > 0
        for joint_idx, devs in deviations.items():
            assert len(devs) == len(alignment.ref_indices)
            assert all(devs >= 0)  # deviations should be non-negative

    def test_deviation_summary(self):
        """Test deviation summary statistics."""
        ref_poses = create_synthetic_poses(30)
        user_poses = create_synthetic_poses(30)

        alignment = align_sequences(ref_poses, user_poses)
        deviations = compute_aligned_deviations(ref_poses, user_poses, alignment)
        summary = get_deviation_summary(deviations)

        for joint_idx, stats in summary.items():
            assert 'mean' in stats
            assert 'max' in stats
            assert 'std' in stats
            assert 'median' in stats
            assert all(v >= 0 for v in stats.values())

    def test_sequence_aligner_class(self):
        """Test SequenceAligner high-level class."""
        # Create mock PoseSequence objects
        class MockPoseSequence:
            def __init__(self, poses):
                self._data = poses

            def to_numpy(self):
                return self._data

        ref = MockPoseSequence(create_synthetic_poses(40))
        user = MockPoseSequence(create_synthetic_poses(35))

        aligner = SequenceAligner()
        result = aligner.align(ref, user)

        assert result.distance >= 0

        # Test similarity score
        similarity = aligner.get_similarity_score(result)
        assert 0 <= similarity <= 100

    def test_alignment_frame_mapping(self):
        """Test frame mapping functions."""
        ref_poses = create_synthetic_poses(50)
        user_poses = create_synthetic_poses(40)

        result = align_sequences(ref_poses, user_poses)

        # Test get_user_frame_for_ref
        user_frame = result.get_user_frame_for_ref(25)
        assert 0 <= user_frame < 40

        # Test get_ref_frame_for_user
        ref_frame = result.get_ref_frame_for_user(20)
        assert 0 <= ref_frame < 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
