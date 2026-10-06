"""
Tests for the image reconstruction API — the path the iOS app uses.

A stand-in estimator replaces SAM 3D Body so the full request/response contract
is exercised without the 2.6 GB checkpoint or a GPU.
"""

import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def load_api():
    """Import api/main.py under a distinct name.

    The repo has two modules called `main` — the photo CLI at the root and the
    FastAPI app under api/ — so a plain `import main` resolves to whichever is
    first on sys.path. Loading by explicit path removes the ambiguity.
    """
    if "footylevels_api" in sys.modules:
        return sys.modules["footylevels_api"]

    path = Path(__file__).parent.parent / "api" / "main.py"
    spec = importlib.util.spec_from_file_location("footylevels_api", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["footylevels_api"] = module
    spec.loader.exec_module(module)
    return module

pytest.importorskip("pxr", reason="usd-core not installed")
pytest.importorskip("fastapi", reason="fastapi not installed")

import cv2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Small enough to keep tests fast; the shape of the contract is what matters.
NUM_VERTICES, NUM_FACES, NUM_JOINTS = 200, 300, 127


class FakeEstimator:
    """Mimics SAM 3D Body's documented process_one_image contract."""

    def __init__(self, people: int = 1):
        self.people = people
        rng = np.random.default_rng(0)
        self.faces = rng.integers(0, NUM_VERTICES, size=(NUM_FACES, 3))
        self._rng = rng

    def process_one_image(self, image, bbox_thr=0.5, use_mask=False):
        height, width = image.shape[:2]
        out = []
        for i in range(self.people):
            # Scale keypoints so person 0 has the largest box and wins `primary`.
            span = 1.0 / (i + 1)
            out.append({
                "pred_vertices": self._rng.normal(scale=0.3, size=(NUM_VERTICES, 3)),
                "pred_keypoints_3d": self._rng.normal(size=(NUM_JOINTS, 3)),
                "pred_keypoints_2d": self._rng.uniform(
                    [0, 0], [width * span, height * span], size=(NUM_JOINTS, 2)
                ),
                "pred_cam_t": np.array([0.0, 0.0, 3.2]),
                "focal_length": np.array([1180.0]),
            })
        return out


def jpeg_bytes(width: int = 600, height: int = 900) -> bytes:
    """Encode a plain test image."""
    image = np.full((height, width, 3), 90, np.uint8)
    return cv2.imencode(".jpg", image)[1].tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An API client backed by a stand-in model and a temp output dir."""
    api = load_api()

    monkeypatch.setattr(api, "RECONSTRUCTION_DIR", tmp_path)
    api.get_extractor()._estimator = FakeEstimator()
    monkeypatch.setattr(api, "_reconstructor", None)

    yield TestClient(api.app)

    api._reconstructor = None
    api._extractor = None


@pytest.fixture
def reconstruction(client):
    """A completed reconstruction's response body."""
    response = client.post(
        "/api/reconstruct", files={"image": ("player.jpg", jpeg_bytes(), "image/jpeg")}
    )
    assert response.status_code == 200
    return response.json()


class TestReconstructEndpoint:
    def test_returns_200(self, reconstruction):
        assert reconstruction["id"].startswith("rec_")

    def test_reports_the_source_image_dimensions(self, reconstruction):
        assert reconstruction["image"] == {"width": 600, "height": 900}

    def test_describes_the_model_for_the_client(self, reconstruction):
        """These fields tell the app what it is about to download and render."""
        model = reconstruction["model"]

        assert model["format"] == "usdz"
        assert model["vertices"] == NUM_VERTICES
        assert model["faces"] == NUM_FACES
        assert model["units"] == "meters"
        assert model["up_axis"] == "Y"
        assert model["bytes"] > 0

    def test_describes_the_subject(self, reconstruction):
        subject = reconstruction["subject"]

        assert subject["num_joints"] == NUM_JOINTS
        assert len(subject["bbox"]) == 4
        assert subject["focal_length"] == pytest.approx(1180.0)

    def test_returns_urls_not_bytes(self, reconstruction):
        """RealityKit needs a local file, so the app fetches then writes to disk."""
        rid = reconstruction["id"]

        assert reconstruction["model_url"] == f"/api/reconstruct/{rid}/model.usdz"
        assert reconstruction["keypoints_url"] == f"/api/reconstruct/{rid}/keypoints"
        assert reconstruction["preview_url"] == f"/api/reconstruct/{rid}/preview.jpg"

    def test_preview_can_be_skipped(self, client):
        response = client.post(
            "/api/reconstruct",
            files={"image": ("p.jpg", jpeg_bytes(), "image/jpeg")},
            data={"preview": "false"},
        )

        assert response.json()["has_preview"] is False
        assert response.json()["preview_url"] is None

    def test_counts_multiple_people(self, client, tmp_path, monkeypatch):
        """Everyone is reported, but only the primary subject gets a model."""
        api = load_api()

        api.get_extractor()._estimator = FakeEstimator(people=3)
        monkeypatch.setattr(api, "_reconstructor", None)

        body = client.post(
            "/api/reconstruct", files={"image": ("p.jpg", jpeg_bytes(), "image/jpeg")}
        ).json()

        assert body["people_detected"] == 3
        assert body["model"]["vertices"] == NUM_VERTICES


class TestModelDownload:
    def test_serves_a_valid_usdz(self, client, reconstruction):
        response = client.get(reconstruction["model_url"])

        assert response.status_code == 200
        assert zipfile.is_zipfile(io.BytesIO(response.content))

    def test_uses_the_usdz_media_type(self, client, reconstruction):
        """The correct type matters for QuickLook and for URLSession handling."""
        response = client.get(reconstruction["model_url"])

        assert response.headers["content-type"] == "model/vnd.usdz+zip"

    def test_download_size_matches_the_metadata(self, client, reconstruction):
        response = client.get(reconstruction["model_url"])

        assert len(response.content) == reconstruction["model"]["bytes"]


class TestKeypointsAndPreview:
    def test_keypoints_payload(self, client, reconstruction):
        body = client.get(reconstruction["keypoints_url"]).json()

        assert body["num_joints"] == NUM_JOINTS
        assert len(body["keypoints_3d"]) == NUM_JOINTS
        assert len(body["keypoints_2d"]) == NUM_JOINTS
        assert body["units"] == "meters"

    def test_keypoints_payload_stays_small(self, client, reconstruction):
        """The skeleton is cheap to fetch, so a client can skip the mesh.

        Size here tracks joint count only, not mesh complexity. This fixture
        uses a toy mesh, so the two are comparable; at real MHR scale (18,439
        vertices) it is roughly 13 KB of keypoints against 650 KB of model.
        """
        keypoints = len(client.get(reconstruction["keypoints_url"]).content)

        assert keypoints < 100_000

    def test_preview_is_a_jpeg(self, client, reconstruction):
        response = client.get(reconstruction["preview_url"])

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"


class TestListingAndDeletion:
    def test_metadata_is_retrievable(self, client, reconstruction):
        body = client.get(f"/api/reconstruct/{reconstruction['id']}").json()

        assert body["id"] == reconstruction["id"]
        assert body["model_url"] == reconstruction["model_url"]

    def test_listing_includes_it(self, client, reconstruction):
        listed = client.get("/api/reconstructions").json()

        assert [m["id"] for m in listed] == [reconstruction["id"]]

    def test_delete_removes_everything(self, client, reconstruction):
        rid = reconstruction["id"]

        assert client.delete(f"/api/reconstruct/{rid}").status_code == 200
        assert client.get(f"/api/reconstruct/{rid}").status_code == 404
        assert client.get(f"/api/reconstruct/{rid}/model.usdz").status_code == 404
        assert client.get("/api/reconstructions").json() == []


class TestErrorHandling:
    def test_unknown_id_is_404(self, client):
        assert client.get("/api/reconstruct/nope").status_code == 404
        assert client.get("/api/reconstruct/nope/model.usdz").status_code == 404
        assert client.delete("/api/reconstruct/nope").status_code == 404

    def test_non_image_upload_is_415(self, client):
        response = client.post(
            "/api/reconstruct", files={"image": ("a.txt", b"hello", "text/plain")}
        )

        assert response.status_code == 415

    def test_undecodable_image_is_422(self, client):
        """Right content type, unreadable bytes — a client error, not a 500."""
        response = client.post(
            "/api/reconstruct", files={"image": ("a.jpg", b"not a jpeg", "image/jpeg")}
        )

        assert response.status_code == 422

    def test_no_person_detected_is_422(self, client, monkeypatch):
        api = load_api()

        api.get_extractor()._estimator = FakeEstimator(people=0)
        monkeypatch.setattr(api, "_reconstructor", None)

        response = client.post(
            "/api/reconstruct", files={"image": ("p.jpg", jpeg_bytes(), "image/jpeg")}
        )

        assert response.status_code == 422
        assert "No person detected" in response.json()["detail"]


class TestRawBuffers:
    """The raw path, for clients building a RealityKit mesh themselves."""

    @pytest.fixture
    def raw_reconstruction(self, client):
        response = client.post(
            "/api/reconstruct",
            files={"image": ("p.jpg", jpeg_bytes(), "image/jpeg")},
            data={"raw": "true"},
        )
        assert response.status_code == 200
        return response.json()

    def test_raw_is_absent_by_default(self, reconstruction):
        """USDZ stays the default; raw buffers are opt-in."""
        assert reconstruction["raw"] is None

    def test_raw_describes_the_buffer_layout(self, raw_reconstruction):
        """The client maps these bytes straight into a vertex buffer."""
        raw = raw_reconstruction["raw"]

        assert raw["vertex_count"] == NUM_VERTICES
        for name in ("positions", "normals"):
            assert raw[name]["dtype"] == "float32"
            assert raw[name]["components"] == 3
            assert raw[name]["bytes"] == NUM_VERTICES * 3 * 4

    def test_indices_url_points_at_shared_topology(self, raw_reconstruction):
        """Topology is not per-reconstruction — that is what makes raw small."""
        assert raw_reconstruction["raw"]["indices_url"] == "/api/topology"

    def test_positions_decode_to_the_right_shape(self, client, raw_reconstruction):
        response = client.get(raw_reconstruction["raw"]["positions_url"])

        assert response.headers["content-type"] == "application/octet-stream"
        positions = np.frombuffer(response.content, dtype="<f4").reshape(-1, 3)
        assert positions.shape == (NUM_VERTICES, 3)

    def test_normals_are_unit_length(self, client, raw_reconstruction):
        response = client.get(raw_reconstruction["raw"]["normals_url"])

        normals = np.frombuffer(response.content, dtype="<f4").reshape(-1, 3)
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-3)

    def test_requesting_raw_buffers_without_raw_is_404(self, client, reconstruction):
        """A USDZ-only reconstruction has no buffers, and says so."""
        response = client.get(f"/api/reconstruct/{reconstruction['id']}/positions.bin")

        assert response.status_code == 404
        assert "raw=true" in response.json()["detail"]


class TestTopologyEndpoint:
    def test_decodes_to_triangles(self, client):
        response = client.get("/api/topology")

        assert response.status_code == 200
        indices = np.frombuffer(response.content, dtype="<u4").reshape(-1, 3)
        assert indices.shape == (NUM_FACES, 3)

    def test_indices_stay_within_the_vertex_range(self, client):
        """An out-of-range index would crash the client's mesh build."""
        indices = np.frombuffer(client.get("/api/topology").content, dtype="<u4")

        assert indices.max() < NUM_VERTICES

    def test_is_aggressively_cacheable(self, client):
        """Topology never changes, so the client should fetch it once."""
        headers = client.get("/api/topology").headers

        assert "immutable" in headers["cache-control"]
        assert headers["etag"]

    def test_describes_its_own_format(self, client):
        headers = client.get("/api/topology").headers

        assert headers["x-index-dtype"] == "uint32-le"
        assert headers["x-triangle-count"] == str(NUM_FACES)

    def test_is_stable_across_calls(self, client):
        """Two fetches must agree, or a cached copy would go stale silently."""
        assert client.get("/api/topology").content == client.get("/api/topology").content
