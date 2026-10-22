import sys
import builtins
from pathlib import Path
import runpy

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


def test_frozen_serve_does_not_write_chinese_to_legacy_redirected_stdout(monkeypatch):
    import io
    import assistant.__main__ as entry
    monkeypatch.setattr(entry.Settings, "load", lambda: Settings())
    monkeypatch.setattr(entry, "create_app", lambda settings: "controlled-app")
    monkeypatch.setattr(entry.uvicorn, "run", lambda *args, **kwargs: None)
    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding='cp1252')
    with monkeypatch.context() as frozen:
        frozen.setattr(entry.sys, "frozen", True, raising=False)
        frozen.setattr(entry.sys, "stdout", legacy)
        frozen.setattr(entry.sys, "stderr", None)
        assert entry.main(["--serve"]) == 0
        legacy.flush()
    assert raw.getvalue() == b''


@pytest.mark.parametrize('preexisting', [False, True])
def test_smoke_entry_import_error_reports_without_replacing_existing_files(tmp_path, monkeypatch, preexisting):
    entry = Path(__file__).parents[1] / 'tools/desktop_entry.py'
    report = tmp_path / 'packaged-smoke-error.txt'
    if preexisting:
        report.write_text('protected existing diagnostics', encoding='utf-8')
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'assistant.__main__':
            raise ModuleNotFoundError('controlled_packaged_component_missing')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    monkeypatch.setenv('ASSISTANT_PACKAGED_SMOKE', '1')
    monkeypatch.setenv('SMOKE_DIAGNOSTIC_SECRET', 'fixture-value-never-dump')
    monkeypatch.setattr(sys, 'argv', [str(entry), '--serve'])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ModuleNotFoundError, match='controlled_packaged_component_missing'):
        runpy.run_path(str(entry), run_name='__main__')
    content = report.read_text(encoding='utf-8')
    if preexisting:
        assert content == 'protected existing diagnostics'
    else:
        assert 'ModuleNotFoundError: controlled_packaged_component_missing' in content
        assert 'fixture-value-never-dump' not in content


@pytest.mark.parametrize('enabled,arguments', [('0', ['--serve']), ('1', [])])
def test_normal_entry_error_does_not_create_smoke_reports(tmp_path, monkeypatch, enabled, arguments):
    entry = Path(__file__).parents[1] / 'tools/desktop_entry.py'
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'assistant.__main__':
            raise ModuleNotFoundError('controlled_normal_import_error')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    monkeypatch.setenv('ASSISTANT_PACKAGED_SMOKE', enabled)
    monkeypatch.setattr(sys, 'argv', [str(entry), *arguments])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ModuleNotFoundError, match='controlled_normal_import_error'):
        runpy.run_path(str(entry), run_name='__main__')
    assert not (tmp_path / 'packaged-smoke-error.txt').exists()


def test_smoke_surfaces_actual_entry_missing_import_from_child_process(tmp_path):
    fake = tmp_path / 'controlled-modules'
    (fake / 'assistant').mkdir(parents=True)
    (fake / 'assistant/__init__.py').write_text('', encoding='utf-8')
    (fake / 'assistant/__main__.py').write_text("raise ModuleNotFoundError('controlled_child_import_missing')\n", encoding='utf-8')
    protected = tmp_path / 'keep.txt'
    protected.write_text('original file', encoding='utf-8')
    entry = Path(__file__).parents[1] / 'tools/desktop_entry.py'
    script = f"import runpy,sys; sys.path.insert(0, {str(fake)!r}); runpy.run_path({str(entry)!r}, run_name='__main__')"
    with pytest.raises(RuntimeError, match='ModuleNotFoundError: controlled_child_import_missing'):
        smoke([sys.executable, '-c', script], __version__)
    assert protected.read_text(encoding='utf-8') == 'original file'


def test_smoke_limits_diagnostic_tail_and_stops_the_reporting_child():
    script = ("import os,time; from pathlib import Path; "
              "assert os.environ.get('ASSISTANT_PACKAGED_SMOKE') == '1'; "
              "Path('packaged-smoke-error.txt').write_text('x' * 8000 + '\\nRuntimeError: controlled startup failure', encoding='utf-8'); "
              "time.sleep(30)")
    with pytest.raises(RuntimeError, match='RuntimeError: controlled startup failure') as error:
        smoke([sys.executable, '-c', script], __version__)
    assert len(str(error.value)) <= 3100
