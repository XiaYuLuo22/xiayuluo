import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import sys

import pytest

from assistant.config import Settings
from assistant.tools import CodexChat
from conftest import events


def setup_chat(client_factory, tmp_path, **options):
    client, records = client_factory(settings=Settings(api_key='', data_dir=str(tmp_path), **options))
    engine = client.app.state.codex_chat
    engine.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    session = client.post('/api/sessions').json()['id']
    return client, engine, session, records


def proposal(client, session, message='你好', **extra):
    response = client.post('/api/chat', json={'session_id': session, 'message': message, **extra})
    assert response.status_code == 200, response.text
    return next(packet['action'] for packet in events(response) if packet['type'] == 'action')


def test_no_api_key_chat_proposes_but_does_not_execute(client_factory, tmp_path):
    client, engine, session, records = setup_chat(client_factory, tmp_path)
    action = proposal(client, session)
    assert action['tool'] == 'codex_chat'
    assert not records
    history = client.get(f'/api/sessions/{session}').json()['messages']
    assert not any('chat fixture:' in message['content'] for message in history)
    assert client.get('/api/actions').json()[0]['id'] == action['id']
    result = events(client.post(f"/api/actions/{action['id']}/approve"))
    assert result[-1]['type'] == 'done'
    assert any('chat fixture: 你好' in packet.get('text', '') for packet in result)
    assert not engine.lock.locked()
    assert not records


def test_context_and_memory_are_reviewable_and_preserved(client_factory, tmp_path):
    client, _, session, _ = setup_chat(client_factory, tmp_path)
    client.post('/api/memories', json={'tier': 'permanent', 'content': '我喜欢喝茶'})
    first = proposal(client, session, '你好我叫小鱼')
    client.post(f"/api/actions/{first['id']}/approve")
    second = proposal(client, session, '我喜欢喝什么？')
    prompt = second['arguments']['prompt']
    assert '我喜欢喝茶' in prompt
    assert '你好我叫小鱼' in prompt
    assert 'chat fixture:' in prompt
    assert '我喜欢喝什么' in prompt
    assert any(item['content'] == '我喜欢喝茶' and item['tier'] == 'permanent'
               for item in client.get('/api/memories').json()['items'])


def test_decline_pending_dedup_and_screenshot_rejection(client_factory, tmp_path):
    client, _, session, _ = setup_chat(client_factory, tmp_path)
    action = proposal(client, session)
    assert client.post('/api/chat', json={'session_id': session, 'message': '另一条'}).status_code == 409
    assert client.delete(f"/api/actions/{action['id']}").status_code == 200
    assert client.post('/api/chat', json={'session_id': session, 'message': '看图', 'screenshot_id': 'no-snapshot'}).status_code == 409
    assert len(client.get(f'/api/sessions/{session}').json()['messages']) == 2
    proposal(client, session, '取消后可继续')


def test_explicit_codex_does_not_use_configured_api(client_factory, tmp_path):
    client, records = client_factory(settings=Settings(api_key='do-not-use-this-test-key', data_dir=str(tmp_path)))
    client.app.state.codex_chat.command_prefix = lambda: ['fixture-only-no-execution']
    session = client.post('/api/sessions').json()['id']
    assert proposal(client, session, provider='codex')['tool'] == 'codex_chat'
    assert not records
    status = client.get('/api/status').json()
    assert 'do-not-use-this-test-key' not in json.dumps(status)


def test_configured_codex_default_never_auto_uses_api(client_factory, tmp_path):
    client, records = client_factory(settings=Settings(api_key='not-for-default-chat', chat_provider='codex', data_dir=str(tmp_path)))
    client.app.state.codex_chat.command_prefix = lambda: ['fixture-only-no-execution']
    session = client.post('/api/sessions').json()['id']
    assert proposal(client, session)['tool'] == 'codex_chat'
    assert not records


def test_unavailable_cli_does_not_fall_back_to_paid_api(client_factory, tmp_path):
    client, _, session, records = setup_chat(client_factory, tmp_path)
    def unavailable():
        raise ValueError('未找到 Codex CLI，请安装并登录。')
    client.app.state.codex_chat.command_prefix = unavailable
    response = client.post('/api/chat', json={'session_id': session, 'message': '你好'})
    assert response.status_code == 409
    assert 'Codex CLI' in response.json()['detail']
    assert not records
    assert client.get(f'/api/sessions/{session}').json()['messages'] == []


def test_api_provider_still_requires_key(client_factory, tmp_path):
    client, _, session, _ = setup_chat(client_factory, tmp_path)
    response = client.post('/api/chat', json={'session_id': session, 'message': '你好', 'provider': 'api'})
    assert response.status_code == 409
    assert 'OPENAI_API_KEY' in response.json()['detail']
    assert client.post('/api/chat', json={'session_id': session, 'message': '你好', 'provider': 'invalid'}).status_code == 422


def test_command_safety_and_environment_does_not_reuse_api_key(monkeypatch):
    engine = CodexChat(Settings(codex_sandbox='workspace-write'))
    engine.command_prefix = lambda: ['codex']
    monkeypatch.setenv('OPENAI_API_KEY', 'not-for-codex-chat')
    monkeypatch.setenv('ASSISTANT_MODEL_KEY', 'not-for-codex-chat')
    command = engine.execution_command(Path('/isolated-dialogue'))
    assert command[command.index('--sandbox') + 1] == 'read-only'
    assert 'workspace-write' not in command
    assert '--ignore-user-config' in command and '--strict-config' in command and '--ephemeral' in command
    assert 'forced_login_method="chatgpt"' in command
    assert 'approval_policy="never"' in command
    assert 'mcp_servers={}' in command
    disabled = {command[index + 1] for index, flag in enumerate(command) if flag == '--disable'}
    assert {'shell_tool', 'apps', 'plugins', 'browser_use', 'computer_use', 'multi_agent', 'hooks', 'js_repl'} <= disabled
    assert not any('dangerously' in argument for argument in command)
    environment = engine.execution_environment()
    assert 'OPENAI_API_KEY' not in environment and 'ASSISTANT_MODEL_KEY' not in environment
    assert 'not-for-codex-chat' not in ' '.join(command)


async def test_dialogue_process_cancel_removes_temporary_workspace(tmp_path):
    engine = CodexChat(Settings())
    engine.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    directories = []
    build = engine.execution_command
    def capture(workspace):
        directories.append(workspace)
        return build(workspace)
    engine.execution_command = capture
    prompt = 'conversation\n' + json.dumps([{'role': 'user', 'content': 'slow'}])
    async with aclosing(engine.events(prompt)) as stream:
        assert (await asyncio.wait_for(anext(stream), 5))['type'] == 'status'
        assert directories[0].is_dir()
        assert list(directories[0].iterdir()) == []
    assert not directories[0].exists()
    assert not engine.lock.locked()


def test_cli_failure_does_not_claim_completed_reply(client_factory, tmp_path):
    client, _, session, _ = setup_chat(client_factory, tmp_path)
    action = proposal(client, session, 'fail')
    result = events(client.post(f"/api/actions/{action['id']}/approve"))
    assert result[-1]['type'] == 'error'
    assert not any(packet['type'] == 'done' for packet in result)
    assert '[工具未完成' in client.get(f'/api/sessions/{session}').json()['messages'][-1]['content']


def test_api_key_login_is_rejected_without_leaking_status_output(client_factory, tmp_path):
    client, engine, session, records = setup_chat(client_factory, tmp_path)
    engine.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py'), '--fixture-api-login']
    action = proposal(client, session)
    result = client.post(f"/api/actions/{action['id']}/approve")
    assert events(result)[-1]['type'] == 'error'
    assert '只接受 ChatGPT 登录' in result.text
    assert 'REDACTED_TEST_KEY' not in result.text
    assert 'chat fixture:' not in result.text
    assert not records


def test_settings_provider_validation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CHAT_PROVIDER', 'codex')
    assert Settings.load().chat_provider == 'codex'
    monkeypatch.setenv('CHAT_PROVIDER', 'invalid')
    with pytest.raises(ValueError, match='CHAT_PROVIDER'):
        Settings.load()


def test_expired_dialogue_not_executed_and_new_request_allowed(client_factory, tmp_path):
    client, _, session, _ = setup_chat(client_factory, tmp_path)
    action = proposal(client, session)
    client.app.state.actions.items[action['id']]['created'] -= 121
    assert client.post(f"/api/actions/{action['id']}/approve").status_code == 404
    assert proposal(client, session, '重新发送')['id'] != action['id']


def test_phone_can_propose_dialogue_but_only_computer_can_approve(client_factory, tmp_path):
    client, _, session, records = setup_chat(client_factory, tmp_path, mobile_enabled=True)
    invite = client.post('/api/devices/invitations').json()['code']
    device = client.post('https://phone.example/mobile/pair', json={'code': invite, 'name': '我的手机'}).json()
    headers = {'Authorization': 'Bearer ' + device['token']}
    base = 'https://phone.example/mobile/v1'
    response = client.post(base + '/chat', headers=headers, json={'session_id': session, 'message': '手机日常问题'})
    action = next(packet['action'] for packet in events(response) if packet['type'] == 'action')
    assert action['tool'] == 'codex_chat'
    assert client.post(base + f"/actions/{action['id']}/approve", headers=headers).status_code == 403
    assert events(client.post(f"/api/actions/{action['id']}/approve"))[-1]['type'] == 'done'
    assert 'chat fixture: 手机日常问题' in client.get(base + f'/sessions/{session}', headers=headers).json()['messages'][-1]['content']
    assert not records


def test_startup_connect_only_checks_login_and_never_sends_a_question(client_factory, tmp_path):
    client, _, _, records = setup_chat(client_factory, tmp_path)
    before = client.get('/api/sessions').json()
    response = client.post('/api/codex/connect')
    assert response.status_code == 200
    assert response.json()['phase'] == 'ready' and response.json()['logged_in']
    assert response.json()['scope'] == 'cli-login-only'
    assert response.json()['checked_at']
    assert client.get('/api/status').json()['model']['codex_connection'] == response.json()
    assert client.get('/api/sessions').json() == before
    assert not client.get('/api/actions').json() and not records


def test_cached_connect_does_not_authorize_chat_after_login_changes(client_factory, tmp_path):
    client, engine, session, records = setup_chat(client_factory, tmp_path)
    checks = []
    original = engine._check_login
    async def check():
        checks.append(True)
        return await original()
    engine._check_login = check
    assert client.post('/api/codex/connect').json()['logged_in']
    assert client.post('/api/codex/connect').json()['logged_in']
    assert len(checks) == 1
    engine.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py'), '--fixture-api-login']
    action = proposal(client, session)
    result = client.post(f"/api/actions/{action['id']}/approve")
    assert events(result)[-1]['type'] == 'error' and len(checks) == 2
    state = client.get('/api/status').json()['model']['codex_connection']
    assert state['phase'] == 'attention' and not state['logged_in']
    assert 'REDACTED_TEST_KEY' not in result.text + json.dumps(state)
    assert not records


def test_manual_connect_recheck_bypasses_diagnostic_cache(client_factory, tmp_path):
    client, engine, _, records = setup_chat(client_factory, tmp_path)
    assert client.post('/api/codex/connect').json()['logged_in']
    engine.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py'), '--fixture-api-login']
    assert client.post('/api/codex/connect').json()['logged_in']
    state = client.post('/api/codex/connect?refresh=true').json()
    assert not state['logged_in'] and state['phase'] == 'attention'
    assert 'REDACTED_TEST_KEY' not in json.dumps(state)
    assert not records


@pytest.mark.parametrize('problem', ['missing', 'api-login'])
def test_connect_failures_are_sanitized_and_do_not_change_provider(client_factory, tmp_path, problem):
    client, records = client_factory(settings=Settings(api_key='never-print-api-key', chat_provider='api', data_dir=str(tmp_path)))
    engine = client.app.state.codex_chat
    if problem == 'missing':
        def missing(): raise ValueError('未找到官方 Codex CLI。')
        engine.command_prefix = missing
    else:
        engine.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py'), '--fixture-api-login']
    response = client.post('/api/codex/connect')
    assert response.json()['phase'] == 'attention' and not response.json()['logged_in']
    assert 'REDACTED_TEST_KEY' not in response.text and 'never-print-api-key' not in response.text
    assert client.get('/api/status').json()['model']['provider'] == 'api'
    assert not records and not client.get('/api/actions').json()


def test_connect_requires_local_authorization_and_is_not_a_mobile_control(client_factory, tmp_path):
    client, _, _, _ = setup_chat(client_factory, tmp_path, mobile_enabled=True)
    assert client.post('/api/codex/connect', headers={'X-Assistant-Token': ''}).status_code == 403
    from test_mobile import pair, phone
    headers = phone(client, pair(client)['token'])
    assert client.post('https://phone.example/mobile/v1/codex/connect', headers=headers).status_code == 403


async def test_cancelled_login_probe_releases_lock_and_does_not_report_ready():
    engine = CodexChat(Settings())
    entered = asyncio.Event()
    async def pending():
        entered.set()
        await asyncio.Event().wait()
    engine._check_login = pending
    task = asyncio.create_task(engine.check_login(cached=True))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert not engine.login_lock.locked()
    assert engine.connection_status()['phase'] == 'unchecked'
    assert not engine.connection_status()['logged_in']
