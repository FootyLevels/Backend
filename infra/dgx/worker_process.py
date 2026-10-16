#!/usr/bin/env python3
"""Bind an owned cloud worker to its API supervisor before exec on Linux."""
import ctypes
import os
import signal
import sys


def main():
    if sys.platform == "linux":
        parent = os.getppid()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Could not bind cloud worker lifetime")
        if parent == 1 or os.getppid() != parent:
            raise SystemExit(1)
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
