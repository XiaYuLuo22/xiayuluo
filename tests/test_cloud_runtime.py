import asyncio
from types import SimpleNamespace

import pytest

from assistant.cloud_runtime import CloudSyncRuntime
from assistant.config import Settings


async def test_default_runtime_never_opens_cloud_or_creates_replica(tmp_path):
    runtime = CloudSyncRuntime(Settings(data_dir=str(tmp_path)), None)
    await runtime.start()
    assert runtime.task is None and runtime.replica is None and not list(tmp_path.iterdir())
    assert not runtime.status()['configured'] and not runtime.status()['executes_tasks']
    with pytest.raises(ValueError):
        await runtime.sync_now()
    await runtime.close()


async def test_runtime_conflicts_and_private_errors_are_not_hidden_or_leaked():
    runtime = CloudSyncRuntime(Settings(), None)
    async def sync():
        pass
    runtime.bridge = SimpleNamespace(sync_once=sync, conflicts=lambda: [{'local': 'private text'}])
    runtime.configured = True
    assert (await runtime.sync_now())['phase'] == 'conflict'
    assert 'private text' not in str(runtime.status())
    async def fail():
        raise RuntimeError('Bearer private-device-token and private-url')
    runtime.bridge.sync_once = fail
    with pytest.raises(ValueError) as error:
        await runtime.sync_now()
    assert 'private-device-token' not in str(error.value) + str(runtime.status())
    assert runtime.status()['phase'] == 'error'
    await runtime.close()


async def test_runtime_pause_cancels_background_sync_before_closing_replica():
    runtime = CloudSyncRuntime(Settings(), None)
    entered = asyncio.Event()
    cancelled = []
    async def sync():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    runtime.configured = True
    runtime.bridge = SimpleNamespace(sync_once=sync, conflicts=lambda: [])
    runtime.replica = SimpleNamespace(close=lambda: cancelled.append('closed'))
    await runtime.start()
    await asyncio.wait_for(entered.wait(), 1)
    await runtime.close()
    assert cancelled == [True, 'closed'] and runtime.task is None


async def test_runtime_does_not_claim_complete_when_more_pages_remain():
    runtime = CloudSyncRuntime(Settings(), None)
    async def sync():
        return {'more': True, 'pending': 12}
    runtime.bridge = SimpleNamespace(sync_once=sync, conflicts=lambda: [])
    runtime.configured = True
    assert (await runtime.sync_now())['phase'] == 'waiting'
    assert '不代表全部同步完成' in runtime.status()['message']
    await runtime.close()


def test_cloud_settings_require_https_and_device_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CLOUD_SYNC_URL', 'https://sync.example')
    monkeypatch.delenv('CLOUD_SYNC_DEVICE_TOKEN', raising=False)
    with pytest.raises(ValueError, match='CLOUD_SYNC'):
        Settings.load()
    monkeypatch.setenv('CLOUD_SYNC_DEVICE_TOKEN', 'private-device-token')
    assert Settings.load().cloud_sync_url == 'https://sync.example'
    for origin in ('http://sync.example', 'https://user:password@sync.example',
                   'https://sync.example/private', 'https://sync.example?token=private'):
        monkeypatch.setenv('CLOUD_SYNC_URL', origin)
        with pytest.raises(ValueError, match='CLOUD_SYNC_URL'):
            Settings.load()


def test_cloud_status_and_manual_sync_require_local_owner(client_factory):
    client, _ = client_factory()
    assert not client.get('/api/cloud-sync').json()['configured']
    assert client.post('/api/cloud-sync', json={}).status_code == 409
    assert client.get('/api/cloud-sync', headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.get('/api/cloud-sync', headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}).status_code == 403
    assert client.post('/api/cloud-sync', json={'url': 'https://untrusted.example'}).status_code == 422


async def test_switching_cloud_origin_blocks_old_outbox_without_stopping_chat(tmp_path):
    from dataclasses import replace
    from assistant.memory import MemoryStore
    from assistant.cloud_replica import CloudReplica
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    settings = Settings(data_dir=str(tmp_path), cloud_sync_url='https://first.example',
                        cloud_sync_device_token='fixture-device-token-0123456789')
    first = CloudSyncRuntime(settings, memory)
    try:
        event = first.replica.enqueue_event('session', 'preserved-session', {
            'title': '原云未发送消息', 'created_at': '2026-10-26T00:00:00+00:00'})
        await first.close()
        changed = CloudSyncRuntime(replace(settings, cloud_sync_url='https://different.example'), memory)
        assert changed.status()['phase'] == 'blocked' and not changed.status()['usable']
        await changed.start()
        assert changed.task is None and changed.client is None
        with pytest.raises(ValueError): await changed.sync_now()
        await changed.close()
        retained = CloudReplica(tmp_path / 'cloud-replica.sqlite3')
        assert retained.pending()[0]['event_id'] == event['event_id']
        retained.close()
        assert memory.create_session()['id']  # Chat remains independently usable.
    finally:
        memory.close()
