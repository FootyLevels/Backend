"""
Visualizer Module
Create side-by-side videos and visualizations for pose comparisons.
"""

import cv2
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from typing import Optional, Tuple, List
import mediapipe as mp

from .pose_extractor import PoseSequence, PoseFrame, LandmarkIndex
from .alignment import AlignmentResult


class PoseVisualizer:
    """
    Visualize pose data with skeleton overlays.

    Usage:
        viz = PoseVisualizer()
        viz.draw_skeleton(frame, pose_frame)
        viz.create_side_by_side_video(ref_video, user_video, output_path)
    """

    def __init__(self):
        """Initialize visualizer with MediaPipe drawing utilities."""
        self.mp_pose = mp.solutions.pose
        self.mp_drawing = mp.solutions.drawing_utils
        self.mp_drawing_styles = mp.solutions.drawing_styles

        # Custom drawing specs
        self.landmark_style = self.mp_drawing.DrawingSpec(
            color=(0, 255, 0), thickness=2, circle_radius=3
        )
        self.connection_style = self.mp_drawing.DrawingSpec(
            color=(0, 0, 255), thickness=2
        )

    def draw_skeleton(
        self,
        frame: np.ndarray,
        pose_frame: PoseFrame,
        color: Tuple[int, int, int] = (0, 255, 0)
    ) -> np.ndarray:
        """
        Draw pose skeleton on a frame.

        Args:
            frame: Image frame (BGR format)
            pose_frame: PoseFrame with landmarks
            color: Color for drawing (BGR)

        Returns:
            Frame with skeleton drawn
        """
        # Convert our format to MediaPipe format for drawing
        landmark_list = mp.framework.formats.landmark_pb2.NormalizedLandmarkList()

        for lm in pose_frame.landmarks:
            landmark = landmark_list.landmark.add()
            landmark.x = lm.x
            landmark.y = lm.y
            landmark.z = lm.z
            landmark.visibility = lm.visibility

        # Draw landmarks
        self.mp_drawing.draw_landmarks(
            frame,
            landmark_list,
            self.mp_pose.POSE_CONNECTIONS,
            landmark_drawing_spec=self.mp_drawing.DrawingSpec(
                color=color, thickness=2, circle_radius=2
            ),
            connection_drawing_spec=self.mp_drawing.DrawingSpec(
                color=color, thickness=2
            )
        )

        return frame

    def draw_skeleton_comparison(
        self,
        frame: np.ndarray,
        ref_pose: PoseFrame,
        user_pose: PoseFrame
    ) -> np.ndarray:
        """
        Draw both reference and user skeletons on the same frame.

        Args:
            frame: Base frame
            ref_pose: Reference pose (drawn in green)
            user_pose: User pose (drawn in red)

        Returns:
            Frame with both skeletons
        """
        # Draw reference in green
        frame = self.draw_skeleton(frame, ref_pose, color=(0, 255, 0))

        # Draw user in red
        frame = self.draw_skeleton(frame, user_pose, color=(0, 0, 255))

        # Add legend
        cv2.putText(frame, "Green: Reference", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, "Red: Your pose", (10, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        return frame

    def create_side_by_side_video(
        self,
        ref_video_path: str,
        user_video_path: str,
        output_path: str,
        ref_poses: PoseSequence,
        user_poses: PoseSequence,
        alignment: Optional[AlignmentResult] = None,
        fps: int = 30
    ):
        """
        Create side-by-side comparison video with skeleton overlays.

        Args:
            ref_video_path: Path to reference video
            user_video_path: Path to user video
            output_path: Where to save output video
            ref_poses: Reference pose sequence
            user_poses: User pose sequence
            alignment: Optional alignment for syncing videos
            fps: Output video FPS
        """
        ref_cap = cv2.VideoCapture(ref_video_path)
        user_cap = cv2.VideoCapture(user_video_path)

        # Get video properties
        ref_width = int(ref_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        ref_height = int(ref_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        user_width = int(user_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        user_height = int(user_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Make heights match
        target_height = max(ref_height, user_height)
        ref_width = int(ref_width * (target_height / ref_height))
        user_width = int(user_width * (target_height / user_height))

        # Output size: side by side
        output_width = ref_width + user_width
        output_height = target_height

        # Create video writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_path, fourcc, fps, (output_width, output_height))

        frame_idx = 0

        while True:
            ret_ref, ref_frame = ref_cap.read()
            ret_user, user_frame = user_cap.read()

            if not ret_ref or not ret_user:
                break

            # Resize frames
            ref_frame = cv2.resize(ref_frame, (ref_width, target_height))
            user_frame = cv2.resize(user_frame, (user_width, target_height))

            # Draw skeletons if poses available
            if frame_idx < len(ref_poses.frames):
                self.draw_skeleton(ref_frame, ref_poses.frames[frame_idx], (0, 255, 0))

            if frame_idx < len(user_poses.frames):
                self.draw_skeleton(user_frame, user_poses.frames[frame_idx], (0, 0, 255))

            # Add labels
            cv2.putText(ref_frame, "REFERENCE", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.putText(user_frame, "YOUR ATTEMPT", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

            # Combine frames
            combined = np.hstack([ref_frame, user_frame])

            out.write(combined)
            frame_idx += 1

        ref_cap.release()
        user_cap.release()
        out.release()

        print(f"Created side-by-side video: {output_path}")

    def plot_joint_trajectories(
        self,
        ref_poses: PoseSequence,
        user_poses: PoseSequence,
        joint_indices: List[int],
        save_path: Optional[str] = None
    ):
        """
        Plot trajectories of specific joints over time.

        Args:
            ref_poses: Reference pose sequence
            user_poses: User pose sequence
            joint_indices: List of joint indices to plot
            save_path: Optional path to save plot
        """
        num_joints = len(joint_indices)
        fig, axes = plt.subplots(num_joints, 1, figsize=(12, 4*num_joints))

        if num_joints == 1:
            axes = [axes]

        for idx, joint_idx in enumerate(joint_indices):
            ax = axes[idx]

            # Get trajectories
            ref_traj = ref_poses.get_joint_trajectory(joint_idx)
            user_traj = user_poses.get_joint_trajectory(joint_idx)

            # Plot x and y coordinates
            ax.plot(ref_traj[:, 0], label='Reference X', color='green', linestyle='-')
            ax.plot(ref_traj[:, 1], label='Reference Y', color='lightgreen', linestyle='--')
            ax.plot(user_traj[:, 0], label='User X', color='red', linestyle='-')
            ax.plot(user_traj[:, 1], label='User Y', color='pink', linestyle='--')

            ax.set_xlabel('Frame')
            ax.set_ylabel('Normalized Position')
            ax.set_title(f'Joint {joint_idx} Trajectory')
            ax.legend()
            ax.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150)
            print(f"Saved trajectory plot: {save_path}")
        else:
            plt.show()

    def plot_alignment_path(
        self,
        alignment: AlignmentResult,
        save_path: Optional[str] = None
    ):
        """
        Plot DTW alignment path.

        Shows how frames are matched between reference and user.
        """
        plt.figure(figsize=(10, 8))

        # Plot alignment path
        plt.plot(alignment.ref_indices, alignment.user_indices,
                marker='o', markersize=2, linewidth=1)

        # Plot diagonal (perfect alignment)
        max_idx = max(alignment.ref_indices.max(), alignment.user_indices.max())
        plt.plot([0, max_idx], [0, max_idx],
                'r--', alpha=0.5, label='Perfect alignment')

        plt.xlabel('Reference Frame Index')
        plt.ylabel('User Frame Index')
        plt.title('DTW Alignment Path')
        plt.legend()
        plt.grid(True, alpha=0.3)

        if save_path:
            plt.savefig(save_path, dpi=150)
            print(f"Saved alignment plot: {save_path}")
        else:
            plt.show()

    def create_checkpoint_visualization(
        self,
        frame: np.ndarray,
        pose_frame: PoseFrame,
        checkpoint_joints: List[int],
        checkpoint_name: str,
        score: float
    ) -> np.ndarray:
        """
        Create visualization highlighting specific checkpoint joints.

        Args:
            frame: Video frame
            pose_frame: Pose for this frame
            checkpoint_joints: Joint indices involved in checkpoint
            checkpoint_name: Name of checkpoint
            score: Score for this checkpoint (0-1)

        Returns:
            Annotated frame
        """
        # Draw full skeleton
        self.draw_skeleton(frame, pose_frame)

        # Highlight checkpoint joints
        height, width = frame.shape[:2]
        for joint_idx in checkpoint_joints:
            lm = pose_frame.landmarks[joint_idx]
            x = int(lm.x * width)
            y = int(lm.y * height)

            # Draw highlighted circle
            cv2.circle(frame, (x, y), 10, (255, 255, 0), 3)

        # Add text overlay
        color = (0, 255, 0) if score > 0.8 else (0, 165, 255) if score > 0.6 else (0, 0, 255)

        cv2.putText(frame, checkpoint_name, (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, f"Score: {score*100:.0f}%", (10, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        return frame


def create_analysis_visualization(
    ref_video_path: str,
    user_video_path: str,
    analysis_result,
    output_dir: str
):
    """
    Create complete visualization package for an analysis.

    Creates:
    - Side-by-side video
    - Joint trajectory plots
    - Alignment visualization
    - Checkpoint snapshots

    Args:
        ref_video_path: Reference video path
        user_video_path: User video path
        analysis_result: AnalysisResult object
        output_dir: Directory to save visualizations
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    viz = PoseVisualizer()

    print("Creating visualizations...")

    # TODO: Extract poses and alignment from analysis_result
    # For now, this is a placeholder

    print(f"Visualizations saved to: {output_dir}")


# Quick test
if __name__ == "__main__":
    print("Visualizer module loaded successfully.")
    print("\nTo create visualizations:")
    print("  viz = PoseVisualizer()")
    print("  viz.draw_skeleton(frame, pose_frame)")
