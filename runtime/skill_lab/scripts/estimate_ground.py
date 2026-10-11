"""Estimate pitch orientation from MoGe point maps of visible turf.

MoGe-1 supplies relative geometry, not metric camera height. Its intrinsics are
fixed to the body camera model so the resulting normals share camera coordinates.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fit_plane(points, seed=0):
    rng = np.random.default_rng(seed)
    points = points[np.isfinite(points).all(1)]
    if len(points) < 300:
        raise ValueError('Too few visible ground points')
    points = points[rng.choice(len(points), min(6000, len(points)), replace=False)]
    threshold = np.median(np.linalg.norm(points, axis=1)) * .012
    best = np.zeros(len(points), bool)
    for _ in range(160):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        normal = np.cross(b - a, c - a)
        normal /= max(np.linalg.norm(normal), 1e-12)
        if abs(normal[1]) < .65:
            continue
        inliers = np.abs((points - a) @ normal) < threshold
        if inliers.sum() > best.sum():
            best = inliers
    if best.mean() < .65:
        raise ValueError('Visible turf does not support a reliable plane')
    center = points[best].mean(0)
    _, _, vt = np.linalg.svd(points[best] - center, full_matrices=False)
    normal = vt[-1]
    if normal[1] < 0:
        normal *= -1
    return normal, {'inlierFraction': float(best.mean()),
        'relativeResidual': float(np.median(np.abs((points[best] - center) @ normal)) / np.linalg.norm(center)),
        'points': int(best.sum())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--stride', type=int, default=10)
    args = parser.parse_args()
    root = args.project_dir / 'motion'
    tracking = json.loads((root / 'tracking.json').read_text())
    import torch
    from moge.model.v1 import MoGeModel
    model = MoGeModel.from_pretrained('Ruicheng/moge-vitl').to('cuda').eval()
    samples = []
    count = tracking['frameCount']
    ends = tracking['cuts'][1:] + [count]
    debug = root / 'ground-debug'
    debug.mkdir(exist_ok=True)
    for shot, (start, end) in enumerate(zip(tracking['cuts'], ends)):
        for index in sorted(set(range(start, end, args.stride)) | {end - 1}):
            bgr = cv2.imread(str(root / 'source' / f'{index:06d}.jpg'))
            h, w = bgr.shape[:2]
            raw = np.load(root / 'raw' / f'{index:06d}.npz')
            focal = float(raw['focal'])
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            mask = ((hsv[:, :, 0] >= 18) & (hsv[:, :, 0] <= 95) &
                    (hsv[:, :, 1] > 22) & (hsv[:, :, 2] > 35))
            mask[:int(h * .48)] = False
            mask[int(h * .97):] = False
            for kind in ['person', 'ball']:
                subject = cv2.imread(str(root / f'masks/{kind}' / f'{index:06d}.png'), 0)
                if subject is not None:
                    mask[cv2.dilate(subject, np.ones((15, 15), np.uint8)) > 0] = False
            image = torch.from_numpy(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).to('cuda').permute(2, 0, 1).float() / 255
            with torch.inference_mode():
                output = model.infer(image, fov_x=float(np.degrees(2 * np.arctan(w / (2 * focal)))), resolution_level=5)
            points = output['points'].cpu().numpy() * [1, -1, -1]
            normal, quality = fit_plane(points[mask], index)
            samples.append({'index': index, 'shot': shot, 'normal': normal.tolist(), **quality})
            overlay = bgr.copy()
            overlay[mask] = (overlay[mask] * .5 + np.array([0, 180, 255]) * .5).astype(np.uint8)
            cv2.imwrite(str(debug / f'{index:06d}.jpg'), overlay)
            print(index, normal.round(4), quality, flush=True)
    data = {'model': 'MoGe-1 Ruicheng/moge-vitl', 'method': 'RANSAC + SVD on lower-image turf, excluding SAM 3 person/ball masks',
            'intrinsics': 'fixed to SAM 3D Body focal length', 'coordinates': 'camera X right, Y up, Z backward',
            'metricScale': False, 'samples': samples}
    (root / 'ground-observations.json').write_text(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
