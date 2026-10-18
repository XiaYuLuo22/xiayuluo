"""Ephemeral, device/session-bound authorization for read-only Codex voice chat.

This is not a tool approval. No arbitrary prompts, workspaces, modes or API keys
can be selected through a lease. Only the existing isolated CodexChat engine.
"""
import asyncio
from contextlib import aclosing
import hashlib
import secrets
import time


class VoiceConsents:
    def __init__(self, memory, clock=time.monotonic):
        self.memory, self.clock, self.items = memory, clock, {}

    def start(self, owner):
        self.revoke_owner(owner)
        self.prune()
        if len(self.items) >= 32:
            raise ValueError("同时开启的语音设备过多，请先停止其他设备。")
        token = secrets.token_urlsafe(32)
        session = self.memory.create_session()["id"]
        self.items[self.key(token)] = {"owner": owner, "session": session, "expires": self.clock() + 150}
        return {"session_id": session, "voice_token": token}

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def valid(self, token, owner, session=None):
        item = self.items.get(self.key(token))
        return bool(item and item["expires"] > self.clock() and item["owner"] == owner
                    and (session is None or item["session"] == session))

    def renew(self, token, owner):
        if not self.valid(token, owner):
            raise ValueError("语音授权已失效，请停止监听后重新开启。")
        self.items[self.key(token)]["expires"] = self.clock() + 150

    def revoke(self, token, owner):
        item = self.items.get(self.key(token))
        if item and item["owner"] == owner:
            del self.items[self.key(token)]

    def revoke_owner(self, owner):
        for key, item in list(self.items.items()):
            if item["owner"] == owner:
                del self.items[key]

    def prune(self):
        for key, item in list(self.items.items()):
            if item["expires"] <= self.clock():
                del self.items[key]

    async def events(self, engine, prompt, token, owner, session):
        """Cancel a slow/in-flight CLI even if it isn't currently producing text."""
        async with aclosing(engine.events(prompt)) as stream:
            pending = None
            try:
                while True:
                    if not self.valid(token, owner, session):
                        raise ValueError("语音已停止或授权过期，本轮已取消。")
                    pending = asyncio.create_task(anext(stream))
                    while not pending.done():
                        await asyncio.wait({pending}, timeout=.25)
                        if not self.valid(token, owner, session):
                            raise ValueError("语音已停止或授权过期，本轮已取消。")
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        return
                    yield event
            finally:
                if pending:
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
