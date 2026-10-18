"""ASGI gateway: stream on the same event loop/store as the desktop, never proxy bootstrap.

Only /mobile is eligible for HTTPS reverse-proxy publishing. Local /api stays loopback-only.
"""
import re

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=20, max_length=128)
    name: str = Field(min_length=1, max_length=60)


class Forward(Response):
    def __init__(self, core, token, path, body=None):
        self.core, self.token, self.path, self.request_body = core, token, path, body
        super().__init__()

    async def __call__(self, scope, receive, send):
        forwarded = dict(scope)
        forwarded.update(path=self.path, raw_path=self.path.encode(), root_path="")
        # Never pass phone Authorization through to the model, logs, or core debug token.
        excluded = {b"authorization", b"x-assistant-token", b"origin", b"host"}
        forwarded["headers"] = [(key, value) for key, value in scope["headers"] if key.lower() not in excluded]
        forwarded["headers"] += [(b"host", b"localhost"), (b"x-assistant-token", self.token.encode())]
        consumed = False
        async def replay():
            nonlocal consumed
            if self.request_body is not None and not consumed:
                consumed = True
                return {"type": "http.request", "body": self.request_body, "more_body": False}
            return await receive()
        await self.core(forwarded, replay, send)


def gateway(core, devices, token):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security(request, call_next):
        if request.url.scheme != "https":
            return JSONResponse({"detail": "手机连接必须使用 HTTPS。"}, status_code=426)
        # This gateway is for native clients, not arbitrary websites with Origin headers.
        if request.headers.get("origin"):
            return JSONResponse({"detail": "不允许网页跨域访问手机接口。"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.post("/mobile/pair")
    async def pair(body: PairRequest):
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "请输入设备名称。")
        try:
            return devices.pair(body.code, name)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.api_route("/mobile/v1/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        authorization = request.headers.get("authorization", "")
        device = devices.authenticate(authorization[7:]) if authorization.startswith("Bearer ") else None
        if not device:
            raise HTTPException(401, "设备未配对、已撤销或凭据已过期。")
        allowed = {"GET": r"(?:sessions(?:/[a-f0-9]{32})?|memories|status|actions)",
                   "POST": r"(?:sessions|chat|memories)"}
        if not re.fullmatch(allowed.get(request.method, r"(?!)"), path):
            raise HTTPException(403, "手机无权执行此操作，请在电脑端确认。")
        if path == "status":
            # Exclude filesystem paths, provider URL, plugin schemas and local auth token.
            return {"device": {"id": device["id"], "name": device["name"]},
                    "online": True, "sync_mode": "computer-authority", "approvals": "desktop-only"}
        body = None
        if path == "chat":
            body = await request.body()
            try:
                data = await request.json()
            except ValueError as error:
                raise HTTPException(422, "消息格式不正确。") from error
            if not isinstance(data, dict) or data.get("screenshot_id") is not None:
                raise HTTPException(403, "手机不能读取或转发电脑截图。请在电脑端主动附图。")
        return Forward(core, token, "/api/" + path, body)

    return app


class MobileAccess:
    def __init__(self, app, *, devices, token):
        self.app = app
        self.gateway = gateway(app, devices, token) if devices else None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope["path"] == "/mobile" or scope["path"].startswith("/mobile/")):
            if not self.gateway:
                await JSONResponse({"detail": "手机同步尚未启用。"}, status_code=404)(scope, receive, send)
            else:
                # Bound both content-length and chunked bodies before JSON validation.
                original_receive = receive
                body = bytearray()
                if scope["method"] in {"POST", "PATCH", "DELETE"}:
                    while True:
                        message = await original_receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > 65536:
                            await JSONResponse({"detail": "请求过大。"}, status_code=413)(scope, original_receive, send)
                            return
                        if not message.get("more_body", False):
                            break
                    consumed = False
                    async def replay_body():
                        nonlocal consumed
                        if not consumed:
                            consumed = True
                            return {"type": "http.request", "body": bytes(body), "more_body": False}
                        return await original_receive()
                    receive = replay_body
                await self.gateway(scope, receive, send)
            return
        await self.app(scope, receive, send)
