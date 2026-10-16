"""
Tests for metrics module.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels.metrics import (
    calculate_angle_2vectors,
    calculate_angle_3points,
    calculate_angular_velocity,
    calculate_distance,
    calculate_velocity,
)


class TestAngles:
    def test_angle_3points_90deg(self):
        """Right angle between perpendicular limbs."""
        p1 = np.array([0, 0, 0])
        p2 = np.array([1, 0, 0])  # Vertex
        p3 = np.array([1, 1, 0])

        assert calculate_angle_3points(p1, p2, p3) == pytest.approx(90.0, abs=0.1)

    def test_angle_3points_180deg(self):
        """Collinear points give a straight angle."""
        p1 = np.array([0, 0, 0])
        p2 = np.array([1, 0, 0])
        p3 = np.array([2, 0, 0])

        assert calculate_angle_3points(p1, p2, p3) == pytest.approx(180.0, abs=0.1)

    def test_angle_2vectors(self):
        """Perpendicular vectors are 90 degrees apart."""
        v1 = np.array([1, 0])
        v2 = np.array([0, 1])

        assert calculate_angle_2vectors(v1, v2) == pytest.approx(90.0, abs=0.1)

    def test_angle_is_clamped_for_parallel_vectors(self):
        """Floating point error must not push arccos out of domain."""
        v = np.array([1.0, 2.0, 3.0])

        assert calculate_angle_2vectors(v, v) == pytest.approx(0.0, abs=0.1)

    def test_zero_vector_does_not_raise(self):
        """A degenerate limb returns a number rather than dividing by zero."""
        assert np.isfinite(calculate_angle_2vectors(np.zeros(3), np.array([1, 0, 0])))


class TestDistance:
    def test_distance_2d(self):
        """Classic 3-4-5 triangle."""
        assert calculate_distance(np.array([0, 0]), np.array([3, 4])) == pytest.approx(5.0)

    def test_distance_3d(self):
        """Distance generalizes to three dimensions."""
        p1 = np.array([0, 0, 0])
        p2 = np.array([1, 2, 2])

        assert calculate_distance(p1, p2) == pytest.approx(3.0)


class TestVelocity:
    def test_velocity_constant_speed(self):
        """Even spacing in even time gives constant speed."""
        positions = np.array([[0, 0], [1, 0], [2, 0]])
        timestamps = np.array([0, 100, 200])  # ms

        velocities = calculate_velocity(positions, timestamps)

        assert len(velocities) == 2
        assert velocities == pytest.approx([10.0, 10.0])

    def test_velocity_acceleration(self):
        """Widening spacing gives increasing speed."""
        positions = np.array([[0, 0], [1, 0], [3, 0], [6, 0]])
        timestamps = np.array([0, 100, 200, 300])

        velocities = calculate_velocity(positions, timestamps)

        assert velocities == pytest.approx([10.0, 20.0, 30.0])

    def test_velocity_single_frame(self):
        """A single position has no speed to report."""
        assert calculate_velocity(np.array([[0, 0]]), np.array([0])) == pytest.approx([0.0])


class TestAngularVelocity:
    def test_angular_velocity(self):
        """Steady rotation gives steady angular speed."""
        angles = np.array([0, 10, 20])
        timestamps = np.array([0, 100, 200])

        result = calculate_angular_velocity(angles, timestamps)

        assert result == pytest.approx([100.0, 100.0])

    def test_angular_velocity_wrapping(self):
        """Crossing 360 degrees must not register as a huge jump."""
        angles = np.array([350, 10])  # +20 degrees, not -340
        timestamps = np.array([0, 100])

        result = calculate_angular_velocity(angles, timestamps)

        assert result == pytest.approx([200.0])
