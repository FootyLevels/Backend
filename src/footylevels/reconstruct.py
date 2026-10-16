"""
Reconstruct Module
Turn one image into a renderable bundle: a USDZ model plus its metadata.

This is the path the iOS app uses. SAM 3D Body is a single-image model, so one
photo is one forward pass — fast enough to answer a request inline, unlike the
per-frame video path.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .body_extractor import BodyDetection, BodyFrame, SAM3DBodyExtractor
from .usdz import compute_vertex_normals, write_usdz

# Raw buffers are little-endian to match Apple silicon, so the client can point
# a Metal buffer straight at the bytes with no byte-swapping or parsing.
POSITION_DTYPE = "<f4"   # float32, matches SIMD3<Float>
INDEX_DTYPE = "<u4"      # uint32,  matches MeshDescriptor triangle indices

MODEL_FILENAME = "model.usdz"
POSITIONS_FILENAME = "positions.bin"
NORMALS_FILENAME = "normals.bin"
PREVIEW_FILENAME = "preview.jpg"
KEYPOINTS_FILENAME = "keypoints.json"
METADATA_FILENAME = "metadata.json"


class ReconstructionError(RuntimeError):
    """Raised when an image cannot be turned into a model."""


def _jsonable(value):
    """Convert numpy values into something json.dump accepts."""
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


class ImageReconstructor:
    """
    Reconstruct a 3D body from a photo and write it out for a client to render.

    Usage:
        reconstructor = ImageReconstructor(output_dir="data/output/reconstructions")
        metadata = reconstructor.reconstruct("photo.jpg")
    """

    def __init__(
        self,
        output_dir: str = "data/output/reconstructions",
        extractor: Optional[SAM3DBodyExtractor] = None,
    ):
        """
        Args:
            output_dir: Directory that each reconstruction gets a subfolder in.
            extractor: Reuse an existing extractor so the model loads once.
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Meshes are the whole point here, so vertices are always kept.
        self.extractor = extractor or SAM3DBodyExtractor(include_vertices=True)
        self.extractor.include_vertices = True

    def _write_preview(self, image_bgr: np.ndarray, frame: BodyFrame, path: Path) -> bool:
        """Draw detected keypoints over the source image for debugging."""
        from .visualizer import BodyVisualizer

        annotated = image_bgr.copy()
        visualizer = BodyVisualizer()
        for detection in frame.detections:
            visualizer.draw_keypoints(annotated, detection)
            visualizer.draw_bbox(annotated, detection)
        return bool(cv2.imwrite(str(path), annotated))

    def _write_raw_buffers(self, detection: BodyDetection, faces: np.ndarray,
                           bundle: Path, y_up: bool) -> dict:
        """Write positions and normals as flat little-endian float32 buffers.

        These are for clients that build their own mesh — RealityKit's
        MeshDescriptor or LowLevelMesh — rather than loading a USDZ. Indices are
        deliberately not written here: MHR topology is identical for every
        reconstruction, so it is served once from /api/topology and cached.
        """
        vertices = np.asarray(detection.vertices, dtype=np.float64)
        if y_up:
            vertices = vertices.copy()
            vertices[:, 1] *= -1
            vertices[:, 2] *= -1

        normals = compute_vertex_normals(vertices, np.asarray(faces))

        positions_path = bundle / POSITIONS_FILENAME
        normals_path = bundle / NORMALS_FILENAME
        positions_path.write_bytes(
            np.ascontiguousarray(vertices, dtype=POSITION_DTYPE).tobytes()
        )
        normals_path.write_bytes(
            np.ascontiguousarray(normals, dtype=POSITION_DTYPE).tobytes()
        )

        return {
            "vertex_count": int(len(vertices)),
            "positions": {
                "filename": POSITIONS_FILENAME,
                "bytes": positions_path.stat().st_size,
                "dtype": "float32",
                "components": 3,
                "layout": "xyz interleaved, little-endian",
            },
            "normals": {
                "filename": NORMALS_FILENAME,
                "bytes": normals_path.stat().st_size,
                "dtype": "float32",
                "components": 3,
                "layout": "xyz interleaved, little-endian",
            },
        }

    def _write_keypoints(self, detection: BodyDetection, path: Path) -> None:
        """Write the subject's joints as JSON.

        Far smaller than the mesh — a few KB against ~0.7 MB — so a client that
        only needs a skeleton can skip the model download entirely.
        """
        payload = {
            "num_joints": detection.num_joints,
            "units": "meters",
            "space": "camera",
            "keypoints_3d": detection.keypoints_3d.tolist(),
            "keypoints_2d": detection.keypoints_2d.tolist(),
        }
        with open(path, "w") as f:
            json.dump(payload, f)

    def reconstruct(
        self,
        image_path: str,
        reconstruction_id: Optional[str] = None,
        y_up: bool = True,
        write_preview: bool = True,
        write_raw: bool = False,
    ) -> dict:
        """
        Reconstruct the primary subject in an image and write a USDZ bundle.

        Args:
            image_path: Photo to reconstruct.
            reconstruction_id: Override the generated id.
            y_up: Convert the camera frame to USD's Y-up convention.
            write_preview: Also save a 2D keypoint overlay.
            write_raw: Also write positions/normals as raw float32 buffers for
                clients that build their own mesh instead of loading the USDZ.

        Returns:
            The metadata dict, also written to metadata.json.

        Raises:
            ReconstructionError: If the image is unreadable, nobody is detected,
                or the model returns no mesh.
        """
        source = Path(image_path)
        image_bgr = cv2.imread(str(source))
        if image_bgr is None:
            raise ReconstructionError(f"Could not read image: {source.name}")

        height, width = image_bgr.shape[:2]

        frame = self.extractor.extract_from_image(
            cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        )

        detection = frame.primary
        if detection is None:
            raise ReconstructionError("No person detected in the image")
        if detection.vertices is None:
            raise ReconstructionError("Model returned no mesh vertices")

        faces = self.extractor.faces
        if faces is None:
            raise ReconstructionError("Model exposed no mesh topology")

        reconstruction_id = reconstruction_id or f"rec_{uuid.uuid4().hex[:12]}"
        bundle = self.output_dir / reconstruction_id
        bundle.mkdir(parents=True, exist_ok=True)

        model_path = bundle / MODEL_FILENAME
        write_usdz(detection.vertices, faces, str(model_path), y_up=y_up)

        self._write_keypoints(detection, bundle / KEYPOINTS_FILENAME)

        raw = None
        if write_raw:
            raw = self._write_raw_buffers(detection, faces, bundle, y_up)

        has_preview = False
        if write_preview:
            has_preview = self._write_preview(
                image_bgr, frame, bundle / PREVIEW_FILENAME
            )

        metadata = {
            "id": reconstruction_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_image": source.name,
            "image": {"width": width, "height": height},
            "people_detected": len(frame.detections),
            "model": {
                "format": "usdz",
                "filename": MODEL_FILENAME,
                "bytes": model_path.stat().st_size,
                "vertices": int(len(detection.vertices)),
                "faces": int(len(faces)),
                "up_axis": "Y" if y_up else "camera",
                "units": "meters",
            },
            "subject": {
                "num_joints": detection.num_joints,
                "bbox": [float(v) for v in detection.bbox],
                "focal_length": _jsonable(detection.focal_length),
                "cam_t": _jsonable(detection.cam_t),
            },
            "raw": raw,
            "has_preview": has_preview,
        }

        with open(bundle / METADATA_FILENAME, "w") as f:
            json.dump(metadata, f, indent=2)

        return metadata

    # -- retrieval --------------------------------------------------------

    def bundle_dir(self, reconstruction_id: str) -> Path:
        """Directory holding one reconstruction's files."""
        return self.output_dir / reconstruction_id

    def load_metadata(self, reconstruction_id: str) -> dict:
        """
        Read a reconstruction's metadata.

        Raises:
            FileNotFoundError: If the reconstruction does not exist.
        """
        path = self.bundle_dir(reconstruction_id) / METADATA_FILENAME
        if not path.exists():
            raise FileNotFoundError(f"Reconstruction not found: {reconstruction_id}")
        with open(path, "r") as f:
            return json.load(f)

    def list_reconstructions(self) -> list:
        """Every stored reconstruction's metadata, newest first."""
        found = []
        for path in self.output_dir.glob(f"*/{METADATA_FILENAME}"):
            with open(path, "r") as f:
                found.append(json.load(f))
        return sorted(found, key=lambda m: m.get("created_at", ""), reverse=True)

    def delete(self, reconstruction_id: str) -> bool:
        """Delete a reconstruction bundle. Returns whether it existed."""
        import shutil

        bundle = self.bundle_dir(reconstruction_id)
        if not bundle.exists():
            return False
        shutil.rmtree(bundle, ignore_errors=True)
        return True
