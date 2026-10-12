#!/usr/bin/env python3
"""Install/start/inspect the owned Development user services; no credentials here."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import urllib.request

MARKER = "# Managed by FootyLevels infra/dgx/services.py\n"
GPU = "GPU-bf91a965-c736-7317-95ff-469e3d182eeb"
SERVICES = ("model", "api", "tunnel")


def units():
    result = {}
    for name, retry in (("model", 60), ("api", 15), ("tunnel", 30)):
        result[f"footylevels-{name}.service"] = MARKER + f"""[Unit]
Description=FootyLevels Development {name}
PartOf=footylevels.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/.local/share/footylevels/services/current/infra/dgx/services.py run {name}
Restart=always
RestartSec={retry}
KillMode=control-group
TimeoutStopSec=45
UMask=0077
Environment=HOME=%h
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
# Do not put arbitrary SDK/model output, URLs or credentials in the journal.
StandardOutput=null
StandardError=null
"""
    result["footylevels.target"] = MARKER + """[Unit]
Description=FootyLevels Development services
Wants=footylevels-model.service footylevels-api.service footylevels-tunnel.service footylevels-health.timer

[Install]
WantedBy=default.target
"""
    result["footylevels-health.service"] = MARKER + """[Unit]
Description=FootyLevels bounded local health snapshot
PartOf=footylevels.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 %h/.local/share/footylevels/services/current/infra/dgx/services.py snapshot
TimeoutStartSec=35
UMask=0077
Environment=HOME=%h
StandardOutput=null
StandardError=null
"""
    result["footylevels-health.timer"] = MARKER + """[Unit]
Description=FootyLevels local health every minute
PartOf=footylevels.target

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5
Unit=footylevels-health.service
"""
    return result


def install(home):
    directory = home / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    desired = units()
    # Check every destination before touching any of them; do not replace an
    # unrelated operator unit or follow an arbitrary symlink.
    for name in desired:
        path = directory / name
        if path.is_symlink() or (path.exists() and not path.read_text().startswith(MARKER)):
            raise RuntimeError("Unmanaged service unit exists")
    for name, content in desired.items():
        path = directory / name
        temporary = path.with_suffix(path.suffix + ".next")
        with temporary.open("x") as output:
            output.write(content)
        temporary.chmod(0o600)
        temporary.replace(path)
    # Installing definitions alone never stops, starts or enables anything.


def launch_command(home, name):
    base = home / ".local/share/footylevels"
    env = {"HOME": str(home), "PATH": f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
           "LANG": "C.UTF-8"}
    dns = base / "fp50-dns/with_network_dns.sh"
    if name == "model":
        release = (base / "runtime/current").resolve(strict=True)
        if not re.fullmatch(r"[0-9a-f]{7,40}", release.name):
            raise RuntimeError("Model release must be a commit")
        env.update(PORT="8013", FOOTYLEVELS_GPU_UUID=GPU,
                   FOOTYLEVELS_RUNTIME_REVISION=release.name)
        command = ["bash", str(release / "infra/dgx/start_runtime.sh")]
    elif name == "api":
        release = (base / "cloud-api/current").resolve(strict=True)
        command = [str(base / "cloud-api/venv/bin/python"),
                   str(release / "infra/dgx/start_cloud_api.py"), "dev", "--port", "8010",
                   "--reconstruction-adapter", str(release / "infra/dgx/reconstruction_adapter.py")]
    elif name == "tunnel":
        command = [str(home / ".local/bin/ngrok"), "http", "8010", "--url",
                   "https://ranting-pesticide-thespian.ngrok-free.dev", "--host-header=localhost"]
    else:
        raise ValueError("Unknown service")
    locks = base / "services/locks"
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    locks.chmod(0o700)
    # The lock survives exec. The service cgroup owns all namespace descendants,
    # cloud workers and detached inference groups, even on an abrupt parent exit.
    return ["/usr/bin/flock", "--no-fork", "--nonblock", str(locks / (name + ".lock")),
            "bash", str(dns), *command], env


def unit_state(name):
    raw = subprocess.run(["systemctl", "--user", "show", f"footylevels-{name}.service",
                          "--property=ActiveState,SubState,NRestarts,MainPID,Result"],
                         capture_output=True, text=True, timeout=5, check=True).stdout
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


def local_health(port):
    # Never follow redirects or use an ambient proxy for a local health probe.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
        return json.loads(response.read(32768))


def snapshot(home):
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "environment": "dev", "services": {}}
    for name in SERVICES:
        try:
            report["services"][name] = unit_state(name)
        except Exception:
            report["services"][name] = {"ActiveState": "unknown"}
    try:
        report["apiHealthy"] = local_health(8010).get("service") == "FootyLevels"
    except Exception:
        report["apiHealthy"] = False
    try:
        runtime = local_health(8013).get("runtime", {})
        report["bodyModelLoaded"] = runtime.get("bodyModelLoaded") is True
        report["modelOwned"] = runtime.get("owner") == "FootyLevels"
    except Exception:
        report["bodyModelLoaded"] = report["modelOwned"] = False
    # This is local liveness/readiness, not cloud access, a heartbeat from an
    # executing job, public-tunnel delivery, GPU reservation or complete E2E.
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "run", "snapshot", "status"])
    parser.add_argument("service", nargs="?", choices=SERVICES)
    args = parser.parse_args()
    home = Path.home()
    os.umask(0o077)
    if args.action == "install":
        install(home)
    elif args.action == "run":
        if not args.service:
            parser.error("run requires a service")
        # Refuse an existing listener before starting API workers or loading GPU
        # weights. Singleton locks handle concurrent launches of our own wrapper.
        port = {"api": 8010, "model": 8013}.get(args.service)
        if port:
            with socket.socket() as probe:
                # Match Uvicorn's bind semantics: a closed connection in
                # TIME_WAIT is not a competing live listener after a crash.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
        command, env = launch_command(home, args.service)
        os.execve(command[0], command, env)
    else:
        report = snapshot(home)
        if args.action == "status":
            print(json.dumps(report, sort_keys=True))
        else:
            path = home / ".local/share/footylevels/services/health.json"
            temporary = path.with_suffix(".next")
            temporary.write_text(json.dumps(report, sort_keys=True) + "\n")
            temporary.chmod(0o600)
            temporary.replace(path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No raw subprocess output, environment, SDK exception or signed URL.
        print("FootyLevels service operation failed; inspect safe status and runbook", file=sys.stderr)
        raise SystemExit(1) from None
