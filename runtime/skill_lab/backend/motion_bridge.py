"""Private, resumable motion work on the same executor as warm body requests."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

PROFILE = 'body-motion-30fps-v1'
ROOT = Path(__file__).resolve().parents[1]


class MotionTasks:
    def __init__(self, root, executor, metadata, infer, *, input_root):
        self.root, self.input_root = Path(root), Path(input_root).resolve()
        self.executor, self.metadata, self.infer = executor, metadata, infer
        self.lock, self.tasks, self.calls = threading.RLock(), {}, 0
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def validate(self, request):
        keys = {'op', 'jobId', 'inputHash', 'profileVersion', 'sourcePath', 'sourceSha256',
                'sourceBytes', 'startFrame', 'endFrame'}
        if not isinstance(request, dict) or set(request) != keys or request['op'] != 'motion' or request['profileVersion'] != PROFILE:
            raise ValueError('Motion request schema')
        for field in ['jobId', 'inputHash', 'sourceSha256']:
            if not isinstance(request[field], str) or not re.fullmatch(r'[a-f0-9]{64}', request[field]):
                raise ValueError('Motion identity')
        start, end, size = request['startFrame'], request['endFrame'], request['sourceBytes']
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= 1802
                or not 30 <= end-start <= 300 or type(size) is not int or not 0 < size <= 52428800):
            raise ValueError('Motion bounds')
        source = Path(request['sourcePath'])
        if not source.is_absolute() or not source.is_relative_to(self.input_root):
            raise ValueError('Motion source location')
        parts = source.relative_to(self.input_root).parts
        if (len(parts) != 3 or parts[0] not in {'dev', 'demo'}
                or not re.fullmatch(r'reconstruction-[a-z0-9_]{8}', parts[1]) or parts[2] != 'motion-input.mp4'
                or any(p.is_symlink() for p in [source, *source.parents]) or not source.is_file()
                or source.stat().st_size != size):
            raise ValueError('Motion source location')
        # A digest excludes the temporary path and attempt, so restarted durable
        # attempts can resume the same immutable source/model checkpoints.
        identity = {k: v for k, v in request.items() if k != 'sourcePath'}
        digest = hashlib.sha256(json.dumps({'input': identity, 'model': self.metadata},
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return source, digest

    def prune(self):
        directories = sorted((p for p in self.root.iterdir() if p.is_dir() and not p.is_symlink()
                              and re.fullmatch(r'[a-f0-9]{64}', p.name)), key=lambda p: p.stat().st_mtime)
        count = len(directories)
        for path in directories:
            task = self.tasks.get(path.name)
            if task and not task[0].done():
                continue
            if count < 2 and path.stat().st_mtime > time.time()-86400:
                continue
            shutil.rmtree(path)
            self.tasks.pop(path.name, None)
            count -= 1

    def submit(self, request):
        source, digest = self.validate(request)
        with self.lock:
            task = self.tasks.get(digest)
            if task and not task[0].cancelled() and not task[1].is_set() and (not task[0].done() or task[0].exception() is None):
                return task[0]
            if task and not task[0].done():
                raise RuntimeError('Previous motion stage is still stopping')
            directory = self.root / digest
            if (directory/'result.json').is_file():
                from concurrent.futures import Future
                future = Future()
                future.set_result(self.response(directory))
                stop = threading.Event()
            else:
                if not directory.exists():
                    self.prune()
                    directory.mkdir(mode=0o700)
                # Copy before enqueue: cancellation may remove the adapter's
                # staging directory immediately after its socket disconnects.
                target = directory/'video.mp4'
                if not target.exists():
                    temporary = directory/'source.tmp'
                    hasher, total = hashlib.sha256(), 0
                    with source.open('rb') as input_stream, temporary.open('wb') as output:
                        for chunk in iter(lambda: input_stream.read(1024*1024), b''):
                            total += len(chunk)
                            if total > request['sourceBytes']:
                                raise ValueError('Motion source changed')
                            hasher.update(chunk)
                            output.write(chunk)
                    if total != request['sourceBytes'] or hasher.hexdigest() != request['sourceSha256']:
                        temporary.unlink(missing_ok=True)
                        raise ValueError('Motion source changed')
                    temporary.replace(target)
                elif hashlib.sha256(target.read_bytes()).hexdigest() != request['sourceSha256']:
                    raise ValueError('Motion checkpoint source changed')
                (directory/'request.json').write_text(json.dumps({k: v for k, v in request.items() if k != 'sourcePath'}))
                stop = threading.Event()
                future = self.executor.submit(self.run, request, directory, stop)
            self.tasks[digest] = (future, stop)
            return future

    def response(self, directory):
        manifest = json.loads((directory/'result.json').read_text())
        for asset in manifest['files']:
            path = directory/'motion'/asset['name']
            if (not path.is_file() or path.is_symlink() or path.stat().st_size != asset['byteSize']
                    or hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']):
                raise ValueError('Motion checkpoint changed')
        return {**manifest, 'directory': str(directory/'motion')}

    def run(self, request, directory, stop):
        with self.lock:
            self.calls += 1
        files = self.infer(request, directory, stop)
        if stop.is_set():
            raise RuntimeError('Motion stopped')
        manifest = {'status': 'ready', 'profileVersion': PROFILE, **self.metadata, 'files': files}
        temporary = directory/'result.tmp'
        temporary.write_text(json.dumps(manifest, allow_nan=False))
        temporary.replace(directory/'result.json')
        response = self.response(directory)
        # Commit the replayable result before removing raw recovery work.
        names = {asset['name'] for asset in files}
        for path in list((directory/'motion').iterdir()):
            if path.name not in names:
                if path.is_dir(): shutil.rmtree(path)
                else: path.unlink()
        return response

    def cancel(self, future):
        with self.lock:
            for candidate, stop in self.tasks.values():
                if candidate is future:
                    stop.set()
                    candidate.cancel()

    def active(self):
        with self.lock:
            return sum(not task[0].done() for task in self.tasks.values())

    def close(self):
        with self.lock:
            for future, stop in self.tasks.values():
                if not future.done():
                    stop.set()
                    future.cancel()


def pipeline(request, directory, stop):
    from .motion import paths
    from .providers.local import LocalProvider
    settings = paths()
    count = request['endFrame']-request['startFrame']
    (directory/'project.json').write_text(json.dumps({'video': {'fps': 30, 'duration': count/30, 'frameCount': count}}))
    output = directory/'motion'
    output.mkdir(exist_ok=True, mode=0o700)
    wrapper = ROOT.parents[1]/'infra/dgx/motion_stage.py'
    stages = [(settings['segPython'], 'segment_video.py', ['--checkpoint', str(settings['segCheckpoint']), '--body-only'], 700),
              (settings['bodyPython'], 'reconstruct_motion.py', [], 1800)]
    LocalProvider.release()
    try:
        for python, script, extra, seconds in stages:
            tracking = output/'tracking.json'
            if script == 'segment_video.py' and tracking.exists():
                track = json.loads(tracking.read_text())
                if len(track['frames']) == count and all('person' in f and 'shot' in f and 'ball' in f for f in track['frames']):
                    continue
            if stop.is_set():
                raise RuntimeError('Motion stopped')
            with (output/(script+'.log')).open('a') as log:
                process = subprocess.Popen([sys.executable, str(wrapper), '--timeout', str(seconds),
                    '--directory', str(directory), str(python), '-u', str(ROOT/'scripts'/script), str(directory), *extra],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                try:
                    while process.poll() is None:
                        if stop.wait(.1):
                            process.terminate()
                            break
                    process.wait(timeout=10)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
                if stop.is_set() or process.returncode:
                    raise RuntimeError('Motion stage failed or stopped')
        sequence = json.loads((output/'sequence.json').read_text())
        if sequence['frameCount'] != count or sequence['fps'] != 30 or sequence['validBodyFrames'] < 1:
            raise ValueError('Motion does not match canonical segment')
        names = ['sequence.json', sequence['topologyFile'], *[c['file'] for c in sequence['chunks']]]
        # Tracking previews share exact canonical segment indices; the cloud
        # adapter later pins their original source frame/time in the manifest.
        for index in sorted({round(i*(count-1)/5) for i in range(6)}):
            name = f'preview-{index:04d}.jpg'
            shutil.copyfile(output/'source'/f'{index:06d}.jpg', output/name)
            names.append(name)
        files = []
        for name in names:
            path = output/name
            if Path(name).name != name or path.is_symlink() or not 0 < path.stat().st_size <= 52428800:
                raise ValueError('Unbounded motion artifact')
            files.append({'name': name, 'byteSize': path.stat().st_size,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        return files
    finally:
        # Returning to the shared executor is safe only after stage groups stop.
        # The body reload is bounded; a hung reload exits this owned runtime.
        timer = threading.Timer(180, lambda: os._exit(1))
        timer.daemon = True
        timer.start()
        try:
            LocalProvider().load()
        except Exception:
            LocalProvider.release()
        finally:
            timer.cancel()


def start_tasks(root, executor, metadata):
    from .motion import paths
    settings = paths()
    # Pin actual segmentation weights plus all reused tracking/packing code in
    # the profile configuration. Body weights/upstream stay separately declared.
    hasher = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()+PROFILE.encode())
    with settings['segCheckpoint'].open('rb') as stream:
        segmentation_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    hasher.update(segmentation_hash.encode())
    for name in ['scripts/segment_video.py', 'scripts/reconstruct_motion.py', 'backend/motion_data.py', 'backend/motion_bridge.py']:
        hasher.update((ROOT/name).read_bytes())
    metadata = {**metadata, 'engine': {**metadata['engine'], 'configHash': hasher.hexdigest()},
                'segmentation': {'model': 'Meta SAM 3', 'checkpointHash': segmentation_hash}}
    return MotionTasks(root/'motion-cache', executor, metadata, pipeline,
                       input_root=Path.home()/'.local/share/footylevels/reconstruction-worker')
