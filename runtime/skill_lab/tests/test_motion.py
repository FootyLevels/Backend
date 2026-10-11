import gzip
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import store, jobs
from backend.app import app
from backend.motion_data import pack_sequence, smooth_within_shots, sphere_from_mask


def test_no_temporal_blending_across_cut_or_missing_pose():
    values = np.array([0., 2., 0., 100., 200., 300.])
    shots = np.array([0, 0, 0, 1, 1, 1])
    valid = np.ones(6, bool)
    result = smooth_within_shots(values, valid, shots)
    assert result[1] == pytest.approx(1.2)
    assert result[2] == 0 and result[3] == 100
    valid[0] = False
    assert smooth_within_shots(values, valid, shots)[1] == 2


def test_vertex_cache_roundtrip_and_missing_frame(tmp_path):
    vertices = np.random.default_rng(21).normal(size=(5, 7, 3)).astype(np.float32)
    valid = np.array([True, True, False, True, True])
    vertices[2] = np.nan
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    manifest = pack_sequence(vertices, faces, valid, tmp_path, chunk_size=2)
    encoded = np.concatenate([np.frombuffer(gzip.decompress((tmp_path / c['file']).read_bytes()), '<u2') for c in manifest['chunks']]).reshape(5, 7, 3)
    restored = encoded / 65535 * manifest['span'] + manifest['min']
    assert np.max(np.abs(restored[valid] - vertices[valid])) < .0001
    assert [c['count'] for c in manifest['chunks']] == [2, 2, 1]
    assert np.array_equal(np.fromfile(tmp_path / 'faces.bin', '<u4').reshape(-1, 3), faces)


def test_ball_depth_and_camera_axes():
    mask = np.zeros((640, 360), np.uint8)
    cv2.circle(mask, (200, 400), 20, 255, -1)
    position, radius = sphere_from_mask(mask, 700)
    assert radius == pytest.approx(20, abs=.001)
    assert position[0] == pytest.approx(.11, abs=.001)
    assert position[1] == pytest.approx(-.44, abs=.001)
    assert position[2] == pytest.approx(-3.85, abs=.001)
    assert sphere_from_mask(np.zeros_like(mask), 700) is None
    # A foot can split the ball mask. Both visible sides still constrain its radius.
    mask[:, 193:207] = 0
    split_position, _ = sphere_from_mask(mask, 700)
    assert split_position[2] == pytest.approx(-3.85, abs=.05)


def test_motion_assets_recovery_and_mutation_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    project = {'id': 'motion-test', 'createdAt': '2026', 'frames': [], 'job': None,
               'motion': {'status': 'running'}}
    store.save(project)
    root = tmp_path / 'motion-test/motion';root.mkdir()
    (root / 'body-000000.bin.gz').write_bytes(b'cache')
    (root / 'body-ground-abc123-000000.bin.gz').write_bytes(b'ground cache')
    (root / 'faces-ground-abc123.bin').write_bytes(b'topology')
    (root / 'tracking.json').write_text('{}')
    (root / 'reconstruct_motion.log').write_text('private diagnostic')
    with TestClient(app) as client:
        assert store.read('motion-test')['motion']['status'] == 'interrupted'
        assert client.get('/assets-data/motion-test/motion/body-000000.bin.gz').status_code == 200
        assert client.get('/assets-data/motion-test/motion/body-ground-abc123-000000.bin.gz').content == b'ground cache'
        assert client.get('/assets-data/motion-test/motion/faces-ground-abc123.bin').content == b'topology'
        assert client.get('/assets-data/motion-test/motion/tracking.json').status_code == 404
        assert client.get('/assets-data/motion-test/motion/reconstruct_motion.log').status_code == 404
        project['motion']['status'] = 'running';store.save(project)
        assert client.post('/api/projects/motion-test/frames', json={'timestamp': 0}).status_code == 409


def test_ground_rotation_and_ray_preserve_image_projection():
    from backend.ground import normal_to_up, ground_ray
    normal = np.array([.03, .97, .24]);normal /= np.linalg.norm(normal)
    rotation = normal_to_up(normal)
    assert np.allclose(rotation @ normal, [0, 1, 0])
    assert np.allclose(rotation @ rotation.T, np.eye(3))
    assert np.linalg.det(rotation) == pytest.approx(1)
    point = np.array([.4, -.8, -4.])
    aligned = ground_ray(point, normal, 1.5, .085)
    assert normal @ aligned + 1.5 == pytest.approx(.085)
    assert np.allclose(aligned[:2]/aligned[2], point[:2]/point[2])
    with pytest.raises(ValueError):
        ground_ray([0, 1, 0], [0, 1, 0], 1.5, .1)


def test_ground_contact_preserves_unconstrained_flight_and_shot_boundaries():
    from backend.ground import align_scene, enforce_contacts
    count = 24
    base = np.array([[-.3, -1.5, -4], [.3, -1.5, -4], [0, .2, -4], [0, -.7, -3.9]])
    vertices = np.repeat(base[None], count, axis=0)
    translations = np.tile([0, -.6, -4], (count, 1))
    balls = np.tile([.3, -1.39, -4.], (count, 1));balls[8:12, 1] = -.9;balls[20:, 1] = -.8
    valid = np.ones(count, bool);shots = np.repeat([0, 1], 12)
    review = {'frameCount': count, 'ballGroundRanges': [[0, 7], [12, 19]], 'bodySupportRanges': [[0, 7], [12, 19]]}
    observations = {'samples': [{'shot': shot, 'index': index, 'normal': [0, 1, 0], 'inlierFraction': 1}
        for shot, index in [(0, 0), (0, 11), (1, 12), (1, 23)]]}
    body, ball, info, frames, grounded, supported = align_scene(vertices, translations, balls,
        valid, valid, shots, observations, review, .085)
    body, ball = enforce_contacts(body, ball, valid, valid, grounded, supported, .085)
    assert np.allclose(ball[grounded, 1], .085)
    assert np.allclose(body[supported, :, 1].min(axis=1), 0)
    assert ball[10, 1] == pytest.approx(.6)  # positive flight survives
    assert ball[22, 1] == pytest.approx(.7)
    assert info[0]['bodyScale'] == pytest.approx(1)
    assert frames[10]['ballConstraint'] == 'unconstrained'
    observations['samples'] = observations['samples'][:2]
    with pytest.raises(ValueError, match='Shot 1'):
        align_scene(vertices, translations, balls, valid, valid, shots, observations, review, .085)
