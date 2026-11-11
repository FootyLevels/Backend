"""Fail before loading weights if the selected user-owned runtime is unsuitable."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

import torch

CHECKPOINT_HASH = "b5a2f9d305dd02626b967aa2e86021fba07065df66ce7a7e00ffb9664f150abf"
MHR_HASH = "352e271a6c42729c68554ceaea0c955e866970160c31e35506d782dc0f7377bc"
BODY_REVISION = "b5c765a0d89d789985e186d396315e7590887b94"


def sha256(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError('Select exactly one CUDA GPU; CPU fallback is unavailable')
    if 'A100' not in torch.cuda.get_device_name(0):
        raise RuntimeError('This verified launch contract requires an A100')
    free, total = torch.cuda.mem_get_info()
    if free < 12 * 1024 ** 3:
        raise RuntimeError('Less than 12 GiB GPU headroom; leave other jobs untouched and retry later')
    checkpoint = Path(os.environ['SAM3D_CHECKPOINT'])
    if sha256(checkpoint) != CHECKPOINT_HASH or sha256(checkpoint.parent / 'assets/mhr_model.pt') != MHR_HASH:
        raise RuntimeError('Body/MHR weights differ from the validated model')
    revision = subprocess.check_output(['git', '-C', os.environ['SAM3D_REPO'], 'rev-parse', 'HEAD'], text=True).strip()
    if revision != BODY_REVISION:
        raise RuntimeError('Body source differs from the validated revision')
    for tool in ['ffmpeg', 'ffprobe']:
        subprocess.run([tool, '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    with socket.socket() as probe:
        # Match Uvicorn and services.py: a TIME_WAIT connection left by the previous
        # process is not a live listener and must not fail a restart.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', int(os.environ.get('PORT', '8013'))))
    print(json.dumps({'preflight': 'passed', 'gpu': torch.cuda.get_device_name(0),
                      'freeGiB': round(free / 1024 ** 3, 2), 'totalGiB': round(total / 1024 ** 3, 2),
                      'bodyRevision': revision, 'weightsVerified': True}), flush=True)


if __name__ == '__main__':
    main()
