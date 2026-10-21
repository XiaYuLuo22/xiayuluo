"""Automatic application approval never grants new OS, model, or device privileges."""
import json
from pathlib import Path
import socket
import sys
from threading import Thread
import time

from fastapi.testclient import TestClient
import httpx
import pytest
import uvicorn

from assistant.automation import Automation
from assistant.config import Settings
from assistant.server import create_app
from conftest import events, model_stream
from test_mobile import pair, phone
from test_tools import FakeDesktop


def fixture_cli(client):
    command = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    client.app.state.codex.command_prefix = command
    client.app.state.codex_chat.command_prefix = command


def tool_stream(name, arguments):
    return model_stream({'tool_calls': [{'index': 0, 'function': {
        'name': name, 'arguments': json.dumps(arguments)}}]}, finish='tool_calls')


def test_production_default_directly_answers_without_action_or_api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('APPROVAL_MODE', raising=False)
    assert Settings.load().approval_mode == 'auto'
    assert Settings().approval_mode == 'auto'
    with TestClient(create_app(Settings(data_dir=str(tmp_path))), base_url='http://localhost') as client:
        client.headers['X-Assistant-Token'] = client.get('/api/bootstrap').json()['token']
        fixture_cli(client)
        assert client.get('/api/status').json()['model']['confirmation_required'] is False
        session = client.post('/api/sessions').json()['id']
        response = client.post('/api/chat', json={'session_id': session, 'message': '好久不见'})
        packets = events(response)
        assert packets[-1]['type'] == 'done'
        assert any('chat fixture: 好久不见' in item.get('text', '') for item in packets)
        assert not any(item['type'] == 'action' for item in packets)
        assert '请确认后继续' not in response.text
        assert client.get('/api/actions').json() == []


def test_auto_chat_context_memory_and_paid_api_isolation(client_factory, tmp_path):
    client, api = client_factory(settings=Settings(api_key='never-use', chat_provider='codex',
                                                  data_dir=str(tmp_path)), approval_mode='auto')
    fixture_cli(client)
    prompts, commands = [], []
    engine = client.app.state.codex_chat
    original_events, original_command = engine.events, engine.execution_command
    async def capture(prompt):
        prompts.append(prompt)
        async for item in original_events(prompt):
            yield item
    def command(directory):
        commands.append((directory, original_command(directory)))
        return commands[-1][1]
    engine.events, engine.execution_command = capture, command
    client.post('/api/memories', json={'tier': 'permanent', 'content': '我喜欢喝茶'})
    session = client.post('/api/sessions').json()['id']
    for message in ('你好我叫小鱼', '我喜欢喝什么？'):
        assert events(client.post('/api/chat', json={'session_id': session, 'message': message}))[-1]['type'] == 'done'
    assert '我喜欢喝茶' in prompts[-1] and '你好我叫小鱼' in prompts[-1] and 'chat fixture:' in prompts[-1]
    assert not api and not client.get('/api/actions').json()
    for directory, args in commands:
        assert args[args.index('--sandbox') + 1] == 'read-only'
        assert '--ignore-user-config' in args and '--strict-config' in args
        assert not directory.exists()


@pytest.mark.parametrize('failure', ['fail', 'api-login', 'missing-cli'])
def test_auto_failures_do_not_fall_back_or_report_done(client_factory, tmp_path, failure):
    client, api = client_factory(settings=Settings(api_key='never-use', chat_provider='codex',
                                                  data_dir=str(tmp_path)), approval_mode='auto')
    fixture_cli(client)
    if failure == 'api-login':
        client.app.state.codex_chat.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py'), '--fixture-api-login']
    if failure == 'missing-cli':
        def missing(): raise ValueError('CLI missing')
        client.app.state.codex_chat.command_prefix = missing
    session = client.post('/api/sessions').json()['id']
    response = client.post('/api/chat', json={'session_id': session, 'message': 'fail' if failure == 'fail' else '你好'})
    if failure == 'missing-cli':
        assert response.status_code == 409
    else:
        assert events(response)[-1]['type'] == 'error'
        assert not any(item['type'] == 'done' for item in events(response))
        assert '[回复未完成]' in client.get(f'/api/sessions/{session}').json()['messages'][-1]['content']
    assert 'REDACTED_TEST_KEY' not in response.text
    assert not api and not client.get('/api/actions').json()


def test_paired_phone_auto_executes_only_in_bound_workspace(client_factory, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    client, api = client_factory(settings=Settings(data_dir=str(tmp_path / 'data'), mobile_enabled=True,
        codex_workspace=str(project), codex_sandbox='workspace-write'), approval_mode='auto')
    fixture_cli(client)
    headers = phone(client, pair(client)['token'])
    base = 'https://phone.example/mobile/v1'
    workspace = client.get(base + '/workspace', headers=headers).json()
    assert workspace['approvals'] == 'automatic'
    assert client.get(base + '/status', headers=headers).json()['approvals'] == 'automatic'
    session = client.post(base + '/sessions', headers=headers).json()['id']
    body = {'session_id': session, 'message': 'fixture-write-workspace', 'mode': 'codex'}
    assert client.post(base + '/chat', headers=headers, json={**body, 'workspace_id': '0' * 32}).status_code == 409
    result = events(client.post(base + '/chat', headers=headers, json={**body, 'workspace_id': workspace['id']}))
    assert result[-1]['type'] == 'done' and not any(item['type'] == 'action' for item in result)
    assert (project / 'phone-result.txt').read_text(encoding='utf-8') == 'approved phone instruction'
    assert not (tmp_path / 'phone-result.txt').exists()
    assert client.get(base + f'/sessions/{session}', headers=headers).json() == client.get(f'/api/sessions/{session}').json()
    assert not client.get('/api/actions').json() and not api
    for path in ('automation', 'actions/fake/approve', 'desktop/screenshot'):
        assert client.post(base + '/' + path, headers=headers, json={'mode': 'auto'}).status_code == 403
    assert client.delete('/api/devices/' + client.get('/api/devices').json()['items'][0]['id']).status_code == 200
    assert client.post(base + '/chat', headers=headers, json=body).status_code == 401


def test_auto_remember_executes_once_without_pending_action(client_factory):
    client, _ = client_factory(stream=tool_stream('remember', {'tier': 'permanent', 'content': '我喜欢茶'}), approval_mode='auto')
    session = client.post('/api/sessions').json()['id']
    output = events(client.post('/api/chat', json={'session_id': session, 'message': '永久记住我喜欢茶'}))
    assert output[-1]['type'] == 'done' and not any(item['type'] == 'action' for item in output)
    assert any('已保存到永久记忆' in item.get('text', '') for item in output)
    saved = client.get('/api/memories').json()['items']
    assert len([item for item in saved if item['tier'] == 'permanent' and item['content'] == '我喜欢茶']) == 1
    assert client.get('/api/actions').json() == []


@pytest.mark.parametrize('target', ['valid', 'changed', 'expired', 'unshared'])
def test_auto_click_preserves_snapshot_guards(client_factory, target):
    arguments = {}
    client, _ = client_factory(handler=lambda request: httpx.Response(200, text=tool_stream('desktop_click', arguments)), approval_mode='auto')
    desktop = client.app.state.desktop
    desktop.enabled, desktop.driver = True, FakeDesktop()
    snapshot = client.post('/api/desktop/screenshot').json()
    arguments.update(x=30, y=40, screenshot_id=snapshot['id'])
    if target == 'changed': desktop.driver.image.putpixel((30, 40), (0, 0, 0))
    if target == 'expired': desktop.snapshots[snapshot['id']]['created'] -= 121
    session = client.post('/api/sessions').json()['id']
    response = client.post('/api/chat', json={'session_id': session, 'message': '点击这里',
        **({'screenshot_id': snapshot['id']} if target != 'unshared' else {})})
    if target == 'expired':
        assert response.status_code == 409
    else:
        assert events(response)[-1]['type'] == ('done' if target == 'valid' else 'error')
    assert desktop.driver.clicks == ([(30, 40, 'left')] if target == 'valid' else [])
    assert not client.get('/api/actions').json()


def test_approval_preferences_persist_validate_and_fail_closed(tmp_path, monkeypatch):
    policy = Automation(tmp_path)
    assert policy.automatic
    policy.set_mode('manual')
    assert Automation(tmp_path).mode == 'manual'
    with pytest.raises(ValueError): policy.set_mode('yes')
    original = policy.path.read_bytes()
    def deny(*args): raise OSError('fixture write denied')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', deny)
        with pytest.raises(OSError): policy.set_mode('auto')
    assert policy.mode == 'manual' and policy.path.read_bytes() == original
    assert not list(tmp_path.glob('automation-*.tmp'))
    policy.path.write_text('{broken', encoding='utf-8')
    closed = Automation(tmp_path)
    assert not closed.automatic and closed.error


def test_desktop_can_switch_without_replaying_pending_actions(client_factory):
    client, _ = client_factory(approval_mode='auto')
    assert client.post('/api/automation', json={'mode': 'invalid'}).status_code == 422
    assert client.post('/api/automation', headers={'X-Assistant-Token': ''}, json={'mode': 'manual'}).status_code == 403
    assert client.post('/api/automation', json={'mode': 'manual'}).json()['mode'] == 'manual'
    session = client.post('/api/sessions').json()['id']
    action = client.post('/api/actions', json={'tool': 'remember', 'session_id': session,
        'arguments': {'tier': 'permanent', 'content': '等待处理'}}).json()
    assert client.post('/api/automation', json={'mode': 'auto'}).json()['mode'] == 'auto'
    assert client.get('/api/actions').json()[0]['id'] == action['id']
    assert client.get('/api/status').json()['memory']['permanent'] == 0


def test_auto_http_stop_releases_cli_lock_and_prevents_mode_change_inflight(client_factory, tmp_path):
    client, _ = client_factory(settings=Settings(data_dir=str(tmp_path)), approval_mode='auto')
    fixture_cli(client)
    session = client.post('/api/sessions').json()['id']
    headers = {'X-Assistant-Token': client.headers['X-Assistant-Token']}
    engine = client.app.state.codex_chat
    directories = []
    original = engine.execution_command
    def command(directory):
        directories.append(directory)
        return original(directory)
    engine.execution_command = command
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(client.app, log_level='error', lifespan='off'))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    try:
        assert server.started
        base = f'http://127.0.0.1:{sock.getsockname()[1]}'
        started = False
        with httpx.Client(base_url=base, headers=headers, trust_env=False, timeout=5) as transport:
            with transport.stream('POST', '/api/chat', json={'session_id': session, 'message': 'slow'}) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    if not line.startswith('data: '): continue
                    packet = json.loads(line[6:])
                    if packet.get('type') == 'status' and 'Codex 正在处理任务' in packet.get('message', ''):
                        started = True
                        assert transport.post('/api/automation', json={'mode': 'manual'}).status_code == 409
                        assert transport.post('/api/chat', json={'session_id': session, 'message': '不能并发'}).status_code == 409
                        break
            assert started
            deadline = time.monotonic() + 5
            while engine.lock.locked() and time.monotonic() < deadline:
                time.sleep(.01)
            assert not engine.lock.locked()
            assert directories and all(not directory.exists() for directory in directories)
            result = events(transport.post('/api/chat', json={'session_id': session, 'message': '停止后重新问'}))
            assert result[-1]['type'] == 'done'
            assert any('chat fixture: 停止后重新问' in item.get('text', '') for item in result)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_invalid_approval_environment_rejected(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('APPROVAL_MODE', 'unrestricted')
    with pytest.raises(ValueError, match='APPROVAL_MODE'):
        Settings.load()
