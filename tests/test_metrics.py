"""
Tests for metrics module.
"""

import pytest
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from footylevels.metrics import (
    calculate_angle_3points,
    calculate_angle_2vectors,
    calculate_distance,
    calculate_hip_rotation,
    calculate_velocity,
    calculate_angular_velocity,
    calculate_arc_completeness,
    calculate_motion_smoothness,
    calculate_direction_change
)


class TestBasicMetrics:
    def test_angle_3points_90deg(self):
        """Test 90 degree angle calculation."""
        p1 = np.array([0, 0, 0])
        p2 = np.array([1, 0, 0])
        p3 = np.array([1, 1, 0])

        angle = calculate_angle_3points(p1, p2, p3)
        assert abs(angle - 90.0) < 0.1

    def test_angle_3points_180deg(self):
        """Test 180 degree angle (straight line)."""
        p1 = np.array([0, 0, 0])
        p2 = np.array([1, 0, 0])
        p3 = np.array([2, 0, 0])

        angle = calculate_angle_3points(p1, p2, p3)
        assert abs(angle - 180.0) < 0.1

    def test_angle_2vectors(self):
        """Test angle between two vectors."""
        v1 = np.array([1, 0, 0])
        v2 = np.array([0, 1, 0])

        angle = calculate_angle_2vectors(v1, v2)
        assert abs(angle - 90.0) < 0.1

    def test_distance_2d(self):
        """Test 2D distance calculation."""
        p1 = np.array([0, 0])
        p2 = np.array([3, 4])

        dist = calculate_distance(p1, p2)
        assert abs(dist - 5.0) < 0.01

    def test_distance_3d(self):
        """Test 3D distance calculation."""
        p1 = np.array([0, 0, 0])
        p2 = np.array([1, 1, 1])

        dist = calculate_distance(p1, p2)
        expected = np.sqrt(3)
        assert abs(dist - expected) < 0.01


class TestHipRotation:
    def test_hip_rotation_facing_right(self):
        """Test hip rotation when facing right."""
        left_hip = np.array([0.4, 0.5])
        right_hip = np.array([0.6, 0.5])

        rotation = calculate_hip_rotation(left_hip, right_hip)
        # Should be close to 90 degrees (facing up in image coordinates)
        assert abs(rotation - 90.0) < 10

    def test_hip_rotation_different_orientation(self):
        """Test hip rotation with different orientations."""
        left_hip = np.array([0.5, 0.4])
        right_hip = np.array([0.5, 0.6])

        rotation = calculate_hip_rotation(left_hip, right_hip)
        assert -180 <= rotation <= 180


class TestVelocity:
    def test_velocity_constant_speed(self):
        """Test velocity with constant speed motion."""
        positions = np.array([[0, 0], [1, 0], [2, 0], [3, 0]])
        timestamps = np.array([0, 100, 200, 300])  # milliseconds

        velocities = calculate_velocity(positions, timestamps)

        # Each step is 1 unit in 100ms = 10 units/second
        expected = np.array([10.0, 10.0, 10.0])
        np.testing.assert_allclose(velocities, expected, rtol=0.01)

    def test_velocity_acceleration(self):
        """Test velocity with acceleration."""
        positions = np.array([[0, 0], [1, 0], [3, 0], [6, 0]])
        timestamps = np.array([0, 100, 200, 300])

        velocities = calculate_velocity(positions, timestamps)

        # Should show increasing velocity
        assert velocities[0] < velocities[1] < velocities[2]

    def test_angular_velocity(self):
        """Test angular velocity calculation."""
        angles = np.array([0, 45, 90, 135])  # degrees
        timestamps = np.array([0, 100, 200, 300])  # ms

        ang_vel = calculate_angular_velocity(angles, timestamps)

        # 45 degrees in 100ms = 450 deg/s
        expected = np.array([450.0, 450.0, 450.0])
        np.testing.assert_allclose(ang_vel, expected, rtol=0.01)

    def test_angular_velocity_wrapping(self):
        """Test angular velocity handles angle wrapping."""
        angles = np.array([350, 10, 30])  # wraps around 0
        timestamps = np.array([0, 100, 200])

        ang_vel = calculate_angular_velocity(angles, timestamps)

        # Should handle 350->10 as 20 degrees, not 340
        assert ang_vel[0] < 300  # Not a huge jump


class TestArcCompleteness:
    def test_full_circle(self):
        """Test arc completeness for a full circle."""
        # Create circular trajectory
        t = np.linspace(0, 2*np.pi, 50)
        trajectory = np.column_stack([np.cos(t), np.sin(t)])

        completeness = calculate_arc_completeness(trajectory, expected_arc_degrees=360)

        # Should be close to 1.0 for full circle
        assert completeness > 0.9

    def test_half_circle(self):
        """Test arc completeness for a semicircle."""
        t = np.linspace(0, np.pi, 30)
        trajectory = np.column_stack([np.cos(t), np.sin(t)])

        completeness = calculate_arc_completeness(trajectory, expected_arc_degrees=180)

        assert 0.8 < completeness <= 1.0


class TestMotionSmoothness:
    def test_smooth_motion(self):
        """Test smoothness with smooth velocity profile."""
        # Constant velocity
        velocities = np.ones(50) * 10.0

        smoothness = calculate_motion_smoothness(velocities)

        # Should be very smooth
        assert smoothness > 0.9

    def test_jerky_motion(self):
        """Test smoothness with jerky motion."""
        # Alternating velocities
        velocities = np.array([1, 10, 1, 10, 1, 10, 1, 10])

        smoothness = calculate_motion_smoothness(velocities)

        # Should be less smooth
        assert smoothness < 0.8


class TestDirectionChange:
    def test_direction_change_90deg(self):
        """Test detection of 90 degree direction change."""
        # Move right, then up
        positions = np.array([
            [0, 0],
            [1, 0],
            [2, 0],
            [3, 0],  # turn point
            [3, 1],
            [3, 2],
            [3, 3]
        ])

        max_change, frame_idx = calculate_direction_change(positions, window_before=2, window_after=2)

        # Should detect ~90 degree change around frame 3
        assert 70 < max_change < 110
        assert 2 <= frame_idx <= 4

    def test_no_direction_change(self):
        """Test with straight line motion."""
        positions = np.array([[i, 0] for i in range(10)])

        max_change, _ = calculate_direction_change(positions)

        # Should be close to 0 or 180 (straight line)
        assert max_change < 10 or max_change > 170


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
