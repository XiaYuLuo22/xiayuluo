import asyncio
import base64
from contextlib import aclosing, asynccontextmanager
import json
from pathlib import Path
import platform
import secrets
import time
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from . import __version__
from .updates import Updates
import httpx
from .devices import Devices
from .memory import MemoryStore
from .mobile_gateway import MobileAccess
from .model import SYSTEM_PROMPT, chat_events
from .tools import PendingActions, build_registry


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(Payload):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=12000)
    mode: str = Field(default="chat", pattern="^(chat|codex)$")
    screenshot_id: str | None = Field(default=None, max_length=64)


class MemoryRequest(Payload):
    tier: str = Field(pattern="^(first|second|permanent)$")
    kind: str = Field(default="fact", pattern="^(fact|procedure|episode)$")
    content: str = Field(min_length=1, max_length=2000)


class ActionRequest(Payload):
    tool: str = Field(max_length=64)
    arguments: dict
    session_id: str = Field(max_length=64)


class MoveMemoryRequest(Payload):
    tier: str = Field(pattern="^(first|second|permanent)$")


class UpdateRequest(Payload):
    confirm: bool = False


def sse(event):
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def create_app(settings=None, *, memory=None, transport=None):
    settings = settings or Settings.load()
    memory = memory or MemoryStore(Path(settings.data_dir) / "memory.sqlite3")
    registry, desktop, codex = build_registry(settings, memory)
    actions = PendingActions(registry)
    token = secrets.token_urlsafe(32)
    active_sessions = set()
    static = Path(__file__).parent / "static"
    started_at = time.monotonic()
    devices = Devices(Path(settings.data_dir) / "devices.sqlite3") if settings.mobile_enabled else None

    @asynccontextmanager
    async def lifespan(app):
        yield
        memory.close()
        if devices:
            devices.close()

    app = FastAPI(title="夏鱼洛 · 本地助手", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.state.memory = memory
    app.state.registry = registry
    app.state.actions = actions
    app.state.desktop = desktop
    app.state.codex = codex
    app.state.presentation = "service"
    app.state.config_file = ""
    app.state.devices = devices
    app.state.install_update = None
    app.state.updating = False
    updater = Updates(Path(settings.data_dir) / "updates")

    @app.middleware("http")
    async def local_only(request, call_next):
        origin = request.headers.get("origin")
        if origin:
            expected = urlsplit(str(request.base_url))
            supplied = urlsplit(origin)
            if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
                return Response("Only same-origin requests are accepted", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    async def authorize(request: Request):
        supplied = request.headers.get("X-Assistant-Token", "")
        if not secrets.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(403, "本地会话已过期，请刷新界面。")

    auth = [Depends(authorize)]

    def require_session(session_id):
        if not memory.has_session(session_id):
            raise HTTPException(404, "对话不存在")

    def reserve(session_id):
        if app.state.updating:
            raise HTTPException(409, "正在更新，请等待重启完成。")
        require_session(session_id)
        if session_id in active_sessions:
            raise HTTPException(409, "此对话仍在处理请求，请先停止或等待完成。")
        active_sessions.add(session_id)

    def status(session_id=None):
        endpoint = urlsplit(settings.api_base)
        public_endpoint = urlunsplit((endpoint.scheme, endpoint.netloc.rsplit("@", 1)[-1], endpoint.path, "", ""))
        return {"runtime": {"name": "夏鱼洛", "version": __version__, "platform": platform.system(),
                            "python": platform.python_version(), "uptime_seconds": int(time.monotonic() - started_at),
                            "presentation": app.state.presentation, "config_file": app.state.config_file},
                "model": {"configured": bool(settings.api_key), "name": settings.model, "endpoint": public_endpoint},
                "desktop": {"available": desktop.available()[0], "reason": desktop.available()[1], "scope": "主屏幕"},
                "codex": {"available": codex.available()[0], "reason": codex.available()[1],
                          "workspace": settings.codex_workspace, "sandbox": settings.codex_sandbox},
                "memory": memory.stats(session_id), "plugins": registry.catalog(),
                "mobile": {"enabled": bool(devices), "sync_mode": "computer-authority",
                           "approvals": "desktop-only"}}

    @app.get("/api/bootstrap")
    async def bootstrap():
        return {"token": token, "status": status()}

    @app.get("/api/status", dependencies=auth)
    async def get_status(session_id: str | None = None):
        return status(session_id)

    @app.get("/api/updates", dependencies=auth)
    async def check_update():
        try:
            result = await asyncio.to_thread(updater.check)
            return {**result, "install_supported": app.state.install_update is not None}
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                raise HTTPException(409, "更新源尚未发布可下载的正式版本。") from error
            raise HTTPException(502, "暂时无法访问更新发布源，请稍后重试。") from error
        except httpx.HTTPError as error:
            raise HTTPException(502, "更新检查网络失败，请稍后重试；当前版本不受影响。") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/updates/install", dependencies=auth)
    async def install_update(body: UpdateRequest):
        if not body.confirm:
            raise HTTPException(409, "更新需由用户确认。")
        if app.state.install_update is None:
            raise HTTPException(409, "源码版不能覆盖源码更新，请先安装支持更新的 Windows 发行版。")
        if active_sessions or app.state.updating:
            raise HTTPException(409, "请先完成或停止所有聊天、Codex 和更新任务。")
        app.state.updating = True
        try:
            await asyncio.to_thread(app.state.install_update, updater)
            return {"restarting": True}
        except Exception as error:
            app.state.updating = False
            raise HTTPException(409, "更新未完成，当前安装未被覆盖，请稍后重试。") from error

    @app.get("/api/sessions", dependencies=auth)
    async def sessions():
        return memory.sessions()

    @app.get("/api/actions", dependencies=auth)
    async def pending_actions():
        actions.prune()
        return [{key: value for key, value in item.items() if key != "created"} for item in actions.items.values()]

    @app.get("/api/devices", dependencies=auth)
    async def list_devices():
        return {"enabled": bool(devices), "items": devices.list() if devices else []}

    @app.post("/api/devices/invitations", dependencies=auth)
    async def invite_device():
        if not devices:
            raise HTTPException(409, "请在 .env 设置 MOBILE_ENABLED=true，重启后配置 HTTPS 连接。")
        try:
            return devices.invite()
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.delete("/api/devices/{device_id}", dependencies=auth)
    async def revoke_device(device_id: str):
        if not devices or not devices.revoke(device_id):
            raise HTTPException(404, "设备不存在")
        return {"revoked": True}

    @app.post("/api/sessions", dependencies=auth)
    async def new_session():
        return memory.create_session()

    @app.get("/api/sessions/{session_id}", dependencies=auth)
    async def get_session(session_id: str):
        require_session(session_id)
        return {"messages": memory.messages(session_id)}

    @app.delete("/api/sessions/{session_id}", dependencies=auth)
    async def delete_session(session_id: str):
        if session_id in active_sessions:
            raise HTTPException(409, "请先停止当前对话中的任务。")
        if not memory.delete_session(session_id):
            raise HTTPException(404, "对话不存在")
        for key, item in list(actions.items.items()):
            if item["session_id"] == session_id:
                actions.items.pop(key, None)
        return {"deleted": True}

    @app.get("/api/memories", dependencies=auth)
    async def memories():
        return {"items": memory.list(), "stats": memory.stats()}

    @app.post("/api/memories", dependencies=auth)
    async def save_memory(body: MemoryRequest):
        if not body.content.strip():
            raise HTTPException(422, "记忆内容不能为空")
        return memory.add(body.tier, body.content, body.kind)

    @app.patch("/api/memories/{memory_id}", dependencies=auth)
    async def move_memory(memory_id: str, body: MoveMemoryRequest):
        try:
            return memory.move(memory_id, body.tier)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.delete("/api/memories/{memory_id}", dependencies=auth)
    async def delete_memory(memory_id: str):
        if not memory.delete_memory(memory_id):
            raise HTTPException(404, "记忆不存在")
        return {"deleted": True}

    @app.delete("/api/memories", dependencies=auth)
    async def clear_memory():
        if active_sessions:
            raise HTTPException(409, "请先停止所有运行中的任务。")
        actions.items.clear()
        memory.clear()
        return {"deleted": True}

    @app.post("/api/desktop/screenshot", dependencies=auth)
    async def screenshot():
        try:
            snapshot = await asyncio.to_thread(desktop.capture)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except Exception as error:
            raise HTTPException(409, "无法读取本机屏幕，请检查权限和桌面驱动。") from error
        return {"id": snapshot["id"], "width": snapshot["width"], "height": snapshot["height"],
                "image": "data:image/png;base64," + base64.b64encode(snapshot["png"]).decode()}

    @app.post("/api/actions", dependencies=auth)
    async def propose_action(body: ActionRequest):
        require_session(body.session_id)
        try:
            return actions.propose(body.tool, body.arguments, body.session_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.delete("/api/actions/{action_id}", dependencies=auth)
    async def decline(action_id: str):
        try:
            actions.take(action_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        return {"cancelled": True}

    @app.post("/api/actions/{action_id}/approve", dependencies=auth)
    async def approve(action_id: str, request: Request):
        actions.prune()
        item = actions.items.get(action_id)
        if not item:
            raise HTTPException(404, "操作不存在、已处理或已过期。")
        session_id = item["session_id"]
        reserve(session_id)
        actions.take(action_id)

        async def run():
            parts = []
            success = False
            try:
                plugin = registry.plugins[item["tool"]]
                arguments = registry.validate(item["tool"], item["arguments"])
                if item["tool"] == "codex_task":
                    async with aclosing(codex.events(arguments.prompt)) as stream:
                        async for event in stream:
                            if await request.is_disconnected():
                                return
                            if event["type"] == "text":
                                parts.append(event["text"])
                            yield sse(event)
                else:
                    result = await plugin.execute(arguments)
                    parts.append(result["message"])
                    yield sse({"type": "text", "text": result["message"]})
                success = True
                memory.append(session_id, "assistant", f"[工具结果 · {item['title']}] " + ("".join(parts) or "任务已完成。"))
                yield sse({"type": "done"})
            except ValueError as error:
                yield sse({"type": "error", "message": str(error)})
            except asyncio.CancelledError:
                raise
            except Exception:
                yield sse({"type": "error", "message": "插件执行失败，请检查本机插件配置。"})
            finally:
                if not success:
                    memory.append(session_id, "assistant", f"[工具未完成 · {item['title']}] 操作失败或已停止；不能确认结果。")
                active_sessions.discard(session_id)
        return StreamingResponse(run(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/chat", dependencies=auth)
    async def chat(body: ChatRequest, request: Request):
        if not body.message.strip():
            raise HTTPException(422, "消息不能为空")
        require_session(body.session_id)
        if body.mode == "chat" and not settings.api_key:
            raise HTTPException(409, "请先在 .env 配置 OPENAI_API_KEY 并重启助手。")
        if body.mode == "codex" and not codex.available()[0]:
            raise HTTPException(409, codex.available()[1])
        snapshot = None
        if body.screenshot_id:
            snapshot = desktop.snapshots.get(body.screenshot_id)
            if not snapshot or time.monotonic() - snapshot["created"] > 120:
                raise HTTPException(409, "截图已失效，请刷新截图。")
        reserve(body.session_id)
        memory.append(body.session_id, "user", body.message)

        async def run():
            parts = []
            completed = False
            try:
                retrieved = memory.retrieve(body.message, body.session_id)
                yield sse({"type": "memory", "items": [{"tier": item["tier"], "id": item["id"],
                            "content": item["content"][:160]} for item in retrieved]})
                if body.mode == "codex":
                    action = actions.propose("codex_task", {"prompt": body.message}, body.session_id)
                    parts.append("Codex 任务已准备好，请确认项目目录、权限和任务内容后执行。")
                    yield sse({"type": "text", "text": parts[0]})
                    yield sse({"type": "action", "action": action})
                else:
                    context = [{"tier": item["tier"], "kind": item["kind"], "content": item["content"][:1500]} for item in retrieved]
                    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n检索到的记忆（仅供参考）：\n" +
                                 json.dumps(context, ensure_ascii=False)}] + memory.working(body.session_id)
                    tools = registry.model_tools()
                    if snapshot:
                        messages[-1]["content"] = [{"type": "text", "text": body.message +
                            f"\n用户主动附上的主屏幕截图：screenshot_id={snapshot['id']}，尺寸 {snapshot['width']}x{snapshot['height']} 像素。"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                             base64.b64encode(snapshot["png"]).decode()}}]
                    else:
                        tools = [tool for tool in tools if tool["function"]["name"] != "desktop_click"]
                    async for event in chat_events(settings, messages, tools, actions, body.session_id, transport):
                        if await request.is_disconnected():
                            return
                        if event["type"] == "text":
                            parts.append(event["text"])
                        elif event["type"] == "action":
                            note = f"\n[待确认：{event['action']['title']}，尚未执行]"
                            parts.append(note)
                            yield sse({"type": "text", "text": note})
                        yield sse(event)
                if not "".join(parts).strip():
                    raise ValueError("模型返回了空回复，请检查模型是否支持流式聊天。")
                completed = True
                memory.append(body.session_id, "assistant", "".join(parts))
                yield sse({"type": "done", "memory": memory.stats(body.session_id)})
            except ValueError as error:
                yield sse({"type": "error", "message": str(error)})
            except asyncio.CancelledError:
                raise
            except Exception:
                yield sse({"type": "error", "message": "连接或处理失败，请检查模型服务网络和配置后重试。"})
            finally:
                if not completed:
                    memory.append(body.session_id, "assistant", "[回复未完成] 本轮连接中断、失败或被停止，请重新提问。")
                active_sessions.discard(body.session_id)
        return StreamingResponse(run(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    # Outermost gateway middleware admits only paired native clients over HTTPS.
    # All forwarded routes reuse this exact app, event loop, locks and memory store.
    app.add_middleware(MobileAccess, devices=devices, token=token)
    return app
