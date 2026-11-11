"""Shared test isolation."""
import pytest


@pytest.fixture(autouse=True)
def no_private_lesson_env(tmp_path_factory, monkeypatch):
    # The lesson CLI loads ~/.config/footylevels/lesson.env; tests must never see real keys or hosts.
    monkeypatch.setenv('LESSON_ENV_FILE', str(tmp_path_factory.getbasetemp() / 'no-lesson.env'))
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_MODEL', 'LESSON_GPU_HOST'):
        monkeypatch.delenv(name, raising=False)
