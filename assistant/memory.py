"""Four retention tiers: temporary, first, second, and permanent.

Recency/reinforcement affect retrieval, never silently delete a user's memories.
"""
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
import sqlite3
import time
from threading import RLock
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def terms(text):
    # Chinese bigrams allow local search without a tokenizer service or embedding API.
    words = re.findall(r"[a-z0-9_]+", text.lower())
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(chunk[i:i + 2] for i in range(max(1, len(chunk) - 1)))
    return set(words)


class MemoryStore:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.buffers = {}
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, tier TEXT NOT NULL CHECK(tier IN ('first', 'second', 'permanent')),
                    kind TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
                    accessed_at TEXT NOT NULL, expires_at TEXT,
                    uses INTEGER NOT NULL DEFAULT 0, source_session TEXT
                );
            """)

    def close(self):
        with self.lock:
            self.db.close()

    def create_session(self):
        session = {"id": uuid4().hex, "title": "新的对话", "created_at": now()}
        with self.lock, self.db:
            self.db.execute("INSERT INTO sessions VALUES (:id, :title, :created_at)", session)
        return session

    def has_session(self, session_id):
        with self.lock:
            return self.db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is not None

    def sessions(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM sessions ORDER BY created_at DESC")]

    def messages(self, session_id):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))]

    def append(self, session_id, role, content):
        if not content.strip():
            return
        with self.lock, self.db:
            self.db.execute("INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                            (session_id, role, content, now()))
            if role == "user":
                self.db.execute("UPDATE sessions SET title=? WHERE id=? AND title='新的对话'",
                                (content[:28], session_id))
            self._touch_buffer(session_id)
            self.buffers[session_id]["messages"].append({"role": role, "content": content})
            self.buffers[session_id]["messages"] = self.buffers[session_id]["messages"][-12:]
            if role == "user":
                self.add("first", content[:2000], "episode", session_id)

    def _touch_buffer(self, session_id):
        if session_id not in self.buffers or time.monotonic() - self.buffers[session_id]["touched"] > 7200:
            self.buffers[session_id] = {"messages": [], "touched": time.monotonic()}
        self.buffers[session_id]["touched"] = time.monotonic()

    def working(self, session_id, max_messages=12, budget=18000):
        # Temporary context is volatile and expires after 2h inactivity or restart.
        with self.lock:
            if session_id not in self.buffers or time.monotonic() - self.buffers[session_id]["touched"] > 7200:
                return []
            rows = list(reversed(self.buffers[session_id]["messages"][-max_messages:]))
        result = []
        for row in rows:
            if budget <= 0:
                break
            content = row["content"][-budget:]
            result.append({"role": row["role"], "content": content})
            budget -= len(content)
        return list(reversed(result))

    def add(self, tier, content, kind="fact", source_session=None):
        if tier not in {"first", "second", "permanent"}:
            raise ValueError("无效的记忆层级")
        if kind not in {"episode", "fact", "procedure"}:
            raise ValueError("无效的记忆类型")
        expires_at = None if tier == "permanent" else (
            datetime.now(timezone.utc) + timedelta(days=7 if tier == "first" else 30)).isoformat()
        item = {"id": uuid4().hex, "tier": tier, "kind": kind, "content": content.strip(),
                "created_at": now(), "accessed_at": now(), "expires_at": expires_at,
                "uses": 0, "source_session": source_session}
        if not item["content"]:
            raise ValueError("记忆内容不能为空")
        with self.lock, self.db:
            duplicate = self.db.execute("SELECT * FROM memories WHERE tier=? AND content=? AND kind=? AND source_session IS ?",
                                        (tier, item["content"], kind, source_session)).fetchone()
            if duplicate:
                self.db.execute("UPDATE memories SET expires_at=?, accessed_at=? WHERE id=?",
                                (expires_at, item["accessed_at"], duplicate["id"]))
                refreshed = dict(duplicate)
                refreshed.update(expires_at=expires_at, accessed_at=item["accessed_at"])
                return refreshed
            self.db.execute("INSERT INTO memories VALUES (:id, :tier, :kind, :content, :created_at, :accessed_at, :expires_at, :uses, :source_session)", item)
        return item

    def list(self, tier=None):
        with self.lock:
            rows = self.db.execute("SELECT * FROM memories WHERE (? IS NULL OR tier=?) ORDER BY created_at DESC",
                                   (tier, tier))
            return [dict(row) for row in rows]

    def delete_memory(self, memory_id):
        with self.lock, self.db:
            return self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    def delete_session(self, session_id):
        with self.lock, self.db:
            self.buffers.pop(session_id, None)
            self.db.execute("DELETE FROM memories WHERE source_session=?", (session_id,))
            return self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,)).rowcount > 0

    def clear(self):
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions")
            self.db.execute("DELETE FROM memories")
            self.buffers.clear()
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")

    def retrieve(self, query, session_id, limit=6):
        query_terms = terms(query)
        current_time = datetime.now(timezone.utc)
        candidates = []
        with self.lock:
            memories = [dict(row) for row in self.db.execute(
                "SELECT * FROM memories WHERE (expires_at IS NULL OR expires_at>?) AND (source_session IS NULL OR source_session<>?)",
                (now(), session_id))]
        for item in memories:
            overlap = len(query_terms & terms(item["content"]))
            age_days = max(0, (current_time - datetime.fromisoformat(item["accessed_at"])).total_seconds() / 86400)
            # Confirmed stable facts are useful even when wording doesn't overlap.
            base = 0.35 if item["kind"] == "fact" and item["tier"] in {"second", "permanent"} else 0
            if overlap or base:
                decay = 1 if item["tier"] == "permanent" else math.exp(-age_days / (7 if item["tier"] == "first" else 30))
                item["score"] = overlap * 2 + base + 0.2 * math.log1p(item["uses"]) + 0.2 * decay
                candidates.append(item)
        selected = sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]
        with self.lock, self.db:
            for item in selected:
                uses = item["uses"] + 1
                tier = "second" if item["tier"] == "first" and uses >= 3 else item["tier"]
                expiry = None if tier == "permanent" else (current_time + timedelta(days=7 if tier == "first" else 30)).isoformat()
                self.db.execute("UPDATE memories SET uses=?, accessed_at=?, tier=?, expires_at=? WHERE id=?",
                                (uses, now(), tier, expiry, item["id"]))
                item.update(tier=tier, uses=uses, expires_at=expiry)
        return selected

    def stats(self, session_id=None):
        with self.lock:
            counts = {row[0]: row[1] for row in self.db.execute(
                "SELECT tier, count(*) FROM memories WHERE expires_at IS NULL OR expires_at>? GROUP BY tier", (now(),))}
        return {"temporary": len(self.working(session_id)) if session_id else 0,
                "first": counts.get("first", 0), "second": counts.get("second", 0),
                "permanent": counts.get("permanent", 0)}

    def move(self, memory_id, tier):
        if tier not in {"first", "second", "permanent"}:
            raise ValueError("无效的记忆层级")
        expires = None if tier == "permanent" else (datetime.now(timezone.utc) + timedelta(days=7 if tier == "first" else 30)).isoformat()
        with self.lock, self.db:
            if not self.db.execute("UPDATE memories SET tier=?, expires_at=?, source_session=NULL WHERE id=?",
                                   (tier, expires, memory_id)).rowcount:
                raise ValueError("记忆不存在")
        return next(item for item in self.list() if item["id"] == memory_id)
