"""
Tests for usdz module.

Verifies the USDZ we hand to RealityKit is well-formed: correct stage metadata,
constant topology, time-sampled points, and valid normals.
"""

import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels.usdz import compute_vertex_normals, write_usdz, write_usdz_animation

pytest.importorskip("pxr", reason="usd-core not installed")

from pxr import Usd, UsdGeom, UsdShade  # noqa: E402

# An octahedron — every vertex referenced, closed surface, cheap to build.
VERTICES = np.array(
    [[0, 1, 0], [1, 0, 0], [0, 0, 1], [-1, 0, 0], [0, 0, -1], [0, -1, 0]],
    dtype=np.float64,
)
# Wound counter-clockwise seen from outside, so normals face outward.
FACES = np.array(
    [[0, 2, 1], [0, 3, 2], [0, 4, 3], [0, 1, 4],
     [5, 1, 2], [5, 2, 3], [5, 3, 4], [5, 4, 1]],
    dtype=np.int64,
)


def read_points(usdz_path) -> np.ndarray:
    """Read a stage's points, holding the stage open while doing so.

    A Usd prim keeps only a weak reference to its stage, so letting the stage
    fall out of scope mid-expression invalidates the prim.
    """
    stage = Usd.Stage.Open(str(usdz_path))
    return np.array(UsdGeom.Mesh(stage.GetDefaultPrim()).GetPointsAttr().Get())


class TestVertexNormals:
    def test_normals_are_unit_length(self):
        normals = compute_vertex_normals(VERTICES, FACES)

        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0)

    def test_one_normal_per_vertex(self):
        assert compute_vertex_normals(VERTICES, FACES).shape == VERTICES.shape

    def test_orphan_vertex_gets_a_unit_normal(self):
        """A vertex no face touches accumulates zero — it must not stay zero."""
        vertices = np.vstack([VERTICES, [[9.0, 9.0, 9.0]]])  # unreferenced

        normals = compute_vertex_normals(vertices, FACES)

        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0)

    def test_normals_point_outward_on_a_convex_hull(self):
        """For a convex shape centred on the origin, normals face away from it."""
        normals = compute_vertex_normals(VERTICES, FACES)

        outward = np.einsum("ij,ij->i", normals, VERTICES)
        assert (outward > 0).all()


class TestStaticUsdz:
    @pytest.fixture
    def stage(self, tmp_path):
        path = write_usdz(VERTICES, FACES, str(tmp_path / "body.usdz"))
        return path, Usd.Stage.Open(path)

    def test_is_a_valid_zip(self, stage):
        """USDZ is a zip container — RealityKit rejects anything else."""
        path, _ = stage

        assert zipfile.is_zipfile(path)

    def test_declares_y_up_and_metres(self, stage):
        """SAM 3D Body output is metric, and Apple's viewers expect Y-up."""
        _, opened = stage

        assert UsdGeom.GetStageUpAxis(opened) == UsdGeom.Tokens.y
        assert UsdGeom.GetStageMetersPerUnit(opened) == 1.0

    def test_has_a_default_prim(self, stage):
        """Without a default prim the asset will not load as an entity."""
        _, opened = stage

        assert opened.GetDefaultPrim().IsValid()

    def test_geometry_survives(self, stage):
        _, opened = stage
        mesh = UsdGeom.Mesh(opened.GetDefaultPrim())

        assert len(mesh.GetPointsAttr().Get()) == len(VERTICES)
        assert len(mesh.GetFaceVertexCountsAttr().Get()) == len(FACES)
        assert len(mesh.GetNormalsAttr().Get()) == len(VERTICES)

    def test_extent_is_authored(self, stage):
        """A missing extent makes framing and hit-testing misbehave."""
        _, opened = stage

        assert UsdGeom.Mesh(opened.GetDefaultPrim()).GetExtentAttr().Get() is not None

    def test_material_is_bound(self, stage):
        """Unbound meshes render as flat default grey."""
        _, opened = stage
        binding = UsdShade.MaterialBindingAPI(opened.GetDefaultPrim())

        assert binding.GetDirectBinding().GetMaterial()

    def test_y_up_flip_is_applied(self, tmp_path):
        path = write_usdz(VERTICES, FACES, str(tmp_path / "f.usdz"), y_up=True)

        np.testing.assert_allclose(read_points(path)[:, 1], -VERTICES[:, 1], atol=1e-6)

    def test_y_up_can_be_disabled(self, tmp_path):
        path = write_usdz(VERTICES, FACES, str(tmp_path / "n.usdz"), y_up=False)

        np.testing.assert_allclose(read_points(path)[:, 1], VERTICES[:, 1], atol=1e-6)


class TestAnimatedUsdz:
    @pytest.fixture
    def stage(self, tmp_path):
        frames = [VERTICES + t * 0.1 for t in range(10)]
        path = write_usdz_animation(frames, FACES, str(tmp_path / "a.usdz"), fps=30.0)
        return path, Usd.Stage.Open(path)

    def test_time_range_and_rate(self, stage):
        _, opened = stage

        assert opened.GetStartTimeCode() == 0
        assert opened.GetEndTimeCode() == 9
        assert opened.GetTimeCodesPerSecond() == 30.0

    def test_points_are_time_sampled(self, stage):
        _, opened = stage
        mesh = UsdGeom.Mesh(opened.GetDefaultPrim())

        assert mesh.GetPointsAttr().GetNumTimeSamples() == 10

    def test_topology_is_written_once(self, stage):
        """MHR's vertex count never changes, so topology must not be per-frame.

        Time-sampling it would multiply file size for no benefit.
        """
        _, opened = stage
        mesh = UsdGeom.Mesh(opened.GetDefaultPrim())

        assert mesh.GetFaceVertexCountsAttr().GetNumTimeSamples() == 0
        assert mesh.GetFaceVertexIndicesAttr().GetNumTimeSamples() == 0

    def test_frames_differ(self, stage):
        _, opened = stage
        points = UsdGeom.Mesh(opened.GetDefaultPrim()).GetPointsAttr()

        first = np.array(points.Get(Usd.TimeCode(0)))
        last = np.array(points.Get(Usd.TimeCode(9)))
        assert not np.allclose(first, last)


class TestValidation:
    def test_rejects_no_frames(self, tmp_path):
        with pytest.raises(ValueError, match="No frames"):
            write_usdz_animation([], FACES, str(tmp_path / "x.usdz"))

    def test_rejects_non_triangular_faces(self, tmp_path):
        with pytest.raises(ValueError, match="shape"):
            write_usdz(VERTICES, FACES[:, :2], str(tmp_path / "x.usdz"))

    def test_rejects_out_of_range_face_index(self, tmp_path):
        with pytest.raises(ValueError, match="only 6 vertices"):
            write_usdz(VERTICES, np.array([[0, 1, 99]]), str(tmp_path / "x.usdz"))

    def test_rejects_inconsistent_frame_topology(self, tmp_path):
        """Every frame must share one topology or the animation is meaningless."""
        with pytest.raises(ValueError, match="topology must be constant"):
            write_usdz_animation(
                [VERTICES, VERTICES[:3]], FACES, str(tmp_path / "x.usdz")
            )

    def test_creates_missing_parent_directories(self, tmp_path):
        path = write_usdz(VERTICES, FACES, str(tmp_path / "a" / "b" / "m.usdz"))

        assert Path(path).exists()
