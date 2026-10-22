"""The chat toggle authorizes one fresh, local mouse plan, never ambient screen access."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from assistant.config import Settings
from conftest import events
from test_mobile import pair, phone
from test_tools import FakeDesktop


def ready_desktop(client):
    desktop = client.app.state.desktop
    desktop.enabled, desktop.driver = True, FakeDesktop()
    return desktop.driver


def enable(client, enabled=True):
    response = client.post('/api/mouse-control', json={'enabled': enabled})
    assert response.status_code == 200, response.text
    assert response.json()['enabled'] is enabled
    return response.json()


def stub_plan(client, *, action='click', message='已规划，不代表已经操作。'):
    calls = []

    async def plan(text, snapshot, provider, transport=None):
        calls.append((text, snapshot, provider))
        return {'action': action, 'x': 30 if action != 'noop' else None,
                'y': 40 if action != 'noop' else None, 'scroll': 0,
                'message': message, 'width': snapshot['width'], 'height': snapshot['height']}

    client.app.state.mouse_planner.plan = plan
    return calls


def chat(client, session, **extra):
    return client.post('/api/chat', json={'session_id': session, 'message': '点击屏幕上的设置按钮',
                                         'mouse_control': True, **extra})


def test_mouse_toggle_is_off_authenticated_and_blocked_during_update(client_factory):
    client, _ = client_factory()
    ready_desktop(client)
    assert client.get('/api/status').json()['mouse_control']['enabled'] is False
    assert client.post('/api/desktop/screenshot').status_code == 409
    assert client.post('/api/mouse-control', headers={'X-Assistant-Token': ''},
                       json={'enabled': True}).status_code == 403
    assert client.post('/api/mouse-control', json={'enabled': 'yes'}).status_code == 422
    client.app.state.updating = True
    assert client.post('/api/mouse-control', json={'enabled': True}).status_code == 409
    assert client.post('/api/mouse-control', json={'enabled': False}).status_code == 200
    client.app.state.updating = False
    enable(client)
    assert client.get('/api/status').json()['mouse_control']['enabled'] is True
    enable(client, False)


@pytest.mark.parametrize('extra,code', [({}, 409), ({'mode': 'codex'}, 422),
                                      ({'voice_token': 'a' * 32}, 403)])
def test_mouse_rejections_do_not_save_user_message(client_factory, extra, code):
    client, _ = client_factory()
    ready_desktop(client)
    session = client.post('/api/sessions').json()['id']
    assert chat(client, session, **extra).status_code == code
    assert client.get(f'/api/sessions/{session}').json()['messages'] == []


@pytest.mark.parametrize('flag,message', [(False, '今天过得怎么样？'), (True, '今天过得怎么样？'),
                                        (False, '点击屏幕上的设置按钮')])
def test_ordinary_chat_never_captures_or_exposes_mouse_tool(client_factory, monkeypatch, flag, message):
    client, records = client_factory()
    ready_desktop(client)
    enable(client)

    def forbidden():
        raise AssertionError('ordinary chat must not capture the desktop')

    monkeypatch.setattr(client.app.state.mouse_control, 'capture', forbidden)
    session = client.post('/api/sessions').json()['id']
    result = client.post('/api/chat', json={'session_id': session, 'message': message,
                                          'mouse_control': flag})
    assert events(result)[-1]['type'] == 'done'
    assert all(tool['function']['name'] not in {'desktop_mouse', 'desktop_click'}
               for tool in records[0]['tools'])
    assert client.app.state.desktop.snapshots == {}


def test_chat_mouse_auto_executes_one_real_action_and_only_reports_result(client_factory):
    client, records = client_factory(approval_mode='auto')
    driver = ready_desktop(client)
    enable(client)
    calls = stub_plan(client)
    session = client.post('/api/sessions').json()['id']
    result = events(chat(client, session))
    assert result[-1]['type'] == 'done'
    assert driver.clicks == [(30, 40, 'left')]
    assert len(calls) == 1 and calls[0][2] == 'api'
    assert calls[0][1]['control_generation'] == client.app.state.mouse_control.generation
    assert calls[0][1]['png']
    assert not any(item['type'] == 'action' for item in result)
    assert '已规划，不代表已经操作。' not in str(result)
    assert client.get('/api/actions').json() == []
    assert records == []


def test_chat_mouse_manual_keeps_proposal_until_approval(client_factory):
    client, _ = client_factory()
    driver = ready_desktop(client)
    enable(client)
    stub_plan(client)
    session = client.post('/api/sessions').json()['id']
    result = events(chat(client, session))
    action = next(item['action'] for item in result if item['type'] == 'action')
    assert result[-1]['type'] == 'done' and action['tool'] == 'desktop_mouse'
    assert driver.clicks == []
    assert events(client.post(f"/api/actions/{action['id']}/approve"))[-1]['type'] == 'done'
    assert driver.clicks == [(30, 40, 'left')]


def test_codex_mouse_selection_does_not_use_paid_chat_or_switch_to_programming(client_factory, tmp_path):
    client, records = client_factory(settings=Settings(api_key='must-not-be-used', chat_provider='codex',
                                                      data_dir=str(tmp_path)), approval_mode='auto')
    driver = ready_desktop(client)
    enable(client)
    client.app.state.codex_chat.available = lambda: (True, 'fixture')
    calls = stub_plan(client)
    session = client.post('/api/sessions').json()['id']
    assert events(chat(client, session))[-1]['type'] == 'done'
    assert driver.clicks == [(30, 40, 'left')]
    assert calls[0][2] == 'codex' and records == []
    assert client.app.state.mouse_planner.lock is client.app.state.codex.lock


def test_closing_switch_invalidates_old_manual_proposal_even_after_reopening(client_factory):
    client, _ = client_factory()
    driver = ready_desktop(client)
    enable(client)
    stub_plan(client)
    session = client.post('/api/sessions').json()['id']
    action = next(item['action'] for item in events(chat(client, session)) if item['type'] == 'action')
    enable(client, False)
    assert client.app.state.desktop.snapshots == {}
    enable(client)
    assert events(client.post(f"/api/actions/{action['id']}/approve"))[-1]['type'] == 'error'
    assert driver.clicks == []


def test_noop_plan_is_a_reply_not_a_mouse_execution(client_factory):
    client, _ = client_factory(approval_mode='auto')
    driver = ready_desktop(client)
    enable(client)
    stub_plan(client, action='noop', message='没有找到可确认的设置按钮，请说明目标。')
    session = client.post('/api/sessions').json()['id']
    result = events(chat(client, session))
    assert result[-1]['type'] == 'done'
    assert any('没有找到' in item.get('text', '') for item in result)
    assert driver.clicks == [] and client.get('/api/actions').json() == []


def test_disable_cancels_inflight_plan_and_never_clicks(client_factory):
    client, _ = client_factory(approval_mode='auto')
    driver = ready_desktop(client)
    enable(client)
    started, cancelled = Event(), Event()

    async def plan(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client.app.state.mouse_planner.plan = plan
    session = client.post('/api/sessions').json()['id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(chat, client, session)
        assert started.wait(5)
        enable(client, False)
        assert cancelled.wait(5)
        # A cancelled response may close without an SSE error, but must terminate.
        try:
            task.result(timeout=5)
        except asyncio.CancelledError:
            pass
    assert driver.clicks == []
    assert client.app.state.desktop.snapshots == {}
    assert client.get('/api/actions').json() == []


def test_phone_cannot_enable_mouse_or_send_mouse_authorization(client_factory, tmp_path):
    client, _ = client_factory(settings=Settings(api_key='fixture', data_dir=str(tmp_path), mobile_enabled=True))
    ready_desktop(client)
    enable(client)
    headers = phone(client, pair(client)['token'])
    base = 'https://phone.example/mobile/v1'
    session = client.post(base + '/sessions', headers=headers).json()['id']
    assert client.post(base + '/mouse-control', headers=headers, json={'enabled': True}).status_code == 403
    assert client.post(base + '/desktop/screenshot', headers=headers).status_code == 403
    assert client.post(base + '/chat', headers=headers,
                       json={'session_id': session, 'message': '点击屏幕上的设置按钮',
                             'mouse_control': True}).status_code == 403
    assert client.get(f'/api/sessions/{session}').json()['messages'] == []
    assert 'mouse_control' not in client.get(base + '/status', headers=headers).json()
