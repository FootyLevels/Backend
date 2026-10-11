"""SAM 3 video segmentation in a separate environment from SAM 3D Body.

Run with CUDA_VISIBLE_DEVICES set to exactly one chosen GPU. The official SAM 3
checkpoint is mandatory; no heuristic masks are presented as model output.
"""
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.motion_data import mask_box, shot_boundaries


def array(value):
    return value.detach().cpu().numpy() if hasattr(value, 'detach') else np.asarray(value)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('project_dir', type=Path)
    p.add_argument('--checkpoint', type=Path, required=True)
    a = p.parse_args()
    project = json.loads((a.project_dir / 'project.json').read_text())
    root = a.project_dir / 'motion'
    root.mkdir(exist_ok=True)
    cuts, count = shot_boundaries(a.project_dir / 'video.mp4')
    source = root / 'source'
    source.mkdir(exist_ok=True)
    cap = cv2.VideoCapture(str(a.project_dir / 'video.mp4'))
    for i in range(count):
        ok, image = cap.read()
        if not ok:
            raise RuntimeError('Video decode stopped early')
        cv2.imwrite(str(source / f'{i:06d}.jpg'), image, [cv2.IMWRITE_JPEG_QUALITY, 96])
    cap.release()
    from sam3.model_builder import build_sam3_video_predictor
    predictor = build_sam3_video_predictor(checkpoint_path=str(a.checkpoint), gpus_to_use=[0], compile=False)
    records = [{'index': i, 'timestamp': i / project['video']['fps']} for i in range(count)]
    for shot_id, start in enumerate(cuts):
        end = cuts[shot_id + 1] if shot_id + 1 < len(cuts) else count
        folder = root / f'shot-{shot_id}'
        folder.mkdir(exist_ok=True)
        for i in range(start, end):
            records[i]['shot'] = shot_id
            target = folder / f'{i - start:06d}.jpg'
            if not target.exists():
                os.link(source / f'{i:06d}.jpg', target)
        for kind, prompt in [('person', 'person'), ('ball', 'soccer ball')]:
            (root / 'masks' / kind).mkdir(parents=True, exist_ok=True)
            session = predictor.handle_request({'type': 'start_session', 'resource_path': str(folder),
                                                 'offload_video_to_cpu': True})['session_id']
            try:
                seed = 0 if kind == 'person' else min(15, end - start - 1)
                initial = predictor.handle_request({'type': 'add_prompt', 'session_id': session,
                    'frame_index': seed, 'text': prompt})['outputs']
                ids = array(initial['out_obj_ids']).tolist()
                masks = array(initial['out_binary_masks'])
                target_id = None
                if ids:
                    # Foreground athlete / dominant ball. Keep that track ID throughout the shot.
                    target_id = ids[int(np.argmax(masks.reshape(len(ids), -1).sum(1)))]
                print(f'Shot {shot_id}: {kind}, seed {seed}, target {target_id}, candidates {ids}', flush=True)
                found = 0
                for response in predictor.handle_stream_request({'type': 'propagate_in_video',
                        'session_id': session, 'start_frame_idx': seed, 'propagation_direction': 'both'}):
                    i = start + response['frame_index']
                    out = response['outputs']
                    ids = array(out['out_obj_ids']).tolist()
                    if target_id is None and ids:
                        # Only initialize after a detection if the seed was occluded.
                        areas = array(out['out_binary_masks']).reshape(len(ids), -1).sum(1)
                        target_id = ids[int(np.argmax(areas))]
                    if target_id not in ids:
                        records[i][kind] = None
                        continue
                    k = ids.index(target_id)
                    mask = array(out['out_binary_masks'])[k].astype(np.uint8).squeeze()
                    box = mask_box(mask)
                    if box is None:
                        records[i][kind] = None
                        continue
                    cv2.imwrite(str(root / 'masks' / kind / f'{i:06d}.png'), mask * 255)
                    probability = out.get('out_probs')
                    records[i][kind] = {'trackId': int(target_id), 'bbox': box,
                        'area': int(mask.sum()),
                        'score': float(array(probability)[k]) if probability is not None else None}
                    found += 1
                print(f'Shot {shot_id}: {kind} masks {found}/{end - start}', flush=True)
            finally:
                predictor.handle_request({'type': 'close_session', 'session_id': session})
        checkpoint = {'model': 'Meta SAM 3', 'fps': project['video']['fps'],
                      'frameCount': count, 'cuts': cuts, 'frames': records,
                      'selection': 'largest subject at seed, persistent track ID within each shot'}
        (root / 'tracking.json').write_text(json.dumps(checkpoint, indent=2))
    predictor.shutdown()
    print('Segmentation complete:', root / 'tracking.json', flush=True)


if __name__ == '__main__':
    main()
