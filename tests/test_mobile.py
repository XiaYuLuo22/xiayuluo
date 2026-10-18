import hashlib
import asyncio
import json
from pathlib import Path
import socket
import sys
from threading import Thread
import time

import httpx
import uvicorn

from assistant.config import Settings
from assistant.devices import Devices
from conftest import events


def phone(client, token=None):
    return {"Authorization": f"Bearer {token}"} if token else {}


def pair(client, name="我的手机"):
    invitation = client.post('/api/devices/invitations').json()
    response = client.post('https://phone.example/mobile/pair',
                           json={"code": invitation['code'], "name": name})
    assert response.status_code == 200, response.text
    return response.json()


def mobile_client(client_factory, tmp_path):
    return client_factory(settings=Settings(api_key="fixture", data_dir=str(tmp_path), mobile_enabled=True))[0]


def test_mobile_disabled_and_invites_require_local_authorization(client_factory):
    client, _ = client_factory()
    assert client.get('https://phone.example/mobile/v1/sessions').status_code == 404
    assert client.post('/api/devices/invitations').status_code == 409
    assert client.get('/api/devices', headers={'X-Assistant-Token': ''}).status_code == 403


def test_pairing_single_use_expiry_hashing_and_tls(client_factory, tmp_path):
    client = mobile_client(client_factory, tmp_path)
    invitation = client.post('/api/devices/invitations').json()
    body = {"code": invitation['code'], "name": '手机'}
    assert client.post('http://phone.example/mobile/pair', json=body).status_code == 426
    assert client.post('https://phone.example/mobile/pair', content=b'a' * 65537).status_code == 413
    expired = client.post('/api/devices/invitations').json()
    key = hashlib.sha256(expired['code'].encode()).hexdigest()
    client.app.state.devices.invitations[key] = time.monotonic() - 1
    assert client.post('https://phone.example/mobile/pair', json={**body, 'code': expired['code']}).status_code == 400
    response = client.post('https://phone.example/mobile/pair', json=body).json()
    assert client.post('https://phone.example/mobile/pair', json=body).status_code == 400
    listing = client.get('/api/devices').json()
    assert response['token'] not in str(listing)
    row = client.app.state.devices.db.execute('SELECT token_hash FROM devices').fetchone()
    assert row[0] != response['token']
    assert row[0] == hashlib.sha256(response['token'].encode()).hexdigest()
    assert client.get('https://phone.example/mobile/v1/status', headers=phone(client, response['token'])).status_code == 200


def test_phone_desktop_share_sessions_stream_and_four_tiers(client_factory, tmp_path):
    client = mobile_client(client_factory, tmp_path)
    credential = pair(client)
    headers = phone(client, credential['token'])
    base = 'https://phone.example/mobile/v1'
    session = client.post(base + '/sessions', headers=headers).json()['id']
    assert client.get('/api/sessions').json()[0]['id'] == session
    response = client.post(base + '/chat', headers=headers, json={'session_id': session, 'message': '手机发起对话'})
    assert response.status_code == 200, response.text
    assert events(response)[-1]['type'] == 'done'
    assert client.get(f'/api/sessions/{session}').json()['messages'][-1]['role'] == 'assistant'
    client.post('/api/memories', json={'tier': 'permanent', 'content': '喜欢喝茶'})
    memory = client.get(base + '/memories', headers=headers).json()
    assert any(item['content'] == '喜欢喝茶' for item in memory['items'])
    assert set(memory['stats']) == {'temporary', 'first', 'second', 'permanent'}
    client.post(base + '/memories', headers=headers, json={'tier': 'second', 'content': '手机记下的偏好'})
    assert any(item['content'] == '手机记下的偏好' for item in client.get('/api/memories').json()['items'])


def test_phone_cannot_read_secrets_or_approve_tasks(client_factory, tmp_path):
    client = mobile_client(client_factory, tmp_path)
    credential = pair(client)
    headers = phone(client, credential['token'])
    base = 'https://phone.example/mobile/v1'
    session = client.post('/api/sessions').json()['id']
    action = client.post('/api/actions', json={'tool': 'remember', 'session_id': session,
                         'arguments': {'tier': 'permanent', 'content': '等待电脑确认'}}).json()
    for path in ('bootstrap', 'devices', 'voice', 'voice/start', 'voice/stop', 'desktop/screenshot', 'status/../bootstrap', 'sessions/../bootstrap'):
        assert client.get(base + '/' + path, headers=headers).status_code in (403, 404)
    assert client.post(base + f"/actions/{action['id']}/approve", headers=headers).status_code == 403
    assert client.post(base + '/voice/start', headers=headers, json={'confirm': True}).status_code == 403
    assert client.delete(base + '/memories', headers=headers).status_code == 403
    assert client.post(base + '/chat', headers=headers, json={'session_id': session, 'message': '看截图',
                       'screenshot_id': 'fixture-screen'}).status_code == 403
    assert client.get('/api/status').json()['memory']['permanent'] == 0
    assert client.get(base + '/actions', headers=headers).json()[0]['id'] == action['id']
    status = client.get(base + '/status', headers=headers).json()
    assert 'runtime' not in status and 'token' not in status and 'codex' not in status
    assert client.post(f"/api/actions/{action['id']}/approve").status_code == 200


def test_revocation_rejects_cached_tokens_and_browser_origin(client_factory, tmp_path):
    client = mobile_client(client_factory, tmp_path)
    credential = pair(client)
    headers = phone(client, credential['token'])
    url = 'https://phone.example/mobile/v1/sessions'
    assert client.get(url, headers={**headers, 'Origin': 'https://attacker.example'}).status_code == 403
    assert client.get(url, headers={'Authorization': 'Bearer invalid'}).status_code == 401
    assert client.delete('/api/devices/' + credential['device']['id']).status_code == 200
    assert client.get(url, headers=headers).status_code == 401
    assert client.get('https://phone.example/api/bootstrap').status_code == 400


def test_devices_persist_but_pending_invitations_do_not(tmp_path):
    store = Devices(tmp_path / 'devices.sqlite3')
    paired = store.pair(store.invite()['code'], 'device')
    invitation = store.invite()
    store.close()
    reopened = Devices(tmp_path / 'devices.sqlite3')
    try:
        assert reopened.authenticate(paired['token'])['name'] == 'device'
        assert not reopened.invitations
        assert invitation['code'] not in str(reopened.list())
        reopened.db.execute("UPDATE devices SET expires_at='2000-01-01'")
        assert reopened.authenticate(paired['token']) is None
    finally:
        reopened.close()


def test_phone_codex_task_only_executes_after_desktop_confirmation(client_factory, tmp_path):
    client, _ = client_factory(settings=Settings(data_dir=str(tmp_path), mobile_enabled=True,
                                                codex_workspace=str(tmp_path)))
    client.app.state.codex.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    headers = phone(client, pair(client)['token'])
    base = 'https://phone.example/mobile/v1'
    session = client.post(base + '/sessions', headers=headers).json()['id']
    proposed = events(client.post(base + '/chat', headers=headers,
                      json={'session_id': session, 'message': '检查项目结构', 'mode': 'codex'}))
    action = next(event['action'] for event in proposed if event['type'] == 'action')
    assert 'fixture:' not in str(client.get(f'/api/sessions/{session}').json())
    assert client.post(base + f"/actions/{action['id']}/approve", headers=headers).status_code == 403
    executed = events(client.post(f"/api/actions/{action['id']}/approve"))
    assert any('fixture: 检查项目结构' in event.get('text', '') for event in executed)
    assert 'fixture: 检查项目结构' in str(client.get(base + f'/sessions/{session}', headers=headers).json())


def test_gateway_forwards_text_before_stream_finishes(client_factory, tmp_path):
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"},"finish_reason":null}]}\n\n'
            await asyncio.sleep(.8)
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    client, _ = client_factory(settings=Settings(api_key='fixture', data_dir=str(tmp_path), mobile_enabled=True),
        handler=lambda request: httpx.Response(200, stream=Slow(), headers={'Content-Type': 'text/event-stream'}))
    credential = pair(client)
    session = client.post('/api/sessions').json()['id']
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(client.app, log_level='error', lifespan='off'))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    first = None
    try:
        url = f'http://127.0.0.1:{sock.getsockname()[1]}/mobile/v1/chat'
        with httpx.Client(trust_env=False, timeout=5) as transport:
            started = time.monotonic()
            with transport.stream('POST', url, headers={**phone(client, credential['token']), 'X-Forwarded-Proto': 'https'},
                                  json={'session_id': session, 'message': '流式检查'}) as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        event = json.loads(line[6:])
                        if event['type'] == 'text':
                            first = time.monotonic() - started
                            # The local desktop and phone hit the exact same active-session lock.
                            assert client.post('/api/chat', json={'session_id': session, 'message': '并发消息'}).status_code == 409
                        if event['type'] == 'done': break
            elapsed = time.monotonic() - started
        assert first is not None and elapsed - first > .5, (first, elapsed)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
