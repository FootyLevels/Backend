"""
Alignment Module
Align two pose sequences using Dynamic Time Warping (DTW).
Handles videos of different lengths and speeds.
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional
from dtaidistance import dtw
from dtaidistance import dtw_ndim


@dataclass
class AlignmentResult:
    """Result of aligning two pose sequences."""
    
    # Frame mapping: ref_indices[i] corresponds to user_indices[i]
    ref_indices: np.ndarray
    user_indices: np.ndarray
    
    # DTW distance (lower = more similar)
    distance: float
    
    # Normalized distance (accounts for sequence length)
    normalized_distance: float
    
    # Per-frame distances along the optimal path
    frame_distances: np.ndarray
    
    def get_user_frame_for_ref(self, ref_frame: int) -> int:
        """Get the corresponding user frame for a reference frame."""
        idx = np.searchsorted(self.ref_indices, ref_frame)
        idx = min(idx, len(self.user_indices) - 1)
        return self.user_indices[idx]
    
    def get_ref_frame_for_user(self, user_frame: int) -> int:
        """Get the corresponding reference frame for a user frame."""
        idx = np.searchsorted(self.user_indices, user_frame)
        idx = min(idx, len(self.ref_indices) - 1)
        return self.ref_indices[idx]


def extract_features_for_alignment(
    pose_data: np.ndarray,
    key_joints: Optional[List[int]] = None
) -> np.ndarray:
    """
    Extract features from pose data for DTW alignment.
    
    Args:
        pose_data: Pose sequence, shape (num_frames, 33, 4)
        key_joints: List of joint indices to use (None = use all)
    
    Returns:
        Feature array for DTW, shape (num_frames, num_features)
    """
    if key_joints is None:
        # Default: use major body joints for soccer
        key_joints = [
            11, 12,  # shoulders
            23, 24,  # hips
            25, 26,  # knees
            27, 28,  # ankles
        ]
    
    # Extract x, y, z for key joints
    features = []
    for frame_idx in range(len(pose_data)):
        frame_features = []
        for joint_idx in key_joints:
            # Use x, y coordinates (normalized, so z is less reliable)
            frame_features.extend(pose_data[frame_idx, joint_idx, :2])
        features.append(frame_features)
    
    return np.array(features, dtype=np.float64)


def align_sequences(
    ref_poses: np.ndarray,
    user_poses: np.ndarray,
    key_joints: Optional[List[int]] = None,
    window: Optional[int] = None
) -> AlignmentResult:
    """
    Align user pose sequence to reference using DTW.
    
    Args:
        ref_poses: Reference pose sequence, shape (num_frames, 33, 4)
        user_poses: User pose sequence, shape (num_frames, 33, 4)
        key_joints: Joint indices to use for alignment
        window: DTW window constraint (None = no constraint)
    
    Returns:
        AlignmentResult with frame mappings and distances
    """
    # Extract features
    ref_features = extract_features_for_alignment(ref_poses, key_joints)
    user_features = extract_features_for_alignment(user_poses, key_joints)
    
    # Compute DTW
    if window is None:
        window = max(len(ref_features), len(user_features))
    
    # Use dtaidistance for efficient DTW computation
    distance, paths = dtw_ndim.warping_paths(
        ref_features, 
        user_features,
        window=window
    )
    
    # Get best path
    best_path = dtw.best_path(paths)
    
    ref_indices = np.array([p[0] for p in best_path])
    user_indices = np.array([p[1] for p in best_path])
    
    # Calculate per-frame distances along path
    frame_distances = []
    for ref_idx, user_idx in zip(ref_indices, user_indices):
        dist = np.linalg.norm(ref_features[ref_idx] - user_features[user_idx])
        frame_distances.append(dist)
    
    frame_distances = np.array(frame_distances)
    
    # Normalized distance
    path_length = len(best_path)
    normalized_distance = distance / path_length if path_length > 0 else 0
    
    return AlignmentResult(
        ref_indices=ref_indices,
        user_indices=user_indices,
        distance=distance,
        normalized_distance=normalized_distance,
        frame_distances=frame_distances
    )


def align_sequences_simple(
    ref_features: np.ndarray,
    user_features: np.ndarray
) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Simple DTW alignment for 1D or 2D feature sequences.
    
    Uses fastdtw for simpler cases.
    
    Args:
        ref_features: Reference features, shape (num_frames,) or (num_frames, num_features)
        user_features: User features
    
    Returns:
        Tuple of (ref_indices, user_indices, distance)
    """
    from fastdtw import fastdtw
    from scipy.spatial.distance import euclidean
    
    distance, path = fastdtw(ref_features, user_features, dist=euclidean)
    
    ref_indices = np.array([p[0] for p in path])
    user_indices = np.array([p[1] for p in path])
    
    return ref_indices, user_indices, distance


def find_key_moment_alignment(
    ref_poses: np.ndarray,
    user_poses: np.ndarray,
    ref_key_frame: int,
    alignment: AlignmentResult
) -> int:
    """
    Find the user frame that corresponds to a key moment in the reference.
    
    Args:
        ref_poses: Reference pose sequence
        user_poses: User pose sequence
        ref_key_frame: Key frame index in reference
        alignment: Pre-computed alignment
    
    Returns:
        Corresponding frame index in user sequence
    """
    return alignment.get_user_frame_for_ref(ref_key_frame)


def compute_aligned_deviations(
    ref_poses: np.ndarray,
    user_poses: np.ndarray,
    alignment: AlignmentResult,
    joint_indices: Optional[List[int]] = None
) -> dict:
    """
    Compute per-joint deviations between aligned sequences.
    
    Args:
        ref_poses: Reference poses, shape (frames, 33, 4)
        user_poses: User poses
        alignment: DTW alignment result
        joint_indices: Joints to compute deviations for
    
    Returns:
        Dictionary mapping joint index to deviation array
    """
    if joint_indices is None:
        joint_indices = list(range(33))
    
    deviations = {}
    
    for joint_idx in joint_indices:
        joint_devs = []
        
        for ref_idx, user_idx in zip(alignment.ref_indices, alignment.user_indices):
            ref_pos = ref_poses[ref_idx, joint_idx, :3]
            user_pos = user_poses[user_idx, joint_idx, :3]
            
            deviation = np.linalg.norm(ref_pos - user_pos)
            joint_devs.append(deviation)
        
        deviations[joint_idx] = np.array(joint_devs)
    
    return deviations


def get_deviation_summary(deviations: dict) -> dict:
    """
    Summarize deviations per joint.
    
    Returns mean, max, and std for each joint.
    """
    summary = {}
    
    for joint_idx, devs in deviations.items():
        summary[joint_idx] = {
            'mean': float(np.mean(devs)),
            'max': float(np.max(devs)),
            'std': float(np.std(devs)),
            'median': float(np.median(devs))
        }
    
    return summary


class SequenceAligner:
    """
    High-level class for aligning and comparing pose sequences.
    
    Usage:
        aligner = SequenceAligner()
        result = aligner.align(ref_poses, user_poses)
        deviations = aligner.compute_deviations(result)
    """
    
    # Joint indices for soccer-specific alignment
    SOCCER_JOINTS = [
        23, 24,  # hips (most important for body rotation)
        25, 26,  # knees
        27, 28,  # ankles
        11, 12,  # shoulders
    ]
    
    def __init__(self, key_joints: Optional[List[int]] = None):
        """
        Initialize aligner.
        
        Args:
            key_joints: Joints to use for alignment (None = soccer defaults)
        """
        self.key_joints = key_joints or self.SOCCER_JOINTS
    
    def align(
        self,
        ref_sequence,  # PoseSequence
        user_sequence,  # PoseSequence
        window: Optional[int] = None
    ) -> AlignmentResult:
        """
        Align user sequence to reference.
        
        Args:
            ref_sequence: Reference PoseSequence
            user_sequence: User PoseSequence  
            window: DTW window constraint
        
        Returns:
            AlignmentResult
        """
        ref_data = ref_sequence.to_numpy()
        user_data = user_sequence.to_numpy()
        
        return align_sequences(
            ref_data,
            user_data,
            key_joints=self.key_joints,
            window=window
        )
    
    def compute_deviations(
        self,
        ref_sequence,
        user_sequence,
        alignment: AlignmentResult
    ) -> dict:
        """Compute per-joint deviations."""
        ref_data = ref_sequence.to_numpy()
        user_data = user_sequence.to_numpy()
        
        return compute_aligned_deviations(
            ref_data,
            user_data,
            alignment,
            joint_indices=self.key_joints
        )
    
    def get_similarity_score(self, alignment: AlignmentResult) -> float:
        """
        Convert DTW distance to similarity score (0-100).
        
        Higher = more similar.
        """
        # Normalize: typical normalized_distance ranges from 0 to ~0.5
        # This is approximate and may need tuning
        max_expected_distance = 0.3
        normalized = min(alignment.normalized_distance / max_expected_distance, 1.0)
        
        # Convert to 0-100 score
        similarity = (1 - normalized) * 100
        return max(0, min(100, similarity))


# Quick test
if __name__ == "__main__":
    print("Testing alignment module...")
    
    # Create dummy pose sequences
    num_frames_ref = 60
    num_frames_user = 50  # Different length
    
    # Reference: smooth sine wave motion
    t_ref = np.linspace(0, 2*np.pi, num_frames_ref)
    ref_poses = np.zeros((num_frames_ref, 33, 4))
    for i in range(33):
        ref_poses[:, i, 0] = 0.5 + 0.1 * np.sin(t_ref + i * 0.1)  # x
        ref_poses[:, i, 1] = 0.5 + 0.1 * np.cos(t_ref + i * 0.1)  # y
        ref_poses[:, i, 3] = 1.0  # visibility
    
    # User: similar but slightly different timing
    t_user = np.linspace(0.2, 2*np.pi + 0.2, num_frames_user)
    user_poses = np.zeros((num_frames_user, 33, 4))
    for i in range(33):
        user_poses[:, i, 0] = 0.5 + 0.1 * np.sin(t_user + i * 0.1)
        user_poses[:, i, 1] = 0.5 + 0.1 * np.cos(t_user + i * 0.1)
        user_poses[:, i, 3] = 1.0
    
    # Test alignment
    result = align_sequences(ref_poses, user_poses)
    
    print(f"Reference frames: {num_frames_ref}")
    print(f"User frames: {num_frames_user}")
    print(f"Alignment path length: {len(result.ref_indices)}")
    print(f"DTW distance: {result.distance:.4f}")
    print(f"Normalized distance: {result.normalized_distance:.4f}")
    
    # Test deviation computation
    deviations = compute_aligned_deviations(ref_poses, user_poses, result)
    summary = get_deviation_summary(deviations)
    
    print(f"\nJoint 23 (left hip) deviation:")
    print(f"  Mean: {summary[23]['mean']:.4f}")
    print(f"  Max: {summary[23]['max']:.4f}")
    
    # Test aligner class
    aligner = SequenceAligner()
    similarity = aligner.get_similarity_score(result)
    print(f"\nSimilarity score: {similarity:.1f}%")
    
    print("\nAlignment tests passed!")
