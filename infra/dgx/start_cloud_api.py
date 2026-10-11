#!/usr/bin/env python3
"""Independent authenticated API on loopback, until FP-39 integrates model routes."""
import argparse
import sys
import subprocess
import threading
import signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cloud_config import load

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "demo"])
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    load(args.environment)
    import uvicorn
    # One selected deployment owns one API and one serial CPU worker. Its cloud
    # child uses the same scoped environment; decoders receive a scrubbed one.
    server = uvicorn.Server(uvicorn.Config("api.cloud:app_factory", factory=True, host="127.0.0.1",
                                         port=args.port, workers=1, access_log=False))
    # Uvicorn restores/re-raises captured signals after graceful shutdown. The
    # default SIGTERM action can otherwise terminate Python before our finally
    # reaps the child, leaving its environment lock held by an orphan worker.
    def request_stop(signum, frame):
        server.should_exit = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, request_stop)
    worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name("worker_process.py")),
        sys.executable, str(Path(__file__).with_name("run_media_worker.py")), args.environment])
    stopped = threading.Event()
    def supervise():
        while not stopped.wait(2):
            if worker.poll() is not None:
                print("Media worker exited; stopping the deployment", flush=True)
                server.should_exit = True
                return
    monitor = threading.Thread(target=supervise, daemon=True)
    monitor.start()
    try:
        server.run()
    finally:
        stopped.set()
        monitor.join(timeout=3)
        if worker.poll() is None:
            worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
