"""Regression for orphaned workers on graceful and forced API shutdown."""
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest


@pytest.mark.parametrize('shutdown', ['graceful', 'forced'])
def test_api_shutdown_releases_owned_worker(tmp_path, shutdown):
    if shutdown == 'forced' and sys.platform != 'linux':
        pytest.skip('Linux parent-death signal is a deployment property')
    source = Path(__file__).parents[1] / 'infra/dgx'
    scripts = tmp_path / 'infra/dgx'
    scripts.mkdir(parents=True)
    for name in ['start_cloud_api.py', 'worker_process.py']:
        shutil.copyfile(source / name, scripts / name)
    (scripts / 'cloud_config.py').write_text('def load(environment): pass\n')
    pid_file = tmp_path / 'worker.pid'
    (scripts / 'run_media_worker.py').write_text(
        f'import os, pathlib, time\npathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api/__init__.py').write_text('')
    (tmp_path / 'api/cloud.py').write_text(
        'from fastapi import FastAPI\ndef app_factory():\n app = FastAPI()\n app.get("/api/health")(lambda: {"status": "ok"})\n return app\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    parent = subprocess.Popen([sys.executable, str(scripts / 'start_cloud_api.py'), 'dev', '--port', str(port)],
        cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child = None
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=0.2) as response:
                    assert response.status == 200
                child = int(pid_file.read_text())
                break
            except (OSError, ValueError): time.sleep(0.05)
        else: pytest.fail('Test API did not start')
        parent.send_signal(signal.SIGTERM if shutdown == 'graceful' else signal.SIGKILL)
        parent.wait(timeout=10)
        for _ in range(100):
            try:
                os.kill(child, 0)
                if sys.platform == 'linux' and Path(f'/proc/{child}/stat').read_text().split()[2] == 'Z':
                    break  # dead and no longer holds any descriptors/locks
            except (ProcessLookupError, FileNotFoundError): break
            time.sleep(0.05)
        else: pytest.fail('API exit left its worker running')
        if shutdown == 'graceful':
            assert parent.returncode == 0
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        if child is not None:
            try: os.kill(child, signal.SIGKILL)
            except ProcessLookupError: pass
