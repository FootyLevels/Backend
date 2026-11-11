"""Load only this user's private per-environment runtime configuration."""
import json
import os
from pathlib import Path
import stat

PROJECTS = {"dev": "bwzxrvkxfubnmuieljoj", "demo": "skzgjwbkadiazfvuhtts"}


def load(environment):
    if environment not in PROJECTS:
        raise ValueError("Select dev or demo")
    directory = Path.home() / ".config/footylevels" / environment
    def private(path):
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Configuration must be private to its owner")
        return json.loads(path.read_text())
    config = private(directory / "runtime.json")
    project = f"footylevels-{environment}-20261009"
    key_path = directory / "google-service-account.json"
    key = private(key_path)
    if (config.get("FIREBASE_PROJECT_ID") != project or config.get("FOOTYLEVELS_ENVIRONMENT") != environment
            or config.get("SUPABASE_URL") != f"https://{PROJECTS[environment]}.supabase.co"
            or key.get("project_id") != project
            or key.get("client_email") != f"footylevels-api-{environment}@{project}.iam.gserviceaccount.com"):
        raise ValueError("Runtime environment mismatch")
    allowed = ["FIREBASE_PROJECT_ID", "SUPABASE_URL", "SUPABASE_SECRET_KEY"]
    for name in allowed:
        os.environ[name] = config[name]
    # Lesson authoring (optional until lessons are enabled). The Gemini key is read
    # only by the worker's parent process; the GPU adapter child never inherits it.
    for name in ["GEMINI_API_KEY", "GEMINI_MODEL", "LESSON_MAX_SECONDS", "LESSON_DAILY_LIMIT"]:
        if config.get(name) not in (None, ""):
            os.environ[name] = str(config[name])
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_path)
    return config
