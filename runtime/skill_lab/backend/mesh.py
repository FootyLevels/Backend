"""Common mesh export: camera-facing, Y-up, ground-centered, without rescaling."""
import json
from pathlib import Path

import numpy as np
import trimesh


def export_mesh(vertices, faces, destination: Path, *, camera_space=True, metadata=None) -> dict:
    vertices = np.asarray(vertices, dtype=np.float32).copy()
    raw_faces = np.asarray(faces)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError("Invalid mesh vertices")
    if raw_faces.ndim != 2 or raw_faces.shape[1] != 3 or len(raw_faces) == 0:
        raise ValueError("A triangular surface mesh is required")
    if not np.isfinite(raw_faces).all() or not np.equal(raw_faces, np.floor(raw_faces)).all():
        raise ValueError("Face indices must be integers")
    faces = raw_faces.astype(np.int64)
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError("Invalid mesh face indices")
    # Same rotation as Meta's renderer.vertices_to_trimesh: 180 degrees around X.
    if camera_space:
        vertices *= np.array([1, -1, -1], dtype=np.float32)
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    offset = np.array([(low[0] + high[0]) / 2, low[1], (low[2] + high[2]) / 2])
    vertices -= offset
    if high[1] - low[1] < 1e-5:
        raise ValueError("Degenerate mesh height")
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.visual.vertex_colors = [173, 202, 174, 255]
    destination.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(destination, file_type="glb", include_normals=True)
    info = {"vertexCount": len(vertices), "faceCount": len(faces), "coordinates": "Y-up",
            "cameraToViewer": [1, -1, -1] if camera_space else [1, 1, 1],
            "centeringOffset": offset.tolist(), "scaleApplied": 1,
            "height": float(high[1] - low[1]), **(metadata or {})}
    destination.with_suffix(".json").write_text(json.dumps(info, indent=2, allow_nan=False))
    return info


def normalize_file(source: Path, destination: Path, *, camera_space=False, metadata=None) -> dict:
    mesh = trimesh.load(source, force="mesh", process=False)
    return export_mesh(mesh.vertices, mesh.faces, destination,
                       camera_space=camera_space, metadata=metadata)
