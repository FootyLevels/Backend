"""
Tests for pose extractor module.
"""

import pytest
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from footylevels.pose_extractor import (
    PoseLandmark,
    PoseFrame,
    PoseSequence,
    LandmarkIndex,
    normalize_poses
)


class TestPoseLandmark:
    def test_creation(self):
        lm = PoseLandmark(x=0.5, y=0.5, z=0.0, visibility=1.0)
        assert lm.x == 0.5
        assert lm.visibility == 1.0


class TestPoseFrame:
    def test_to_numpy(self):
        landmarks = [
            PoseLandmark(x=i*0.1, y=i*0.1, z=0, visibility=1.0)
            for i in range(33)
        ]
        frame = PoseFrame(frame_index=0, timestamp_ms=0, landmarks=landmarks)
        
        arr = frame.to_numpy()
        assert arr.shape == (33, 4)
        assert arr[0, 0] == 0.0
        assert arr[10, 0] == 1.0
    
    def test_get_landmark(self):
        landmarks = [
            PoseLandmark(x=i*0.1, y=i*0.1, z=0, visibility=1.0)
            for i in range(33)
        ]
        frame = PoseFrame(frame_index=0, timestamp_ms=0, landmarks=landmarks)
        
        hip = frame.get_landmark(LandmarkIndex.LEFT_HIP)
        assert hip.x == 2.3  # index 23


class TestPoseSequence:
    def test_to_numpy(self):
        # Create a simple sequence
        frames = []
        for f in range(10):
            landmarks = [
                PoseLandmark(x=0.5, y=0.5, z=0, visibility=1.0)
                for _ in range(33)
            ]
            frames.append(PoseFrame(frame_index=f, timestamp_ms=f*33, landmarks=landmarks))
        
        seq = PoseSequence(
            frames=frames,
            fps=30.0,
            width=640,
            height=480,
            duration_ms=330
        )
        
        arr = seq.to_numpy()
        assert arr.shape == (10, 33, 4)
    
    def test_get_joint_trajectory(self):
        frames = []
        for f in range(10):
            landmarks = [
                PoseLandmark(x=0.5 + f*0.01, y=0.5, z=0, visibility=1.0)
                for _ in range(33)
            ]
            frames.append(PoseFrame(frame_index=f, timestamp_ms=f*33, landmarks=landmarks))
        
        seq = PoseSequence(frames=frames, fps=30.0, width=640, height=480, duration_ms=330)
        
        trajectory = seq.get_joint_trajectory(LandmarkIndex.LEFT_HIP)
        assert trajectory.shape == (10, 4)
        assert trajectory[0, 0] == 0.5
        assert trajectory[9, 0] == pytest.approx(0.59, abs=0.01)


class TestNormalization:
    def test_normalize_poses(self):
        # Create sequence with different positions
        frames = []
        for f in range(5):
            landmarks = []
            for i in range(33):
                # Place joints at different positions
                x = 0.3 + i * 0.01
                y = 0.3 + i * 0.01
                landmarks.append(PoseLandmark(x=x, y=y, z=0, visibility=1.0))
            frames.append(PoseFrame(frame_index=f, timestamp_ms=f*33, landmarks=landmarks))
        
        seq = PoseSequence(frames=frames, fps=30.0, width=640, height=480, duration_ms=165)
        
        normalized = normalize_poses(seq)
        
        # After normalization, left hip should be at origin (0, 0)
        first_frame = normalized.frames[0]
        left_hip = first_frame.get_landmark(LandmarkIndex.LEFT_HIP)
        
        # Should be normalized (close to 0)
        assert abs(left_hip.x) < 0.1
        assert abs(left_hip.y) < 0.1


class TestLandmarkIndex:
    def test_indices(self):
        assert LandmarkIndex.NOSE == 0
        assert LandmarkIndex.LEFT_HIP == 23
        assert LandmarkIndex.RIGHT_ANKLE == 28
        assert LandmarkIndex.RIGHT_FOOT_INDEX == 32


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
