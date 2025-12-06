"""
Video Processor Module
Utilities for video loading, frame extraction, and preprocessing.
"""

import cv2
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple, Generator, List
import tempfile


@dataclass
class VideoInfo:
    """Information about a video file."""
    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    codec: str


class VideoProcessor:
    """
    Process and manipulate video files.

    Usage:
        processor = VideoProcessor()
        info = processor.get_video_info("video.mp4")
        frames = list(processor.extract_frames("video.mp4"))
    """

    @staticmethod
    def get_video_info(video_path: str) -> VideoInfo:
        """
        Get detailed information about a video file.

        Args:
            video_path: Path to video file

        Returns:
            VideoInfo object with video metadata
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        cap = cv2.VideoCapture(str(video_path))

        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_seconds = frame_count / fps if fps > 0 else 0

        # Get codec
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])

        cap.release()

        return VideoInfo(
            path=str(video_path),
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration_seconds=duration_seconds,
            codec=codec
        )

    @staticmethod
    def extract_frames(
        video_path: str,
        start_frame: int = 0,
        end_frame: Optional[int] = None,
        step: int = 1
    ) -> Generator[Tuple[int, np.ndarray], None, None]:
        """
        Extract frames from video as generator.

        Args:
            video_path: Path to video
            start_frame: First frame to extract
            end_frame: Last frame to extract (None = all)
            step: Frame step (1 = every frame, 2 = every other frame, etc.)

        Yields:
            Tuple of (frame_index, frame_array)
        """
        cap = cv2.VideoCapture(str(video_path))

        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        # Seek to start frame
        if start_frame > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        frame_idx = start_frame

        while cap.isOpened():
            ret, frame = cap.read()

            if not ret:
                break

            if end_frame is not None and frame_idx >= end_frame:
                break

            if (frame_idx - start_frame) % step == 0:
                yield frame_idx, frame

            frame_idx += 1

        cap.release()

    @staticmethod
    def resize_video(
        input_path: str,
        output_path: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        scale: Optional[float] = None,
        maintain_aspect: bool = True
    ):
        """
        Resize a video file.

        Args:
            input_path: Input video path
            output_path: Output video path
            width: Target width (if None, calculated from height or scale)
            height: Target height (if None, calculated from width or scale)
            scale: Scale factor (e.g., 0.5 for half size)
            maintain_aspect: Whether to maintain aspect ratio
        """
        cap = cv2.VideoCapture(input_path)

        if not cap.isOpened():
            raise ValueError(f"Could not open video: {input_path}")

        # Get original dimensions
        orig_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)

        # Calculate target dimensions
        if scale is not None:
            width = int(orig_width * scale)
            height = int(orig_height * scale)
        elif width is not None and height is None and maintain_aspect:
            height = int(orig_height * (width / orig_width))
        elif height is not None and width is None and maintain_aspect:
            width = int(orig_width * (height / orig_height))
        elif width is None or height is None:
            width, height = orig_width, orig_height

        # Create video writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            resized = cv2.resize(frame, (width, height))
            out.write(resized)

        cap.release()
        out.release()

    @staticmethod
    def trim_video(
        input_path: str,
        output_path: str,
        start_seconds: float = 0,
        end_seconds: Optional[float] = None
    ):
        """
        Trim video to specific time range.

        Args:
            input_path: Input video path
            output_path: Output video path
            start_seconds: Start time in seconds
            end_seconds: End time in seconds (None = end of video)
        """
        cap = cv2.VideoCapture(input_path)

        if not cap.isOpened():
            raise ValueError(f"Could not open video: {input_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        start_frame = int(start_seconds * fps)
        end_frame = int(end_seconds * fps) if end_seconds else None

        # Seek to start
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        frame_idx = start_frame

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if end_frame and frame_idx >= end_frame:
                break

            out.write(frame)
            frame_idx += 1

        cap.release()
        out.release()

    @staticmethod
    def extract_clip_around_frame(
        video_path: str,
        center_frame: int,
        window_frames: int = 30,
        output_path: Optional[str] = None
    ) -> str:
        """
        Extract a short clip centered around a specific frame.

        Useful for extracting key moments.

        Args:
            video_path: Input video path
            center_frame: Frame to center clip around
            window_frames: Number of frames before and after center
            output_path: Where to save clip (None = temp file)

        Returns:
            Path to extracted clip
        """
        if output_path is None:
            output_path = tempfile.mktemp(suffix='.mp4')

        cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        start_frame = max(0, center_frame - window_frames)
        end_frame = center_frame + window_frames

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        frame_idx = start_frame

        while cap.isOpened() and frame_idx <= end_frame:
            ret, frame = cap.read()
            if not ret:
                break

            out.write(frame)
            frame_idx += 1

        cap.release()
        out.release()

        return output_path

    @staticmethod
    def combine_videos_side_by_side(
        left_video: str,
        right_video: str,
        output_path: str,
        match_fps: bool = True
    ):
        """
        Combine two videos side by side.

        Args:
            left_video: Path to left video
            right_video: Path to right video
            output_path: Output path
            match_fps: Whether to match FPS of both videos
        """
        left_cap = cv2.VideoCapture(left_video)
        right_cap = cv2.VideoCapture(right_video)

        if not left_cap.isOpened() or not right_cap.isOpened():
            raise ValueError("Could not open one or both videos")

        # Get properties
        left_fps = left_cap.get(cv2.CAP_PROP_FPS)
        right_fps = right_cap.get(cv2.CAP_PROP_FPS)
        fps = min(left_fps, right_fps) if match_fps else left_fps

        left_width = int(left_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        left_height = int(left_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        right_width = int(right_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        right_height = int(right_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Match heights
        target_height = max(left_height, right_height)
        left_width = int(left_width * (target_height / left_height))
        right_width = int(right_width * (target_height / right_height))

        output_width = left_width + right_width

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_path, fourcc, fps, (output_width, target_height))

        while True:
            ret_left, left_frame = left_cap.read()
            ret_right, right_frame = right_cap.read()

            if not ret_left or not ret_right:
                break

            # Resize frames
            left_frame = cv2.resize(left_frame, (left_width, target_height))
            right_frame = cv2.resize(right_frame, (right_width, target_height))

            # Combine
            combined = np.hstack([left_frame, right_frame])
            out.write(combined)

        left_cap.release()
        right_cap.release()
        out.release()

    @staticmethod
    def normalize_video_lighting(frame: np.ndarray) -> np.ndarray:
        """
        Normalize lighting in a frame.

        Helps with videos recorded in different lighting conditions.

        Args:
            frame: Input frame

        Returns:
            Normalized frame
        """
        # Convert to LAB color space
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)

        # Split channels
        l, a, b = cv2.split(lab)

        # Apply CLAHE to L channel
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l = clahe.apply(l)

        # Merge and convert back
        lab = cv2.merge([l, a, b])
        normalized = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        return normalized


# Quick test
if __name__ == "__main__":
    print("Video Processor module loaded successfully.")
    print("\nExample usage:")
    print("  processor = VideoProcessor()")
    print("  info = processor.get_video_info('video.mp4')")
    print("  print(f'Duration: {info.duration_seconds}s, FPS: {info.fps}')")
