"""
Pose Extractor Module
Extracts body pose landmarks from video using MediaPipe.
"""

import cv2
import numpy as np
import mediapipe as mp
from dataclasses import dataclass
from typing import List, Optional, Tuple
from pathlib import Path


@dataclass
class PoseLandmark:
    """Single landmark with x, y, z coordinates and visibility."""
    x: float
    y: float
    z: float
    visibility: float


@dataclass
class PoseFrame:
    """Pose data for a single frame."""
    frame_index: int
    timestamp_ms: float
    landmarks: List[PoseLandmark]  # 33 landmarks from MediaPipe
    
    def to_numpy(self) -> np.ndarray:
        """Convert landmarks to numpy array of shape (33, 4)."""
        return np.array([[lm.x, lm.y, lm.z, lm.visibility] for lm in self.landmarks])
    
    def get_landmark(self, index: int) -> PoseLandmark:
        """Get specific landmark by index."""
        return self.landmarks[index]


@dataclass 
class PoseSequence:
    """Sequence of poses extracted from a video."""
    frames: List[PoseFrame]
    fps: float
    width: int
    height: int
    duration_ms: float
    
    def to_numpy(self) -> np.ndarray:
        """Convert entire sequence to numpy array of shape (num_frames, 33, 4)."""
        return np.array([frame.to_numpy() for frame in self.frames])
    
    def get_joint_trajectory(self, joint_index: int) -> np.ndarray:
        """Get trajectory of a specific joint over time. Shape: (num_frames, 4)."""
        return np.array([
            [frame.landmarks[joint_index].x,
             frame.landmarks[joint_index].y,
             frame.landmarks[joint_index].z,
             frame.landmarks[joint_index].visibility]
            for frame in self.frames
        ])
    
    def __len__(self) -> int:
        return len(self.frames)


# MediaPipe landmark indices
class LandmarkIndex:
    """MediaPipe pose landmark indices."""
    NOSE = 0
    LEFT_EYE_INNER = 1
    LEFT_EYE = 2
    LEFT_EYE_OUTER = 3
    RIGHT_EYE_INNER = 4
    RIGHT_EYE = 5
    RIGHT_EYE_OUTER = 6
    LEFT_EAR = 7
    RIGHT_EAR = 8
    MOUTH_LEFT = 9
    MOUTH_RIGHT = 10
    LEFT_SHOULDER = 11
    RIGHT_SHOULDER = 12
    LEFT_ELBOW = 13
    RIGHT_ELBOW = 14
    LEFT_WRIST = 15
    RIGHT_WRIST = 16
    LEFT_PINKY = 17
    RIGHT_PINKY = 18
    LEFT_INDEX = 19
    RIGHT_INDEX = 20
    LEFT_THUMB = 21
    RIGHT_THUMB = 22
    LEFT_HIP = 23
    RIGHT_HIP = 24
    LEFT_KNEE = 25
    RIGHT_KNEE = 26
    LEFT_ANKLE = 27
    RIGHT_ANKLE = 28
    LEFT_HEEL = 29
    RIGHT_HEEL = 30
    LEFT_FOOT_INDEX = 31
    RIGHT_FOOT_INDEX = 32


class PoseExtractor:
    """
    Extract pose landmarks from video using MediaPipe.
    
    Usage:
        extractor = PoseExtractor()
        poses = extractor.extract_from_video("path/to/video.mp4")
    """
    
    def __init__(
        self,
        model_complexity: int = 2,  # 0, 1, or 2 (higher = more accurate but slower)
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        enable_segmentation: bool = False
    ):
        """
        Initialize the pose extractor.
        
        Args:
            model_complexity: 0=lite, 1=full, 2=heavy (most accurate)
            min_detection_confidence: Minimum confidence for person detection
            min_tracking_confidence: Minimum confidence for landmark tracking
            enable_segmentation: Whether to enable body segmentation mask
        """
        self.mp_pose = mp.solutions.pose
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=model_complexity,
            enable_segmentation=enable_segmentation,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence
        )
        self.mp_drawing = mp.solutions.drawing_utils
    
    def extract_from_video(
        self,
        video_path: str,
        max_frames: Optional[int] = None,
        skip_frames: int = 0
    ) -> PoseSequence:
        """
        Extract pose sequence from a video file.
        
        Args:
            video_path: Path to video file
            max_frames: Maximum number of frames to process (None = all)
            skip_frames: Number of frames to skip between processing (0 = process all)
        
        Returns:
            PoseSequence containing all extracted poses
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")
        
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_ms = (total_frames / fps) * 1000 if fps > 0 else 0
        
        frames = []
        frame_index = 0
        processed_count = 0
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            # Skip frames if requested
            if skip_frames > 0 and frame_index % (skip_frames + 1) != 0:
                frame_index += 1
                continue
            
            # Process frame
            timestamp_ms = (frame_index / fps) * 1000 if fps > 0 else 0
            pose_frame = self._process_frame(frame, frame_index, timestamp_ms)
            
            if pose_frame is not None:
                frames.append(pose_frame)
            
            processed_count += 1
            frame_index += 1
            
            if max_frames and processed_count >= max_frames:
                break
        
        cap.release()
        
        return PoseSequence(
            frames=frames,
            fps=fps,
            width=width,
            height=height,
            duration_ms=duration_ms
        )
    
    def extract_from_webcam(
        self,
        duration_seconds: float = 10.0,
        camera_index: int = 0
    ) -> PoseSequence:
        """
        Extract pose sequence from webcam for a specified duration.
        
        Args:
            duration_seconds: How long to record
            camera_index: Camera device index (usually 0)
        
        Returns:
            PoseSequence containing all extracted poses
        """
        cap = cv2.VideoCapture(camera_index)
        
        if not cap.isOpened():
            raise ValueError(f"Could not open camera {camera_index}")
        
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0  # Default to 30 if unknown
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        frames = []
        frame_index = 0
        start_time = cv2.getTickCount()
        
        print(f"Recording for {duration_seconds} seconds... Press 'q' to stop early.")
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            elapsed = (cv2.getTickCount() - start_time) / cv2.getTickFrequency()
            timestamp_ms = elapsed * 1000
            
            pose_frame = self._process_frame(frame, frame_index, timestamp_ms)
            
            if pose_frame is not None:
                frames.append(pose_frame)
                
                # Draw landmarks on frame for preview
                self._draw_landmarks(frame, pose_frame)
            
            cv2.imshow('Recording - Press Q to stop', frame)
            
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
            if elapsed >= duration_seconds:
                break
            
            frame_index += 1
        
        cap.release()
        cv2.destroyAllWindows()
        
        duration_ms = frames[-1].timestamp_ms if frames else 0
        
        return PoseSequence(
            frames=frames,
            fps=fps,
            width=width,
            height=height,
            duration_ms=duration_ms
        )
    
    def _process_frame(
        self,
        frame: np.ndarray,
        frame_index: int,
        timestamp_ms: float
    ) -> Optional[PoseFrame]:
        """Process a single frame and extract pose landmarks."""
        # Convert BGR to RGB for MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Process the frame
        results = self.pose.process(rgb_frame)
        
        if results.pose_landmarks is None:
            return None
        
        # Extract landmarks
        landmarks = []
        for lm in results.pose_landmarks.landmark:
            landmarks.append(PoseLandmark(
                x=lm.x,
                y=lm.y,
                z=lm.z,
                visibility=lm.visibility
            ))
        
        return PoseFrame(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            landmarks=landmarks
        )
    
    def _draw_landmarks(self, frame: np.ndarray, pose_frame: PoseFrame) -> None:
        """Draw pose landmarks on a frame for visualization."""
        # Convert our format back to MediaPipe format for drawing
        landmark_list = mp.framework.formats.landmark_pb2.NormalizedLandmarkList()
        
        for lm in pose_frame.landmarks:
            landmark = landmark_list.landmark.add()
            landmark.x = lm.x
            landmark.y = lm.y
            landmark.z = lm.z
            landmark.visibility = lm.visibility
        
        self.mp_drawing.draw_landmarks(
            frame,
            landmark_list,
            self.mp_pose.POSE_CONNECTIONS,
            self.mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=2),
            self.mp_drawing.DrawingSpec(color=(0, 0, 255), thickness=2)
        )
    
    def close(self):
        """Release resources."""
        self.pose.close()


def normalize_poses(
    pose_sequence: PoseSequence,
    center_joint: int = LandmarkIndex.LEFT_HIP,
    scale_reference: Tuple[int, int] = (LandmarkIndex.LEFT_HIP, LandmarkIndex.LEFT_SHOULDER)
) -> PoseSequence:
    """
    Normalize pose sequence for comparison.
    
    - Centers poses around a reference joint (default: left hip)
    - Scales based on body proportions (default: hip to shoulder distance)
    
    This makes poses comparable regardless of:
    - Person's position in frame
    - Distance from camera
    - Body size
    
    Args:
        pose_sequence: Original pose sequence
        center_joint: Joint index to use as center (default: left hip)
        scale_reference: Tuple of (joint1, joint2) to use for scaling
    
    Returns:
        Normalized PoseSequence
    """
    normalized_frames = []
    
    for frame in pose_sequence.frames:
        landmarks_array = frame.to_numpy()
        
        # Get center point
        center = landmarks_array[center_joint, :3]  # x, y, z
        
        # Calculate scale factor (distance between reference joints)
        j1, j2 = scale_reference
        scale_dist = np.linalg.norm(
            landmarks_array[j1, :3] - landmarks_array[j2, :3]
        )
        
        if scale_dist < 0.01:  # Avoid division by zero
            scale_dist = 1.0
        
        # Normalize each landmark
        normalized_landmarks = []
        for i, lm in enumerate(frame.landmarks):
            # Center and scale
            norm_x = (lm.x - center[0]) / scale_dist
            norm_y = (lm.y - center[1]) / scale_dist
            norm_z = (lm.z - center[2]) / scale_dist
            
            normalized_landmarks.append(PoseLandmark(
                x=norm_x,
                y=norm_y,
                z=norm_z,
                visibility=lm.visibility
            ))
        
        normalized_frames.append(PoseFrame(
            frame_index=frame.frame_index,
            timestamp_ms=frame.timestamp_ms,
            landmarks=normalized_landmarks
        ))
    
    return PoseSequence(
        frames=normalized_frames,
        fps=pose_sequence.fps,
        width=pose_sequence.width,
        height=pose_sequence.height,
        duration_ms=pose_sequence.duration_ms
    )


# Quick test
if __name__ == "__main__":
    print("Testing PoseExtractor with webcam...")
    print("This will record for 5 seconds and show pose detection.")
    
    extractor = PoseExtractor(model_complexity=1)
    
    try:
        poses = extractor.extract_from_webcam(duration_seconds=5)
        print(f"\nExtracted {len(poses)} frames")
        print(f"FPS: {poses.fps}")
        print(f"Duration: {poses.duration_ms:.1f}ms")
        
        if poses.frames:
            # Show first frame's hip positions
            first_frame = poses.frames[0]
            left_hip = first_frame.get_landmark(LandmarkIndex.LEFT_HIP)
            right_hip = first_frame.get_landmark(LandmarkIndex.RIGHT_HIP)
            print(f"\nFirst frame hip positions:")
            print(f"  Left hip:  ({left_hip.x:.3f}, {left_hip.y:.3f}, {left_hip.z:.3f})")
            print(f"  Right hip: ({right_hip.x:.3f}, {right_hip.y:.3f}, {right_hip.z:.3f})")
            
            # Normalize and compare
            normalized = normalize_poses(poses)
            norm_first = normalized.frames[0]
            norm_left_hip = norm_first.get_landmark(LandmarkIndex.LEFT_HIP)
            print(f"\nNormalized left hip: ({norm_left_hip.x:.3f}, {norm_left_hip.y:.3f})")
            
    finally:
        extractor.close()
