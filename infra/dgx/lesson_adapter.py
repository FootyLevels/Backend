#!/usr/bin/env python3
"""Credential-free GPU step of a lesson job: 3D motion with ball tracking, then measurements.

Runs as the lesson worker's child with an empty environment: no Gemini key, cloud credential,
lease token or signed URL reaches it. Input: ``job.json`` and ``input.mp4`` (the project's
prepared 30 fps media) in DIRECTORY. Output: ``DIRECTORY/work`` laid out like a CLI work folder
(``upload.mp4``, ``project/motion``, ``part-*/motion``, ``cuts.json``, ``reconstruction.json``,
``analysis/``) and ``DIRECTORY/result.json`` with the engine and frame counts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'runtime/skill_lab'))
from backend.model_bridge import receive, send  # noqa: E402
from footylevels.model_requests import LESSON_MOTION_PROFILE, LESSON_PROFILE, lesson_input_hash  # noqa: E402
from footylevels.records import Engine, JobRecord  # noqa: E402

MOTION_FILES = ('sequence.json', 'tracking.json')


def request_motion(socket_path, job, clip, start, end):
    """Ask the warm runtime for motion of ``clip`` (frames start..end of the source); return its folder."""
    request = {'op': 'motion', 'jobId': job.id, 'inputHash': job.input_hash, 'profileVersion': LESSON_MOTION_PROFILE,
               'sourcePath': str(clip), 'sourceBytes': clip.stat().st_size,
               'sourceSha256': hashlib.sha256(clip.read_bytes()).hexdigest(), 'startFrame': start, 'endFrame': end}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(3300)
        stream.connect(str(socket_path))
        send(stream, request)
        response = receive(stream)
    if response.get('status') != 'ready' or response.get('profileVersion') != LESSON_MOTION_PROFILE:
        raise ValueError('Motion unavailable')
    cache = Path(response['directory'])
    allowed = socket_path.parent / 'motion-cache'
    if (not cache.is_absolute() or not cache.is_relative_to(allowed) or cache.name != 'motion'
            or len(cache.relative_to(allowed).parts) != 2 or any(p.is_symlink() for p in [cache, cache.parent])):
        raise ValueError('Motion cache location')
    files = response['files']
    if not isinstance(files, list) or not 4 <= len(files) <= 120:
        raise ValueError('Motion artifact count')
    return response, cache, files


def copy_motion(cache, files, destination):
    destination.mkdir(parents=True, exist_ok=True)
    names = set()
    for item in files:
        name = item['name']
        if (not isinstance(name, str) or Path(name).name != name or name in names or name in {'.', '..'}
                or not 0 < item['byteSize'] <= 52428800):
            raise ValueError('Motion artifact name')
        names.add(name)
        path = cache / name
        if (not path.is_file() or path.is_symlink() or path.stat().st_size != item['byteSize']
                or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']):
            raise ValueError('Motion artifact changed')
        shutil.copyfile(path, destination / name)
    if not all(name in names for name in MOTION_FILES):
        raise ValueError('Motion lacks its sequence or tracking')


def lesson(directory, socket_path, ffmpeg):
    from lesson_pipeline import analysis, cuts, remote
    job = JobRecord.model_validate_json((directory / 'job.json').read_bytes())
    source = directory / 'input.mp4'
    model = json.loads((directory / 'lesson.json').read_text())['model']
    if (job.kind != 'buildLesson' or job.profile_version != LESSON_PROFILE
            or lesson_input_hash(job.source_asset, model) != job.input_hash
            or source.stat().st_size != job.source_asset.byte_size
            or hashlib.sha256(source.read_bytes()).hexdigest() != job.source_asset.sha256):
        raise ValueError('Unsupported or changed lesson input')
    work = directory / 'work'
    work.mkdir(mode=0o700)
    shutil.copyfile(source, work / 'upload.mp4')
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-count_packets', '-select_streams', 'v:0',
                                       '-show_entries', 'stream=nb_read_packets', '-of', 'json', str(source)],
                                      check=True, capture_output=True, text=True, timeout=60).stdout)
    count = int(probe['streams'][0]['nb_read_packets'])
    found = cuts.detect_cuts(work / 'upload.mp4')
    (work / 'cuts.json').write_text(json.dumps(found))
    # The bridge accepts exactly this staging name; each request copies it before running.
    clip = directory / 'lesson-input.mp4'
    shutil.copyfile(source, clip)
    response, cache, files = request_motion(socket_path, job, clip, 0, count)
    copy_motion(cache, files, work / 'project' / 'motion')
    record = {'parts': []}
    for a, b in remote.segments_needing_rerun(work / 'project', found):
        # A missed cut confuses the tracker; the lost stretch re-runs as its own clip.
        remote.cut_frames(work / 'upload.mp4', a, b, clip)
        _, part_cache, part_files = request_motion(socket_path, job, clip, a, b)
        copy_motion(part_cache, part_files, work / f'part-{a}' / 'motion')
        record['parts'].append({'dir': f'part-{a}', 'start': a})
    (work / 'reconstruction.json').write_text(json.dumps(record))
    parts = [(work / p['dir'], p['start']) for p in record['parts']]
    summary = analysis.analyze(work / 'project', work / 'analysis', found, parts, log=lambda *_: None)
    engine = Engine.model_validate(response['engine'])
    (directory / 'result.json').write_text(json.dumps({
        'engine': engine.model_dump(mode='json', by_alias=True), 'frames': summary['frames'],
        'validFrames': summary['valid'], 'ballFrames': summary['ballFrames'], 'parts': len(parts)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    lesson(args.directory, args.socket, args.ffmpeg)
