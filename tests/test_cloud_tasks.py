"""Explicit Codex queue tasks only; no real CLI, screen, account or deployment."""
import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from assistant.cloud_tasks import CloudTaskWorker
from assistant.memory import MemoryStore
from assistant.cloud_client import CloudClient
from assistant.cloud_replica import validate_payload
from cloud_sync.server import create_app


TOKEN = 'fixture-lease-token-kept-only-in-memory'


def task(session_id='session-main', **changes):
    return {'id': 'task-main', 'workspace_id': 'workspace-main', 'session_id': session_id,
            'prompt': '在当前项目完善一个测试', 'status': 'leased', 'result': None,
            'lease_expires_at': 1060, **changes}


class Client:
    def __init__(self, item=None):
        self.item = task() if item is None else item
        self.calls = []
        self.start_change = self.heartbeat_error = self.result_error = None
    async def claim_task(self, workspace_id):
        self.calls.append(('claim', workspace_id))
        value, self.item = self.item, None
        if value is None: return {'task': None}
        self.current = value
        return {'task': copy.deepcopy(value), 'lease_token': TOKEN, 'lease_expires_at': 1060}
    async def start_task(self, task_id, token):
        assert token == TOKEN
        self.calls.append(('start', task_id))
        if self.start_change: self.start_change()
        self.current = {**self.current, 'status': 'running', 'lease_expires_at': 1120}
        return {'task': copy.deepcopy(self.current)}
    async def heartbeat_task(self, task_id, token):
        assert token == TOKEN
        self.calls.append(('heartbeat', task_id))
        if self.heartbeat_error: raise self.heartbeat_error
        return {'task': copy.deepcopy(self.current)}
    async def finish_task(self, task_id, token, status, result):
        assert token == TOKEN
        self.calls.append(('finish', task_id, status, result))
        if self.result_error: raise self.result_error
        self.current = {**self.current, 'status': status, 'result': result, 'lease_expires_at': None}
        return {'task': copy.deepcopy(self.current)}


class Codex:
    def __init__(self):
        self.approval_mode = 'auto'
        self.lock = asyncio.Lock()
        self.workspace_id = 'workspace-main'
        self.prompts = []
        self.entered = asyncio.Event()
        self.finished = []
        self.block = self.fail = False
        self.text = '任务测试完成'
    def workspace_status(self):
        return {'id': self.workspace_id, 'available': True, 'configured': True, 'sandbox': 'read-only',
                'approvals': 'automatic' if self.approval_mode == 'auto' else 'desktop-only'}
    async def events(self, prompt):
        async with self.lock:
            self.prompts.append(prompt)
            self.entered.set()
            try:
                if self.block: await asyncio.Event().wait()
                if self.fail: raise ValueError('private exception details / unsafe path')
                yield {'type': 'text', 'text': self.text}
            finally:
                self.finished.append(True)


def worker(tmp_path, client=None, codex=None, memory=None, **kwargs):
    return CloudTaskWorker(client or Client(), codex or Codex(), memory, data_dir=tmp_path / 'data',
                           clock=lambda: 1000, heartbeat_interval=.01, **kwargs)


async def test_default_off_and_no_configuration_do_not_create_files_or_network(tmp_path):
    client = Client()
    instance = worker(tmp_path, client)
    await instance.start()
    await asyncio.sleep(.01)
    assert not instance.status()['enabled'] and not client.calls and not (tmp_path / 'data').exists()
    await instance.close()
    missing = CloudTaskWorker(None, Codex(), None, data_dir=tmp_path / 'absent')
    with pytest.raises(ValueError, match='配置'):
        missing.set_enabled(True)
    assert await missing.tick() is None and not (tmp_path / 'absent').exists()
    await missing.close()


async def test_explicit_task_start_precedes_cli_result_ack_and_no_memory_promotion(tmp_path):
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    session = memory.create_session()
    client, codex = Client(task(session['id'])), Codex()
    instance = worker(tmp_path, client, codex, memory)
    try:
        instance.set_enabled(True)
        assert await instance.tick() == {'task_id': 'task-main', 'phase': 'succeeded'}
        assert [entry[0] for entry in client.calls] == ['claim', 'start', 'finish']
        assert codex.prompts == ['在当前项目完善一个测试']
        history = memory.messages(session['id'])
        assert [item['role'] for item in history] == ['user', 'assistant']
        assert not memory.list() and not memory.working(session['id'])
        record = instance.records()[0]
        assert record['confirmed'] == record['mirrored'] == 1 and record['phase'] == 'succeeded'
        assert TOKEN not in json.dumps(instance.status()) + json.dumps(instance.records())
        assert TOKEN.encode() not in instance.path.read_bytes()
    finally:
        await instance.close(); memory.close()


async def test_manual_policy_or_busy_local_session_never_claims(tmp_path):
    codex, client = Codex(), Client()
    codex.approval_mode = 'manual'
    instance = worker(tmp_path, client, codex)
    with pytest.raises(ValueError, match='逐次确认'):
        instance.set_enabled(True)
    assert not (tmp_path / 'data').exists() and not client.calls
    codex.approval_mode = 'auto'
    instance.eligible = lambda: False
    instance.set_enabled(True)
    assert await instance.tick() is None and not client.calls
    await instance.close()


@pytest.mark.parametrize('changes', [{'workspace_id': 'other-workspace'}, {'status': 'queued'},
                                    {'prompt': ''}, {'lease_expires_at': 999}, {'id': '../escape'}])
async def test_bad_claim_never_reaches_start_or_cli(tmp_path, changes):
    client, codex = Client(task(**changes)), Codex()
    instance = worker(tmp_path, client, codex)
    try:
        instance.set_enabled(True)
        await instance.tick()
        assert [entry[0] for entry in client.calls] == ['claim'] and not codex.prompts
        assert not instance.records()
    finally: await instance.close()


async def test_workspace_change_after_start_cannot_execute_in_new_directory(tmp_path):
    client, codex = Client(), Codex()
    client.start_change = lambda: setattr(codex, 'workspace_id', 'different')
    instance = worker(tmp_path, client, codex)
    try:
        instance.set_enabled(True); await instance.tick()
        assert not codex.prompts and instance.records()[0]['phase'] == 'interrupted'
    finally: await instance.close()


async def test_lost_heartbeat_cancels_only_owned_cli_and_never_reports_success(tmp_path):
    client, codex = Client(), Codex()
    codex.block = True
    client.heartbeat_error = ValueError('private-token-sample')
    instance = worker(tmp_path, client, codex)
    try:
        instance.set_enabled(True)
        await asyncio.wait_for(instance.tick(), 1)
        assert codex.finished == [True] and not codex.lock.locked()
        assert instance.records()[0]['phase'] == 'interrupted'
        assert not any(entry[0] == 'finish' for entry in client.calls)
        assert 'private-token-sample' not in str(instance.status())
    finally: await instance.close()


async def test_user_preemption_waits_for_cli_cleanup_and_keeps_scheduler_enabled(tmp_path):
    client, codex = Client(), Codex()
    codex.block = True
    instance = worker(tmp_path, client, codex, poll_interval=.1)
    try:
        instance.set_enabled(True); await instance.start()
        scheduler = instance.scheduler
        await asyncio.wait_for(codex.entered.wait(), 1)
        await instance.interrupt_and_wait()
        assert codex.finished == [True] and not codex.lock.locked()
        assert instance.enabled and instance.scheduler is scheduler and not scheduler.done()
        assert instance.records()[0]['phase'] == 'interrupted'
        assert not any(entry[0] == 'finish' for entry in client.calls)
    finally: await instance.close()


async def test_unconfirmed_result_is_durable_and_not_reexecuted_after_restart(tmp_path):
    client, codex = Client(), Codex()
    client.result_error = ValueError('private network details')
    instance = worker(tmp_path, client, codex)
    instance.set_enabled(True)
    assert (await instance.tick())['phase'] == 'result-unconfirmed'
    assert instance.records()[0]['result'] == '任务测试完成'
    assert instance.status()['unconfirmed_count'] == 1
    await instance.close()
    repeated, replacement = Client(), Codex()
    resumed = worker(tmp_path, repeated, replacement)
    try:
        resumed.set_enabled(True); await resumed.tick()
        assert not replacement.prompts
        assert resumed.records()[0]['phase'] == 'result-unconfirmed'
        assert TOKEN not in str(resumed.records())
    finally: await resumed.close()


async def test_cli_failure_reports_failed_without_exposing_exception_or_promoting_memory(tmp_path):
    client, codex = Client(), Codex()
    codex.fail = True
    instance = worker(tmp_path, client, codex)
    try:
        instance.set_enabled(True)
        assert (await instance.tick())['phase'] == 'failed'
        assert client.calls[-1][2] == 'failed'
        assert 'private exception' not in client.calls[-1][3]
        assert instance.records()[0]['confirmed'] == 1
    finally: await instance.close()


async def test_pause_keeps_choice_but_prevents_claim_until_resume(tmp_path):
    client = Client()
    instance = worker(tmp_path, client)
    try:
        instance.set_enabled(True); await instance.pause()
        assert instance.enabled and instance.paused
        assert await instance.tick() is None and not client.calls
        await instance.start()
        for _ in range(100):
            if any(entry[0] == 'finish' for entry in client.calls): break
            await asyncio.sleep(.001)
        assert any(entry[0] == 'finish' for entry in client.calls)
    finally: await instance.close()


def test_journal_rejects_links_and_unmanaged_files_without_overwrite(tmp_path):
    data = tmp_path / 'data'; data.mkdir()
    original = data / 'cloud-tasks.sqlite3'; original.write_bytes(b'user original unknown file')
    instance = worker(tmp_path)
    if os.name == 'posix': original.chmod(0o600)
    with pytest.raises(ValueError, match='无法验证'):
        instance.set_enabled(True)
    assert original.read_bytes() == b'user original unknown file'


def test_existing_running_task_is_interrupted_not_replayed_on_restart(tmp_path):
    first = worker(tmp_path)
    first.set_enabled(True)
    item = task()
    first._record(item, 'claimed')
    first._record(item, 'running')
    first.db.close(); first.db = None
    second = worker(tmp_path)
    second.set_enabled(True)
    assert second.records()[0]['phase'] == 'interrupted'
    assert not second.codex.prompts
    second.db.close()


async def test_actual_asgi_cloud_executes_only_explicit_task_and_accepts_fenced_result(tmp_path):
    app = create_app(tmp_path / 'cloud.sqlite3', 'private-fixture-admin-' * 2, allowed_hosts=('sync.example',))
    transport = httpx.ASGITransport(app=app)
    memory = MemoryStore(tmp_path / 'local.sqlite3')
    codex = Codex()
    instance = None
    try:
        async with httpx.AsyncClient(transport=transport, base_url='https://sync.example',
                                     headers={'Authorization': 'Bearer ' + 'private-fixture-admin-' * 2}) as admin:
            desktop = (await admin.post('/sync/v1/devices', json={
                'name': 'fixture-computer', 'kind': 'desktop', 'workspace_id': 'workspace-main'})).json()
            phone = (await admin.post('/sync/v1/devices', json={'name': 'fixture-phone', 'kind': 'phone'})).json()
        desktop_client = CloudClient('https://sync.example', desktop['token'], transport=transport)
        phone_client = CloudClient('https://sync.example', phone['token'], transport=transport)
        session = memory.create_session()
        await phone_client.push_events([{'event_id': 'session-event', 'entity': 'session', 'entity_id': session['id'],
            'operation': 'upsert', 'base_revision': 0, 'payload': {'title': 'fixture', 'created_at': session['created_at']}},
            {'event_id': 'ordinary-message', 'entity': 'message', 'entity_id': 'message-event',
             'operation': 'upsert', 'base_revision': 0, 'payload': {'session_id': session['id'], 'role': 'user',
              'content': '普通同步内容，不能作为电脑任务', 'created_at': session['created_at']}}])
        instance = CloudTaskWorker(desktop_client, codex, memory, data_dir=tmp_path / 'worker')
        instance.set_enabled(True)
        assert await instance.tick() is None and not codex.prompts
        submitted = await phone_client.submit_task({'id': 'explicit-task', 'workspace_id': 'workspace-main',
                                                   'session_id': session['id'], 'prompt': '在项目执行明确测试任务'})
        assert submitted['task']['status'] == 'queued'
        instance.next_check = 0
        assert (await instance.tick())['phase'] == 'succeeded'
        result = (await phone_client.get_task('explicit-task'))['task']
        assert result['status'] == 'succeeded' and result['result'] == '任务测试完成'
        assert codex.prompts == ['在项目执行明确测试任务']
        assert not memory.list() and len(memory.messages(session['id'])) == 2
        instance.next_check = 0
        assert await instance.tick() is None and len(codex.prompts) == 1
        assert desktop['token'] not in str(instance.records()) + str(instance.status())
    finally:
        if instance: await instance.close()
        memory.close(); app.state.store.close()


async def test_actual_device_revocation_interrupts_running_cli_on_heartbeat(tmp_path):
    app = create_app(tmp_path / 'cloud.sqlite3', 'private-fixture-admin-' * 2, allowed_hosts=('sync.example',))
    transport = httpx.ASGITransport(app=app)
    codex = Codex(); codex.block = True
    instance = None
    try:
        async with httpx.AsyncClient(transport=transport, base_url='https://sync.example',
                                     headers={'Authorization': 'Bearer ' + 'private-fixture-admin-' * 2}) as admin:
            desktop = (await admin.post('/sync/v1/devices', json={
                'name': 'fixture-computer', 'kind': 'desktop', 'workspace_id': 'workspace-main'})).json()
            phone = (await admin.post('/sync/v1/devices', json={'name': 'fixture-phone', 'kind': 'phone'})).json()
            phone_client = CloudClient('https://sync.example', phone['token'], transport=transport)
            await phone_client.push_events([{'event_id': 'revocation-session', 'entity': 'session',
                'entity_id': 'session-main', 'operation': 'upsert', 'base_revision': 0,
                'payload': {'title': 'fixture task session', 'created_at': '2026-10-26T00:00:00+00:00'}}])
            await phone_client.submit_task({'id': 'revoked-task', 'workspace_id': 'workspace-main',
                                           'session_id': 'session-main', 'prompt': '一个明确项目任务'})
            instance = CloudTaskWorker(CloudClient('https://sync.example', desktop['token'], transport=transport),
                                       codex, None, data_dir=tmp_path / 'worker', heartbeat_interval=.01)
            instance.set_enabled(True)
            running = asyncio.create_task(instance.tick())
            await asyncio.wait_for(codex.entered.wait(), 1)
            assert (await admin.delete('/sync/v1/devices/' + desktop['id'])).status_code == 200
            await asyncio.wait_for(running, 1)
            assert codex.finished == [True] and not codex.lock.locked()
            assert instance.records()[0]['phase'] == 'interrupted'
            assert (await phone_client.get_task('revoked-task'))['task']['status'] == 'interrupted'
    finally:
        if instance: await instance.close()
        app.state.store.close()


async def test_result_output_is_bounded_and_missing_local_session_is_not_invented(tmp_path):
    client, codex = Client(), Codex()
    codex.text = '界' * 100000
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    instance = worker(tmp_path, client, codex, memory)
    try:
        instance.set_enabled(True); await instance.tick()
        output = instance.records()[0]['result']
        assert len(output) <= 64000 and '结果过长' in output
        assert not memory.sessions() and not memory.list()
    finally:
        await instance.close(); memory.close()


async def test_only_claimed_explicit_task_preempts_background_before_start(tmp_path):
    client, calls = Client(), []
    async def prepare():
        assert [entry[0] for entry in client.calls] == ['claim']
        calls.append('background-cleaned')
    instance = worker(tmp_path, client, before_execute=prepare)
    try:
        instance.set_enabled(True); await instance.tick()
        assert calls == ['background-cleaned']
        instance.next_check = 0
        await instance.tick()
        assert calls == ['background-cleaned']  # Empty polls do not stop the observer.
    finally: await instance.close()


async def test_long_unicode_result_keeps_full_journal_but_sync_safe_chat_preview(tmp_path):
    memory = MemoryStore(tmp_path / 'memory.sqlite3')
    session = memory.create_session()
    client, codex = Client(task(session['id'])), Codex()
    codex.text = '😀界' * 20000  # Under task character limit, far over message-byte budget.
    instance = worker(tmp_path, client, codex, memory)
    try:
        instance.set_enabled(True)
        assert (await instance.tick())['phase'] == 'succeeded'
        assert instance.records()[0]['result'] == codex.text
        assert client.calls[-1][3] == codex.text
        messages = memory.messages(session['id'])
        assert len(messages) == 2
        assert '完整结果见云任务或本机任务记录' in messages[-1]['content']
        for message in messages:
            payload = {key: message[key] for key in ('session_id', 'role', 'content', 'created_at')}
            validate_payload('message', 'upsert', payload)
            assert len(json.dumps(payload, ensure_ascii=False).encode()) <= 64000
    finally:
        await instance.close(); memory.close()
