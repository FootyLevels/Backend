"""Apply Linux resource limits in a disposable child, then replace it with FFmpeg.

Separate from the API process: do not use preexec_fn in a multithreaded server.
The cloud worker supplies a scrubbed environment without server credentials.
"""
import os
import ctypes
import resource
import signal
import sys


def main():
    if sys.platform.startswith("linux"):
        parent = os.getppid()
        # A killed supervisor must not leave a detached decoder writing staging
        # files. Check the race where it exited before prctl was installed.
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL) != 0 or parent == 1 or os.getppid() != parent:
            raise SystemExit(1)
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_CPU, (150, 150))
        resource.setrlimit(resource.RLIMIT_FSIZE, (52_428_800, 52_428_800))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
