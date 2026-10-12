#!/usr/bin/env python3
"""Credential-free adapter for keyframes and bounded body-motion playback."""
import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'runtime/skill_lab'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend.model_bridge import receive, send
from body_keyframes_adapter import reconstruct as keyframes
from footylevels.model_requests import MOTION_PROFILE, motion_input_hash
from footylevels.records import AssetRef, Engine, JobRecord, PlaybackContract, ReconstructionRecord


def validate_sequence(sequence, output, names, count, topology_version):
    if (sequence['frameCount'] != count or sequence['fps'] != 30 or len(sequence['frames']) != count
            or sequence['encoding'] != 'uint16-le-xyz-gzip' or sequence['topologyFile'] not in names
            or not 1 <= sequence['vertexCount'] <= 25000 or not 1 <= sequence['faceCount'] <= 50000
            or sequence['validBallFrames'] != 0 or sequence['ball']['file'] is not None or sequence.get('ground') is not None):
        raise ValueError('Motion playback contract')
    if (len(sequence['min']) != 3 or len(sequence['span']) != 3
            or not all(type(n) in (int, float) and math.isfinite(n) for n in sequence['min'])
            or not all(type(n) in (int, float) and math.isfinite(n) and n > 0 for n in sequence['span'])):
        raise ValueError('Motion coordinate bounds')
    topology = (output/sequence['topologyFile']).read_bytes()
    if len(topology) != sequence['faceCount']*12 or topology_version != 'mhr-'+hashlib.sha256(topology).hexdigest()[:24]+'-v1':
        raise ValueError('Motion topology version')
    indices = array.array('I')
    indices.frombytes(topology)
    if sys.byteorder != 'little': indices.byteswap()
    if max(indices) >= sequence['vertexCount']:
        raise ValueError('Motion topology index')
    start, chunk_files = 0, set()
    for chunk in sequence['chunks']:
        if (type(chunk['start']) is not int or type(chunk['count']) is not int or chunk['start'] != start
                or not 1 <= chunk['count'] <= 30 or start+chunk['count'] > count
                or chunk['file'] not in names or chunk['file'] in chunk_files):
            raise ValueError('Motion chunk coverage')
        path = output/chunk['file']
        if path.stat().st_size != chunk['bytes']:
            raise ValueError('Motion chunk size')
        expected = chunk['count']*sequence['vertexCount']*3*2
        decoder = zlib.decompressobj(16+zlib.MAX_WBITS)
        decoded = decoder.decompress(path.read_bytes(), expected+1)
        if len(decoded) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Motion chunk encoding')
        start += chunk['count']
        chunk_files.add(chunk['file'])
    if start != count:
        raise ValueError('Motion chunk coverage')
    valid = 0
    for i, frame in enumerate(sequence['frames']):
        if (frame['index'] != i or type(frame['valid']) is not bool or type(frame['shot']) is not int
                or frame['shot'] < 0 or not math.isfinite(frame['time']) or abs(frame['time']-i/30) > .000001
                or frame['ball'] is not None):
            raise ValueError('Motion timestamp or validity')
        valid += frame['valid']
    if not 0 < valid <= count or sequence['validBodyFrames'] != valid:
        raise ValueError('Motion validity')
    return valid


def motion(directory, socket_path, ffmpeg):
    job = JobRecord.model_validate_json((directory/'job.json').read_bytes())
    segment = job.motion_segment
    source = directory/'input.mp4'
    if (job.kind != 'reconstructMotion' or job.profile_version != MOTION_PROFILE or segment is None
            or job.keyframes is not None or motion_input_hash(job.source_asset, segment) != job.input_hash
            or source.stat().st_size != job.source_asset.byte_size
            or hashlib.sha256(source.read_bytes()).hexdigest() != job.source_asset.sha256):
        raise ValueError('Unsupported or changed motion input')
    clip = directory/'motion-input.mp4'
    subprocess.run([sys.executable, str(ROOT/'src/footylevels/media_limits.py'), str(ffmpeg),
        '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-threads', '2',
        '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:v:0', '-an',
        '-vf', f'trim=start_frame={segment.start_frame}:end_frame={segment.end_frame},setpts=PTS-STARTPTS',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
        '-threads', '2', '-map_metadata', '-1', '-movflags', '+faststart', str(clip)],
        check=True, timeout=90, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    request = {'op': 'motion', 'jobId': job.id, 'inputHash': job.input_hash, 'profileVersion': MOTION_PROFILE,
        'sourcePath': str(clip), 'sourceBytes': clip.stat().st_size,
        'sourceSha256': hashlib.sha256(clip.read_bytes()).hexdigest(),
        'startFrame': segment.start_frame, 'endFrame': segment.end_frame}
    # A dropped socket stops and reaps active model stages. The durable worker
    # retries later; immutable raw/checkpoint work survives privately on the DGX.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(2700)
        stream.connect(str(socket_path))
        send(stream, request)
        response = receive(stream)
    if response.get('status') != 'ready' or response.get('profileVersion') != MOTION_PROFILE:
        raise ValueError('Motion unavailable')
    engine = Engine.model_validate(response['engine'])
    contract = PlaybackContract(topology_version=response['topologyVersion'], encoding='motionBuffers')
    cache = Path(response['directory'])
    allowed = socket_path.parent/'motion-cache'
    if (not cache.is_absolute() or not cache.is_relative_to(allowed) or cache.name != 'motion'
            or len(cache.relative_to(allowed).parts) != 2
            or any(p.is_symlink() for p in [cache, cache.parent])):
        raise ValueError('Motion cache location')
    files = response['files']
    if not isinstance(files, list) or not 3 <= len(files) <= 30:
        raise ValueError('Motion artifact count')
    names = set()
    for item in files:
        name = item['name']
        if (not isinstance(name, str) or Path(name).name != name or name in names
                or name in {'.', '..'} or not 0 < item['byteSize'] <= 52428800):
            raise ValueError('Motion artifact name')
        names.add(name)
        path = cache/name
        if (not path.is_file() or path.is_symlink() or path.stat().st_size != item['byteSize']
                or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']):
            raise ValueError('Motion artifact changed')
        shutil.copyfile(path, directory/'artifacts'/name)
    manifest_path = directory/'artifacts/sequence.json'
    sequence = json.loads(manifest_path.read_text())
    count = segment.end_frame-segment.start_frame
    valid = validate_sequence(sequence, directory/'artifacts', names, count, response['topologyVersion'])
    for i, frame in enumerate(sequence['frames']):
        frame.update(sourceFrameIndex=segment.start_frame+i, sourceTimestamp=(segment.start_frame+i)/30)
    sequence.update(schemaVersion=1, profileVersion=MOTION_PROFILE, jobId=job.id, attempt=job.attempt_count,
        inputRevision=job.input_revision, inputHash=job.input_hash, sourceSha256=job.source_asset.sha256,
        sourceStartFrame=segment.start_frame, sourceEndFrame=segment.end_frame,
        sourceTimeOffset=segment.start_frame/30, playbackVideo='video.mp4',
        engine=engine.model_dump(mode='json', by_alias=True), segmentation=response['segmentation'],
        contract=contract.model_dump(mode='json', by_alias=True),
        subjectSelection='largest foreground person at each shot seed, persistent SAM 3 track within shot',
        groundAligned=False, scale='estimated monocular metres; no calibration',
        optionalAssets={'ball': 'not requested by this body-only profile', 'ground': 'not calibrated'},
        previews=[{'artifact': name, 'frameIndex': int(name[8:12]),
            'timestamp': int(name[8:12])/30, 'sourceFrameIndex': segment.start_frame+int(name[8:12]),
            'sourceTimestamp': (segment.start_frame+int(name[8:12]))/30} for name in sorted(names) if name.startswith('preview-')])
    manifest_path.write_text(json.dumps(sequence, allow_nan=False))
    shutil.copyfile(clip, directory/'artifacts/video.mp4')
    names.add('video.mp4')
    prefix = f'users/{job.owner_id}/projects/{job.project_id}/reconstruction/{job.id}/{job.attempt_count}/'
    artifacts = []
    for name in sorted(names):
        path = directory/'artifacts'/name
        content_type = ('application/json' if name.endswith('.json') else 'image/jpeg' if name.endswith('.jpg')
                        else 'video/mp4' if name.endswith('.mp4') else 'application/gzip' if name.endswith('.gz')
                        else 'application/octet-stream')
        artifacts.append(AssetRef(bucket='private-media', object_key=prefix+name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), byte_size=path.stat().st_size, content_type=content_type))
    result = ReconstructionRecord(id=job.id, job_id=job.id, owner_id=job.owner_id, project_id=job.project_id,
        source_asset=job.source_asset, input_revision=job.input_revision, profile_version=MOTION_PROFILE,
        mode='motion', outcome='ready' if valid == count else 'partial', engine=engine, contract=contract,
        artifacts=artifacts, requested_frame_count=count, reconstructed_frame_count=valid, missing_frame_count=count-valid)
    (directory/'result.json').write_text(result.model_dump_json(by_alias=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    job = JobRecord.model_validate_json((args.directory/'job.json').read_bytes())
    if job.profile_version == MOTION_PROFILE:
        motion(args.directory, args.socket, args.ffmpeg)
    else:
        keyframes(args.directory, args.socket, args.ffmpeg)
