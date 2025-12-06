"""
Metrics Module
Calculate biomechanical metrics from pose data: angles, distances, velocities, rotations.
"""

import numpy as np
from typing import Tuple, List, Optional
from dataclasses import dataclass


@dataclass
class JointMetrics:
    """Metrics calculated for a specific joint or joint group."""
    name: str
    value: float
    unit: str
    frame_index: int
    confidence: float = 1.0


def calculate_angle_3points(
    p1: np.ndarray,
    p2: np.ndarray,
    p3: np.ndarray
) -> float:
    """
    Calculate angle at p2 formed by points p1-p2-p3.
    
    Example: Knee angle with hip-knee-ankle
    
    Args:
        p1: First point (e.g., hip) - shape (2,) or (3,)
        p2: Vertex point (e.g., knee) - shape (2,) or (3,)
        p3: Third point (e.g., ankle) - shape (2,) or (3,)
    
    Returns:
        Angle in degrees (0-180)
    """
    # Vectors from vertex to other points
    v1 = p1 - p2
    v2 = p3 - p2
    
    # Calculate angle using dot product
    cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
    cos_angle = np.clip(cos_angle, -1.0, 1.0)  # Handle numerical errors
    
    angle_rad = np.arccos(cos_angle)
    return np.degrees(angle_rad)


def calculate_angle_2vectors(
    v1: np.ndarray,
    v2: np.ndarray
) -> float:
    """
    Calculate angle between two vectors.
    
    Args:
        v1: First vector
        v2: Second vector
    
    Returns:
        Angle in degrees (0-180)
    """
    cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
    cos_angle = np.clip(cos_angle, -1.0, 1.0)
    return np.degrees(np.arccos(cos_angle))


def calculate_distance(
    p1: np.ndarray,
    p2: np.ndarray
) -> float:
    """
    Calculate Euclidean distance between two points.
    
    Args:
        p1: First point
        p2: Second point
    
    Returns:
        Distance (in same units as input, typically normalized 0-1)
    """
    return np.linalg.norm(p2 - p1)


def calculate_hip_rotation(
    left_hip: np.ndarray,
    right_hip: np.ndarray,
    reference_direction: np.ndarray = np.array([1, 0])
) -> float:
    """
    Calculate hip rotation angle relative to a reference direction.
    
    Args:
        left_hip: Left hip position (x, y) or (x, y, z)
        right_hip: Right hip position
        reference_direction: Direction to measure rotation from (default: facing right)
    
    Returns:
        Rotation angle in degrees (-180 to 180)
    """
    # Hip direction vector (perpendicular to hip line, pointing "forward")
    hip_vector = right_hip[:2] - left_hip[:2]
    
    # Rotate 90 degrees to get forward direction
    forward = np.array([-hip_vector[1], hip_vector[0]])
    
    # Calculate angle from reference
    angle = np.arctan2(forward[1], forward[0]) - np.arctan2(reference_direction[1], reference_direction[0])
    
    return np.degrees(angle)


def calculate_velocity(
    positions: np.ndarray,
    timestamps_ms: np.ndarray
) -> np.ndarray:
    """
    Calculate velocity from position trajectory.
    
    Args:
        positions: Array of positions, shape (num_frames, 2) or (num_frames, 3)
        timestamps_ms: Array of timestamps in milliseconds
    
    Returns:
        Velocity array, shape (num_frames-1,) in units per second
    """
    if len(positions) < 2:
        return np.array([0.0])
    
    # Calculate displacements
    displacements = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    
    # Calculate time differences in seconds
    dt = np.diff(timestamps_ms) / 1000.0
    dt = np.maximum(dt, 1e-6)  # Avoid division by zero
    
    # Velocity = displacement / time
    return displacements / dt


def calculate_angular_velocity(
    angles: np.ndarray,
    timestamps_ms: np.ndarray
) -> np.ndarray:
    """
    Calculate angular velocity from angle trajectory.
    
    Args:
        angles: Array of angles in degrees
        timestamps_ms: Array of timestamps
    
    Returns:
        Angular velocity in degrees per second
    """
    if len(angles) < 2:
        return np.array([0.0])
    
    # Handle angle wrapping (e.g., 359° to 1°)
    angle_diffs = np.diff(angles)
    angle_diffs = np.where(angle_diffs > 180, angle_diffs - 360, angle_diffs)
    angle_diffs = np.where(angle_diffs < -180, angle_diffs + 360, angle_diffs)
    
    dt = np.diff(timestamps_ms) / 1000.0
    dt = np.maximum(dt, 1e-6)
    
    return np.abs(angle_diffs) / dt


def calculate_arc_completeness(
    trajectory: np.ndarray,
    expected_arc_degrees: float = 180.0
) -> float:
    """
    Calculate how complete a circular arc motion is.
    
    Useful for step-overs and other circular foot motions.
    
    Args:
        trajectory: Array of 2D positions, shape (num_frames, 2)
        expected_arc_degrees: Expected arc angle
    
    Returns:
        Completeness score (0-1, where 1 = full expected arc)
    """
    if len(trajectory) < 3:
        return 0.0
    
    # Find center of arc (approximate)
    center = np.mean(trajectory, axis=0)
    
    # Calculate angles from center
    vectors = trajectory - center
    angles = np.arctan2(vectors[:, 1], vectors[:, 0])
    
    # Calculate total arc covered
    angle_diffs = np.diff(angles)
    angle_diffs = np.where(angle_diffs > np.pi, angle_diffs - 2*np.pi, angle_diffs)
    angle_diffs = np.where(angle_diffs < -np.pi, angle_diffs + 2*np.pi, angle_diffs)
    
    total_arc = np.abs(np.sum(angle_diffs))
    expected_arc = np.radians(expected_arc_degrees)
    
    return min(total_arc / expected_arc, 1.0)


def calculate_motion_smoothness(
    velocities: np.ndarray
) -> float:
    """
    Calculate smoothness of motion (lower jerk = smoother).
    
    Returns a score from 0-1 where 1 is perfectly smooth.
    
    Args:
        velocities: Array of velocities
    
    Returns:
        Smoothness score (0-1)
    """
    if len(velocities) < 2:
        return 1.0
    
    # Jerk is the derivative of acceleration (second derivative of velocity)
    accelerations = np.diff(velocities)
    jerk = np.diff(accelerations)
    
    if len(jerk) == 0:
        return 1.0
    
    # Normalize jerk by mean velocity
    mean_vel = np.mean(np.abs(velocities)) + 1e-6
    normalized_jerk = np.mean(np.abs(jerk)) / mean_vel
    
    # Convert to 0-1 score (higher jerk = lower smoothness)
    smoothness = 1.0 / (1.0 + normalized_jerk)
    
    return smoothness


def calculate_direction_change(
    positions: np.ndarray,
    window_before: int = 5,
    window_after: int = 5
) -> Tuple[float, int]:
    """
    Find the maximum direction change in a trajectory.
    
    Useful for detecting fakes/feints.
    
    Args:
        positions: Position trajectory, shape (num_frames, 2)
        window_before: Frames to consider before peak
        window_after: Frames to consider after peak
    
    Returns:
        Tuple of (max_direction_change_degrees, frame_index)
    """
    if len(positions) < window_before + window_after + 1:
        return 0.0, 0
    
    max_change = 0.0
    max_frame = 0
    
    for i in range(window_before, len(positions) - window_after):
        # Direction before
        dir_before = positions[i] - positions[i - window_before]
        # Direction after
        dir_after = positions[i + window_after] - positions[i]
        
        if np.linalg.norm(dir_before) > 1e-6 and np.linalg.norm(dir_after) > 1e-6:
            angle = calculate_angle_2vectors(dir_before, dir_after)
            if angle > max_change:
                max_change = angle
                max_frame = i
    
    return max_change, max_frame


class PoseMetricsCalculator:
    """
    Calculate various metrics from pose sequences.
    
    Usage:
        calculator = PoseMetricsCalculator(pose_sequence)
        knee_angle = calculator.get_knee_angle(frame_index=10)
        hip_rotation = calculator.get_hip_rotation_over_time()
    """
    
    def __init__(self, pose_sequence):
        """
        Initialize with a PoseSequence.
        
        Args:
            pose_sequence: PoseSequence from pose_extractor
        """
        self.poses = pose_sequence
        self.data = pose_sequence.to_numpy()  # Shape: (frames, 33, 4)
        self.timestamps = np.array([f.timestamp_ms for f in pose_sequence.frames])
    
    def get_joint_position(self, joint_index: int, frame_index: int) -> np.ndarray:
        """Get position of a joint at a specific frame."""
        return self.data[frame_index, joint_index, :3]  # x, y, z
    
    def get_joint_trajectory(self, joint_index: int) -> np.ndarray:
        """Get full trajectory of a joint. Shape: (num_frames, 3)."""
        return self.data[:, joint_index, :3]
    
    def get_knee_angle(self, frame_index: int, side: str = 'left') -> float:
        """
        Get knee angle (hip-knee-ankle) at a specific frame.
        
        Args:
            frame_index: Frame to calculate for
            side: 'left' or 'right'
        """
        if side == 'left':
            hip_idx, knee_idx, ankle_idx = 23, 25, 27
        else:
            hip_idx, knee_idx, ankle_idx = 24, 26, 28
        
        hip = self.get_joint_position(hip_idx, frame_index)
        knee = self.get_joint_position(knee_idx, frame_index)
        ankle = self.get_joint_position(ankle_idx, frame_index)
        
        return calculate_angle_3points(hip, knee, ankle)
    
    def get_elbow_angle(self, frame_index: int, side: str = 'left') -> float:
        """Get elbow angle (shoulder-elbow-wrist)."""
        if side == 'left':
            shoulder_idx, elbow_idx, wrist_idx = 11, 13, 15
        else:
            shoulder_idx, elbow_idx, wrist_idx = 12, 14, 16
        
        shoulder = self.get_joint_position(shoulder_idx, frame_index)
        elbow = self.get_joint_position(elbow_idx, frame_index)
        wrist = self.get_joint_position(wrist_idx, frame_index)
        
        return calculate_angle_3points(shoulder, elbow, wrist)
    
    def get_hip_rotation_over_time(self) -> np.ndarray:
        """Get hip rotation angle for each frame."""
        rotations = []
        for i in range(len(self.data)):
            left_hip = self.get_joint_position(23, i)
            right_hip = self.get_joint_position(24, i)
            rotation = calculate_hip_rotation(left_hip, right_hip)
            rotations.append(rotation)
        return np.array(rotations)
    
    def get_total_hip_rotation(self, start_frame: int, end_frame: int) -> float:
        """Calculate total hip rotation between two frames."""
        rotations = self.get_hip_rotation_over_time()
        return rotations[end_frame] - rotations[start_frame]
    
    def get_joint_velocity(self, joint_index: int) -> np.ndarray:
        """Get velocity of a joint over time."""
        trajectory = self.get_joint_trajectory(joint_index)
        return calculate_velocity(trajectory, self.timestamps)
    
    def get_center_of_mass_velocity(self) -> np.ndarray:
        """
        Estimate center of mass velocity.
        Uses midpoint of hips as proxy for COM.
        """
        left_hip = self.get_joint_trajectory(23)
        right_hip = self.get_joint_trajectory(24)
        com = (left_hip + right_hip) / 2
        return calculate_velocity(com, self.timestamps)
    
    def get_plant_foot_distance(self, frame_index: int, plant_side: str = 'left') -> float:
        """
        Get distance of plant foot from center (proxy for ball position).
        
        In real implementation, you'd track the ball separately.
        Here we use distance from center of stance as approximation.
        """
        if plant_side == 'left':
            plant_ankle = self.get_joint_position(27, frame_index)
            other_ankle = self.get_joint_position(28, frame_index)
        else:
            plant_ankle = self.get_joint_position(28, frame_index)
            other_ankle = self.get_joint_position(27, frame_index)
        
        # Use midpoint between feet as reference
        center = (plant_ankle + other_ankle) / 2
        return calculate_distance(plant_ankle, center)
    
    def get_shoulder_direction_change(self, window: int = 5) -> Tuple[float, int]:
        """
        Detect shoulder fake (direction change).
        
        Returns max direction change and frame where it occurred.
        """
        left_shoulder = self.get_joint_trajectory(11)[:, :2]  # Just x, y
        right_shoulder = self.get_joint_trajectory(12)[:, :2]
        
        # Use shoulder midpoint
        shoulder_mid = (left_shoulder + right_shoulder) / 2
        
        return calculate_direction_change(shoulder_mid, window, window)
    
    def get_movement_smoothness(self, joint_index: int) -> float:
        """Get smoothness score for a joint's movement."""
        velocities = self.get_joint_velocity(joint_index)
        return calculate_motion_smoothness(velocities)
    
    def get_foot_arc_completeness(self, foot_side: str = 'right') -> float:
        """
        Calculate arc completeness for step-over motion.
        """
        if foot_side == 'right':
            ankle_trajectory = self.get_joint_trajectory(28)[:, :2]
        else:
            ankle_trajectory = self.get_joint_trajectory(27)[:, :2]
        
        return calculate_arc_completeness(ankle_trajectory)


# Quick test
if __name__ == "__main__":
    # Test basic calculations
    p1 = np.array([0, 0, 0])
    p2 = np.array([1, 0, 0])  # Vertex
    p3 = np.array([1, 1, 0])
    
    angle = calculate_angle_3points(p1, p2, p3)
    print(f"Angle test (should be 90°): {angle:.1f}°")
    
    # Test distance
    dist = calculate_distance(np.array([0, 0]), np.array([3, 4]))
    print(f"Distance test (should be 5): {dist:.1f}")
    
    # Test velocity
    positions = np.array([[0, 0], [1, 0], [3, 0], [6, 0]])
    timestamps = np.array([0, 100, 200, 300])  # ms
    velocities = calculate_velocity(positions, timestamps)
    print(f"Velocity test: {velocities}")  # Should be [10, 20, 30] units/sec
    
    # Test hip rotation
    left_hip = np.array([0.4, 0.5])
    right_hip = np.array([0.6, 0.5])
    rotation = calculate_hip_rotation(left_hip, right_hip)
    print(f"Hip rotation (facing up): {rotation:.1f}°")
    
    print("\nAll basic metric tests passed!")
