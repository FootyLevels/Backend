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
    parser.add_argument("--reconstruction-adapter", type=Path,
                        help="Explicitly enable the verified private model adapter")
    args = parser.parse_args()
    if args.reconstruction_adapter:
        args.reconstruction_adapter = args.reconstruction_adapter.resolve(strict=True)
        if not args.reconstruction_adapter.is_file() or args.reconstruction_adapter.suffix != ".py":
            parser.error("Reconstruction adapter must be a local Python file")
    load(args.environment)
    import uvicorn
    # The selected deployment owns its serial workers. Their cloud parents use
    # scoped credentials; decoders and model adapters receive a scrubbed env.
    server = uvicorn.Server(uvicorn.Config("api.cloud:app_factory", factory=True, host="127.0.0.1",
                                         port=args.port, workers=1, access_log=False))
    # Uvicorn restores/re-raises captured signals after graceful shutdown. The
    # default SIGTERM action can otherwise terminate Python before our finally
    # reaps the child, leaving its environment lock held by an orphan worker.
    def request_stop(signum, frame):
        server.should_exit = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, request_stop)
    commands = [[sys.executable, str(Path(__file__).with_name("run_media_worker.py")), args.environment]]
    if args.reconstruction_adapter:
        commands.append([sys.executable, str(Path(__file__).with_name("run_reconstruction_worker.py")),
            args.environment, "--adapter", str(args.reconstruction_adapter)])
    workers = []
    stopped = threading.Event()
    def supervise():
        while not stopped.wait(2):
            if any(worker.poll() is not None for worker in workers):
                print("An owned worker exited; stopping the deployment", flush=True)
                server.should_exit = True
                return
    monitor = None
    try:
        for command in commands:
            workers.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name("worker_process.py")), *command]))
        monitor = threading.Thread(target=supervise, daemon=True)
        monitor.start()
        server.run()
    finally:
        stopped.set()
        if monitor:
            monitor.join(timeout=3)
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
        for worker in workers:
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
