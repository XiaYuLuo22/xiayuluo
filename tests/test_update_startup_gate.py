import json
from types import SimpleNamespace

import pytest

from assistant import __version__
from assistant.cloud_runtime import CloudSyncRuntime
from assistant.config import Settings
from assistant.update_startup import StartupBackgroundGate
from assistant.update_worker import confirm_background_start


def probe(tmp_path, monkeypatch):
    folder = tmp_path / 'xiayuluo-update-gate'
    folder.mkdir()
    monkeypatch.setattr('assistant.update_startup.gettempdir', lambda: str(tmp_path))
    monkeypatch.setenv('XIAYULUO_UPDATE_HEALTH', json.dumps({'nonce': 'b' * 64, 'version': __version__,
        'acknowledgement': str(folder / 'health-ready.json')}))
    return StartupBackgroundGate(), folder


def test_manual_start_has_no_update_wait(monkeypatch):
    monkeypatch.delenv('XIAYULUO_UPDATE_HEALTH', raising=False)
    assert StartupBackgroundGate().ready()


def test_window_ack_is_not_permission_for_cloud_writes_until_helper_commits(tmp_path, monkeypatch):
    gate, folder = probe(tmp_path, monkeypatch)
    (folder / 'health-ready.json').write_text(json.dumps({'nonce': 'b' * 64, 'version': __version__, 'ready': True}))
    assert not gate.ready()
    confirm_background_start(folder, 'b' * 64, __version__)
    assert gate.ready()
    original = (folder / 'health-accepted.json').read_bytes()
    with pytest.raises(FileExistsError): confirm_background_start(folder, 'different', __version__)
    assert (folder / 'health-accepted.json').read_bytes() == original
    assert not list(folder.glob('.health-accepted-*'))


@pytest.mark.parametrize('change', [{'nonce': 'c' * 64}, {'version': '0.0.1'}, {'accepted': 1}, {'accepted': False}])
def test_wrong_confirmation_never_unblocks_background_data(tmp_path, monkeypatch, change):
    gate, folder = probe(tmp_path, monkeypatch)
    content = {'nonce': 'b' * 64, 'version': __version__, 'accepted': True, **change}
    (folder / 'health-accepted.json').write_text(json.dumps(content))
    assert not gate.ready()


async def test_pending_update_does_not_call_sync_or_lose_existing_state(tmp_path):
    ready = [False]
    runtime = CloudSyncRuntime(Settings(data_dir=str(tmp_path)), None, ready=lambda: ready[0])
    calls = []
    async def sync(): calls.append(True); return {'unsyncable_count': 2}
    runtime.bridge = SimpleNamespace(sync_once=sync, conflicts=lambda: [])
    runtime.configured = True
    with pytest.raises(ValueError, match='等待更新助手'):
        await runtime.sync_now()
    assert not calls and not runtime.status()['startup_ready']
    ready[0] = True
    assert (await runtime.sync_now())['phase'] == 'partial'
    assert runtime.status()['unsyncable_count'] == 2 and calls == [True]
    await runtime.close()
