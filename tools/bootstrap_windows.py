"""One-click source setup. Only stdlib is needed before dependencies exist."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


PROBE = (
    "import sys; assert sys.version_info >= (3, 11); "
    "import fastapi, uvicorn, httpx, dotenv, webview, transitions, mss, cv2, numpy; "
    "from importlib.metadata import version; "
    "[version(name) for name in ('xiayuluo-assistant', 'PyAutoGUI', 'Pillow')]; "
    "import PIL.Image; import clr"
)


def announce(message):
    print(message, flush=True)


def run(command, root, runner, quiet=False):
    options = {"cwd": str(root), "check": False}
    if quiet:
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return runner([str(item) for item in command], **options).returncode


def fingerprint(root):
    digest = hashlib.sha256(sys.version.encode("utf-8"))
    digest.update(str(root).encode("utf-8"))
    for name in ("pyproject.toml", "requirements.lock"):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def matches_marker(marker, expected):
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("fingerprint") == expected
    except (OSError, ValueError, AttributeError):
        return False


def bootstrap(root, *, runner=subprocess.run, executable=None, platform=None):
    platform = sys.platform if platform is None else platform
    executable = sys.executable if executable is None else executable
    root = Path(root).resolve()
    if platform != "win32":
        announce("此一键启动器只用于 Windows；云环境请使用 python -m assistant --serve。")
        return 1
    if sys.version_info < (3, 11):
        announce("需要 Python 3.11 或以上，建议安装 Python 3.12。")
        return 1
    required = ("pyproject.toml", "requirements.lock", "assistant/__main__.py")
    if not all((root / name).is_file() for name in required):
        announce("应用文件不完整：请把压缩包完整解压后，再双击 start.cmd。")
        return 1
    python = root / ".venv/Scripts/python.exe"
    announce("夏鱼洛 · 自动准备运行环境（不会覆盖你的配置和记忆）")
    if not python.is_file():
        announce("[1/3] 正在创建此文件夹专用的 Python 环境…")
        if run([executable, "-m", "venv", str(root / ".venv")], root, runner):
            announce("创建环境失败。请确认 Python 含有 venv，且解压文件夹可以写入。")
            return 1
        if not python.is_file():
            announce("未找到新环境的 python.exe。请检查 Python 安装是否完整。")
            return 1
    else:
        announce("[1/3] 使用已有的 .venv，不删除环境或用户数据。")

    marker = root / ".venv/xiayuluo-setup.json"
    expected = fingerprint(root)
    healthy = run([python, "-c", PROBE], root, runner, quiet=True) == 0
    if healthy:
        healthy = run([python, "-m", "pip", "check"], root, runner, quiet=True) == 0
    if not healthy or not matches_marker(marker, expected):
        announce("[2/3] 正在安装/修复依赖，包括桌面窗口、本地视觉与主动决策组件。")
        announce("首次需要联网，可能需要几分钟；请保留此窗口。")
        if run([python, "-m", "pip", "--version"], root, runner, quiet=True):
            if run([python, "-m", "ensurepip", "--upgrade"], root, runner):
                announce("无法准备 pip。请修复 Python 安装后再试。")
                return 1
        command = [python, "-m", "pip", "install", "--disable-pip-version-check",
                   "-c", "requirements.lock", "-e", ".[app,desktop,control]"]
        if run(command, root, runner):
            announce("依赖安装失败：请查看上方安装错误。检查网络；不会继续启动不完整的 App。")
            return 1
        if run([python, "-m", "pip", "check"], root, runner):
            announce("依赖仍有冲突，未启动 App。请把上方报错发给开发者。")
            return 1
        if run([python, "-c", PROBE], root, runner, quiet=True):
            run([python, "-c", PROBE], root, runner)
            announce("依赖导入失败。请检查 Python 位数与系统运行库；建议 64 位 Python 3.12。")
            return 1
        # Record success only after both dependency and Windows runtime checks pass.
        marker.write_text(json.dumps({"fingerprint": expected}), encoding="utf-8")
    else:
        announce("[2/3] 依赖检查通过，跳过下载和重复安装。")
    announce("[3/3] 正在启动夏鱼洛；关闭 App 后此启动窗口自动退出。")
    code = run([python, "-m", "assistant"], root, runner)
    if code:
        announce("App 启动失败。请检查错误提示、.env 配置及 Microsoft Edge WebView2 Runtime。")
        announce("WebView2 官方下载：https://developer.microsoft.com/microsoft-edge/webview2/")
    return code


def main():
    # UTF-8 console output also works for Chinese paths; never read or print credentials.
    os.environ["PYTHONUTF8"] = "1"
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return bootstrap(Path(__file__).resolve().parents[1])
    except KeyboardInterrupt:
        announce("已取消，配置和记忆保留。重新双击 start.cmd 可以重试。")
        return 130
    except (OSError, subprocess.SubprocessError):
        announce("无法完成启动：请确认文件夹可写、Python 环境完整且没有其他安装同时进行。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
