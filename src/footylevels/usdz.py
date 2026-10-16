"""
USDZ Module
Package MHR meshes as USDZ, the format Apple's RealityKit loads natively.

SceneKit went into maintenance mode at WWDC25, so RealityKit is the path for new
iOS work and its native asset format is USD. Writing USDZ here means the app can
do `ModelEntity(contentsOf:)` with no client-side conversion.

Requires `usd-core`, imported lazily so the rest of the package works without it.
"""

import tempfile
from pathlib import Path
from typing import Sequence

import numpy as np

# Root prim path inside the generated stage.
BODY_PRIM = "/Body"

# A neutral mid-tone so the model does not render as flat default grey.
DEFAULT_COLOR = (0.82, 0.71, 0.62)


def _require_usd():
    """Import pxr, with an actionable error if usd-core is absent."""
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils

        return Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils
    except ImportError as exc:
        raise ImportError(
            "USDZ export needs the 'usd-core' package.\n"
            "  pip install usd-core"
        ) from exc


def compute_vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """
    Compute area-weighted smooth vertex normals.

    Without normals a renderer falls back to flat shading, which makes the body
    look faceted. Area weighting means large triangles influence a shared vertex
    more than slivers do.

    Args:
        vertices: (V, 3) positions.
        faces: (F, 3) zero-indexed triangles.

    Returns:
        (V, 3) unit normals.
    """
    normals = np.zeros_like(vertices, dtype=np.float64)

    v0 = vertices[faces[:, 0]]
    v1 = vertices[faces[:, 1]]
    v2 = vertices[faces[:, 2]]
    # Cross product magnitude is twice the triangle area, giving the weighting.
    face_normals = np.cross(v1 - v0, v2 - v0)

    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)

    lengths = np.linalg.norm(normals, axis=1, keepdims=True)

    # A vertex no face references, or one ringed by degenerate triangles,
    # accumulates a zero vector. Normalizing that would emit an invalid normal,
    # so substitute an arbitrary unit vector instead.
    degenerate = lengths[:, 0] < 1e-12
    normals[degenerate] = (0.0, 1.0, 0.0)
    lengths[degenerate] = 1.0

    return normals / lengths


def _orient_for_usd(vertices: np.ndarray) -> np.ndarray:
    """Convert SAM 3D Body's camera frame to the Y-up frame USD declares.

    SAM 3D Body returns camera coordinates on the image convention, where Y
    increases downwards. USD stages here declare upAxis=Y, so Y and Z are
    negated to match. Pass y_up=False to skip this if a future model build
    already returns Y-up.
    """
    flipped = np.asarray(vertices, dtype=np.float64).copy()
    flipped[:, 1] *= -1
    flipped[:, 2] *= -1
    return flipped


def _define_material(stage, mesh, color) -> None:
    """Bind a simple UsdPreviewSurface so the mesh shades sensibly on device."""
    Gf, Sdf, _, _, UsdShade, _ = _require_usd()

    material = UsdShade.Material.Define(stage, f"{BODY_PRIM}/Material")
    shader = UsdShade.Shader.Define(stage, f"{BODY_PRIM}/Material/Surface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.75)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)

    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI(mesh).Bind(material)


def _build_stage(
    stage_path: Path,
    frames: Sequence[np.ndarray],
    faces: np.ndarray,
    fps: float,
    color,
    with_normals: bool,
):
    """Author a USD stage holding one mesh, optionally animated.

    Topology is written once; only the points attribute is time-sampled, which
    is exactly how MHR behaves — the rig's vertex count never changes.
    """
    _, _, Usd, UsdGeom, _, _ = _require_usd()

    stage = Usd.Stage.CreateNew(str(stage_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)  # SAM 3D Body output is metric

    mesh = UsdGeom.Mesh.Define(stage, BODY_PRIM)
    mesh.CreateFaceVertexCountsAttr(
        Vt_int_array(np.full(len(faces), 3, dtype=np.int32))
    )
    mesh.CreateFaceVertexIndicesAttr(
        Vt_int_array(np.ascontiguousarray(faces, dtype=np.int32).ravel())
    )
    # Treat the mesh as a polygon soup rather than a subdivision cage, so the
    # device renders the vertices we actually shipped.
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)

    points_attr = mesh.CreatePointsAttr()
    animated = len(frames) > 1

    if animated:
        stage.SetStartTimeCode(0)
        stage.SetEndTimeCode(len(frames) - 1)
        stage.SetTimeCodesPerSecond(fps)

    if with_normals:
        normals_attr = mesh.CreateNormalsAttr()
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    else:
        normals_attr = None

    lows, highs = [], []
    for index, vertices in enumerate(frames):
        vertices = np.ascontiguousarray(vertices, dtype=np.float32)
        time = Usd.TimeCode(index) if animated else Usd.TimeCode.Default()

        points_attr.Set(Vt_vec3f_array(vertices), time)
        if normals_attr is not None:
            normals = compute_vertex_normals(vertices.astype(np.float64), faces)
            normals_attr.Set(Vt_vec3f_array(normals.astype(np.float32)), time)

        lows.append(vertices.min(axis=0))
        highs.append(vertices.max(axis=0))

    low = np.min(np.stack(lows), axis=0)
    high = np.max(np.stack(highs), axis=0)
    mesh.CreateExtentAttr().Set(
        Vt_vec3f_array(np.stack([low, high]).astype(np.float32))
    )

    _define_material(stage, mesh, color)

    stage.SetDefaultPrim(mesh.GetPrim())
    stage.GetRootLayer().Save()


def Vt_int_array(array: np.ndarray):
    """Wrap a numpy int array as a USD Vt.IntArray."""
    from pxr import Vt

    return Vt.IntArray.FromNumpy(np.ascontiguousarray(array, dtype=np.int32))


def Vt_vec3f_array(array: np.ndarray):
    """Wrap an (N, 3) numpy array as a USD Vt.Vec3fArray."""
    from pxr import Vt

    return Vt.Vec3fArray.FromNumpy(np.ascontiguousarray(array, dtype=np.float32))


def write_usdz(
    vertices: np.ndarray,
    faces: np.ndarray,
    output_path: str,
    y_up: bool = True,
    color=DEFAULT_COLOR,
    with_normals: bool = True,
) -> str:
    """
    Write a single static mesh as USDZ.

    Args:
        vertices: (V, 3) positions in metres.
        faces: (F, 3) zero-indexed triangles.
        output_path: Destination .usdz path.
        y_up: Convert from the camera's Y-down frame to USD's Y-up.
        color: Diffuse colour for the preview surface.
        with_normals: Author smooth vertex normals for better shading.

    Returns:
        The output path.

    Raises:
        ImportError: If usd-core is not installed.
        ValueError: If the geometry is empty or malformed.
    """
    return write_usdz_animation(
        [vertices], faces, output_path,
        fps=1.0, y_up=y_up, color=color, with_normals=with_normals,
    )


def write_usdz_animation(
    frames: Sequence[np.ndarray],
    faces: np.ndarray,
    output_path: str,
    fps: float = 30.0,
    y_up: bool = True,
    color=DEFAULT_COLOR,
    with_normals: bool = True,
) -> str:
    """
    Write a sequence of meshes as one animated USDZ.

    Every frame must share the topology in `faces`, which MHR guarantees. Only
    the point positions are time-sampled, so topology cost is paid once.

    Args:
        frames: Sequence of (V, 3) vertex arrays, one per frame.
        faces: (F, 3) zero-indexed triangles, shared by every frame.
        output_path: Destination .usdz path.
        fps: Playback rate written into the stage.
        y_up: Convert from the camera's Y-down frame to USD's Y-up.
        color: Diffuse colour for the preview surface.
        with_normals: Author smooth vertex normals for better shading.

    Returns:
        The output path.

    Raises:
        ImportError: If usd-core is not installed.
        ValueError: If the geometry is empty, malformed, or inconsistent.
    """
    _require_usd()
    from pxr import UsdUtils

    if not frames:
        raise ValueError("No frames to write")

    faces = np.asarray(faces)
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError(f"Expected faces of shape (F, 3), got {faces.shape}")

    prepared = []
    for index, vertices in enumerate(frames):
        vertices = np.asarray(vertices, dtype=np.float64)
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError(
                f"Frame {index}: expected vertices of shape (V, 3), got {vertices.shape}"
            )
        if vertices.shape != np.asarray(frames[0]).shape:
            raise ValueError(
                f"Frame {index} has {len(vertices)} vertices but frame 0 has "
                f"{len(frames[0])} — topology must be constant"
            )
        prepared.append(_orient_for_usd(vertices) if y_up else vertices)

    if faces.max() >= len(prepared[0]):
        raise ValueError(
            f"Face references vertex {faces.max()} but only "
            f"{len(prepared[0])} vertices were given"
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    # USDZ is a zip of a USD layer, so author the layer in a scratch dir first.
    with tempfile.TemporaryDirectory() as scratch:
        stage_path = Path(scratch) / "body.usdc"
        _build_stage(stage_path, prepared, faces, fps, color, with_normals)
        UsdUtils.CreateNewUsdzPackage(str(stage_path), str(output))

    return str(output)
