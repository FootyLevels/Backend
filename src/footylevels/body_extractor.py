"""
Body Extractor Module
Extract 3D body meshes and keypoints from video using Meta's SAM 3D Body (3DB).

SAM 3D Body is a single-image full-body human mesh recovery model built on the
Momentum Human Rig (MHR). Video is handled by running inference frame by frame.

Model card: https://huggingface.co/facebook/sam-3d-body-dinov3
Code:       https://github.com/facebookresearch/sam-3d-body
"""

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

# Default location the checkpoint is downloaded to:
#   hf download facebook/sam-3d-body-dinov3 \
#       --local-dir models/checkpoints/sam-3d-body-dinov3
DEFAULT_CHECKPOINT_DIR = (
    Path(__file__).parent.parent.parent / "models" / "checkpoints" / "sam-3d-body-dinov3"
)
DEFAULT_HF_REPO_ID = "facebook/sam-3d-body-dinov3"

# Keys returned per detected person, per the SAM 3D Body model card.
PRED_KEYPOINTS_3D = "pred_keypoints_3d"
PRED_KEYPOINTS_2D = "pred_keypoints_2d"
PRED_VERTICES = "pred_vertices"
PRED_CAM_T = "pred_cam_t"
FOCAL_LENGTH = "focal_length"
BODY_POSE_PARAMS = "body_pose_params"
HAND_POSE_PARAMS = "hand_pose_params"
SHAPE_PARAMS = "shape_params"


def _to_numpy(value: Any) -> Optional[np.ndarray]:
    """Convert a torch tensor / list / array to a detached numpy array."""
    if value is None:
        return None
    if hasattr(value, "detach"):  # torch.Tensor
        value = value.detach().cpu().numpy()
    return np.asarray(value)


def _to_float(value: Any) -> Optional[float]:
    """Convert a scalar tensor or number to a plain float."""
    array = _to_numpy(value)
    if array is None or array.size == 0:
        return None
    return float(array.reshape(-1)[0])


@dataclass
class BodyDetection:
    """One detected person in one frame, in MHR space.

    Coordinates follow SAM 3D Body's convention: 3D values are in the camera
    frame in metres and already include the estimated camera translation, and
    2D values are pixels on the source image.
    """

    keypoints_3d: np.ndarray            # (J, 3) metres, camera frame
    keypoints_2d: np.ndarray            # (J, 2) pixels on source image
    cam_t: Optional[np.ndarray] = None  # (3,) camera translation
    focal_length: Optional[float] = None
    shape_params: Optional[np.ndarray] = None
    body_pose_params: Optional[np.ndarray] = None
    hand_pose_params: Optional[np.ndarray] = None
    vertices: Optional[np.ndarray] = None  # (V, 3), only if include_vertices

    @property
    def num_joints(self) -> int:
        """Number of MHR keypoints returned by the model."""
        return int(self.keypoints_3d.shape[0])

    @property
    def bbox(self) -> np.ndarray:
        """Axis-aligned 2D bounds [x1, y1, x2, y2] derived from keypoints_2d.

        SAM 3D Body does not return a box in its documented output, so this is
        computed from the projected keypoint extent.
        """
        xy = self.keypoints_2d[:, :2]
        return np.array([xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()])

    @property
    def bbox_area(self) -> float:
        """Area of the derived 2D bounds. Used to pick the main subject."""
        x1, y1, x2, y2 = self.bbox
        return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))


@dataclass
class BodyFrame:
    """All people detected in a single video frame."""

    frame_index: int
    timestamp_ms: float
    detections: List[BodyDetection] = field(default_factory=list)

    @property
    def primary(self) -> Optional[BodyDetection]:
        """The largest detected person, assumed to be the subject."""
        if not self.detections:
            return None
        return max(self.detections, key=lambda d: d.bbox_area)


@dataclass
class BodySequence:
    """A sequence of per-frame body reconstructions from one video."""

    frames: List[BodyFrame]
    fps: float
    width: int
    height: int
    duration_ms: float
    model_id: str = DEFAULT_HF_REPO_ID
    faces: Optional[np.ndarray] = None  # (F, 3) shared mesh topology

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def detected_frames(self) -> List[BodyFrame]:
        """Frames where at least one person was found."""
        return [f for f in self.frames if f.primary is not None]

    @property
    def primary_detections(self) -> List[BodyDetection]:
        """The main subject from each frame that has one."""
        return [f.primary for f in self.frames if f.primary is not None]

    @property
    def num_joints(self) -> Optional[int]:
        """Joint count reported by the model, or None if nothing was detected."""
        for detection in self.primary_detections:
            return detection.num_joints
        return None

    def keypoints_3d(self) -> np.ndarray:
        """Primary subject's 3D keypoints per detected frame. Shape (T, J, 3)."""
        return np.array([d.keypoints_3d for d in self.primary_detections])

    def keypoints_2d(self) -> np.ndarray:
        """Primary subject's 2D keypoints per detected frame. Shape (T, J, 2)."""
        return np.array([d.keypoints_2d for d in self.primary_detections])

    def joint_trajectory(self, joint_index: int) -> np.ndarray:
        """Track one joint's 3D position across detected frames. Shape (T, 3)."""
        return np.array([d.keypoints_3d[joint_index] for d in self.primary_detections])

    def summary(self) -> dict:
        """Lightweight description, safe to serialize."""
        return {
            "model_id": self.model_id,
            "total_frames": len(self.frames),
            "frames_with_detection": len(self.detected_frames),
            "num_joints": self.num_joints,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration_ms": round(self.duration_ms, 1),
            "has_vertices": any(
                f.primary is not None and f.primary.vertices is not None
                for f in self.frames
            ),
        }


def resolve_device(device: Optional[str] = None) -> str:
    """Pick a torch device: CUDA when present, otherwise CPU.

    MPS is deliberately excluded from auto-detection. The MHR backend inside
    mhr_model.pt is a TorchScript module that computes skeleton state in
    float64, and Apple's MPS backend has no float64 support at all. Because
    that code is compiled into the serialized model it cannot be patched, so
    MPS fails partway through inference rather than merely running slowly.

    Passing device='mps' explicitly is still honoured, but it will raise
    "Cannot convert a MPS Tensor to float64 dtype" during the pose branch.
    """
    if device is not None:
        if device == "mps":
            warnings.warn(
                "SAM 3D Body cannot run on MPS: its TorchScript MHR backend "
                "requires float64, which MPS does not support. Use 'cpu'.",
                RuntimeWarning,
                stacklevel=2,
            )
        return device

    try:
        import torch
    except ImportError:
        return "cpu"

    return "cuda" if torch.cuda.is_available() else "cpu"


def _redirect_upstream_cuda(device: str) -> None:
    """Make upstream's hardcoded CUDA transfers honour the chosen device.

    SAM 3D Body hardcodes CUDA in two ways: `recursive_to(batch, "cuda")` with
    a literal string in three places, and five bare `tensor.cuda()` calls in the
    pose branch and hand decoders. All are unconditional, so inference raises
    "Torch not compiled with CUDA enabled" on any machine without CUDA —
    including every Mac, whether the target device is MPS or CPU.

    Rather than editing the vendored source, which would be lost on re-clone,
    both routes are redirected here. Note that patching `torch.Tensor.cuda` is
    process-global: every `.cuda()` in this interpreter lands on `device`
    afterwards. That is the intent — this process exists to run this model —
    but it is worth knowing if other torch code shares the process.

    No-op when already running on CUDA.
    """
    if device == "cuda":
        return

    import torch

    from sam_3d_body import sam_3d_body_estimator
    from sam_3d_body.models.meta_arch import sam3d_body

    # Covers every bare `.cuda()` call site at once.
    if not getattr(torch.Tensor.cuda, "_footylevels_patched", False):
        def tensor_to_device(self, *args, **kwargs):
            return self.to(device)

        tensor_to_device._footylevels_patched = True
        torch.Tensor.cuda = tensor_to_device

    for module in (sam_3d_body_estimator, sam3d_body):
        original = getattr(module, "recursive_to", None)
        if original is None or getattr(original, "_footylevels_patched", False):
            continue

        def redirected(x, target, _original=original, _device=device):
            if target == "cuda":
                target = _device
            if str(target).startswith("mps"):
                x = _downcast_float64(x)
            return _original(x, target)

        setattr(redirected, "_footylevels_patched", True)
        module.recursive_to = redirected


def _downcast_float64(value):
    """Recursively cast float64 tensors to float32.

    Apple's MPS backend has no float64 support, and upstream's batches carry
    double-precision bookkeeping tensors alongside the image data. Inference
    runs in float32 regardless, so narrowing on transfer costs nothing.
    """
    import torch

    if torch.is_tensor(value):
        return value.to(torch.float32) if value.dtype == torch.float64 else value
    if isinstance(value, dict):
        return {k: _downcast_float64(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_downcast_float64(v) for v in value)
    return value


class SAM3DBodyExtractor:
    """Run SAM 3D Body over images and videos.

    The model weights are large (~2.8 GB) and the upstream package pulls in
    heavy dependencies, so both are loaded lazily on first use. Constructing
    this class is cheap and does not require either to be present.

    Usage:
        extractor = SAM3DBodyExtractor()
        sequence = extractor.extract_from_video("data/reference/cruyff_turn.mp4")
    """

    def __init__(
        self,
        checkpoint_dir: Optional[str] = None,
        hf_repo_id: Optional[str] = None,
        device: Optional[str] = None,
        include_vertices: bool = False,
        use_mask: bool = False,
        bbox_threshold: float = 0.5,
    ):
        """
        Args:
            checkpoint_dir: Local dir holding model.ckpt and assets/mhr_model.pt.
                Defaults to models/checkpoints/sam-3d-body-dinov3.
            hf_repo_id: Load from Hugging Face instead of a local checkpoint.
            device: 'cuda', 'mps' or 'cpu'. Auto-detected when omitted.
            include_vertices: Keep the full mesh (~18.4k vertices per person per
                frame, ~220 KB each). Off by default; keypoints are usually enough.
            use_mask: Pass segmentation masks as auxiliary prompts.
            bbox_threshold: Person detection confidence threshold.
        """
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else DEFAULT_CHECKPOINT_DIR
        self.hf_repo_id = hf_repo_id
        self.device = resolve_device(device)
        self.include_vertices = include_vertices
        self.use_mask = use_mask
        self.bbox_threshold = bbox_threshold

        self._estimator = None

    # -- model loading ----------------------------------------------------

    @property
    def checkpoint_path(self) -> Path:
        return self.checkpoint_dir / "model.ckpt"

    @property
    def mhr_path(self) -> Path:
        return self.checkpoint_dir / "assets" / "mhr_model.pt"

    def check_checkpoint(self) -> List[str]:
        """Return a list of missing checkpoint files (empty when complete)."""
        expected = {
            "model.ckpt": self.checkpoint_path,
            "assets/mhr_model.pt": self.mhr_path,
        }
        return [name for name, path in expected.items() if not path.exists()]

    def _build_estimator(self):
        """Construct the upstream estimator.

        Raises ImportError only when the sam_3d_body package itself cannot be
        found. Anything that goes wrong after that — a missing transitive
        dependency, a bad checkpoint — is allowed to propagate as itself rather
        than being reported as a missing entry point.
        """
        if self.hf_repo_id is None:
            missing = self.check_checkpoint()
            if missing:
                raise FileNotFoundError(
                    f"SAM 3D Body checkpoint incomplete in {self.checkpoint_dir}.\n"
                    f"Missing: {', '.join(missing)}\n"
                    f"Download it with:\n"
                    f"  hf download {DEFAULT_HF_REPO_ID} --local-dir {self.checkpoint_dir}\n"
                    f"(requires accepting the gated licence on Hugging Face first)"
                )

        try:
            import sam_3d_body  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                f"Could not import sam_3d_body ({exc}).\n"
                f"It is not on PyPI — clone it and put the repo root on sys.path:\n"
                f"  git clone https://github.com/facebookresearch/sam-3d-body.git\n"
                f"See https://github.com/facebookresearch/sam-3d-body/blob/main/INSTALL.md"
            ) from exc

        from sam_3d_body import SAM3DBodyEstimator, load_sam_3d_body, load_sam_3d_body_hf

        if self.hf_repo_id is not None:
            model, model_cfg = load_sam_3d_body_hf(self.hf_repo_id, device=self.device)
        else:
            model, model_cfg = load_sam_3d_body(
                str(self.checkpoint_path),
                device=self.device,
                mhr_path=str(self.mhr_path),
            )

        _redirect_upstream_cuda(self.device)

        # Detector, segmentor and FOV estimator are optional. Without a
        # detector the model treats the whole frame as one person crop, which
        # suits single-subject photos; pass explicit bboxes for busier scenes.
        return SAM3DBodyEstimator(model, model_cfg)

    @property
    def estimator(self):
        """The upstream estimator, built on first access."""
        if self._estimator is None:
            self._estimator = self._build_estimator()
        return self._estimator

    @property
    def faces(self) -> Optional[np.ndarray]:
        """MHR mesh topology, if the loaded estimator exposes it."""
        return _to_numpy(getattr(self.estimator, "faces", None))

    # -- inference --------------------------------------------------------

    def _parse_person(self, person: Dict[str, Any]) -> Optional[BodyDetection]:
        """Convert one raw person dict from the model into a BodyDetection."""
        keypoints_3d = _to_numpy(person.get(PRED_KEYPOINTS_3D))
        keypoints_2d = _to_numpy(person.get(PRED_KEYPOINTS_2D))

        if keypoints_3d is None or keypoints_2d is None:
            return None

        vertices = _to_numpy(person.get(PRED_VERTICES)) if self.include_vertices else None

        return BodyDetection(
            keypoints_3d=keypoints_3d.reshape(-1, keypoints_3d.shape[-1]),
            keypoints_2d=keypoints_2d.reshape(-1, keypoints_2d.shape[-1]),
            cam_t=_to_numpy(person.get(PRED_CAM_T)),
            focal_length=_to_float(person.get(FOCAL_LENGTH)),
            shape_params=_to_numpy(person.get(SHAPE_PARAMS)),
            body_pose_params=_to_numpy(person.get(BODY_POSE_PARAMS)),
            hand_pose_params=_to_numpy(person.get(HAND_POSE_PARAMS)),
            vertices=vertices,
        )

    def _parse_outputs(self, outputs: Any) -> List[BodyDetection]:
        """Normalize process_one_image output into a list of detections.

        The model returns one dict per detected person; some builds wrap that
        list in a dict, so both shapes are accepted.
        """
        if outputs is None:
            return []
        if isinstance(outputs, dict):
            # Either a single person, or a container holding the list.
            if PRED_KEYPOINTS_3D in outputs:
                outputs = [outputs]
            else:
                outputs = next(
                    (v for v in outputs.values() if isinstance(v, (list, tuple))), []
                )

        detections = []
        for person in outputs:
            if not isinstance(person, dict):
                continue
            detection = self._parse_person(person)
            if detection is not None:
                detections.append(detection)
        return detections

    def extract_from_image(self, image: np.ndarray, frame_index: int = 0,
                           timestamp_ms: float = 0.0) -> BodyFrame:
        """Run the model on a single RGB image.

        Args:
            image: RGB image array of shape (H, W, 3).
            frame_index: Index to record on the resulting frame.
            timestamp_ms: Timestamp to record on the resulting frame.

        Returns:
            BodyFrame holding every person found.
        """
        outputs = self.estimator.process_one_image(
            image,
            bbox_thr=self.bbox_threshold,
            use_mask=self.use_mask,
        )
        return BodyFrame(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            detections=self._parse_outputs(outputs),
        )

    def extract_from_video(
        self,
        video_path: str,
        max_frames: Optional[int] = None,
        stride: int = 1,
        progress: bool = True,
    ) -> BodySequence:
        """Run the model over every (or every Nth) frame of a video.

        SAM 3D Body is a single-image model, so this is per-frame inference with
        no temporal smoothing. Expect it to be slow: the checkpoint is 840M
        parameters and each frame runs a full forward pass.

        Args:
            video_path: Path to the video file.
            max_frames: Stop after this many processed frames.
            stride: Process every Nth frame (1 = every frame).
            progress: Print a progress line as frames are processed.

        Returns:
            BodySequence covering the processed frames.
        """
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {path}")

        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_ms = (total_frames / fps) * 1000 if fps > 0 else 0.0

        frames: List[BodyFrame] = []
        frame_index = 0
        processed = 0

        try:
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break

                if stride > 1 and frame_index % stride != 0:
                    frame_index += 1
                    continue

                timestamp_ms = (frame_index / fps) * 1000 if fps > 0 else 0.0
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                frames.append(self.extract_from_image(rgb, frame_index, timestamp_ms))

                processed += 1
                if progress:
                    print(
                        f"\r  frame {frame_index + 1}/{total_frames} "
                        f"({processed} processed)",
                        end="",
                        flush=True,
                    )

                frame_index += 1
                if max_frames and processed >= max_frames:
                    break
        finally:
            cap.release()
            if progress:
                print()

        return BodySequence(
            frames=frames,
            fps=fps,
            width=width,
            height=height,
            duration_ms=duration_ms,
            model_id=self.hf_repo_id or DEFAULT_HF_REPO_ID,
            faces=self.faces,
        )

    def close(self) -> None:
        """Drop the loaded model and free its memory."""
        self._estimator = None


def extract_bodies(video_path: str, **kwargs) -> BodySequence:
    """Extract a BodySequence from a video in one call.

    Args:
        video_path: Path to the video file.
        **kwargs: Passed through to SAM3DBodyExtractor.

    Returns:
        BodySequence for the video.
    """
    extractor = SAM3DBodyExtractor(**kwargs)
    try:
        return extractor.extract_from_video(video_path)
    finally:
        extractor.close()
