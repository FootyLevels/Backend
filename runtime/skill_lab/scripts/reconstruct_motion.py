"""Reconstruct every tracked frame, with resumable raw caches and a web vertex cache."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.motion_data import pack_sequence, smooth_within_shots, sphere_from_mask
from backend.providers.local import LocalProvider
from backend.ground import align_scene, enforce_contacts


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False))
    temp.replace(path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('project_dir', type=Path)
    p.add_argument('--pack-only', action='store_true', help='Repack saved raw results without loading CUDA')
    p.add_argument('--align-ground', action='store_true', help='Use estimated turf planes and reviewed contact ranges')
    a = p.parse_args()
    os.environ.setdefault('SAM3D_REPO', str(ROOT / 'vendor/sam-3d-body'))
    os.environ.setdefault('SAM3D_CHECKPOINT', str(ROOT / 'checkpoints/sam-3d-body-dinov3/model.ckpt'))
    root = a.project_dir / 'motion'
    tracking = json.loads((root / 'tracking.json').read_text())
    records = tracking['frames']
    cache = root / 'raw'
    cache.mkdir(exist_ok=True)
    estimator = None if a.pack_only else LocalProvider().load()
    failures = []
    for record in records:
        i = record['index']
        path = cache / f'{i:06d}.npz'
        if path.exists() or not record.get('person') or a.pack_only:
            continue
        image = cv2.imread(str(root / 'source' / f'{i:06d}.jpg'))
        h, w = image.shape[:2]
        bbox = np.asarray(record['person']['bbox'], np.float32) * [w, h, w, h]
        try:
            result = estimator.process_one_image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB),
                bboxes=bbox.reshape(1, 4), inference_type='full')[0]
            vertices = np.asarray(result['pred_vertices'], np.float32)
            translation = np.asarray(result['pred_cam_t'], np.float32).reshape(3)
            if not np.isfinite(vertices).all() or not np.isfinite(translation).all():
                raise ValueError('Non-finite reconstruction')
            np.savez_compressed(path, vertices=vertices, translation=translation,
                focal=np.asarray(result['focal_length']).reshape(-1)[0],
                joints=np.asarray(result['pred_keypoints_3d']),
                keypoints2d=np.asarray(result['pred_keypoints_2d']))
        except Exception as exc:
            failures.append({'index': i, 'error': type(exc).__name__})
            print(f'Frame {i} failed: {exc}', flush=True)
        if i % 10 == 0:
            atomic_json(root / 'progress.json', {'stage': 'body', 'completed': i + 1, 'total': len(records)})
            print(f'Body frame {i + 1}/{len(records)}', flush=True)
    faces = np.fromfile(root / 'faces.bin', '<u4').reshape(-1, 3) if a.pack_only else np.asarray(estimator.faces)
    count = len(records)
    valid = np.zeros(count, bool)
    ball_valid = np.zeros(count, bool)
    vertices = np.zeros((count, int(faces.max()) + 1, 3), np.float32)
    balls = np.zeros((count, 3), np.float32)
    translations = np.zeros((count, 3), np.float32)
    shots = np.asarray([r['shot'] for r in records])
    for r in records:
        i = r['index']
        path = cache / f'{i:06d}.npz'
        if not path.exists():
            continue
        raw = np.load(path)
        vertices[i] = (raw['vertices'] + raw['translation']) * [1, -1, -1]
        translations[i] = raw['translation'] * [1, -1, -1]
        valid[i] = True
        if r.get('ball'):
            mask = cv2.imread(str(root / 'masks/ball' / f'{i:06d}.png'), 0)
            estimate = sphere_from_mask(mask, float(raw['focal']))
            if estimate:
                balls[i] = estimate[0]
                ball_valid[i] = True
    ground = None
    revision = ''
    if a.align_ground:
        import trimesh
        observations = json.loads((root / 'ground-observations.json').read_text())
        review = json.loads((root / 'contact-review.json').read_text())
        if abs(review['fps'] - tracking['fps']) > 1e-6:
            raise ValueError('Contact review FPS does not match video')
        mesh = trimesh.load(root / 'ball.glb', force='mesh', process=False)
        support_height = float(-mesh.bounds[0, 1])
        vertices, balls, shot_info, calibration, grounded, body_support = align_scene(
            vertices, translations, balls, valid, ball_valid, shots, observations, review, support_height)
        revision = 'ground-' + hashlib.sha256(json.dumps([observations, review, support_height, 2], sort_keys=True).encode()).hexdigest()[:10]
        ground = {'method': 'MoGe turf normals; ball-size camera-height anchors; reviewed contact constraints',
            'calibration': 'estimated, not measured', 'ballSupportHeight': support_height,
            'review': review, 'frames': calibration,
            'limits': 'Camera yaw and world translation are not recovered. Flight depth remains uncertain; nonpenetration only outside reviewed rolling ranges.'}
    else:
        # Uncalibrated fallback for new videos without a contact review.
        shot_info = []
        for shot in np.unique(shots):
            indices = np.flatnonzero((shots == shot) & valid)
            if not len(indices):
                continue
            first = vertices[indices[0]]
            offset = np.array([(first[:, 0].min() + first[:, 0].max()) / 2,
                               np.median(vertices[indices].min(axis=1)[:, 1]),
                              (first[:, 2].min() + first[:, 2].max()) / 2])
            vertices[shots == shot] -= offset
            balls[shots == shot] -= offset
            shot_info.append({'id': int(shot), 'start': int(indices[0]), 'offset': offset.tolist()})
    vertices = smooth_within_shots(vertices, valid, shots)
    balls = smooth_within_shots(balls, ball_valid, shots)
    if ground:
        vertices, balls = enforce_contacts(vertices, balls, valid, ball_valid, grounded, body_support, support_height)
        bottoms = vertices[valid, :, 1].min(axis=1)
        ground['validation'] = {'groundedBallFrames': int(grounded.sum()), 'supportedBodyFrames': int(body_support.sum()),
            'maxGroundedBallClearance': float(np.abs(balls[grounded, 1] - support_height).max()),
            'maxSupportedBodyClearance': float(np.abs(vertices[body_support, :, 1].min(axis=1)).max()),
            'minimumBodyClearance': float(bottoms.min()), 'minimumBallClearance': float((balls[ball_valid, 1] - support_height).min())}
    packed = pack_sequence(vertices, faces, valid, root, tag=revision)
    packed.update(version=2 if ground else 1, revision=revision, ground=ground, fps=tracking['fps'], duration=count / tracking['fps'],
        bodyModel='Meta SAM 3D Body', segmentationModel='Meta SAM 3',
        coordinates='ground-aligned pitch/roll, per-shot horizontal origin, estimated metric scale' if ground else 'camera-relative, Y up; fixed origin within each shot; metres estimated',
        smoothing='symmetric 3-frame [0.2, 0.6, 0.2], never across cuts or missing frames',
        shots=shot_info, validBodyFrames=int(valid.sum()), validBallFrames=int(ball_valid.sum()),
        ball={'model': 'Meta SAM 3D Objects', 'file': 'ball.glb' if (root / 'ball.glb').exists() else None,
              'diameter': .22, 'depth': 'shared ground/camera estimate; reviewed rolling contact; size-based flight estimate' if ground else 'estimated from SAM 3 mask radius and assumed 22 cm diameter',
              'rotation': 'not estimated; fixed orientation'},
        frames=[{'index': i, 'time': r['timestamp'], 'shot': r['shot'], 'valid': bool(valid[i]),
                 'ball': balls[i].tolist() if ball_valid[i] else None} for i, r in enumerate(records)],
        failures=failures)
    atomic_json(root / 'sequence.json', packed)
    atomic_json(root / 'progress.json', {'stage': 'body-complete', 'completed': int(valid.sum()), 'total': count})
    print(f'Body sequence complete: {valid.sum()}/{count}; ball locations {ball_valid.sum()}/{count}', flush=True)


if __name__ == '__main__':
    main()
