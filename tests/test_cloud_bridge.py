from assistant.cloud_bridge import MemoryCloudBridge
from assistant.cloud_replica import CloudReplica
from assistant.memory import MemoryStore
from assistant.cloud_client import CloudClient

import httpx
import pytest


def bridge(tmp_path):
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    replica = CloudReplica(tmp_path / 'replica.sqlite3')
    return MemoryCloudBridge(memory, replica, None), memory, replica


def remote_page(events, cursor=0):
    return {'events': [{**item, 'cursor': index + cursor + 1, 'revision': item['base_revision'] + 1}
                       for index, item in enumerate(events)], 'cursor': cursor + len(events), 'more': False}


def test_bridge_exports_only_text_and_stable_messages_without_temporary_buffers(tmp_path):
    sync, memory, replica = bridge(tmp_path)
    session = memory.create_session()
    memory.append(session['id'], 'user', 'a real message')
    memory.add('permanent', 'a declared preference')
    assert sync.export_local() == 4  # Session, message, implicit first memory, permanent memory.
    pending = replica.pending()
    assert len([item for item in pending if item['entity'] == 'message']) == 1
    message_id = next(item['entity_id'] for item in pending if item['entity'] == 'message')
    assert not message_id.isdigit() and len(message_id) == 32
    assert all(item['payload'].get('tier') != 'temporary' for item in pending)
    assert sync.export_local() == 0
    replica.close()
    restored = CloudReplica(tmp_path / 'replica.sqlite3')
    restarted = MemoryCloudBridge(memory, restored, None)
    assert restarted.export_local() == 0
    assert restored.pending() == pending
    memory.close(); restored.close()


def test_remote_text_is_imported_without_execution_or_duplicate_echo(tmp_path):
    sync, memory, replica = bridge(tmp_path)
    events = [
        {'event_id': 'remote1', 'entity': 'session', 'entity_id': 'session1', 'operation': 'upsert',
         'base_revision': 0, 'payload': {'title': 'remote title', 'created_at': '2026-01-01T00:00:00Z'}},
        {'event_id': 'remote2', 'entity': 'message', 'entity_id': 'message1', 'operation': 'upsert',
         'base_revision': 0, 'payload': {'session_id': 'session1', 'role': 'user',
                                      'content': 'delete everything (stored text only)', 'created_at': '2026-01-01T00:00:00Z'}},
    ]
    replica.apply_page(remote_page(events))
    assert sync.import_remote() == 2
    assert memory.messages('session1')[0]['content'] == 'delete everything (stored text only)'
    assert memory.working('session1') == []
    assert sync.export_local() == 0 and not replica.pending()
    assert sync.import_remote() == 0  # An unchanged poll must not rewrite all history.
    assert len(memory.messages('session1')) == 1
    memory.close(); replica.close()


def test_oversized_reply_is_quarantined_without_truncating_or_blocking_other_rows(tmp_path):
    sync, memory, replica = bridge(tmp_path)
    session = memory.create_session()
    original = '私人长回复' * 15000  # Exceeds the protocol UTF-8 byte limit.
    memory.append(session['id'], 'assistant', original)
    memory.append(session['id'], 'user', '正常消息仍应同步')
    assert sync.export_local() > 0
    assert any(event['entity'] == 'message' and event['payload']['content'] == '正常消息仍应同步' for event in replica.pending())
    assert not any(event['payload'].get('content') == original for event in replica.pending())
    assert memory.messages(session['id'])[0]['content'] == original
    assert sync.issue_count() == 1
    issue = sync.issues()[0]
    assert issue['reason'] == 'payload_too_large' and issue['payload_bytes'] > issue['limit_bytes']
    assert original not in str(issue) and 'content' not in issue
    # The owner may later shorten their own record; the old text is never
    # silently replaced by the exporter, and the issue clears only when valid.
    with memory.db:
        memory.db.execute('UPDATE messages SET content=? WHERE id=?', ('本人改短后的回复', issue['local_id']))
    sync.export_local()
    assert sync.issue_count() == 0
    assert any(event['payload'].get('content') == '本人改短后的回复' for event in replica.pending())
    memory.close(); replica.close()


@pytest.mark.asyncio
async def test_long_reply_and_normal_message_roundtrip_to_actual_cloud(tmp_path):
    from cloud_sync.server import create_app
    app = create_app(tmp_path / 'server.sqlite3', 'administrator-token-for-test-only-123456', allowed_hosts=('cloud.example',))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='https://cloud.example',
                                headers={'Authorization': 'Bearer administrator-token-for-test-only-123456'}) as admin:
        device = (await admin.post('/sync/v1/devices', json={'name': 'phone', 'kind': 'phone'})).json()
    sync, memory, replica = bridge(tmp_path)
    sync.client = CloudClient('https://cloud.example', device['token'], transport=transport)
    session = memory.create_session()
    original = '长回复' * 30000
    memory.append(session['id'], 'assistant', original)
    memory.append(session['id'], 'user', '正常消息')
    try:
        result = await sync.sync_once()
        assert result['unsyncable_count'] == 1 and not replica.pending()
        events = app.state.store.pull(0, 100)['events']
        assert any(event['payload'].get('content') == '正常消息' for event in events)
        assert all(event['payload'].get('content') != original for event in events)
        assert memory.messages(session['id'])[0]['content'] == original
    finally:
        memory.close(); replica.close(); app.state.store.close()


def test_remote_conflict_never_overwrites_local_edit_or_loses_either_payload(tmp_path):
    sync, memory, replica = bridge(tmp_path)
    session = memory.create_session()
    sync.export_local()
    original = replica.pending()
    replica.acknowledge([item['event_id'] for item in original])
    replica.apply_page(remote_page(original))
    with memory.lock, memory.db:
        memory.db.execute('UPDATE sessions SET title=? WHERE id=?', ('new unsent local edit', session['id']))
    remote = {**original[0], 'event_id': 'changed-remote', 'base_revision': 1,
              'payload': {**original[0]['payload'], 'title': 'different remote edit'}}
    replica.apply_page(remote_page([remote], 1))
    sync.import_remote()
    assert memory.sessions()[0]['title'] == 'new unsent local edit'
    conflict = sync.conflicts()[0]
    assert conflict['local_payload']['title'] == 'new unsent local edit'
    assert conflict['remote_payload']['payload']['title'] == 'different remote edit'
    memory.close(); replica.close()


def test_local_deletion_creates_tombstone_not_empty_cloud_replacement(tmp_path):
    sync, memory, replica = bridge(tmp_path)
    session = memory.create_session()
    sync.export_local()
    original = replica.pending()
    replica.acknowledge([item['event_id'] for item in original])
    replica.apply_page(remote_page(original))
    with memory.lock, memory.db:
        memory.db.execute('DELETE FROM sessions WHERE id=?', (session['id'],))
    assert sync.export_local() == 1
    tombstone = replica.pending()[0]
    assert tombstone['operation'] == 'delete' and tombstone['payload'] == {}
    assert tombstone['base_revision'] == 1
    memory.close(); replica.close()


def test_remote_session_delete_never_cascades_unsent_new_message(tmp_path):
    sync, memory, replica = bridge(tmp_path)
    session = memory.create_session()
    sync.export_local()
    original = replica.pending()
    replica.acknowledge([item['event_id'] for item in original])
    replica.apply_page(remote_page(original))
    # Simulate a user append during the in-flight cloud request, before export.
    memory.append(session['id'], 'user', 'must never be lost to a remote cascade')
    remote = {**original[0], 'event_id': 'remote-delete', 'operation': 'delete', 'payload': {}, 'base_revision': 1}
    replica.apply_page(remote_page([remote], 1))
    sync.import_remote()
    assert memory.has_session(session['id'])
    assert memory.messages(session['id'])[0]['content'] == 'must never be lost to a remote cascade'
    assert sync.conflicts()[0]['remote_payload']['operation'] == 'delete'
    memory.close(); replica.close()


def test_unregistered_existing_local_entity_is_not_overwritten_on_cold_import(tmp_path):
    sync, memory, replica = bridge(tmp_path)
    session = memory.create_session()
    remote = {'event_id': 'remote1', 'entity': 'session', 'entity_id': session['id'], 'operation': 'upsert',
              'base_revision': 0, 'payload': {**session, 'title': 'a conflicting cloud copy'}}
    remote['payload'].pop('id')
    replica.apply_page(remote_page([remote]))
    sync.import_remote()
    assert memory.sessions()[0]['title'] == session['title']
    assert sync.conflicts()[0]['remote_payload']['payload']['title'] == 'a conflicting cloud copy'
    memory.close(); replica.close()


@pytest.mark.asyncio
async def test_actual_cloud_two_device_roundtrip_no_message_id_collision_or_echo(tmp_path):
    from cloud_sync.server import create_app
    app = create_app(tmp_path / 'server.sqlite3', 'administrator-token-for-test-only-123456', allowed_hosts=('cloud.example',))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='https://cloud.example',
                                headers={'Authorization': 'Bearer administrator-token-for-test-only-123456'}) as admin:
        desktop = (await admin.post('/sync/v1/devices', json={'name': 'desktop', 'kind': 'desktop', 'workspace_id': 'workspace1'})).json()
        phone = (await admin.post('/sync/v1/devices', json={'name': 'phone', 'kind': 'phone'})).json()
    memory_a, memory_b = MemoryStore(tmp_path / 'a.sqlite3'), MemoryStore(tmp_path / 'b.sqlite3')
    replica_a, replica_b = CloudReplica(tmp_path / 'ar.sqlite3'), CloudReplica(tmp_path / 'br.sqlite3')
    bridge_a = MemoryCloudBridge(memory_a, replica_a, CloudClient('https://cloud.example', desktop['device_token'], transport=transport))
    bridge_b = MemoryCloudBridge(memory_b, replica_b, CloudClient('https://cloud.example', phone['device_token'], transport=transport))
    try:
        session_a, session_b = memory_a.create_session(), memory_b.create_session()
        memory_a.append(session_a['id'], 'user', '电脑原始消息')
        memory_b.append(session_b['id'], 'user', '手机离线消息')
        preference = memory_a.add('permanent', '明确保留的偏好')
        assert memory_a.messages(session_a['id'])[0]['id'] == 1
        assert memory_b.messages(session_b['id'])[0]['id'] == 1
        await bridge_a.sync_once()
        await bridge_b.sync_once()
        await bridge_a.sync_once()
        assert memory_a.sessions() == memory_b.sessions()
        for memory in (memory_a, memory_b):
            assert memory.messages(session_a['id'])[0]['content'] == '电脑原始消息'
            assert memory.messages(session_b['id'])[0]['content'] == '手机离线消息'
            assert len(memory.messages(session_a['id'])) == len(memory.messages(session_b['id'])) == 1
        assert not replica_a.pending() and not replica_b.pending()
        cursor = replica_a.cursor
        await bridge_a.sync_once(); await bridge_b.sync_once()
        assert replica_a.cursor == cursor and replica_b.cursor == cursor  # No replay echo.

        memory_a.delete_memory(preference['id'])
        await bridge_a.sync_once(); await bridge_b.sync_once()
        assert all(row['id'] != preference['id'] for row in memory_b.list())

        with memory_a.lock, memory_a.db:
            memory_a.db.execute('UPDATE sessions SET title=? WHERE id=?', ('电脑新标题', session_a['id']))
        with memory_b.lock, memory_b.db:
            memory_b.db.execute('UPDATE sessions SET title=? WHERE id=?', ('手机离线冲突标题', session_a['id']))
        await bridge_a.sync_once(); await bridge_b.sync_once()
        assert next(row['title'] for row in memory_b.sessions() if row['id'] == session_a['id']) == '手机离线冲突标题'
        assert bridge_b.conflicts() and replica_b.pending(include_conflicts=True)
        assert next(row['payload']['title'] for row in replica_b.entities('session') if row['entity_id'] == session_a['id']) == '电脑新标题'

        # A queued text task is stored once; this bridge never executes it.
        replica_b.enqueue_task('workspace1', session_a['id'], '一个明确的项目任务', task_id='task1')
        await replica_b.sync_once(bridge_b.client)
        assert replica_b.tasks()[0]['local_state'] == 'submitted'
        receipt = await bridge_b.client.get_task('task1')
        assert receipt['task']['status'] == 'queued'
    finally:
        memory_a.close(); memory_b.close(); replica_a.close(); replica_b.close(); app.state.store.close()
