"""Real ASGI cloud + actual clients/MemoryStore, no public deployment or user data."""
import httpx

from assistant.cloud_bridge import MemoryCloudBridge
from assistant.cloud_client import CloudClient
from assistant.cloud_replica import CloudReplica
from assistant.memory import MemoryStore
from cloud_sync.server import create_app


def test_imported_earlier_message_is_not_sorted_after_newer_local_message(tmp_path, monkeypatch):
    memory = MemoryStore(tmp_path / 'history.sqlite3')
    try:
        session = memory.create_session()
        monkeypatch.setattr('assistant.memory.now', lambda: '2026-10-26T01:00:00+00:00')
        memory.append(session['id'], 'user', '较晚的本机消息')
        monkeypatch.setattr('assistant.memory.now', lambda: '2026-10-26T00:00:00+00:00')
        memory.append(session['id'], 'user', '随后导入的更早消息')
        assert [row['content'] for row in memory.messages(session['id'])] == ['随后导入的更早消息', '较晚的本机消息']
    finally:
        memory.close()


async def enroll(app, name):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://sync.example') as admin:
        response = await admin.post('/sync/v1/devices', headers={'Authorization': 'Bearer ' + 'fixture-admin-' * 4},
                                    json={'name': name, 'kind': 'phone'})
        assert response.status_code == 200, response.text
        return response.json()


async def test_two_devices_sync_text_memory_and_deletion_without_echo_or_execution(tmp_path):
    app = create_app(tmp_path / 'cloud.sqlite3', 'fixture-admin-' * 4, allowed_hosts=('sync.example',))
    first_device, second_device = await enroll(app, 'device-A'), await enroll(app, 'device-B')
    source, target = MemoryStore(tmp_path / 'first.sqlite3'), MemoryStore(tmp_path / 'second.sqlite3')
    first, second = CloudReplica(tmp_path / 'first-replica.sqlite3'), CloudReplica(tmp_path / 'second-replica.sqlite3')
    try:
        transport = httpx.ASGITransport(app=app)
        a = MemoryCloudBridge(source, first, CloudClient('https://sync.example', first_device['token'], transport=transport))
        b = MemoryCloudBridge(target, second, CloudClient('https://sync.example', second_device['token'], transport=transport))
        session = source.create_session()
        source.append(session['id'], 'user', '这是同步测试，不是执行指令。')
        source.append(session['id'], 'assistant', '仅同步文字。')
        retained = source.add('permanent', '测试偏好：回答简短', 'fact')
        await a.sync_once()
        await b.sync_once()
        assert target.sessions() == source.sessions()
        assert [row['content'] for row in target.messages(session['id'])] == [row['content'] for row in source.messages(session['id'])]
        assert any(row['id'] == retained['id'] for row in target.list('permanent'))
        assert not target.working(session['id'])  # Imported text cannot trigger a new turn.
        event_count = app.state.store.pull(0, 100)['cursor']
        await b.sync_once()
        await a.sync_once()
        assert app.state.store.pull(0, 100)['cursor'] == event_count
        source.delete_memory(retained['id'])
        await a.sync_once()
        await b.sync_once()
        assert not target.list('permanent')
        assert not a.conflicts() and not b.conflicts()
    finally:
        first.close(); second.close(); source.close(); target.close(); app.state.store.close()


async def test_offline_outbox_survives_reopen_then_reaches_actual_server(tmp_path):
    app = create_app(tmp_path / 'cloud.sqlite3', 'fixture-admin-' * 4, allowed_hosts=('sync.example',))
    device = await enroll(app, 'offline-device')
    path = tmp_path / 'offline.sqlite3'
    replica = CloudReplica(path)
    event = replica.enqueue_event('session', 'offline-session', {'title': '离线记录', 'created_at': '2026-10-26T00:00:00Z'})
    replica.close()
    replica = CloudReplica(path)
    try:
        client = CloudClient('https://sync.example', device['token'], transport=httpx.ASGITransport(app=app))
        await replica.sync_once(client)
        assert not replica.pending() and replica.cursor > 0
        assert replica.entities()[0]['payload']['title'] == '离线记录'
        # A repeated request with the same event ID is idempotent on the actual server.
        count = replica.cursor
        await client.push_events([event])
        assert app.state.store.pull(0, 100)['cursor'] == count
    finally:
        replica.close(); app.state.store.close()
