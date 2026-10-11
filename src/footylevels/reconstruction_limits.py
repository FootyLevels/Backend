"""Limits for the CPU adapter client; GPU weights stay in the existing runtime."""
import ctypes
import os
import resource
import signal
import sys


def main():
    parent = os.getppid()
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Could not bind adapter lifetime")
        if os.getppid() != parent or parent == 1:
            raise SystemExit(1)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_FSIZE, (52428800, 52428800))
    resource.setrlimit(resource.RLIMIT_CPU, (3600, 3600))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
