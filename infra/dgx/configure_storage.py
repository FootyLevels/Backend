#!/usr/bin/env python3
"""Run interactively on DGX; never pass a server secret as a command argument."""
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile

PROJECTS = {"dev": "bwzxrvkxfubnmuieljoj", "demo": "skzgjwbkadiazfvuhtts"}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in PROJECTS:
        raise SystemExit("Usage: python3 configure_storage.py dev|demo")
    if not sys.stdin.isatty():
        raise SystemExit("Use an interactive terminal; secret entry must not echo.")
    environment = sys.argv[1]
    secret = getpass.getpass(f"Supabase {environment} secret key (hidden): ").strip()
    if not secret.startswith("sb_secret_") or len(secret) < 30:
        raise SystemExit("Expected a Supabase secret API key beginning sb_secret_.")
    os.umask(0o077)
    directory = Path.home() / ".config/footylevels" / environment
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    configuration = {
        "FOOTYLEVELS_ENVIRONMENT": environment,
        "FIREBASE_PROJECT_ID": f"footylevels-{environment}-20261009",
        "GOOGLE_CLOUD_PROJECT": f"footylevels-{environment}-20261009",
        "GOOGLE_APPLICATION_CREDENTIALS": str(directory / "google-service-account.json"),
        "SUPABASE_URL": f"https://{PROJECTS[environment]}.supabase.co",
        "SUPABASE_SECRET_KEY": secret,
        "SUPABASE_MEDIA_BUCKET": "private-media",
        "SUPABASE_REFERENCE_BUCKET": "reference-assets",
    }
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".runtime-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(configuration, stream)
            stream.write("\n")
        os.replace(temporary, directory / "runtime.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Saved private {environment} configuration. No secret value displayed.")


if __name__ == "__main__":
    main()
