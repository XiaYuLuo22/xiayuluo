"""Local observation/greeting grants never leak through the paired gateway."""
import asyncio
from types import SimpleNamespace

import pytest

from assistant.config import Settings
from conftest import events
from test_mobile import pair, phone
from test_speech_api import attach


def enable_fixture(client):
    app = client.app
    app.state.native_window_open = True
    app.state.desktop.available = lambda: (True, '')
    observer = app.state.proactive
    observer.screen.platform = 'win32'
    observer.screen.enabled = True
    return observer


def test_observation_off_by_default_and_unavailable_in_headless(client_factory):
    client, records = client_factory()
    state = client.get('/api/proactive').json()
    assert not state['enabled'] and not state['available']
    assert state['max_analyses_per_hour'] == 12
    assert state['max_notices_per_hour'] == 3
    assert state['all_activity_visible'] is False
    assert client.post('/api/proactive', json={'enabled': True}).status_code == 409
    assert client.get('/api/proactive/events').json()['events'] == []
    assert not records and not client.app.state.mouse_control.enabled
    assert client.post('/api/greeting').json() == {'greeting': None, 'pending': False}


@pytest.mark.parametrize('path,method,body', [('/api/proactive', 'get', None),
    ('/api/proactive', 'post', {'enabled': True}), ('/api/proactive/events', 'get', None),
    ('/api/greeting', 'post', {})])
def test_attention_routes_require_local_auth(client_factory, path, method, body):
    client, _ = client_factory()
    enable_fixture(client)
    kwargs = {'json': body} if method == 'post' else {}
    assert getattr(client, method)(path, headers={'X-Assistant-Token': ''}, **kwargs).status_code == 403


@pytest.mark.parametrize('body', [{'enabled': 'true'}, {'enabled': 1}, {'enabled': True, 'interval': 1},
    {'enabled': True, 'provider': 'unknown'}])
def test_observation_payload_is_strict(client_factory, body):
    client, _ = client_factory()
    enable_fixture(client)
    assert client.post('/api/proactive', json=body).status_code == 422


def test_observer_grant_separate_from_mouse_and_provider_does_not_fallback(client_factory):
    client, records = client_factory()
    observer = enable_fixture(client)
    original = observer.generation
    state = client.post('/api/proactive', json={'enabled': True, 'provider': 'codex'}).json()
    assert state['enabled'] and state['provider'] == 'codex'
    assert state['generation'] > original
    assert not client.app.state.mouse_control.enabled
    assert client.app.state.desktop.snapshots == {}
    assert not records
    assert client.get('/api/proactive').json()['provider'] == 'codex'
    client.app.state.updating = True
    assert client.post('/api/proactive', json={'enabled': True}).status_code == 409
    assert client.post('/api/proactive', json={'enabled': False}).status_code == 200


def test_non_windows_native_window_cannot_claim_observation(client_factory):
    client, _ = client_factory()
    client.app.state.native_window_open = True
    client.app.state.desktop.available = lambda: (True, '')
    client.app.state.proactive.screen.platform = 'darwin'
    assert client.post('/api/proactive', json={'enabled': True}).status_code == 409


@pytest.mark.parametrize('method,path,body', [('get', 'proactive', None),
    ('post', 'proactive', {'enabled': True}), ('get', 'proactive/events', None),
    ('post', 'greeting', {})])
def test_phone_cannot_access_observation_or_make_desktop_greet(client_factory, tmp_path, method, path, body):
    client, _ = client_factory(settings=Settings(api_key='fixture', data_dir=str(tmp_path), mobile_enabled=True))
    enable_fixture(client)
    native = attach(client)
    headers = phone(client, pair(client)['token'])
    kwargs = {'json': body} if method == 'post' else {}
    assert getattr(client, method)('https://phone.example/mobile/v1/' + path, headers=headers,
                                    **kwargs).status_code == 403
    assert getattr(client, method)('/api/' + path, headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'},
                                    **kwargs).status_code == 403
    assert not native.calls and not client.app.state.proactive.enabled


def test_native_greeting_text_and_local_audio_once_never_saved(client_factory):
    client, records = client_factory()
    native = attach(client)
    client.app.state.presentation = 'desktop'
    assert client.post('/api/greeting').json()['pending']
    assert not native.calls
    client.app.state.native_window_open = True
    greeting = client.post('/api/greeting').json()
    assert greeting['greeting']['id'] == 'startup' and not greeting['pending']
    assert '夏鱼洛' in greeting['greeting']['message']
    assert native.calls == [(greeting['greeting']['message'], 'greeting', 0)]
    assert client.post('/api/greeting').json() == {'greeting': None, 'pending': False}
    assert len(native.calls) == 1 and not records
    assert client.get('/api/sessions').json() == []
    assert client.get('/api/memories').json()['items'] == []
    assert client.app.state.voice is None


@pytest.mark.parametrize('mode', ['muted', 'failure', 'updating'])
def test_greeting_audio_unavailable_does_not_break_text_or_retry(client_factory, mode):
    client, _ = client_factory()
    native = attach(client)
    client.app.state.native_window_open = True
    if mode == 'muted':
        native.enabled = False
    elif mode == 'failure':
        native.fail = RuntimeError('private-audio-driver-details')
    else:
        client.app.state.updating = True
    result = client.post('/api/greeting')
    assert result.status_code == 200 and 'private-audio' not in result.text
    assert bool(result.json()['greeting']) == (mode != 'updating')
    assert not native.calls
    assert not client.post('/api/greeting').json()['greeting']


def test_user_question_suppresses_delayed_startup_greeting(client_factory):
    client, _ = client_factory()
    native = attach(client)
    session = client.post('/api/sessions').json()['id']
    assert events(client.post('/api/chat', json={'session_id': session, 'message': '你好'}))[-1]['type'] == 'done'
    client.app.state.native_window_open = True
    assert not client.post('/api/greeting').json()['greeting']
    assert not native.calls


def test_greeting_waits_for_system_audio_initialization_without_claiming_or_replaying(client_factory):
    client, _ = client_factory()
    native = attach(client)
    client.app.state.native_window_open = True
    original = native.status
    native.status = lambda: {**original(), 'available': False, 'phase': 'starting'}
    assert client.post('/api/greeting').json() == {'greeting': None, 'pending': True}
    assert not client.app.state.greeting_claimed and not native.calls
    native.status = original
    assert client.post('/api/greeting').json()['greeting']['id'] == 'startup'
    assert len(native.calls) == 1
    assert not client.post('/api/greeting').json()['greeting']


def test_greeting_does_not_wait_forever_for_a_broken_audio_driver(client_factory):
    client, _ = client_factory()
    native = attach(client)
    client.app.state.native_window_open = True
    native.status = lambda: {'available': False, 'phase': 'starting', 'enabled': True}
    client.app.state.greeting_wait_started = -100
    assert client.post('/api/greeting').json()['greeting']['id'] == 'startup'
    assert not native.calls


def test_new_chat_awaits_background_lock_cleanup_before_codex(client_factory):
    client, _ = client_factory()
    observer = enable_fixture(client)
    observer.set_enabled(True)
    lock = client.app.state.codex.lock
    cleanup = []

    async def background():
        async with lock:
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(.05)
                cleanup.append('released')

    async def launch():
        observer.task = asyncio.create_task(background())
        await asyncio.sleep(0)

    client.portal.call(launch)
    assert lock.locked()
    session = client.post('/api/sessions').json()['id']
    result = events(client.post('/api/chat', json={'session_id': session, 'message': '你好'}))
    assert result[-1]['type'] == 'done'
    assert cleanup == ['released'] and not lock.locked()
    assert observer.enabled


def test_transient_events_not_chat_memory_and_off_clears(client_factory):
    client, records = client_factory()
    observer = enable_fixture(client)
    now = [0]
    observer.clock, observer.random = lambda: now[0], lambda: 0
    observer.capture = lambda: {'width': 80, 'height': 80, 'png': b'fixture', 'fingerprint': 'new'}

    async def plan(*args):
        return {'kind': 'question', 'message': '需要我帮你检查这个问题吗？'}

    observer.planner = SimpleNamespace(plan=plan)
    assert client.post('/api/proactive', json={'enabled': True}).status_code == 200
    now[0] = 121
    client.portal.call(observer.tick)
    result = client.get('/api/proactive/events?after=0').json()
    assert result['events'][0]['kind'] == 'question' and result['cursor'] == 1
    assert client.get('/api/proactive/events?after=1').json()['events'] == []
    assert client.get('/api/proactive/events?after=-1').status_code == 422
    assert not records and client.get('/api/sessions').json() == []
    assert client.get('/api/memories').json()['items'] == []
    assert client.post('/api/proactive', json={'enabled': False}).status_code == 200
    assert client.get('/api/proactive/events').json()['events'] == []


def test_greeting_and_observation_request_schemas_cannot_change_other_permissions(client_factory):
    client, _ = client_factory()
    assert client.post('/api/proactive', json={'enabled': True, 'mouse_control': True}).status_code == 422
    assert not client.app.state.mouse_control.enabled
