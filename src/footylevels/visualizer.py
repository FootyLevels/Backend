"""
Visualizer Module
Overlay SAM 3D Body output on video frames and plot joint trajectories.

Keypoints are drawn as points rather than a connected skeleton: MHR's joint
hierarchy is defined by the loaded model asset, not by a fixed public index
table, so drawing bones here would mean guessing at connectivity. For full
textured mesh rendering use the upstream helper instead:

    from tools.vis_utils import visualize_sample_together
    rend = visualize_sample_together(img_bgr, outputs, estimator.faces)
"""

from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .body_extractor import BodyDetection, BodySequence

KEYPOINT_COLOR = (0, 255, 0)
BBOX_COLOR = (255, 200, 0)


class BodyVisualizer:
    """
    Draw extracted body data onto frames and charts.

    Usage:
        viz = BodyVisualizer()
        viz.render_video("input.mp4", sequence, "output.mp4")
    """

    def __init__(self, point_radius: int = 3, thickness: int = 2):
        """
        Args:
            point_radius: Radius of each drawn keypoint, in pixels.
            thickness: Line thickness for boxes and text.
        """
        self.point_radius = point_radius
        self.thickness = thickness

    def draw_keypoints(
        self,
        frame: np.ndarray,
        detection: BodyDetection,
        color: Tuple[int, int, int] = KEYPOINT_COLOR,
        label_indices: bool = False,
    ) -> np.ndarray:
        """
        Draw a detection's 2D keypoints onto a BGR frame, in place.

        Args:
            frame: BGR image to draw on.
            detection: Detection whose keypoints_2d are in this frame's pixels.
            color: BGR colour for the points.
            label_indices: Write each joint's index beside it. Useful for
                working out which MHR indices matter for a given skill.

        Returns:
            The same frame, with keypoints drawn.
        """
        height, width = frame.shape[:2]

        for index, point in enumerate(detection.keypoints_2d):
            x, y = int(round(float(point[0]))), int(round(float(point[1])))
            if not (0 <= x < width and 0 <= y < height):
                continue

            cv2.circle(frame, (x, y), self.point_radius, color, -1)
            if label_indices:
                cv2.putText(
                    frame, str(index), (x + 4, y - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1,
                )

        return frame

    def draw_bbox(
        self,
        frame: np.ndarray,
        detection: BodyDetection,
        color: Tuple[int, int, int] = BBOX_COLOR,
    ) -> np.ndarray:
        """Draw the detection's derived 2D bounds onto a BGR frame, in place."""
        x1, y1, x2, y2 = (int(round(v)) for v in detection.bbox)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, self.thickness)
        return frame

    def render_video(
        self,
        video_path: str,
        sequence: BodySequence,
        output_path: str,
        draw_bbox: bool = True,
        label_indices: bool = False,
    ) -> str:
        """
        Write a copy of the video with extracted keypoints overlaid.

        Frames the model found nobody in are written through unannotated.

        Args:
            video_path: Source video.
            sequence: Sequence extracted from that video.
            output_path: Where to write the annotated video.
            draw_bbox: Also draw the derived bounding box.
            label_indices: Write joint indices beside each point.

        Returns:
            The output path.

        Raises:
            FileNotFoundError: If the source video does not exist.
            ValueError: If the source video cannot be opened.
        """
        source = Path(video_path)
        if not source.exists():
            raise FileNotFoundError(f"Video not found: {source}")

        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {source}")

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        # Index detections by their source frame so stride'd sequences line up.
        by_index = {f.frame_index: f.primary for f in sequence.frames if f.primary}

        frame_index = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                detection = by_index.get(frame_index)
                if detection is not None:
                    self.draw_keypoints(frame, detection, label_indices=label_indices)
                    if draw_bbox:
                        self.draw_bbox(frame, detection)

                writer.write(frame)
                frame_index += 1
        finally:
            cap.release()
            writer.release()

        return output_path

    def plot_joint_trajectory(
        self,
        sequence: BodySequence,
        joint_indices: List[int],
        output_path: Optional[str] = None,
    ):
        """
        Plot the 3D path of one or more joints over the sequence.

        Args:
            sequence: Extracted sequence.
            joint_indices: MHR joint indices to plot.
            output_path: Save the figure here instead of returning it unsaved.

        Returns:
            The matplotlib Figure.

        Raises:
            ValueError: If the sequence has no detections to plot.
        """
        import matplotlib.pyplot as plt

        if not sequence.primary_detections:
            raise ValueError("Sequence has no detections to plot")

        figure = plt.figure(figsize=(10, 5))

        axis_3d = figure.add_subplot(1, 2, 1, projection="3d")
        axis_time = figure.add_subplot(1, 2, 2)

        timestamps = [f.timestamp_ms / 1000.0 for f in sequence.detected_frames]

        for joint_index in joint_indices:
            trajectory = sequence.joint_trajectory(joint_index)
            axis_3d.plot(
                trajectory[:, 0], trajectory[:, 1], trajectory[:, 2],
                label=f"joint {joint_index}",
            )
            height = trajectory[:, 1]
            axis_time.plot(timestamps, height, label=f"joint {joint_index}")

        axis_3d.set_title("3D trajectory (camera frame, metres)")
        axis_3d.set_xlabel("x")
        axis_3d.set_ylabel("y")
        axis_3d.set_zlabel("z")
        axis_3d.legend(fontsize=8)

        axis_time.set_title("Vertical position over time")
        axis_time.set_xlabel("seconds")
        axis_time.set_ylabel("y (metres)")
        axis_time.legend(fontsize=8)

        figure.tight_layout()

        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            figure.savefig(output_path, dpi=150)

        return figure
