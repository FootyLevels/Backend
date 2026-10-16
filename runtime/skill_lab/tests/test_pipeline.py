import copy
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from backend import jobs, store, video
from backend.app import app
from backend.mesh import export_mesh


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "projects")
    source = tmp_path / "input.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 240))
    for i in range(20):
        frame = np.full((240, 160, 3), 30, dtype=np.uint8)
        cv2.rectangle(frame, (10 + i * 3, 50), (30 + i * 3, 190), (80, 200, 240), -1)
        writer.write(frame)
    writer.release()
    return video.prepare(source, "Pipeline test", count=3, project_id="test-clip")


def test_prepare_and_exact_frame_extraction(project):
    assert len(project["frames"]) == 3
    times = [f["timestamp"] for f in project["frames"]]
    assert times == sorted(set(times))
    assert all(0 <= t < 2 for t in times)
    frame = video.add_frame(project, .91)
    assert frame["timestamp"] == pytest.approx(.9)
    assert frame["frameIndex"] == 9
    assert video.add_frame(project, .91) is frame
    with pytest.raises(ValueError):
        video.add_frame(project, 9)


def test_mesh_coordinates_and_round_trip(tmp_path):
    vertices = np.array([[-.5, 0, 0], [.5, 0, 0], [0, -2, .2], [0, -1, -.2]])
    faces = np.array([[0, 1, 2], [1, 0, 3], [1, 3, 2], [0, 2, 3]])
    out = tmp_path / "pose.glb"
    info = export_mesh(vertices, faces, out)
    recovered = trimesh.load(out, force="mesh", process=False)
    assert info["height"] == pytest.approx(2)
    assert recovered.bounds[0, 1] == pytest.approx(0)
    assert recovered.vertices[2, 1] > recovered.vertices[0, 1]  # camera Y-down → viewer Y-up
    assert np.linalg.norm(recovered.vertices[2] - recovered.vertices[0]) == pytest.approx(np.linalg.norm(vertices[2] - vertices[0]))
    assert len(recovered.faces) == 4
    assert json.loads(out.with_suffix('.json').read_text())["scaleApplied"] == 1
    with pytest.raises(ValueError):
        export_mesh(vertices, [[0, 1, 99]], out)
    with pytest.raises(ValueError):
        export_mesh([[float('nan'), 0, 0]], [[0, 0, 0]], out)


def test_api_edit_invalidation_and_limits(project):
    with TestClient(app) as client:
        pid = project["id"]
        frame = project["frames"][0]
        url = f"/api/projects/{pid}/frames/{frame['id']}"
        assert client.patch(url, json={"bbox": [0, .1, 1, .95]}).status_code == 200
        assert client.patch(url, json={"bbox": [1, 0, 0, 1]}).status_code == 422
        assert client.patch(url, json={"bbox": [0, 0, 1]}).status_code == 422
        assert client.post(f"/api/projects/{pid}/frames", json={"timestamp": 999}).status_code == 422
        assert client.get('/api/projects/not-found').status_code == 404
        assert client.post(f"/api/projects/{pid}/generate", json={"provider": "invalid"}).status_code == 422
        assert client.post(f"/api/projects/{pid}/frames", json={"timestamp": 1}, headers={"Origin": "https://example.com"}).status_code == 403
        assert client.get(project["video"]["url"], headers={"Range": "bytes=0-99"}).status_code == 206
        assert client.get(frame["imageUrl"]).status_code == 200
        p = store.read(pid)
        p["frames"][0].update(status="ready", meshUrl="old.glb", meshInfo={"stale": True})
        store.save(p)
        changed = client.patch(url, json={"bbox": [.1, .1, .9, .9]}).json()["frames"][0]
        assert changed["status"] == "pending" and changed["meshUrl"] is None
        assert changed["meshInfo"] is None


def test_job_partial_failure_and_retry(project, monkeypatch):
    monkeypatch.setattr(jobs, 'capabilities', lambda: [{"id": "local", "ready": True}])
    seen = []

    class FakeProvider:
        def reconstruct(self, image, bbox, destination):
            seen.append(image.stem)
            if image.stem == project["frames"][1]["id"]:
                raise RuntimeError("Subject is obscured")
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(b"test-only")
            return {"testFixture": True}

    monkeypatch.setattr(jobs, "get_provider", lambda name: FakeProvider())
    jobs.start(project["id"], "local", background=False)
    result = store.read(project["id"])
    assert result["job"]["status"] == "partial"
    assert result["job"]["completed"] == 2
    assert result["job"]["failed"] == 1
    assert seen == [f["id"] for f in project["frames"]]
    assert result["frames"][1]["meshUrl"] is None
    seen.clear()
    jobs.start(project["id"], "local", background=False)
    assert seen == [project["frames"][1]["id"]]


def test_restart_recovers_without_resubmitting(project):
    project["job"] = {"status": "running"}
    project["frames"][0]["status"] = "running"
    store.save(project)
    jobs.recover_interrupted()
    result = store.read(project["id"])
    assert result["job"]["status"] == "interrupted"
    assert result["frames"][0]["status"] == "failed"


def test_no_write_during_generation(project):
    with TestClient(app) as client:
        project["job"] = {"status": "running"}
        store.save(project)
        url = f"/api/projects/{project['id']}/frames/{project['frames'][0]['id']}"
        assert client.patch(url, json={"bbox": [0, 0, 1, 1]}).status_code == 409
        assert client.delete(url).status_code == 409
