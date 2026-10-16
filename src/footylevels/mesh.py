"""
Mesh Module
Write MHR meshes out as standard 3D model files.

OBJ and PLY are both plain text, so nothing here needs a 3D library. OBJ opens
in macOS Preview, Blender, and most viewers; PLY is handy when you later want
per-vertex colour.
"""

from pathlib import Path
from typing import Optional

import numpy as np


def _validate(vertices: np.ndarray, faces: Optional[np.ndarray]) -> None:
    """Reject geometry that would silently write a broken file."""
    if vertices is None or vertices.size == 0:
        raise ValueError("No vertices to write")
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError(f"Expected vertices of shape (V, 3), got {vertices.shape}")
    if faces is not None:
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError(f"Expected faces of shape (F, 3), got {faces.shape}")
        if faces.max() >= len(vertices):
            raise ValueError(
                f"Face references vertex {faces.max()} but only "
                f"{len(vertices)} vertices were given"
            )


def orient_y_up(vertices: np.ndarray) -> np.ndarray:
    """Flip the Y and Z sign to convert between Y-down and Y-up conventions.

    SAM 3D Body returns camera-frame coordinates following the image convention,
    where Y increases downwards. Most 3D viewers expect Y up, so a mesh written
    straight out can appear upside down. Apply this if that happens.
    """
    flipped = np.asarray(vertices, dtype=np.float64).copy()
    flipped[:, 1] *= -1
    flipped[:, 2] *= -1
    return flipped


def write_obj(
    vertices: np.ndarray,
    faces: Optional[np.ndarray],
    output_path: str,
    comment: str = "",
) -> str:
    """
    Write a mesh to Wavefront OBJ.

    Args:
        vertices: (V, 3) vertex positions.
        faces: (F, 3) zero-indexed triangles. Written as a point cloud if None.
        output_path: Where to write the .obj file.
        comment: Optional header comment.

    Returns:
        The output path.

    Raises:
        ValueError: If the geometry is empty or malformed.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces) if faces is not None else None
    _validate(vertices, faces)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        f.write("# FootyLevels - SAM 3D Body (MHR) mesh\n")
        if comment:
            f.write(f"# {comment}\n")
        f.write(f"# {len(vertices)} vertices")
        f.write(f", {len(faces)} faces\n" if faces is not None else "\n")

        for x, y, z in vertices:
            f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

        if faces is not None:
            # OBJ face indices are 1-based.
            for a, b, c in faces + 1:
                f.write(f"f {a} {b} {c}\n")

    return str(path)


def write_ply(
    vertices: np.ndarray,
    faces: Optional[np.ndarray],
    output_path: str,
) -> str:
    """
    Write a mesh to ASCII PLY.

    Args:
        vertices: (V, 3) vertex positions.
        faces: (F, 3) zero-indexed triangles. Written as a point cloud if None.
        output_path: Where to write the .ply file.

    Returns:
        The output path.

    Raises:
        ValueError: If the geometry is empty or malformed.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces) if faces is not None else None
    _validate(vertices, faces)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write("comment FootyLevels - SAM 3D Body (MHR) mesh\n")
        f.write(f"element vertex {len(vertices)}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        if faces is not None:
            f.write(f"element face {len(faces)}\n")
            f.write("property list uchar int vertex_index\n")
        f.write("end_header\n")

        for x, y, z in vertices:
            f.write(f"{x:.6f} {y:.6f} {z:.6f}\n")

        if faces is not None:
            for a, b, c in faces:
                f.write(f"3 {a} {b} {c}\n")

    return str(path)


def write_mesh(
    vertices: np.ndarray,
    faces: Optional[np.ndarray],
    output_path: str,
    y_up: bool = False,
    comment: str = "",
) -> str:
    """
    Write a mesh, picking the format from the file extension.

    Args:
        vertices: (V, 3) vertex positions.
        faces: (F, 3) zero-indexed triangles.
        output_path: Destination ending in .obj or .ply.
        y_up: Flip to a Y-up convention before writing.
        comment: Optional header comment (OBJ only).

    Returns:
        The output path.

    Raises:
        ValueError: On an unsupported extension or malformed geometry.
    """
    if y_up:
        vertices = orient_y_up(vertices)

    suffix = Path(output_path).suffix.lower()
    if suffix == ".obj":
        return write_obj(vertices, faces, output_path, comment=comment)
    if suffix == ".ply":
        return write_ply(vertices, faces, output_path)

    raise ValueError(f"Unsupported mesh format '{suffix}'. Use .obj or .ply")
