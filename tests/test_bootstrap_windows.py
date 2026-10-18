"""Test startup decisions without pretending to run Windows CMD/WebView2 on Linux."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.bootstrap_windows import PROBE, bootstrap


def source_tree(tmp_path, environment=False):
    root = tmp_path / "中文路径 with spaces ! &"
    (root / "assistant").mkdir(parents=True)
    (root / "pyproject.toml").write_text("# fixture project", encoding="utf-8")
    (root / "requirements.lock").write_text("# fixture constraints", encoding="utf-8")
    (root / "assistant/__main__.py").write_text("# fixture entry", encoding="utf-8")
    (root / ".env").write_text("# preserve configuration", encoding="utf-8")
    (root / ".assistant").mkdir()
    (root / ".assistant/memory.sqlite3").write_bytes(b"preserve memory")
    if environment:
        python = root / ".venv/Scripts/python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
    return root


class FakeRunner:
    def __init__(self, root, *, installed=False, pip_exists=True, fail=None):
        self.root, self.installed, self.pip_exists, self.fail = root, installed, pip_exists, fail
        self.commands = []

    def __call__(self, command, **options):
        assert options["cwd"] == str(self.root)
        assert options["check"] is False
        assert "shell" not in options
        assert isinstance(command, list)
        self.commands.append(command)
        code = 0
        if command[1:3] == ["-m", "venv"]:
            if self.fail == "venv":
                code = 1
            else:
                python = Path(command[3]) / "Scripts/python.exe"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()
        elif command[1:3] == ["-c", PROBE]:
            code = 0 if self.installed and self.fail != "probe" else 1
        elif command[1:4] == ["-m", "pip", "--version"]:
            code = 0 if self.pip_exists else 1
        elif command[1:3] == ["-m", "ensurepip"]:
            code = 1 if self.fail == "ensurepip" else 0
            self.pip_exists = code == 0
        elif command[1:4] == ["-m", "pip", "install"]:
            code = 1 if self.fail == "install" else 0
            self.installed = code == 0
        elif command[1:4] == ["-m", "pip", "check"]:
            code = 1 if self.fail == "check" else 0
        elif command[1:3] == ["-m", "assistant"]:
            code = 1 if self.fail == "launch" else 0
        return SimpleNamespace(returncode=code)


def launch(root, runner):
    return bootstrap(root, runner=runner, executable="C:/Python 312/python.exe", platform="win32")


def installs(runner):
    return [cmd for cmd in runner.commands if cmd[1:4] == ["-m", "pip", "install"]]


def test_first_start_creates_installs_and_launches_same_python(tmp_path):
    root = source_tree(tmp_path)
    runner = FakeRunner(root)
    assert launch(root, runner) == 0
    python = str(root / ".venv/Scripts/python.exe")
    assert installs(runner) == [[python, "-m", "pip", "install", "--disable-pip-version-check",
                                 "-c", "requirements.lock", "-e", ".[app,desktop]"]]
    assert runner.commands[-1] == [python, "-m", "assistant"]
    assert (root / ".env").read_text() == "# preserve configuration"
    assert (root / ".assistant/memory.sqlite3").read_bytes() == b"preserve memory"


def test_repeat_start_checks_dependencies_without_downloading(tmp_path):
    root = source_tree(tmp_path)
    runner = FakeRunner(root)
    assert launch(root, runner) == 0
    runner.commands.clear()
    assert launch(root, runner) == 0
    assert not installs(runner)
    assert [str(root / ".venv/Scripts/python.exe"), "-c", PROBE] in runner.commands


def test_missing_uvicorn_with_existing_success_marker_is_repaired(tmp_path):
    root = source_tree(tmp_path)
    runner = FakeRunner(root)
    assert launch(root, runner) == 0
    runner.installed = False
    runner.commands.clear()
    assert launch(root, runner) == 0
    assert len(installs(runner)) == 1
    assert not any(cmd[1:3] == ["-m", "venv"] for cmd in runner.commands)


def test_changed_constraints_reinstall_without_deleting_environment(tmp_path):
    root = source_tree(tmp_path)
    runner = FakeRunner(root)
    assert launch(root, runner) == 0
    (root / "requirements.lock").write_text("# changed constraints", encoding="utf-8")
    runner.commands.clear()
    assert launch(root, runner) == 0
    assert len(installs(runner)) == 1


def test_existing_dependencies_without_marker_are_installed_once(tmp_path):
    root = source_tree(tmp_path, environment=True)
    runner = FakeRunner(root, installed=True)
    assert launch(root, runner) == 0
    assert len(installs(runner)) == 1


def test_missing_pip_is_bootstrapped(tmp_path):
    root = source_tree(tmp_path)
    runner = FakeRunner(root, pip_exists=False)
    assert launch(root, runner) == 0
    assert any(cmd[1:3] == ["-m", "ensurepip"] for cmd in runner.commands)


def test_failures_do_not_launch_or_record_success(tmp_path):
    for failure in ("venv", "install", "check", "probe", "ensurepip"):
        root = source_tree(tmp_path / failure)
        runner = FakeRunner(root, fail=failure, pip_exists=failure != "ensurepip")
        assert launch(root, runner) == 1
        assert not any(cmd[1:3] == ["-m", "assistant"] for cmd in runner.commands)
        assert not (root / ".venv/xiayuluo-setup.json").exists()
        assert (root / ".env").read_text() == "# preserve configuration"


def test_app_failure_is_not_hidden(tmp_path):
    root = source_tree(tmp_path)
    runner = FakeRunner(root, fail="launch")
    assert launch(root, runner) == 1
    assert runner.commands[-1][1:] == ["-m", "assistant"]


def test_incomplete_source_and_nonwindows_are_read_only(tmp_path):
    runner = FakeRunner(tmp_path)
    assert bootstrap(tmp_path, runner=runner, platform="win32") == 1
    assert bootstrap(tmp_path, runner=runner, platform="linux") == 1
    assert not runner.commands
    assert not (tmp_path / ".venv").exists()


def test_cmd_contract_keeps_errors_visible_and_avoids_detached_launch():
    script = (Path(__file__).resolve().parents[1] / "start.cmd").read_text(encoding="utf-8")
    assert 'pushd "%~dp0"' in script
    assert "DisableDelayedExpansion" in script
    assert "tools\\bootstrap_windows.py" in script
    assert "py -3.12" in script
    assert ":failed\n" in script and "pause" in script
    assert "pythonw.exe" not in script


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CMD integration requires Windows")
@pytest.mark.parametrize("helper_exit", [0, 1])
def test_actual_windows_cmd_with_spaces_unicode_and_visible_failure(tmp_path, helper_exit):
    root = tmp_path / "中文启动 with spaces ! &"
    (root / "tools").mkdir(parents=True)
    script = Path(__file__).resolve().parents[1] / "start.cmd"
    (root / "start.cmd").write_bytes(script.read_bytes())
    # A controlled helper proves dispatch and exit propagation, without installing packages
    # or presenting an actual App. The real bootstrap behavior is tested independently.
    (root / "tools/bootstrap_windows.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "Path('helper-ran.txt').write_text('ok', encoding='utf-8')\n"
        f"sys.exit({helper_exit})\n", encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", "start.cmd"], cwd=root,
                            input=b"\n", capture_output=True, timeout=30, check=False)
    assert result.returncode == helper_exit
    assert (root / "helper-ran.txt").read_text() == "ok"
    if helper_exit:
        assert b"Setup or startup failed" in result.stdout
