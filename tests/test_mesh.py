"""
Tests for mesh module.

Covers writing MHR geometry out as OBJ and PLY, and reading it back.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from footylevels.mesh import orient_y_up, write_mesh, write_obj, write_ply

# A unit tetrahedron: small, valid, and easy to assert on.
VERTICES = np.array(
    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
)
FACES = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])


def read_obj(path):
    """Parse an OBJ back into vertices and zero-indexed faces."""
    vertices, faces = [], []
    for line in Path(path).read_text().splitlines():
        if line.startswith("v "):
            vertices.append([float(v) for v in line.split()[1:4]])
        elif line.startswith("f "):
            faces.append([int(i) - 1 for i in line.split()[1:4]])
    return np.array(vertices), np.array(faces)


class TestObj:
    def test_round_trips_geometry(self, tmp_path):
        """What goes out must come back identical."""
        path = write_obj(VERTICES, FACES, str(tmp_path / "m.obj"))

        vertices, faces = read_obj(path)

        np.testing.assert_allclose(vertices, VERTICES)
        np.testing.assert_array_equal(faces, FACES)

    def test_face_indices_are_one_based(self, tmp_path):
        """OBJ is 1-indexed — an off-by-one here corrupts every mesh."""
        path = write_obj(VERTICES, FACES, str(tmp_path / "m.obj"))

        face_lines = [
            l for l in Path(path).read_text().splitlines() if l.startswith("f ")
        ]

        assert face_lines[0] == "f 1 2 3"
        indices = [int(i) for l in face_lines for i in l.split()[1:]]
        assert min(indices) >= 1

    def test_writes_point_cloud_without_faces(self, tmp_path):
        """No topology is still a usable file, just without faces."""
        path = write_obj(VERTICES, None, str(tmp_path / "m.obj"))

        text = Path(path).read_text()

        assert text.count("\nv ") + text.startswith("v ") == 4
        assert "\nf " not in text

    def test_comment_is_recorded(self, tmp_path):
        path = write_obj(VERTICES, FACES, str(tmp_path / "m.obj"), comment="source: x.jpg")

        assert "# source: x.jpg" in Path(path).read_text()

    def test_creates_missing_parent_directories(self, tmp_path):
        path = write_obj(VERTICES, FACES, str(tmp_path / "deep" / "nested" / "m.obj"))

        assert Path(path).exists()


class TestPly:
    def test_header_counts_match_the_body(self, tmp_path):
        path = write_ply(VERTICES, FACES, str(tmp_path / "m.ply"))

        text = Path(path).read_text()

        assert "element vertex 4" in text
        assert "element face 4" in text
        assert text.startswith("ply\n")

    def test_face_indices_are_zero_based(self, tmp_path):
        """PLY, unlike OBJ, is 0-indexed."""
        path = write_ply(VERTICES, FACES, str(tmp_path / "m.ply"))

        body = Path(path).read_text().split("end_header\n")[1].splitlines()

        assert body[4] == "3 0 1 2"

    def test_omits_face_element_for_point_cloud(self, tmp_path):
        path = write_ply(VERTICES, None, str(tmp_path / "m.ply"))

        assert "element face" not in Path(path).read_text()


class TestOrientation:
    def test_y_up_flips_y_and_z(self):
        flipped = orient_y_up(VERTICES)

        np.testing.assert_allclose(flipped[:, 0], VERTICES[:, 0])
        np.testing.assert_allclose(flipped[:, 1], -VERTICES[:, 1])
        np.testing.assert_allclose(flipped[:, 2], -VERTICES[:, 2])

    def test_does_not_mutate_the_input(self):
        original = VERTICES.copy()

        orient_y_up(VERTICES)

        np.testing.assert_array_equal(VERTICES, original)

    def test_applied_through_write_mesh(self, tmp_path):
        path = write_mesh(VERTICES, FACES, str(tmp_path / "m.obj"), y_up=True)

        vertices, _ = read_obj(path)

        np.testing.assert_allclose(vertices, orient_y_up(VERTICES))


class TestWriteMesh:
    def test_picks_format_from_extension(self, tmp_path):
        obj = write_mesh(VERTICES, FACES, str(tmp_path / "m.obj"))
        ply = write_mesh(VERTICES, FACES, str(tmp_path / "m.ply"))

        assert Path(obj).read_text().startswith("# FootyLevels")
        assert Path(ply).read_text().startswith("ply")

    def test_rejects_unknown_extension(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported mesh format"):
            write_mesh(VERTICES, FACES, str(tmp_path / "m.glb"))


class TestValidation:
    def test_rejects_empty_vertices(self, tmp_path):
        with pytest.raises(ValueError, match="No vertices"):
            write_obj(np.zeros((0, 3)), None, str(tmp_path / "m.obj"))

    def test_rejects_wrong_vertex_shape(self, tmp_path):
        with pytest.raises(ValueError, match="shape"):
            write_obj(np.zeros((4, 2)), None, str(tmp_path / "m.obj"))

    def test_rejects_out_of_range_face_index(self, tmp_path):
        """A face pointing past the vertex list would write a corrupt mesh."""
        bad_faces = np.array([[0, 1, 99]])

        with pytest.raises(ValueError, match="only 4 vertices"):
            write_obj(VERTICES, bad_faces, str(tmp_path / "m.obj"))

    def test_rejects_non_triangular_faces(self, tmp_path):
        with pytest.raises(ValueError, match="shape"):
            write_obj(VERTICES, np.array([[0, 1, 2, 3]]), str(tmp_path / "m.obj"))


class TestRealisticScale:
    def test_handles_a_full_mhr_mesh(self, tmp_path):
        """SAM 3D Body returns 18,439 vertices — make sure that scale works."""
        rng = np.random.default_rng(0)
        vertices = rng.normal(size=(18439, 3))
        faces = rng.integers(0, 18439, size=(36000, 3))

        path = write_obj(vertices, faces, str(tmp_path / "body.obj"))

        read_vertices, read_faces = read_obj(path)
        assert read_vertices.shape == (18439, 3)
        assert read_faces.shape == (36000, 3)
