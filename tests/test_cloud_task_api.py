import sys
from pathlib import Path

import pytest

from assistant.cloud_execution import CloudTaskCodex
from assistant.config import Settings


class DeviceClient:
    def __init__(self, workspace, kind='desktop'):
        self.workspace, self.kind = workspace, kind
        self.calls = []
    async def device_info(self):
        self.calls.append('identity')
        return {'device': {'id': 'desktop-fixture', 'kind': self.kind, 'workspace_id': self.workspace}}
    async def claim_task(self, workspace):
        self.calls.append('claim')
        return {'task': None}


def prepare(client):
    app = client.app
    app.state.presentation = 'desktop'
    app.state.native_window_open = True
    app.state.cloud_sync.bridge = object()  # No actual cloud connection or import.
    app.state.cloud_codex.command_prefix = lambda: [sys.executable, str(Path(__file__).with_name('fake_codex.py'))]
    cloud = DeviceClient(app.state.cloud_codex.workspace_status()['id'])
    app.state.cloud_tasks.client = cloud
    return cloud


def test_tasks_default_off_owner_only_and_do_not_create_journal(client_factory):
    client, _ = client_factory()
    state = client.get('/api/cloud-tasks').json()
    assert not state['enabled'] and not state['executes_tasks']
    assert client.get('/api/cloud-tasks/records').json() == {'items': []}
    assert not client.app.state.cloud_tasks.path.exists()
    for path in ('/api/cloud-tasks', '/api/cloud-tasks/records'):
        assert client.get(path, headers={'X-Assistant-Token': ''}).status_code == 403
        assert client.get(path, headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}).status_code == 403
    assert client.post('/api/cloud-tasks', json={'enabled': True}).status_code == 409
    assert client.post('/api/cloud-tasks', json={'enabled': 'true'}).status_code == 422


def test_cloud_sync_issues_are_owner_only_and_contain_metadata_not_originals(client_factory):
    client, _ = client_factory()
    assert client.get('/api/cloud-sync/issues').json() == {'items': []}
    class Bridge:
        def issues(self):
            return [{'entity': 'message', 'local_id': '12', 'reason': 'oversized',
                     'payload_bytes': 64001, 'limit_bytes': 64000}]
    client.app.state.cloud_sync.bridge = Bridge()
    result = client.get('/api/cloud-sync/issues')
    assert result.status_code == 200
    assert set(result.json()['items'][0]) == {'entity', 'local_id', 'reason', 'payload_bytes', 'limit_bytes'}
    assert client.get('/api/cloud-sync/issues', headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.get('/api/cloud-sync/issues', headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}).status_code == 403


def test_native_owner_can_grant_only_matching_automatic_workspace(client_factory, tmp_path):
    settings = Settings(api_key='fixture', data_dir=str(tmp_path / 'memory'), codex_workspace=str(tmp_path))
    client, _ = client_factory(settings=settings, approval_mode='auto')
    cloud = prepare(client)
    result = client.post('/api/cloud-tasks', json={'enabled': True})
    assert result.status_code == 200 and result.json()['enabled']
    assert client.app.state.cloud_tasks.path.is_file()
    assert cloud.calls[0] == 'identity'
    response = client.post('/api/cloud-tasks', json={'enabled': False})
    assert response.status_code == 200 and not response.json()['enabled']
    assert not response.json()['executes_tasks']


@pytest.mark.parametrize('failure', ['phone-credential', 'workspace', 'manual'])
def test_mismatched_device_or_manual_policy_never_creates_journal(client_factory, tmp_path, failure):
    settings = Settings(api_key='fixture', data_dir=str(tmp_path / 'memory'), codex_workspace=str(tmp_path))
    client, _ = client_factory(settings=settings, approval_mode='manual' if failure == 'manual' else 'auto')
    cloud = prepare(client)
    if failure == 'phone-credential': cloud.kind = 'phone'
    if failure == 'workspace': cloud.workspace = 'different-project'
    result = client.post('/api/cloud-tasks', json={'enabled': True})
    assert result.status_code == 409
    assert not client.app.state.cloud_tasks.enabled and not client.app.state.cloud_tasks.path.exists()
    assert 'claim' not in cloud.calls


def test_local_chat_preempts_cloud_worker_before_model_call(client_factory, monkeypatch):
    client, _ = client_factory()
    calls = []
    async def interrupt(): calls.append('cloud-interrupted')
    monkeypatch.setattr(client.app.state.cloud_tasks, 'interrupt_and_wait', interrupt)
    session = client.post('/api/sessions').json()['id']
    assert client.post('/api/chat', json={'session_id': session, 'message': '优先回答本机问题'}).status_code == 200
    assert calls == ['cloud-interrupted']


def test_cloud_adapter_strips_service_credentials_without_mutating_regular_codex(monkeypatch):
    for name in CloudTaskCodex.PRIVATE_ENVIRONMENT:
        monkeypatch.setenv(name, 'private-never-forward')
    monkeypatch.setenv('PROJECT_TEST_SETTING', 'allowed-project-option')
    cloud = CloudTaskCodex(Settings())
    environment = cloud.execution_environment()
    assert environment['PROJECT_TEST_SETTING'] == 'allowed-project-option'
    assert not any(name in environment for name in CloudTaskCodex.PRIVATE_ENVIRONMENT)
    from assistant.tools import Codex
    assert Codex(Settings()).execution_environment() is None
