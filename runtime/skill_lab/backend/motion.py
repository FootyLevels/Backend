"""Resumable whole-video pipeline. Each model runs in its own Python environment."""
import json
import os
import subprocess
from pathlib import Path

from . import store
from .jobs import EXECUTOR, timestamp


def paths():
    root = store.ROOT
    return {
        'segPython': Path(os.getenv('SAM3_PYTHON', root / '.venv-seg/bin/python')),
        'bodyPython': Path(os.getenv('SAM3D_PYTHON', root / '.venv-gpu/bin/python')),
        'objectsPython': Path(os.getenv('SAM3D_OBJECTS_PYTHON', root / '.venv-objects/bin/python')),
        'segCheckpoint': Path(os.getenv('SAM3_CHECKPOINT', root / 'checkpoints/sam3/sam3.pt')),
        'objectsConfig': Path(os.getenv('SAM3D_OBJECTS_CONFIG', root / 'checkpoints/sam-3d-objects/checkpoints/pipeline.yaml')),
    }


def capability():
    missing = [k for k, p in paths().items() if not p.is_file()]
    return {'ready': not missing, 'reason': 'Missing: ' + ', '.join(missing) if missing else None}


def publish(project_id, status='complete', error=None):
    path = store.project_dir(project_id) / 'motion/sequence.json'
    sequence = json.loads(path.read_text()) if path.exists() else None
    with store.LOCK:
        project = store.read(project_id)
        project['motion'] = {**project.get('motion', {}), 'status': status, 'error': error,
                             'finishedAt': timestamp()}
        if sequence:
            project['motion'].update(sequenceUrl=store.asset_url(project_id, 'motion/sequence.json') + ('?v=' + sequence['revision'] if sequence.get('revision') else ''),
                bodyFrames=sequence['validBodyFrames'], ballFrames=sequence['validBallFrames'], fps=sequence['fps'],
                ballReady=bool(sequence['ball']['file']), groundAligned=bool(sequence.get('ground')))
        store.save(project)


def start(project_id, *, background=True):
    config = capability()
    if not config['ready']:
        raise ValueError(config['reason'])
    with store.LOCK:
        project = store.read(project_id)
        if project.get('motion', {}).get('status') in ('queued', 'running') or (project.get('job') or {}).get('status') in ('queued', 'running'):
            raise ValueError('This project already has a reconstruction job')
        if project['video']['duration'] > 60:
            raise ValueError('Whole-video reconstruction currently supports clips up to 60 seconds')
        project['motion'] = {'status': 'queued', 'stage': 'waiting', 'startedAt': timestamp()}
        store.save(project)
    if background:
        EXECUTOR.submit(run, project_id)
    else:
        run(project_id)
    return store.read(project_id)['motion']


def run(project_id):
    from .providers.local import LocalProvider
    # Keyframes and motion share the single executor. Avoid keeping two copies
    # of the body weights while segmentation/body subprocesses use this GPU.
    LocalProvider.release()
    p = paths()
    directory = store.project_dir(project_id)
    root = directory / 'motion'
    root.mkdir(exist_ok=True)
    steps = [
        ('Tracking player and ball', p['segPython'], 'segment_video.py', ['--checkpoint', p['segCheckpoint']]),
        ('Reconstructing every body pose', p['bodyPython'], 'reconstruct_motion.py', []),
        ('Reconstructing the football', p['objectsPython'], 'reconstruct_ball.py', ['--config', p['objectsConfig']]),
    ]
    if (root / 'contact-review.json').exists():
        steps += [('Estimating pitch orientation', p['objectsPython'], 'estimate_ground.py', []),
                  ('Aligning ground contact', p['bodyPython'], 'reconstruct_motion.py', ['--pack-only', '--align-ground'])]
    try:
        omitted = []
        for stage, python, script, extra in steps:
            if script in ('reconstruct_ball.py', 'estimate_ground.py'):
                import torch
                free, _ = torch.cuda.mem_get_info()
                minimum = float(os.getenv('FOOTYLEVELS_OBJECTS_MIN_FREE_GIB', '32')) * 1024 ** 3
                if free < minimum:
                    omitted.append(stage)
                    continue
            if script == 'reconstruct_motion.py' and '--align-ground' in extra and 'Estimating pitch orientation' in omitted:
                continue
            if script == 'estimate_ground.py' and (root / 'ground-observations.json').exists():
                continue
            if script == 'segment_video.py' and (root / 'tracking.json').exists():
                track = json.loads((root / 'tracking.json').read_text())
                if len(track['frames']) == track['frameCount'] and all('shot' in f and 'person' in f and 'ball' in f for f in track['frames']):
                    continue
            with store.LOCK:
                project = store.read(project_id)
                project['motion'].update(status='running', stage=stage)
                store.save(project)
            with (root / f'{Path(script).stem}.log').open('a') as log:
                subprocess.run([str(python), '-u', str(store.ROOT / 'scripts' / script), str(directory), *map(str, extra)],
                    cwd=store.ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        result = json.loads((root / 'sequence.json').read_text())
        failed = len(result.get('failures', []))
        reasons = ([f'{failed} body frames failed. Retry resumes those frames.'] if failed else [])
        if omitted:
            reasons.append('Optional football/ground processing skipped: insufficient free GPU memory. Body playback is available.')
        publish(project_id, 'partial' if reasons else 'complete', ' '.join(reasons) or None)
    except Exception as exc:
        publish(project_id, 'partial' if (root / 'sequence.json').exists() else 'failed',
                f'{type(exc).__name__} during {stage}. See motion/{Path(script).stem}.log; retry resumes saved work.')
    finally:
        # Restore warm key-frame inference when capacity permits. A warmup
        # failure does not erase a successfully generated body sequence.
        try:
            LocalProvider().load()
        except Exception:
            LocalProvider.release()
