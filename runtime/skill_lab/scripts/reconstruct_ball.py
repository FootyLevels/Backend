"""Reconstruct one visible football with SAM 3D Objects; reuse it along the SAM 3 track."""
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor/sam-3d-objects'))
os.environ['LIDRA_SKIP_INIT'] = 'true'
if Path('/usr/local/cuda').is_dir():
    os.environ.setdefault('CUDA_HOME', '/usr/local/cuda')
os.environ.setdefault('MAX_JOBS', '4')



def write_ball(mesh, path):
    import trimesh
    def material(tree):
        tree['materials'] = [{'name': 'Football', 'pbrMetallicRoughness': {
            'baseColorFactor': [1, 1, 1, 1], 'metallicFactor': 0, 'roughnessFactor': .8}}]
        for model in tree['meshes']:
            for primitive in model['primitives']:
                primitive['material'] = 0
    payload = trimesh.exchange.gltf.export_glb(mesh, include_normals=True, tree_postprocessor=material)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(payload)
    temporary.replace(path)


def select_frame(root, records):
    candidates = []
    for r in records:
        if not r.get('ball'):
            continue
        i = r['index']
        mask = cv2.imread(str(root / 'masks/ball' / f'{i:06d}.png'), 0)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        (_, _), radius = cv2.minEnclosingCircle(contour)
        circularity = area / max(1, np.pi * radius ** 2)
        y, x = np.where(mask)
        h, w = mask.shape
        if min(x.min(), y.min(), w - 1 - x.max(), h - 1 - y.max()) < 3:
            continue
        image = cv2.imread(str(root / 'source' / f'{i:06d}.jpg'))
        sharpness = cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F)[mask > 0].var()
        score = area * circularity ** 4 * np.log1p(sharpness)
        candidates.append((score, i))
    if not candidates:
        raise RuntimeError('No visible football found for object reconstruction')
    return max(candidates)[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('project_dir', type=Path)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--frame', type=int)
    p.add_argument('--rebuild', action='store_true', help='Regenerate the object instead of reusing the saved mesh')
    a = p.parse_args()
    root = a.project_dir / 'motion'
    if a.rebuild or not (root / 'ball.glb').exists():
        from hydra.utils import instantiate
        from omegaconf import OmegaConf
        import torch
        import trimesh
        tracking = json.loads((root / 'tracking.json').read_text())
        index = a.frame if a.frame is not None else select_frame(root, tracking['frames'])
        image = cv2.cvtColor(cv2.imread(str(root / 'source' / f'{index:06d}.jpg')), cv2.COLOR_BGR2RGB)
        mask = cv2.imread(str(root / 'masks/ball' / f'{index:06d}.png'), 0)
        cv2.imwrite(str(root / 'ball-input.png'), cv2.cvtColor(np.dstack([image, mask]), cv2.COLOR_RGBA2BGRA))
        config = OmegaConf.load(a.config)
        config.workspace_dir = str(a.config.resolve().parent)
        config.rendering_engine = 'pytorch3d'
        config.compile_model = False
        print(f'Loading SAM 3D Objects; football source frame {index}', flush=True)
        pipeline = instantiate(config)
        with torch.inference_mode():
            result = pipeline.run(image, mask, seed=42, with_mesh_postprocess=False,
                with_texture_baking=False, with_layout_postprocess=False, use_vertex_color=True)
        mesh = result['glb']
        if mesh is None:
            raise RuntimeError('SAM 3D Objects did not return a mesh')
        if isinstance(mesh, trimesh.Scene):
            mesh = mesh.to_geometry()
        if not len(mesh.vertices) or not np.isfinite(mesh.vertices).all():
            raise RuntimeError('Object mesh is empty or non-finite')
        # The appearance decoder is more detailed than the mesh decoder's coarse RGB.
        # Transfer its degree-zero spherical-harmonic color to the generated surface.
        from scipy.spatial import cKDTree
        gaussian = result['gaussian'][0]
        centers = gaussian.get_xyz.detach().float().cpu().numpy()
        coefficients = gaussian.get_features.detach().float().cpu().numpy().reshape(len(centers), -1)
        if coefficients.shape[1] != 3:
            raise RuntimeError('Unexpected appearance coefficients: expected SH degree zero')
        rgb = np.clip(.28209479177387814 * coefficients + .5, 0, 1)
        opacity = gaussian.get_opacity.detach().float().cpu().numpy().reshape(-1)
        source_vertices = mesh.vertices @ np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
        distance, nearest = cKDTree(centers).query(source_vertices, k=4)
        weights = opacity[nearest] / np.maximum(distance, 1e-5) ** 2
        weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
        colors = (rgb[nearest] * weights[..., None]).sum(axis=1)
        mesh.visual.vertex_colors = np.column_stack([np.round(colors * 255).astype(np.uint8), np.full(len(colors), 255, np.uint8)])
        original_extents = mesh.extents.copy()
        mesh.apply_translation(-mesh.bounds.mean(axis=0))
        mesh.apply_scale(.22 / float(original_extents.max()))
        destination = root / 'ball.glb'
        write_ball(mesh, destination)
        (root / 'ball-original.glb').write_bytes(destination.read_bytes())
        metadata = {'model': 'Meta SAM 3D Objects', 'seed': 42, 'sourceFrame': index,
            'segmentation': 'Meta SAM 3', 'vertices': len(mesh.vertices), 'faces': len(mesh.faces),
            'originalExtents': original_extents.tolist(), 'displayDiameterMetres': .22,
            'texture': 'model-predicted Gaussian appearance transferred to generated mesh (4 nearest, opacity/distance weighted)', 'rotation': 'not reconstructed from video',
            'selection': 'visible mask area, circularity and source sharpness'}
        (root / 'ball.json').write_text(json.dumps(metadata, indent=2))
        print(f'Football mesh saved: {len(mesh.vertices)} vertices', flush=True)
    # The decoder can return almost a million triangles for a small ball.
    # Keep the original and simplify only the browser asset, transferring colors.
    import trimesh
    from scipy.spatial import cKDTree
    display = trimesh.load(root / 'ball.glb', force='mesh', process=False)
    if len(display.faces) > 12000:
        original = root / 'ball-original.glb'
        if not original.exists():
            original.write_bytes((root / 'ball.glb').read_bytes())
        simplified = display.simplify_quadric_decimation(face_count=12000)
        if display.visual.kind == 'vertex':
            nearest = cKDTree(display.vertices).query(simplified.vertices)[1]
            simplified.visual.vertex_colors = display.visual.vertex_colors[nearest]
        write_ball(simplified, root / 'ball.glb')
        info_path = root / 'ball.json'
        info = json.loads(info_path.read_text())
        info.update(displayVertices=len(simplified.vertices), displayFaces=len(simplified.faces),
                    simplification='quadric decimation, nearest original vertex color; original retained')
        info_path.write_text(json.dumps(info, indent=2))
        print(f'Browser football: {len(simplified.faces)} triangles', flush=True)
    else:
        write_ball(display, root / 'ball.glb')
    path = root / 'sequence.json'
    if path.exists():
        sequence = json.loads(path.read_text())
        sequence['ball']['file'] = 'ball.glb'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(sequence, indent=2))
        temp.replace(path)


if __name__ == '__main__':
    main()
