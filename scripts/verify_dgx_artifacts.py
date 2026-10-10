"""Validate saved DGX outputs independently of readiness/status flags."""
import argparse
import gzip
import json
from pathlib import Path
import struct

import numpy as np


def verify_glb(model: Path, metadata_file: Path) -> dict:
    blob = model.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", blob)
    if magic != b"glTF" or version != 2 or length != len(blob):
        raise ValueError("Invalid GLB header")
    position, chunks = 12, {}
    while position < len(blob):
        size, kind = struct.unpack_from("<II", blob, position)
        position += 8
        chunks[kind] = blob[position:position + size]
        position += size
    if position != len(blob):
        raise ValueError("Invalid GLB chunk lengths")
    schema = json.loads(chunks[0x4E4F534A])
    binary = chunks[0x004E4942]
    vertices = faces = 0
    for mesh in schema["meshes"]:
        for primitive in mesh["primitives"]:
            accessor = schema["accessors"][primitive["attributes"]["POSITION"]]
            view = schema["bufferViews"][accessor["bufferView"]]
            if accessor["componentType"] != 5126 or accessor["type"] != "VEC3" or primitive.get("mode", 4) != 4:
                raise ValueError("Expected float32 XYZ positions and triangular faces")
            points = np.ndarray((accessor["count"], 3), dtype="<f4", buffer=binary,
                offset=view.get("byteOffset", 0) + accessor.get("byteOffset", 0),
                strides=(view.get("byteStride", 12), 4))
            index = schema["accessors"][primitive["indices"]]
            iv = schema["bufferViews"][index["bufferView"]]
            indices = np.frombuffer(binary, dtype={5121: "u1", 5123: "<u2", 5125: "<u4"}[index["componentType"]],
                count=index["count"], offset=iv.get("byteOffset", 0) + index.get("byteOffset", 0))
            if not np.isfinite(points).all() or not len(indices) or len(indices) % 3 or indices.max() >= len(points):
                raise ValueError("Invalid mesh coordinates/topology")
            vertices += len(points)
            faces += len(indices) // 3
    metadata = json.loads(metadata_file.read_text())
    if vertices != metadata["vertexCount"] or faces != metadata["faceCount"] or vertices == 0:
        raise ValueError("Metadata and GLB counts disagree")
    joints = np.asarray(metadata["keypoints3dCamera"])
    if joints.ndim != 2 or joints.shape[1] != 3 or not np.isfinite(joints).all():
        raise ValueError("Invalid keypoints")
    return {"bytes": length, "vertices": vertices, "faces": faces, "keypoints": len(joints)}


def local_asset(root: Path, name: str) -> Path:
    if Path(name).name != name:
        raise ValueError("Expected an artifact filename without traversal")
    return root / name


def verify_motion(manifest_file: Path) -> dict:
    data = json.loads(manifest_file.read_text())
    if data["encoding"] != "uint16-le-xyz-gzip" or data["frameCount"] != len(data["frames"]):
        raise ValueError("Invalid motion encoding/frame count")
    faces = np.fromfile(local_asset(manifest_file.parent, data["topologyFile"]), "<u4")
    if len(faces) != data["faceCount"] * 3 or faces.max() >= data["vertexCount"]:
        raise ValueError("Invalid shared topology")
    low, span = np.asarray(data["min"]), np.asarray(data["span"])
    if low.shape != (3,) or span.shape != (3,) or not np.isfinite([low, span]).all() or (span <= 0).any():
        raise ValueError("Invalid quantization bounds")
    next_frame = 0
    for chunk in data["chunks"]:
        path = local_asset(manifest_file.parent, chunk["file"])
        expected = chunk["count"] * data["vertexCount"] * 3 * 2
        # Bound decompression by the expected decoded size, plus one overflow byte.
        with gzip.open(path, "rb") as source:
            decoded = source.read(expected + 1)
        if chunk["start"] != next_frame or path.stat().st_size != chunk["bytes"] or len(decoded) != expected:
            raise ValueError("Invalid motion chunk coverage/size")
        positions = np.frombuffer(decoded, "<u2").reshape(chunk["count"], data["vertexCount"], 3) / 65535 * span + low
        if not np.isfinite(positions).all():
            raise ValueError("Nonfinite decoded motion")
        next_frame += chunk["count"]
    frames = data["frames"]
    times = [frame["time"] for frame in frames]
    if next_frame != data["frameCount"] or sum(bool(f["valid"]) for f in frames) != data["validBodyFrames"]:
        raise ValueError("Motion coverage/validity mismatch")
    if not np.isfinite(times).all() or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("Motion timestamps must increase")
    if not data["validBodyFrames"]:
        raise ValueError("No usable body frames")
    return {"frames": next_frame, "validBodyFrames": data["validBodyFrames"],
            "vertices": data["vertexCount"], "faces": data["faceCount"], "chunks": len(data["chunks"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glb", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--motion", type=Path)
    args = parser.parse_args()
    if bool(args.glb) != bool(args.metadata) or not (args.glb or args.motion):
        parser.error("Supply --glb and --metadata, or --motion, or both")
    if args.glb:
        print(json.dumps({"mesh": verify_glb(args.glb, args.metadata)}))
    if args.motion:
        print(json.dumps({"motion": verify_motion(args.motion)}))
