import sys

import pytest

from assistant import __version__
from assistant.config import Settings
from tools.smoke_windows_app import smoke


def test_actual_source_backend_smoke_isolated_from_user_settings_and_secrets(tmp_path, monkeypatch):
    protected = tmp_path / "existing-user-data"
    protected.mkdir()
    marker = protected / "preserve.txt"
    marker.write_text("original data", encoding="utf-8")
    for name in ("OPENAI_API_KEY", "ASSISTANT_MODEL_KEY", "AZURE_OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.setenv(name, "fixture-secret-never-forward")
    monkeypatch.setenv("ASSISTANT_DATA_DIR", str(protected))
    monkeypatch.setenv("MOBILE_ENABLED", "true")
    monkeypatch.setenv("DESKTOP_ENABLED", "true")
    monkeypatch.setenv("VOICE_AUTOSTART", "true")
    result = smoke([sys.executable, "-m", "assistant"], __version__)
    assert result == {"version": __version__, "voice_running": False, "api_configured": False}
    assert marker.read_text(encoding="utf-8") == "original data"
    assert list(protected.iterdir()) == [marker]


def test_source_smoke_rejects_a_wrong_expected_packaged_version():
    with pytest.raises(RuntimeError, match="version"):
        smoke([sys.executable, "-m", "assistant"], "99.0.0")


def test_smoke_timeout_terminates_only_the_child_it_created(monkeypatch):
    import tools.smoke_windows_app as checker
    original = checker.subprocess.Popen
    children = []
    def create(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(checker.subprocess, "Popen", create)
    with pytest.raises(RuntimeError, match="timeout"):
        smoke([sys.executable, "-c", "import time; time.sleep(30)"], __version__, timeout=.2)
    assert len(children) == 1 and children[0].poll() is not None


def test_windowed_frozen_serve_does_not_configure_missing_console_streams(monkeypatch):
    import assistant.__main__ as entry
    seen = []
    monkeypatch.setattr(entry.Settings, "load", lambda: Settings())
    monkeypatch.setattr(entry, "create_app", lambda settings: "controlled-app")
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kwargs: seen.append((app, kwargs)))
    with monkeypatch.context() as frozen:
        frozen.setattr(entry.sys, "frozen", True, raising=False)
        frozen.setattr(entry.sys, "stdout", None)
        frozen.setattr(entry.sys, "stderr", None)
        assert entry.main(["--serve"]) == 0
    assert seen == [("controlled-app", {"host": "127.0.0.1", "port": 8765, "log_config": None})]
