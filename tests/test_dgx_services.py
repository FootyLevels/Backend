"""Deployment boundaries: no cloud credentials inherited and safe installation."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("dgx_services", Path(__file__).parents[1] / "infra/dgx/services.py")
services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(services)


def release(home, kind, revision):
    root = home / ".local/share/footylevels" / kind
    target = root / "releases" / revision
    target.mkdir(parents=True)
    (root / "current").symlink_to(target)


def test_install_refuses_unmanaged_unit_before_any_write(tmp_path):
    root = tmp_path / ".config/systemd/user"
    root.mkdir(parents=True)
    (root / "footylevels-api.service").write_text("operator service")
    with pytest.raises(RuntimeError):
        services.install(tmp_path)
    assert (root / "footylevels-api.service").read_text() == "operator service"
    assert not (root / "footylevels-model.service").exists()


def test_install_refuses_symlink_and_preserves_unrelated(tmp_path):
    root = tmp_path / ".config/systemd/user"
    root.mkdir(parents=True)
    unrelated = root / "other.service"
    unrelated.write_text("keep")
    (root / "footylevels-api.service").symlink_to(unrelated)
    with pytest.raises(RuntimeError):
        services.install(tmp_path)
    assert unrelated.read_text() == "keep"


def test_install_idempotent_private_units(tmp_path):
    services.install(tmp_path)
    services.install(tmp_path)
    paths = list((tmp_path / ".config/systemd/user").iterdir())
    assert len(paths) == 6
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)


@pytest.mark.parametrize("name", services.SERVICES)
def test_launch_environment_never_inherits_credentials(tmp_path, monkeypatch, name):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "secret-sentinel")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "secret-file-sentinel")
    monkeypatch.setenv("FAL_KEY", "secret-sentinel")
    monkeypatch.setenv("NGROK_AUTHTOKEN", "secret-sentinel")
    release(tmp_path, "runtime", "4017f11")
    release(tmp_path, "cloud-api", "0148340")
    command, env = services.launch_command(tmp_path, name)
    assert not any("secret" in value for value in env.values())
    assert not any("secret" in value for value in command)
    assert env["HOME"] == str(tmp_path)
    assert "--no-fork" in command and "--nonblock" in command
    if name == "model":
        assert env["PORT"] == "8013" and env["FOOTYLEVELS_RUNTIME_REVISION"] == "4017f11"
    elif name == "api":
        assert "dev" in command and "8010" in command
    else:
        assert command[-5:] == ["http", "8010", "--url", "https://ranting-pesticide-thespian.ngrok-free.dev", "--host-header=localhost"]


def test_health_contains_no_server_fields_or_error_details(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "unit_state", lambda name: {"ActiveState": "active"})
    def health(port):
        if port == 8010:
            return {"service": "FootyLevels", "secret": "secret-sentinel"}
        raise RuntimeError("secret-sentinel")
    monkeypatch.setattr(services, "local_health", health)
    report = services.snapshot(tmp_path)
    assert report["apiHealthy"] is True and report["bodyModelLoaded"] is False
    assert "secret" not in str(report)
