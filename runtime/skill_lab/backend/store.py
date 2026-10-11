"""Small, atomic on-disk project store for a single local API process."""
import json
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("SKILL_LAB_DATA", ROOT / "data")).resolve()
LOCK = threading.RLock()


def project_dir(project_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", project_id):
        raise ValueError("Invalid project ID")
    return DATA / project_id


def read(project_id: str) -> dict:
    with LOCK:
        return json.loads((project_dir(project_id) / "project.json").read_text())


def save(project: dict) -> None:
    with LOCK:
        directory = project_dir(project["id"])
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "project.json.tmp"
        tmp.write_text(json.dumps(project, indent=2, allow_nan=False))
        tmp.replace(directory / "project.json")


def asset_url(project_id: str, relative: str) -> str:
    return f"/assets-data/{project_id}/{relative}"


def list_projects() -> list[dict]:
    DATA.mkdir(parents=True, exist_ok=True)
    with LOCK:
        return sorted([json.loads(p.read_text()) for p in DATA.glob("*/project.json")],
                      key=lambda p: p["createdAt"], reverse=True)
