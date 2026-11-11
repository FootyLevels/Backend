"""Private local settings for the lesson CLI, outside every repository.

Reads ``$LESSON_ENV_FILE`` or ``~/.config/footylevels/lesson.env``: ``NAME=value`` lines, ``#``
comments. Only GEMINI_API_KEY, GOOGLE_API_KEY, GEMINI_MODEL and LESSON_GPU_HOST are taken, and
never over a value already in the environment. The file must belong to you and be private
(mode 600); a readable file is refused rather than trusted. The GPU host stays out of git this way.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

NAMES = ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_MODEL', 'LESSON_GPU_HOST')
DEFAULT = Path.home() / '.config' / 'footylevels' / 'lesson.env'


class EnvError(RuntimeError):
    pass


def load(path: Path | None = None) -> list[str]:
    """Set the allowed names that are missing from the environment; return which were set."""
    path = Path(path or os.environ.get('LESSON_ENV_FILE') or DEFAULT).expanduser()
    if not path.is_file():
        return []
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise EnvError(f'{path} must be private to you (chmod 600).')
    loaded = []
    for line in path.read_text().splitlines():
        name, separator, value = line.strip().partition('=')
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if separator and name in NAMES and value and not os.environ.get(name):
            os.environ[name] = value
            loaded.append(name)
    return loaded
