"""Runs whole-video 3D reconstruction on the GPU server and fetches the result.

Talks to the private ``runtime/skill_lab`` API bound to loopback on the GPU host,
over SSH: no port is exposed. Needs key-based SSH access (``ssh -o BatchMode=yes`` works).
The backend may belong to another account on that host: videos go in through its API, and results
are read from its data folder, which only has to be readable.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path

MAX_HEIGHT = 1280          # taller videos ran out of GPU memory while tracking; 720p tracks well


class RemoteError(RuntimeError):
    pass


class Server:
    def __init__(self, host: str, port: int = 8012, root: str = 'skill-lab', data_dir: str | None = None):
        """``root`` is the backend's folder on the server: absolute, or relative to the SSH user's home."""
        if not host or host.startswith('-'):
            raise RemoteError('Give the GPU host as user@host')
        self.host, self.port, self.root_name = host, int(port), root
        self.data_dir = data_dir
        self._home = None

    @property
    def home(self) -> str:
        """The SSH user's home on the server."""
        if self._home is None:
            self._home = self.ssh('printf %s "$HOME"').strip()
        return self._home

    @property
    def root(self) -> str:
        """Absolute path of the backend folder on the server; its projects are in ``root/data``."""
        return self.root_name if self.root_name.startswith('/') else f'{self.home}/{self.root_name}'

    @property
    def data(self) -> str:
        """The runtime's SKILL_LAB_DATA, which need not live under its source folder."""
        if self.data_dir is None:
            return f'{self.root}/data'
        if self.data_dir.startswith('/'):
            return self.data_dir
        return f'{self.home}/{self.data_dir.removeprefix("~/")}'

    def ssh(self, command: str, timeout=120) -> str:
        done = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', self.host, command],
                              capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise RemoteError(f'ssh {self.host} failed: {done.stderr.strip()[-400:]}')
        return done.stdout

    def api(self, method: str, path: str, form_file: str | None = None) -> dict:
        args = ['curl', '-sS', '-f', '-X', method]
        if form_file:
            args += ['-F', f'file=@{form_file};type=video/mp4']
        args.append(f'http://127.0.0.1:{self.port}{path}')
        out = self.ssh(' '.join(shlex.quote(a) for a in args), timeout=600)
        return json.loads(out) if out.strip() else {}

    def upload(self, video: Path, name: str) -> str:
        remote_dir = f'{self.home}/.cache/lesson-pipeline'          # the SSH user's own, always writable
        self.ssh(f'mkdir -p {shlex.quote(remote_dir)}')
        remote = f'{remote_dir}/{name}'
        subprocess.run(['scp', '-q', str(video), f'{self.host}:{remote}'], check=True, timeout=600)
        project = self.api('POST', '/api/projects', form_file=remote)
        if 'id' not in project:
            raise RemoteError(f'upload refused: {project}')
        return project['id']

    def start_motion(self, project_id: str) -> None:
        self.api('POST', f'/api/projects/{project_id}/motion')

    def status(self, project_id: str) -> dict:
        return self.api('GET', f'/api/projects/{project_id}').get('motion') or {}

    def wait(self, project_id: str, log=print, poll=30, limit=3 * 3600) -> dict:
        start, last = time.time(), None
        while time.time() - start < limit:
            m = self.status(project_id)
            line = f"{m.get('status')} · {m.get('stage', '')}"
            if line != last:
                log(f'  {project_id}: {line}')
                last = line
            # "partial" usually means only the optional ball mesh failed; the body motion is there.
            if m.get('status') in ('complete', 'partial', 'failed'):
                return m
            time.sleep(poll)
        raise RemoteError(f'{project_id} did not finish within {limit // 60} minutes')

    def download(self, project_id: str, dest: Path) -> Path:
        motion = Path(dest) / 'motion'
        motion.mkdir(parents=True, exist_ok=True)
        src = f'{self.host}:{self.data}/{project_id}/motion/'
        subprocess.run(['rsync', '-az', '--include=sequence.json', '--include=faces*.bin', '--include=body-*.bin.gz',
                        '--include=tracking.json', '--exclude=*', src, str(motion) + '/'], check=True, timeout=1800)
        if not (motion / 'sequence.json').exists():
            raise RemoteError(f'{project_id} has no motion yet')
        return Path(dest)


def web_ready_copy(video: Path, out: Path) -> Path:
    """The upload copy: at most 1280 px tall, no audio. Timing is unchanged."""
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                       'stream=height', '-of', 'json', str(video)], capture_output=True, text=True).stdout)
    height = probe['streams'][0]['height']
    vf = ['-vf', f'scale=-2:{MAX_HEIGHT}'] if height > MAX_HEIGHT else []
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(video), *vf, '-an', '-c:v', 'libx264', '-crf', '20',
                    '-preset', 'fast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(out)], check=True)
    return out


def cut_frames(video: Path, start: int, end: int, out: Path) -> Path:
    """Frames [start, end) as their own clip, frame-exact."""
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(video), '-vf',
                    f"select='between(n,{start},{end - 1})',setpts=N/FRAME_RATE/TB", '-an', '-c:v', 'libx264',
                    '-crf', '18', '-preset', 'fast', str(out)], check=True)
    return out


def segments_needing_rerun(project: Path, cuts_s: list[float], min_valid=0.5) -> list[tuple[int, int]]:
    """Segments (between our cuts) where the tracker mostly lost the player, e.g. after a cut the
    backend missed. Re-running them as their own clip fixes that."""
    seq = json.loads((project / 'motion' / 'sequence.json').read_text())
    fps, n = float(seq['fps']), seq['frameCount']
    valid = [bool(f.get('valid')) for f in seq['frames']]
    bounds = [0] + [int(round(c * fps)) for c in cuts_s] + [n]
    bad = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a >= int(fps) and sum(valid[a:b]) < min_valid * (b - a):
            bad.append((a, b))
    return bad


def reconstruct(video: Path, work: Path, server: Server, cuts_s: list[float], reuse: str | None = None, log=print) -> dict:
    """Uploads (or reuses) a project, waits for its motion, downloads it, and repairs segments the
    tracker lost. Returns the record saved as ``work/reconstruction.json``."""
    work.mkdir(parents=True, exist_ok=True)
    upload = web_ready_copy(video, work / 'upload.mp4')
    project_id = reuse
    if not project_id:
        project_id = server.upload(upload, f'{work.name}.mp4')
        server.start_motion(project_id)
        log(f'  uploaded as {project_id}; reconstructing')
    status = server.wait(project_id, log=log)
    project = server.download(project_id, work / 'project')
    record = {'projectId': project_id, 'status': status.get('status'), 'parts': []}
    for a, b in segments_needing_rerun(project, cuts_s):
        log(f'  frames {a}-{b - 1} lost the player; re-running them as their own clip')
        clip = cut_frames(upload, a, b, work / f'part-{a}.mp4')
        part_id = server.upload(clip, f'{work.name}-part-{a}.mp4')
        server.start_motion(part_id)
        server.wait(part_id, log=log)
        server.download(part_id, work / f'part-{a}')
        record['parts'].append({'projectId': part_id, 'dir': f'part-{a}', 'start': a})
    (work / 'reconstruction.json').write_text(json.dumps(record, indent=1))
    return record
