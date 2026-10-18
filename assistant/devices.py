"""Single-owner device pairing: short-lived invitations, hashed revocable tokens."""
from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
import time


class Devices:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        self.invitations = {}
        self.attempts = deque()
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
            )""")

    def close(self):
        self.db.close()

    def invite(self):
        with self.lock:
            self.invitations = {key: value for key, value in self.invitations.items() if value > time.monotonic()}
            if len(self.invitations) >= 10:
                raise ValueError("待配对邀请过多，请等待 5 分钟后重试。")
            code = secrets.token_urlsafe(24)
            self.invitations[hashlib.sha256(code.encode()).hexdigest()] = time.monotonic() + 300
            return {"code": code, "expires_in": 300}

    def pair(self, code, name):
        with self.lock:
            while self.attempts and self.attempts[0] < time.monotonic() - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 30:
                raise ValueError("配对尝试过多，请稍后重试。")
            self.attempts.append(time.monotonic())
            deadline = self.invitations.pop(hashlib.sha256(code.encode()).hexdigest(), 0)
            if deadline <= time.monotonic():
                raise ValueError("配对邀请无效、已使用或已过期，请在电脑重新生成。")
            token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            device = {"id": secrets.token_hex(16), "name": name,
                      "created_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat()}
            with self.db:
                self.db.execute("INSERT INTO devices(id,name,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
                                (device["id"], name, hashlib.sha256(token.encode()).hexdigest(),
                                 device["created_at"], device["expires_at"]))
            return {"device": device, "token": token}

    def authenticate(self, token):
        if not 20 <= len(token) <= 128:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT id,name,expires_at FROM devices WHERE token_hash=? AND revoked=0",
                                  (digest,)).fetchone()
            if not row or row["expires_at"] <= datetime.now(timezone.utc).isoformat():
                return None
            return dict(row)

    def list(self):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT id,name,created_at,expires_at,revoked FROM devices ORDER BY created_at DESC")]

    def revoke(self, device_id):
        with self.lock, self.db:
            return self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,)).rowcount > 0
