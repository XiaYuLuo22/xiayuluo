"""Natural initiative uses current grants, input yielding and one completed turn."""
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import sys
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import events
from test_proactive_api import enable_fixture
from test_speech_api import attach
from test_tools import FakeDesktop


def native(client):
    app = client.app
    app.state.presentation = 'desktop'
    observer = enable_fixture(client)
    app.state.desktop.enabled, app.state.desktop.driver = True, FakeDesktop()
    now = [0.0]
    observer.clock = lambda: now[0]

    async def pause_scheduler():
        observer.scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await observer.scheduler

    client.portal.call(pause_scheduler)
    return observer, now


def capabilities(content):
    raw = content.split('状态不是执行授权）：\n', 1)[1]
    return json.JSONDecoder().raw_decode(raw)[0]


@pytest.mark.parametrize('enabled', [False, True])
def test_current_grants_reach_normal_answer_without_capturing_screen(client_factory, enabled):
    client, records = client_factory()
    observer, _ = native(client)
    client.post('/api/mouse-control', json={'enabled': enabled})
    client.post('/api/proactive', json={'enabled': enabled})
    observer.capture = lambda: pytest.fail('A capability question must not capture the desktop')
    session = client.post('/api/sessions').json()['id']
    result = client.post('/api/chat', json={'session_id': session,
        'message': '你现在能主动消息和控制鼠标吗？', 'mouse_control': enabled})
    assert events(result)[-1]['type'] == 'done'
    state = capabilities(records[0]['messages'][0]['content'])
    assert state['mouse_control']['enabled'] is enabled
    assert state['mouse_control']['this_message_authorized'] is enabled
    assert state['proactive']['enabled'] is enabled
    assert not state['proactive']['executes_actions']
    assert not state['mouse_control']['keyboard_allowed']


def test_codex_prompt_uses_real_state_not_unconditional_enable_instructions(client_factory):
    client, records = client_factory()
    observer, _ = native(client)
    engine = client.app.state.codex_chat
    engine.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    client.post('/api/mouse-control', json={'enabled': True})
    client.post('/api/proactive', json={'enabled': True})
    session = client.post('/api/sessions').json()['id']
    result = events(client.post('/api/chat', json={'session_id': session, 'provider': 'codex',
        'message': '现在开关是开着的吗', 'mouse_control': True}))
    action = next(packet['action'] for packet in result if packet['type'] == 'action')
    prompt = action['arguments']['prompt']
    messages = json.loads(prompt.rsplit('\n', 1)[1])
    state = capabilities(messages[0]['content'])
    assert state['mouse_control']['enabled'] and state['proactive']['enabled']
    assert '如用户要求鼠标操作，请说明在对话栏开启鼠标控制' not in prompt
    assert not records and not observer.pending_conversation


def test_completed_local_turn_offers_one_text_opportunity_and_never_self_requeues(client_factory):
    client, records = client_factory()
    observer, now = native(client)
    seen = []

    async def plan_conversation(messages, provider, transport=None):
        seen.append((messages, provider))
        return {'kind': 'comment', 'message': '这个方向也可以从一个小例子开始。'}

    observer.planner.plan_conversation = plan_conversation
    observer.capture = lambda: pytest.fail('A text opportunity must not read the assistant window')
    client.post('/api/proactive', json={'enabled': True})
    session = client.post('/api/sessions').json()['id']
    assert events(client.post('/api/chat', json={'session_id': session, 'message': '一起想想学习方法'}))[-1]['type'] == 'done'
    assert observer.pending_conversation is not None
    now[0] = 3
    notice = client.portal.call(observer.tick)
    assert notice['kind'] == 'comment' and len(seen) == 1
    assert observer.pending_conversation is None
    assert len(records) == 1  # Controlled initiative planner did not invoke another API.
    assert client.get('/api/proactive/events').json()['cursor'] == 1
    assert len(client.get(f'/api/sessions/{session}').json()['messages']) == 2


def test_enabling_in_existing_chat_offers_one_opportunity_without_auto_creation(client_factory):
    client, _ = client_factory()
    observer, _ = native(client)
    session = client.post('/api/sessions').json()['id']
    client.post('/api/chat', json={'session_id': session, 'message': '聊聊今天的计划'})
    assert observer.pending_conversation is None
    assert client.post('/api/proactive', json={'enabled': True, 'session_id': 'missing'}).status_code == 404
    assert not observer.enabled
    enabled = client.post('/api/proactive', json={'enabled': True, 'session_id': session})
    assert enabled.status_code == 200
    assert observer.pending_conversation is not None
    assert len(client.get('/api/sessions').json()) == 1


@pytest.mark.parametrize('action', ['switch', 'delete', 'clear'])
def test_session_changes_and_deletion_revoke_old_followup_without_disabling(client_factory, action):
    client, _ = client_factory()
    observer, _ = native(client)
    observer.set_enabled(True)
    session = client.post('/api/sessions').json()['id']
    client.post('/api/chat', json={'session_id': session, 'message': '旧会话的问题'})
    assert observer.pending_conversation is not None
    generation = observer.generation
    if action == 'switch':
        assert client.post('/api/proactive/interrupt', json={}, headers={'X-Assistant-Token': ''}).status_code == 403
        assert client.post('/api/proactive/interrupt', json={}, headers={'X-Xiayuluo-Voice-Device': 'phone:x'}).status_code == 403
        response = client.post('/api/proactive/interrupt', json={})
    elif action == 'delete':
        response = client.delete(f'/api/sessions/{session}')
    else:
        response = client.delete('/api/memories')
    assert response.status_code == 200
    assert observer.enabled and observer.generation > generation
    assert observer.pending_conversation is None
    assert observer.events() == []


@pytest.mark.parametrize('native_window,owner', [(False, 'desktop'), (True, 'phone:fixture')])
def test_other_channels_do_not_offer_desktop_followups(client_factory, native_window, owner):
    client, records = client_factory()
    observer, _ = native(client)
    observer.set_enabled(True)
    client.app.state.native_window_open = native_window
    session = client.post('/api/sessions').json()['id']
    result = client.post('/api/chat', json={'session_id': session, 'message': '普通提问'},
                         headers={'X-Xiayuluo-Voice-Device': owner})
    assert events(result)[-1]['type'] == 'done' and records
    assert observer.pending_conversation is None
    if owner.startswith('phone:'):
        state = capabilities(records[0]['messages'][0]['content'])
        assert not state['mouse_control']['enabled'] and not state['proactive']['enabled']


def test_input_hint_is_local_boolean_only_and_expires_without_recording_drafts(client_factory):
    client, records = client_factory()
    observer, now = native(client)
    observer.set_enabled(True)
    assert client.post('/api/interaction', json={'typing': True}, headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.post('/api/interaction', json={'typing': True}, headers={'X-Xiayuluo-Voice-Device': 'phone:x'}).status_code == 403
    for body in ({'typing': 'true'}, {'typing': True, 'text': 'private draft'}):
        assert client.post('/api/interaction', json=body).status_code == 422
    assert client.post('/api/interaction', json={'typing': True}).json()['yielding_to_input']
    assert not observer._is_eligible()
    client.app.state.input_active_until = 0  # Simulate lease expiry without sleeping.
    assert observer._is_eligible()
    assert client.post('/api/interaction', json={'typing': False}).json()['lease_seconds'] == 0
    assert not records and client.get('/api/sessions').json() == []
    assert client.get('/api/memories').json()['items'] == []


def test_proactive_audio_once_off_stops_and_event_reads_do_not_replay(client_factory):
    client, _ = client_factory()
    observer, now = native(client)
    speech = attach(client)

    async def plan(*args):
        return {'kind': 'comment', 'message': '这一小步看起来清楚多了。'}

    observer.planner.plan = plan
    observer.capture = lambda: {'width': 80, 'height': 80, 'png': b'fixture', 'fingerprint': 'one'}
    observer.set_enabled(True)
    now[0] = 3
    client.portal.call(observer.tick)
    assert speech.calls == [('这一小步看起来清楚多了。', 'proactive', 0)]
    client.get('/api/proactive/events')
    client.get('/api/proactive/events')
    assert len(speech.calls) == 1
    assert client.post('/api/proactive', json={'enabled': False}).status_code == 200
    assert not speech.speaking


@pytest.mark.parametrize('scope', ['same', 'other', 'expired'])
def test_reply_can_reference_recent_question_only_in_same_session_and_ram_lifetime(client_factory, scope):
    client, records = client_factory()
    observer, now = native(client)
    session = client.post('/api/sessions').json()['id']
    other = client.post('/api/sessions').json()['id']
    client.get(f'/api/sessions/{session}')

    async def plan(*args):
        return {'kind': 'question', 'message': 'INITIATIVE_FIXTURE_要不要从例子开始？'}

    observer.planner.plan = plan
    observer.capture = lambda: {'width': 80, 'height': 80, 'png': b'fixture', 'fingerprint': 'one'}
    observer.set_enabled(True)
    now[0] = 3
    client.portal.call(observer.tick)
    assert len(client.app.state.initiative_context) == 1
    client.post('/api/proactive/interrupt', json={})
    if scope == 'expired':
        client.app.state.initiative_context[0]['created'] -= 121
    result = client.post('/api/chat', json={'session_id': other if scope == 'other' else session, 'message': '好'})
    assert events(result)[-1]['type'] == 'done'
    assert ('INITIATIVE_FIXTURE' in records[0]['messages'][0]['content']) is (scope == 'same')
    assert 'INITIATIVE_FIXTURE' not in client.get('/api/memories').text
    assert 'INITIATIVE_FIXTURE' not in client.get(f'/api/sessions/{session}').text


def test_late_proactive_audio_does_not_play_after_off(client_factory, monkeypatch):
    client, _ = client_factory()
    observer, now = native(client)
    speech = attach(client)
    entered, release = Event(), Event()
    original = asyncio.to_thread

    async def delayed(function, *args, **kwargs):
        if function.__name__ == 'enqueue_proactive_speech':
            entered.set()
            await original(release.wait, 3)
        return await original(function, *args, **kwargs)

    async def plan(*args):
        return {'kind': 'comment', 'message': '过期的话不应播出。'}

    observer.planner.plan = plan
    observer.capture = lambda: {'width': 80, 'height': 80, 'png': b'fixture', 'fingerprint': 'one'}
    observer.set_enabled(True)
    now[0] = 3
    monkeypatch.setattr('assistant.server.asyncio.to_thread', delayed)
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(client.portal.call, observer.tick)
        try:
            assert entered.wait(2)
            assert client.post('/api/proactive', json={'enabled': False}).status_code == 200
        finally:
            release.set()
        future.result(timeout=4)
    assert not speech.calls
