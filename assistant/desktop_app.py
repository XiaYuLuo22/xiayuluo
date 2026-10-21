"""Native desktop window and an owned loopback service, in one process."""
from dataclasses import replace
import logging
import json
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Lock, Thread, Timer
from tempfile import mkdtemp
import shutil
import time

import httpx
import uvicorn

from .config import Settings
from .server import create_app


def configuration_directory():
    if not getattr(sys, "frozen", False):
        return Path.cwd()
    if sys.platform == "win32":
        root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(root) / "Xiayuluo"
    return Path.home() / ".local/share/xiayuluo"


def prepare_settings(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    config_file = directory / ".env"
    if not config_file.exists():
        resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        template = resource_root / ".env.example"
        content = template.read_text(encoding="utf-8") if template.is_file() else (
            "# XiaYuLuo local configuration; restart the app after editing.\n"
            "OPENAI_API_KEY=\nCODEX_WORKSPACE=\nDESKTOP_ENABLED=false\n")
        if sys.platform == "win32":
            content = content.replace("DESKTOP_ENABLED=false", "DESKTOP_ENABLED=true")
        # Exclusive creation preserves a config created concurrently by another instance.
        try:
            with config_file.open("x", encoding="utf-8") as stream:
                stream.write(content)
        except FileExistsError:
            pass
    settings = Settings.load(config_file)
    data_dir = Path(settings.data_dir)
    if not data_dir.is_absolute():
        data_dir = directory / data_dir
    workspace = Path(settings.codex_workspace) if settings.codex_workspace else None
    if workspace and not workspace.is_absolute():
        workspace = directory / workspace
    return replace(settings, data_dir=str(data_dir.resolve()),
                   codex_workspace=str(workspace.resolve()) if workspace else ""), config_file


class LocalService:
    """Reserve a private ephemeral port; never attach to or stop another server."""
    def __init__(self, settings, config_file=None, app_factory=create_app):
        self.voice_autostart = settings.voice_autostart
        self.app = app_factory(settings)
        self.app.state.presentation = "desktop"
        self.app.state.config_file = str(config_file) if config_file else ""
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", settings.port if settings.mobile_enabled else 0))
        self.port = self.listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
            loop="asyncio", log_level="warning", log_config=None, access_log=False,
            timeout_graceful_shutdown=3))
        self.error = None
        self.thread = Thread(target=self._run, name="xiayuluo-local-service", daemon=True)
        self.stop_lock = Lock()
        self.stopped = False

    def _run(self):
        try:
            self.server.run(sockets=[self.listener])
        except BaseException as error:
            self.error = error

    def start(self, timeout=10):
        self.thread.start()
        deadline = time.monotonic() + timeout
        # The loopback readiness request must not be routed through a remote proxy.
        with httpx.Client(trust_env=False, timeout=.5) as client:
            while time.monotonic() < deadline:
                if not self.thread.is_alive():
                    self.stop()
                    raise RuntimeError("本地服务启动失败，请检查日志和配置。") from self.error
                if self.server.started:
                    try:
                        if client.get(self.url + "/").status_code == 200:
                            from .voice import DesktopVoice
                            # Owned loopback service only; never write its token to disk/logs.
                            bootstrap = client.get(self.url + "/api/bootstrap").json()
                            self.app.state.voice = DesktopVoice(self.url, bootstrap["token"], autostart=self.voice_autostart)
                            return self
                    except httpx.HTTPError:
                        pass
                time.sleep(.02)
        self.stop()
        raise RuntimeError("本地服务未在 10 秒内就绪。")

    def on_window_shown(self):
        with self.stop_lock:
            if not self.stopped and self.app.state.voice:
                self.app.state.voice.app_opened()

    def stop(self):
        with self.stop_lock:
            if self.stopped:
                return
            if self.app.state.voice:
                self.app.state.voice.stop()
            self.app.state.voice_consents.revoke_owner("desktop")
            self.server.should_exit = True
            if self.thread.is_alive():
                self.thread.join(timeout=12)
            if self.thread.is_alive():
                raise RuntimeError("本地服务仍在停止任务，请稍后退出。")
            self.listener.close()
            self.app.state.memory.close()
            self.stopped = True


def open_directory(directory):
    if sys.platform == "win32":
        os.startfile(str(directory))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(directory)])


def update_location_safe(install_dir, config_file, settings):
    protected = [Path(settings.data_dir), Path(config_file)]
    if settings.codex_workspace:
        protected.append(Path(settings.codex_workspace))
    return not any(path.resolve().is_relative_to(install_dir.resolve()) for path in protected)


def run_window(settings, config_file, *, webview_module=None, debug=False):
    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError as error:
            raise RuntimeError('缺少桌面依赖，请安装 pip install -e ".[app,desktop]"。') from error
    if sys.stderr is None:
        logging.getLogger("pywebview").handlers.clear()
        logging.getLogger("pywebview").propagate = True
    from webview.menu import Menu, MenuAction

    service = LocalService(settings, config_file)
    window = None
    try:
        service.start()
        webview_module.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview_module.settings["ALLOW_FILE_URLS"] = False
        webview_module.settings["ALLOW_DOWNLOADS"] = True
        webview_module.settings["IGNORE_SSL_ERRORS"] = False
        window = webview_module.create_window("夏鱼洛", service.url, width=1440, height=960,
            min_size=(760, 600), background_color="#fcfcfb", text_select=True,
            confirm_close=True)
        if window is None:
            raise RuntimeError("无法创建桌面窗口。")
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            install_dir = Path(sys.executable).resolve().parent
            # Never swap an installation containing configured memory/config data.
            if update_location_safe(install_dir, config_file, settings):
                def install_update(updater):
                    stage = updater.prepare(install_dir)
                    # The old binary directory must be unlockable: run the helper outside it.
                    helper_dir = Path(mkdtemp(prefix="xiayuluo-update-"))
                    try:
                        helper = helper_dir / "XiayuluoUpdater.exe"
                        shutil.copyfile(install_dir / "XiayuluoUpdater.exe", helper)
                        job = helper_dir / "job.json"
                        job.write_text(json.dumps({"pid": os.getpid(), "install": str(install_dir),
                                                   "stage": str(stage)}), encoding="utf-8")
                        subprocess.Popen([str(helper), str(job)], cwd=helper_dir,
                                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
                    except Exception:
                        shutil.rmtree(helper_dir)
                        shutil.rmtree(stage)
                        raise
                    def restart():
                        window.confirm_close = False  # Already explicitly confirmed by the update dialog.
                        window.destroy()
                    Timer(.5, restart).start()
                service.app.state.install_update = install_update
        # Closing the native window cancels active streams and owned Codex subprocesses.
        window.events.closing += service.stop
        window.events.shown += service.on_window_shown
        menu = [Menu("夏鱼洛", [
            MenuAction("打开配置目录", lambda: open_directory(Path(config_file).parent)),
            MenuAction("打开日志目录", lambda: open_directory(Path(settings.data_dir))),
            MenuAction("重新加载界面", lambda: window.load_url(service.url)),
            MenuAction("退出", window.destroy),
        ])]
        webview_module.start(gui="edgechromium" if sys.platform == "win32" else None,
            debug=debug, private_mode=True, http_server=False, menu=menu,
            localization={"global.quitConfirmation": "退出夏鱼洛并停止正在执行的任务？",
                          "global.ok": "确定", "global.cancel": "取消"})
    finally:
        service.stop()


def show_error(message):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "夏鱼洛 · 启动失败", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main(debug=False):
    directory = configuration_directory()
    try:
        settings, config_file = prepare_settings(directory)
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(Path(settings.data_dir) / "desktop.log",
            maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.WARNING)
        try:
            # A frozen --windowed app has no stdout/stderr; avoid console handlers.
            if sys.stderr is None:
                logging.getLogger("pywebview").handlers.clear()
                logging.getLogger("pywebview").propagate = True
            run_window(settings, config_file, debug=debug)
        finally:
            root.removeHandler(handler)
            handler.close()
        return 0
    except Exception:
        message = ("夏鱼洛未能启动。请确认 .env 配置有效、app 依赖已安装，Windows 10/11 上的 Microsoft Edge WebView2 Runtime 可用。\n"
                   f"配置目录：{directory}\n如没有图形桌面，可使用 python -m assistant --serve 进行后端开发。")
        show_error(message)
        return 1
