"""
Metrics Module
Backend-agnostic geometry and kinematics helpers for 3D keypoint data.

These operate on plain numpy arrays and carry no assumptions about which model
produced the keypoints or how they are indexed.
"""

import numpy as np


def calculate_angle_3points(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    """
    Calculate the angle at p2 formed by points p1-p2-p3.

    Example: knee angle from hip-knee-ankle.

    Args:
        p1: First point, shape (2,) or (3,)
        p2: Vertex point, shape (2,) or (3,)
        p3: Third point, shape (2,) or (3,)

    Returns:
        Angle in degrees (0-180)
    """
    v1 = p1 - p2
    v2 = p3 - p2
    return calculate_angle_2vectors(v1, v2)


def calculate_angle_2vectors(v1: np.ndarray, v2: np.ndarray) -> float:
    """
    Calculate the angle between two vectors.

    Args:
        v1: First vector
        v2: Second vector

    Returns:
        Angle in degrees (0-180)
    """
    cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
    cos_angle = np.clip(cos_angle, -1.0, 1.0)  # Handle numerical errors
    return float(np.degrees(np.arccos(cos_angle)))


def calculate_distance(p1: np.ndarray, p2: np.ndarray) -> float:
    """
    Calculate the Euclidean distance between two points.

    Args:
        p1: First point
        p2: Second point

    Returns:
        Distance in the same units as the input (metres for SAM 3D Body output)
    """
    return float(np.linalg.norm(p2 - p1))


def calculate_velocity(positions: np.ndarray, timestamps_ms: np.ndarray) -> np.ndarray:
    """
    Calculate speed along a position trajectory.

    Args:
        positions: Positions, shape (num_frames, 2) or (num_frames, 3)
        timestamps_ms: Timestamps in milliseconds, shape (num_frames,)

    Returns:
        Speeds in units per second, shape (num_frames - 1,)
    """
    if len(positions) < 2:
        return np.array([0.0])

    displacements = np.linalg.norm(np.diff(positions, axis=0), axis=1)

    dt = np.diff(timestamps_ms) / 1000.0
    dt = np.maximum(dt, 1e-6)  # Avoid division by zero

    return displacements / dt


def calculate_angular_velocity(angles: np.ndarray, timestamps_ms: np.ndarray) -> np.ndarray:
    """
    Calculate angular speed along an angle trajectory.

    Args:
        angles: Angles in degrees, shape (num_frames,)
        timestamps_ms: Timestamps in milliseconds, shape (num_frames,)

    Returns:
        Angular speeds in degrees per second, shape (num_frames - 1,)
    """
    if len(angles) < 2:
        return np.array([0.0])

    # Handle angle wrapping (e.g. 359° to 1°)
    angle_diffs = np.diff(angles)
    angle_diffs = np.where(angle_diffs > 180, angle_diffs - 360, angle_diffs)
    angle_diffs = np.where(angle_diffs < -180, angle_diffs + 360, angle_diffs)

    dt = np.diff(timestamps_ms) / 1000.0
    dt = np.maximum(dt, 1e-6)

    return np.abs(angle_diffs) / dt
