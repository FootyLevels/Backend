"""Shared video-motion math. All coordinates are explicitly camera-relative."""
import gzip
import json
from pathlib import Path

import cv2
import numpy as np


def shot_boundaries(video: Path) -> tuple[list[int], int]:
    cap = cv2.VideoCapture(str(video))
    previous = None
    differences = []
    count = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (90, 160)).astype(np.float32)
        differences.append(0 if previous is None else float(np.abs(small - previous).mean()))
        previous = small
        count += 1
    cap.release()
    if not count:
        raise ValueError("No video frames could be decoded")
    values = np.array(differences)
    median = np.median(values)
    threshold = max(25, median + 8 * np.median(np.abs(values - median)))
    cuts = [0]
    for index in np.flatnonzero(values > threshold):
        if index - cuts[-1] >= 10 and count - index >= 5:
            cuts.append(int(index))
    return cuts, count


def smooth_within_shots(values: np.ndarray, valid: np.ndarray, shot_ids: np.ndarray) -> np.ndarray:
    """Small symmetric filter: no lag, no blending across cuts or missing frames."""
    out = values.copy()
    for i in range(1, len(values) - 1):
        if valid[i - 1:i + 2].all() and shot_ids[i - 1] == shot_ids[i] == shot_ids[i + 1]:
            out[i] = values[i - 1] * .2 + values[i] * .6 + values[i + 1] * .2
    return out


def mask_box(mask: np.ndarray, padding=.1) -> list[float] | None:
    y, x = np.where(mask)
    if len(x) < 8:
        return None
    h, w = mask.shape
    x0, x1, y0, y1 = float(x.min()), float(x.max() + 1), float(y.min()), float(y.max() + 1)
    px, py = (x1 - x0) * padding, (y1 - y0) * padding
    return [max(0, (x0 - px) / w), max(0, (y0 - py) / h),
            min(1, (x1 + px) / w), min(1, (y1 + py) / h)]


def sphere_from_mask(mask: np.ndarray, focal: float, diameter=.22) -> tuple[list[float], float] | None:
    """Monocular size-based depth estimate, not measured ball depth or spin.

    Use the mask's enclosing circle rather than its area, so partial foot
    occlusions do not automatically shrink the apparent sphere.
    """
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    # Include disconnected fragments around an occluding foot in the same SAM track.
    contours = [c for c in contours if cv2.contourArea(c) >= 2]
    if not contours:
        return None
    (u, v), radius = cv2.minEnclosingCircle(np.concatenate(contours))
    if radius < 2:
        return None
    z = focal * (diameter / 2) / radius
    h, w = mask.shape
    return [(u - w / 2) * z / focal, -(v - h / 2) * z / focal, -z], float(radius)


def pack_sequence(vertices: np.ndarray, faces: np.ndarray, valid: np.ndarray,
                  output: Path, chunk_size=30, tag='') -> dict:
    """Quantized vertex cache, shared topology, 1-second independently fetchable chunks."""
    if not valid.any():
        raise ValueError("No valid body reconstructions")
    if not np.isfinite(vertices[valid]).all():
        raise ValueError("Non-finite sequence coordinates")
    low = vertices[valid].min(axis=(0, 1))
    high = vertices[valid].max(axis=(0, 1))
    span = np.maximum(high - low, 1e-6)
    # Missing frames stay marked missing. Encoding zeros does not create substitute poses.
    safe = np.where(valid[:, None, None], vertices, low)
    quantized = np.round(np.clip((safe - low) / span, 0, 1) * 65535).astype('<u2')
    output.mkdir(parents=True, exist_ok=True)
    topology = output / (f'faces-{tag}.bin' if tag else 'faces.bin')
    topology.write_bytes(np.asarray(faces, dtype='<u4').tobytes())
    chunks = []
    for start in range(0, len(vertices), chunk_size):
        name = f'body-{tag}-{start:06d}.bin.gz' if tag else f'body-{start:06d}.bin.gz'
        payload = quantized[start:start + chunk_size].tobytes()
        (output / name).write_bytes(gzip.compress(payload, compresslevel=6, mtime=0))
        chunks.append({'start': start, 'count': min(chunk_size, len(vertices) - start),
                       'file': name, 'bytes': (output / name).stat().st_size})
    return {'vertexCount': vertices.shape[1], 'faceCount': len(faces),
            'frameCount': len(vertices), 'min': low.tolist(), 'span': span.tolist(),
            'topologyFile': topology.name, 'encoding': 'uint16-le-xyz-gzip', 'chunks': chunks}
