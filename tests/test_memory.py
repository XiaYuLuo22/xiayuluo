from datetime import datetime, timedelta, timezone

from assistant.memory import MemoryStore


def test_temporary_is_bounded_and_expires(store):
    session = store.create_session()["id"]
    for index in range(16):
        store.append(session, "assistant", f"消息 {index}")
    assert len(store.working(session)) == 12
    assert store.working(session)[0]["content"] == "消息 4"
    assert len("".join(item["content"] for item in store.working(session, budget=7))) <= 7
    store.buffers[session]["touched"] -= 7201
    assert store.working(session) == []
    assert len(store.messages(session)) == 16


def test_restart_clears_only_temporary_memory(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    first = MemoryStore(path)
    session = first.create_session()["id"]
    first.append(session, "user", "喜欢简洁的回复")
    first.add("permanent", "喜欢中文", "fact")
    first.close()
    second = MemoryStore(path)
    try:
        assert second.working(session) == []
        assert len(second.messages(session)) == 1
        assert second.stats()["first"] == 1
        assert second.stats()["permanent"] == 1
    finally:
        second.close()


def test_retrieval_promotes_first_but_never_permanent(store):
    source = store.create_session()["id"]
    destination = store.create_session()["id"]
    store.append(source, "user", "项目使用 Python 编程")
    for _ in range(3):
        found = store.retrieve("Python 编程", destination)
        assert len(found) == 1
    item = store.list()[0]
    assert item["tier"] == "second"
    assert item["kind"] == "episode"  # Not silently inferred as a stable fact.
    assert item["uses"] == 3
    assert datetime.fromisoformat(item["expires_at"]) > datetime.now(timezone.utc) + timedelta(days=29)
    for _ in range(3):
        store.retrieve("Python 编程", destination)
    assert store.list()[0]["tier"] == "second"


def test_expired_memory_is_not_retrieved(store):
    item = store.add("first", "Python 编程", "episode")
    with store.db:
        store.db.execute("UPDATE memories SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", item["id"]))
    assert store.retrieve("Python 编程", "another-session") == []
    assert len(store.list()) == 1
    assert store.stats()["first"] == 0


def test_permanent_requires_explicit_save_or_move(store):
    item = store.add("second", "偏好简洁的回答", "fact")
    promoted = store.move(item["id"], "permanent")
    assert promoted["expires_at"] is None
    assert store.retrieve("偏好", "another")[0]["tier"] == "permanent"
    demoted = store.move(item["id"], "second")
    assert demoted["expires_at"] is not None
    assert store.delete_memory(item["id"])
    assert store.retrieve("偏好", "another") == []


def test_delete_session_removes_generated_not_manual_memories(store):
    source = store.create_session()["id"]
    store.append(source, "user", "编程项目")
    store.add("permanent", "手动保存的偏好")
    assert store.delete_session(source)
    assert store.messages(source) == []
    assert len(store.list()) == 1
    assert store.list()[0]["tier"] == "permanent"


def test_clear_deletes_all_tiers_and_conversations(store):
    source = store.create_session()["id"]
    store.append(source, "user", "测试")
    store.add("permanent", "事实")
    store.clear()
    assert store.sessions() == []
    assert store.list() == []
    assert store.working(source) == []
