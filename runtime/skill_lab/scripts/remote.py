"""Sync this project or its outputs over existing SSH authentication.

Never copies keys, .env files, virtualenvs, or checkpoints. No remote deletions.
"""
import argparse
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["push", "pull", "tunnel"])
    parser.add_argument("host", help="SSH alias or user@host")
    parser.add_argument("--directory", default="skill-lab", help="Relative to the remote home directory")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--remote-port", type=int, default=8000)
    args = parser.parse_args()
    # rsync invokes a remote shell: accept only a conservative path/host alphabet.
    import re
    if not re.fullmatch(r"[\w.@-]+", args.host) or args.host.startswith("-"):
        parser.error("Use a simple SSH alias or user@host")
    if not re.fullmatch(r"[\w/-]+", args.directory) or ".." in args.directory.split("/") or args.directory.startswith("/"):
        parser.error("Directory must be a simple path relative to the remote home")
    if not all(1 <= p <= 65535 for p in [args.port, args.remote_port]):
        parser.error("Invalid TCP port")
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if args.action == "push":
        subprocess.run([*ssh, args.host, f"mkdir -p {shlex.quote(args.directory)}"], check=True)
        exclusions = [".git", ".venv*", "node_modules", ".scratch", "vendor", "checkpoints",
                      ".env", ".media", "__pycache__", ".pytest_cache", "test-results", "playwright-report"]
        subprocess.run(["rsync", "-az", *[f"--exclude={x}" for x in exclusions],
                        str(ROOT) + "/", f"{args.host}:{args.directory}/"], check=True)
    elif args.action == "pull":
        (ROOT / "data").mkdir(exist_ok=True)
        subprocess.run(["rsync", "-az", f"{args.host}:{args.directory}/data/", str(ROOT / "data") + "/"], check=True)
    else:
        print(f"Open http://127.0.0.1:{args.port}; keep this terminal running.", flush=True)
        subprocess.run([*ssh, "-o", "ExitOnForwardFailure=yes", "-N", "-L",
                        f"127.0.0.1:{args.port}:127.0.0.1:{args.remote_port}", args.host], check=True)


if __name__ == "__main__":
    main()
