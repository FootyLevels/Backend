#!/usr/bin/env python3
"""Credential-free CPU client of the owned, serial SAM 3D Body Unix bridge."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "runtime/skill_lab"))
from backend.model_bridge import receive, send
from footylevels.model_requests import BODY_PROFILE, body_input_hash
from footylevels.records import AssetRef, Engine, JobRecord, PlaybackContract, ReconstructionRecord


def request_model(path, request):
    # A lost acknowledgement reuses the exact immutable frame request. The
    # bridge returns the same cached artifact, even after its process restarts.
    for attempt in range(2):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                stream.settimeout(240)
                stream.connect(str(path))
                send(stream, request)
                return receive(stream)
        except (OSError, ConnectionError):
            if attempt == 1:
                raise


def reconstruct(directory, socket_path, ffmpeg):
    job = JobRecord.model_validate_json((directory / "job.json").read_bytes())
    source = directory / "input.mp4"
    if (job.profile_version != BODY_PROFILE or job.kind != "reconstructKeyframes" or not job.keyframes
            or len({f.frame_index for f in job.keyframes}) != len(job.keyframes)
            or body_input_hash(job.source_asset, job.keyframes) != job.input_hash
            or source.stat().st_size != job.source_asset.byte_size
            or hashlib.sha256(source.read_bytes()).hexdigest() != job.source_asset.sha256):
        raise ValueError("Unsupported or changed reconstruction input")
    artifacts, frames, missing = [], [], []
    engine, topology = None, None
    prefix = f"users/{job.owner_id}/projects/{job.project_id}/reconstruction/{job.id}/{job.attempt_count}/"

    def asset(name, content_type):
        path = directory / "artifacts" / name
        return AssetRef(bucket="private-media", object_key=prefix + name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), byte_size=path.stat().st_size,
            content_type=content_type)

    for selection in job.keyframes:
        image = directory / "frame.jpg"
        # Select the exact index in canonical 30 fps playback; no approximate
        # seek, rotation or resize changes the pinned subject box.
        subprocess.run([sys.executable, str(ROOT / "src/footylevels/media_limits.py"), str(ffmpeg),
            "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "2",
            "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:v:0", "-an",
            "-vf", f"select=eq(n\\,{selection.frame_index})", "-frames:v", "1", "-q:v", "2",
            "-threads", "2", str(image)], check=True, timeout=30,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not image.is_file() or image.stat().st_size > 2*1024*1024:
            raise ValueError("Missing or excessive keyframe")
        payload = image.read_bytes()
        response = request_model(socket_path, {"jobId": job.id, "attempt": job.attempt_count,
            "frameIndex": selection.frame_index, "inputHash": job.input_hash, "profileVersion": BODY_PROFILE,
            "bbox": list(selection.bbox), "imageBase64": base64.b64encode(payload).decode(),
            "imageSha256": hashlib.sha256(payload).hexdigest()})
        image.unlink()
        if response == {"error": "model_failed"}:
            missing.append(selection.frame_index)
            continue
        if response.get("status") != "ready":
            raise ValueError("Model request rejected")
        response_engine = Engine.model_validate(response["engine"])
        response_topology = response["topologyVersion"]
        if engine is not None and (engine != response_engine or topology != response_topology):
            raise ValueError("Model changed during reconstruction")
        engine, topology = response_engine, response_topology
        body = base64.b64decode(response["glbBase64"], validate=True)
        if (not 12 <= len(body) <= 4*1024*1024 or body[:4] != b"glTF"
                or struct.unpack("<II", body[4:12]) != (2, len(body))
                or len(body) != response["byteSize"] or hashlib.sha256(body).hexdigest() != response["sha256"]):
            raise ValueError("Invalid model artifact")
        name = f"frame-{selection.frame_index:04d}.glb"
        (directory / "artifacts" / name).write_bytes(body)
        artifacts.append(asset(name, "model/gltf-binary"))
        frames.append({"frameIndex": selection.frame_index, "timestamp": selection.frame_index/30,
            "bbox": list(selection.bbox), "artifact": name, "metadata": response["info"]})
    if not frames:
        raise ValueError("No bodies reconstructed")
    contract = PlaybackContract(topology_version=topology, encoding="glb")
    manifest = {"schemaVersion": 1, "jobId": job.id, "attempt": job.attempt_count,
        "sourceSha256": job.source_asset.sha256, "inputRevision": job.input_revision,
        "inputHash": job.input_hash, "profileVersion": BODY_PROFILE,
        "engine": engine.model_dump(mode="json", by_alias=True),
        "contract": contract.model_dump(mode="json", by_alias=True),
        "frames": frames, "missingFrameIndices": missing}
    (directory / "artifacts/keyframes.json").write_text(json.dumps(manifest, allow_nan=False))
    artifacts.append(asset("keyframes.json", "application/json"))
    result = ReconstructionRecord(id=job.id, job_id=job.id, owner_id=job.owner_id, project_id=job.project_id,
        source_asset=job.source_asset, input_revision=job.input_revision, profile_version=BODY_PROFILE,
        mode="keyframes", outcome="partial" if missing else "ready", engine=engine, contract=contract,
        artifacts=artifacts, requested_frame_count=len(job.keyframes), reconstructed_frame_count=len(frames),
        missing_frame_count=len(missing))
    (directory / "result.json").write_text(result.model_dump_json(by_alias=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    # User namespaces map the DGX account to uid 0; passwd would return /root,
    # while HOME is deliberately temporary. The trusted parent pins both paths.
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    reconstruct(args.directory, args.socket, args.ffmpeg)
