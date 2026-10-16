#!/usr/bin/env python3
"""Reap one model stage's process group on timeout, stop or parent loss."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


def run(command, *, timeout, directory):
    parent = os.getppid()
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    process = subprocess.Popen(command, start_new_session=True)
    started = time.monotonic()
    scanned = started-1
    try:
        while process.poll() is None:
            if stopped.wait(.1) or os.getppid() != parent or parent == 1:
                return 1
            if time.monotonic() - started > timeout:
                return 1
            # Bound decoded frames, raw checkpoints and logs as well as outputs.
            if time.monotonic()-scanned >= 1:
                scanned = time.monotonic()
                if sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) > 2*1024**3:
                    return 1
        return process.returncode
    finally:
        # Descendants can outlive an already-exited stage leader. Always signal
        # the owned group, never a GPU number or another user's process.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 0 < args.timeout <= 2700 or not args.command:
        parser.error('Expected a bounded command')
    raise SystemExit(run(args.command, timeout=args.timeout, directory=args.directory))
