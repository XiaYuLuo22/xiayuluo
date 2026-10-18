import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .plugins import Registry


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClickArguments(Arguments):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    screenshot_id: str = Field(min_length=1, max_length=64)
    button: str = Field(default="left", pattern="^(left|right)$")


class CodexArguments(Arguments):
    prompt: str = Field(min_length=1, max_length=12000)


class RememberArguments(Arguments):
    tier: str = Field(default="second", pattern="^(second|permanent)$")
    kind: str = Field(default="fact", pattern="^(fact|procedure)$")
    content: str = Field(min_length=1, max_length=2000)


class Desktop:
    def __init__(self, enabled):
        self.enabled = enabled
        self.snapshots = OrderedDict()
        self.driver = None
        self.error = ""
        if enabled:
            try:
                if sys.platform == "win32":
                    import ctypes
                    try:
                        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
                    except (AttributeError, OSError):
                        ctypes.windll.user32.SetProcessDPIAware()
                import pyautogui
                pyautogui.FAILSAFE = True
                self.driver = pyautogui
                pyautogui.size()
            except Exception:
                self.error = "桌面驱动不可用：请安装 desktop 依赖，并在有图形桌面的本机运行。"

    def available(self):
        if not self.enabled:
            return False, "在 .env 中设置 DESKTOP_ENABLED=true，然后重启本地服务。"
        return bool(self.driver), self.error

    def capture(self):
        ready, reason = self.available()
        if not ready:
            raise ValueError(reason)
        image = self.driver.screenshot()
        width, height = self.driver.size()
        if image.size != (width, height):
            raise ValueError("截图与鼠标坐标比例不一致。请统一主屏幕缩放设置并重启助手。")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        snapshot = {"id": uuid4().hex, "width": width, "height": height,
                    "image": image, "png": buffer.getvalue(), "created": time.monotonic()}
        self.snapshots[snapshot["id"]] = snapshot
        while len(self.snapshots) > 3:
            self.snapshots.popitem(last=False)
        return snapshot

    def validate_click(self, arguments):
        snapshot = self.snapshots.get(arguments.screenshot_id)
        if not snapshot or time.monotonic() - snapshot["created"] > 120:
            raise ValueError("截图已过期，请刷新截图后重新选择点击位置。")
        if arguments.x >= snapshot["width"] or arguments.y >= snapshot["height"]:
            raise ValueError("点击坐标超出主屏幕范围。")
        return snapshot

    def click(self, arguments):
        snapshot = self.validate_click(arguments)
        current = self.driver.screenshot()
        if current.size != snapshot["image"].size:
            raise ValueError("屏幕尺寸已改变，请重新截图。")
        # Reject a changed target rather than click a moved button/window.
        region = (max(0, arguments.x - 20), max(0, arguments.y - 20),
                  min(current.width, arguments.x + 21), min(current.height, arguments.y + 21))
        if current.crop(region).tobytes() != snapshot["image"].crop(region).tobytes():
            raise ValueError("目标区域已变化，请重新截图并确认。")
        self.driver.click(arguments.x, arguments.y, button=arguments.button)
        self.snapshots.pop(arguments.screenshot_id, None)
        return {"message": f"已执行{('左键' if arguments.button == 'left' else '右键')}点击 ({arguments.x}, {arguments.y})。"}


class DesktopClick:
    name = "desktop_click"
    title = "屏幕点击"
    description = "在用户提供的主屏幕截图上点击指定像素坐标。必须提供该截图的 screenshot_id；每次点击都需用户确认。"
    arguments = ClickArguments

    def __init__(self, desktop):
        self.desktop = desktop

    def available(self):
        return self.desktop.available()

    async def execute(self, arguments):
        return await asyncio.to_thread(self.desktop.click, arguments)


class Remember:
    name = "remember"
    title = "长期记忆"
    description = "提出保存用户明确表达的事实/偏好或方法。二级记忆保留30天，永久记忆不自动过期；两者均需用户确认。不得保存猜测或敏感凭据。"
    arguments = RememberArguments

    def __init__(self, memory):
        self.memory = memory

    def available(self):
        return True, ""

    async def execute(self, arguments):
        item = self.memory.add(arguments.tier, arguments.content, arguments.kind)
        return {"message": "已保存到" + ("永久记忆" if arguments.tier == "permanent" else "二级记忆"), "memory": item}


class Codex:
    name = "codex_task"
    title = "Codex 编程"
    description = "向本机 Codex CLI 提交编程任务，在固定配置的项目目录执行；需用户确认。"
    arguments = CodexArguments

    def __init__(self, settings):
        self.settings = settings
        self.lock = asyncio.Lock()

    def command_prefix(self):
        executable = shutil.which(self.settings.codex_executable)
        if not executable:
            raise ValueError("未找到 Codex CLI。请安装 @openai/codex 并执行 codex login。")
        if sys.platform == "win32" and Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            # Run the npm JS entry directly, never interpolate prompts into cmd.exe.
            node = shutil.which("node")
            entry = Path(executable).parent / "node_modules/@openai/codex/bin/codex.js"
            if not node or not entry.is_file():
                raise ValueError("无法解析 Codex 的 Windows npm 入口，请设置 CODEX_EXECUTABLE 指向 codex.exe。")
            return [node, str(entry)]
        return [executable]

    def available(self):
        if not self.settings.codex_workspace or not Path(self.settings.codex_workspace).is_dir():
            return False, "请在 .env 中设置 CODEX_WORKSPACE 为本机已有项目目录。"
        try:
            self.command_prefix()
        except ValueError as error:
            return False, str(error)
        return True, "CLI 已找到；登录和执行权限以实际调用结果为准。"

    async def events(self, prompt):
        if self.lock.locked():
            raise ValueError("Codex 正在执行任务，请等待结束或停止后再试。")
        async with self.lock:
            ready, reason = self.available()
            if not ready:
                raise ValueError(reason)
            command = self.command_prefix() + ["exec", "--json", "--ephemeral", "--color", "never",
                "--skip-git-repo-check", "--sandbox", self.settings.codex_sandbox,
                "--cd", str(Path(self.settings.codex_workspace).resolve()), "-"]
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=2 ** 20,
                **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                   if sys.platform == "win32" else {"start_new_session": True}))

            async def drain_errors():
                # Drain continuously to avoid deadlock; never return raw credential-bearing stderr.
                while await process.stderr.read(8192):
                    pass

            stderr_task = asyncio.create_task(drain_errors())
            try:
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
                async with asyncio.timeout(600):
                    while line := await process.stdout.readline():
                        try:
                            item = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        event_type = item.get("type", "")
                        if event_type == "item.completed":
                            detail = item.get("item", {})
                            if detail.get("type") == "agent_message":
                                yield {"type": "text", "text": detail.get("text", "") + "\n"}
                            elif detail.get("type") in {"command_execution", "file_change"}:
                                yield {"type": "status", "message": f"Codex 已完成：{detail['type']}"}
                        elif event_type == "turn.failed":
                            raise ValueError("Codex 执行失败，请在项目目录运行 codex 检查登录、权限和配置。")
                        elif event_type == "error":
                            yield {"type": "status", "message": "Codex 报告连接或执行问题，正在等待最终结果…"}
                        elif event_type in {"thread.started", "turn.started"}:
                            yield {"type": "status", "message": "Codex 正在处理任务…"}
                    code = await process.wait()
                    if code:
                        raise ValueError(f"Codex 未成功完成（退出码 {code}）。请检查本机 CLI 登录与配置。")
            except TimeoutError as error:
                raise ValueError("Codex 任务超过 10 分钟，已停止。") from error
            finally:
                if process.returncode is None:
                    if sys.platform == "win32":
                        # Stop the CLI and its spawned commands, not unrelated Codex sessions.
                        killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)
                        await killer.wait()
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except TimeoutError:
                        if sys.platform != "win32":
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        else:
                            process.kill()
                        await process.wait()
                await stderr_task

    async def execute(self, arguments):
        parts = []
        async for event in self.events(arguments.prompt):
            if event["type"] == "text":
                parts.append(event["text"])
        return {"message": "".join(parts) or "Codex 已完成任务。"}


class PendingActions:
    def __init__(self, registry):
        self.registry = registry
        self.items = {}

    def propose(self, name, arguments, session_id):
        validated = self.registry.validate(name, arguments)
        plugin = self.registry.plugins[name]
        if isinstance(plugin, DesktopClick):
            plugin.desktop.validate_click(validated)
        self.prune()
        if len(self.items) >= 30:
            raise ValueError("待确认操作过多，请先处理已有操作。")
        item = {"id": uuid4().hex, "tool": name, "title": plugin.title,
                "arguments": validated.model_dump(), "session_id": session_id,
                "created": time.monotonic(), "created_at": datetime.now(timezone.utc).isoformat()}
        self.items[item["id"]] = item
        return {key: value for key, value in item.items() if key != "created"}

    def prune(self):
        expired = [key for key, item in self.items.items() if time.monotonic() - item["created"] > 120]
        for key in expired:
            self.items.pop(key, None)

    def take(self, action_id):
        self.prune()
        item = self.items.pop(action_id, None)
        if not item:
            raise ValueError("操作不存在、已处理或已过期。请重新提出请求。")
        return item


def build_registry(settings, memory):
    registry = Registry({})
    desktop = Desktop(settings.desktop_enabled)
    codex = Codex(settings)
    registry.register(DesktopClick(desktop))
    registry.register(codex)
    registry.register(Remember(memory))
    return registry, desktop, codex
