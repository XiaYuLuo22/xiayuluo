import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from assistant.config import Settings
from assistant.speech import NativeSpeech
from assistant.visual_loop import VisualLoop
from test_tools import FakeDesktop
from test_visual_loop import Source


def native(client, *, visual=False):
    app = client.app
    app.state.presentation = 'desktop'
    app.state.native_window_open = True
    app.state.autonomy_planner.available = lambda: (True, 'fixture CLI, no network')
    async def planner(goal, event):
        return {'kind': 'ask', 'message': '你想先检查哪一步？', 'step': None}
    app.state.autonomy.planner = planner
    if visual:
        app.state.desktop.enabled = True
        app.state.desktop.driver = FakeDesktop()
        source = Source()
        app.state.visual_loop = VisualLoop(source, eligible=lambda:
            app.state.native_window_open and not app.state.updating and app.state.mouse_control.enabled)
        assert client.post('/api/mouse-control', json={'enabled': True}).status_code == 200
        return source


def test_new_controls_default_off_no_capture_no_network_and_owner_only(client_factory):
    client, network = client_factory()
    for path in ('/api/autonomy', '/api/visual-loop'):
        status = client.get(path)
        assert status.status_code == 200 and not status.json()['available']
        assert not status.json().get('enabled', status.json().get('running'))
        assert client.get(path, headers={'X-Assistant-Token': ''}).status_code == 403
        assert client.get(path, headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}).status_code == 403
    assert client.get('/api/speech/voices').status_code == 409
    assert client.post('/api/autonomy', json={'enabled': True}).status_code == 409
    assert client.post('/api/visual-loop/start', json={'goal': '观察', 'frequency_hz': 20}).status_code == 409
    assert not network


def test_explicit_goal_produces_read_only_decision_without_actions_or_memory(client_factory):
    client, network = client_factory()
    native(client)
    before = client.get('/api/sessions').json()
    assert client.post('/api/autonomy', json={'enabled': True}).json()['enabled']
    goal = client.post('/api/autonomy/goals', json={'goal': '检查我的计划', 'success_criteria': '我确认建议有帮助'})
    assert goal.status_code == 200 and goal.json()['goal']['execution_permitted'] is False
    decision = client.post('/api/autonomy/decide', json={}).json()['last_decision']
    assert decision['kind'] == 'ask' and not decision['execution_permitted']
    assert decision['completion_verified'] is False
    assert not client.app.state.actions.items
    assert client.get('/api/sessions').json() == before
    assert not network
    stopped = client.post('/api/autonomy', json={'enabled': False})
    assert stopped.status_code == 200 and not stopped.json()['enabled']


@pytest.mark.parametrize('path,body', [
    ('/api/autonomy', {'enabled': 'true'}),
    ('/api/autonomy/goals', {'goal': '', 'success_criteria': '完成'}),
    ('/api/autonomy/goals', {'goal': '任务', 'success_criteria': '完成', 'execute': True}),
    ('/api/visual-loop/start', {'goal': '测试', 'frequency_hz': True}),
    ('/api/visual-loop/start', {'goal': '测试', 'frequency_hz': 31}),
    ('/api/visual-loop/start', {'goal': '测试', 'frequency_hz': 20, 'model_path': '/private/model'}),
    ('/api/speech', {'enabled': True, 'rate': True}),
    ('/api/speech', {'enabled': True, 'rate': 301}),
    ('/api/speech', {'enabled': True, 'volume': '0.5'}),
])
def test_control_payloads_are_strict_before_dispatch(client_factory, path, body):
    client, _ = client_factory()
    assert client.post(path, json=body).status_code == 422


@pytest.mark.parametrize('path,body', [
    ('/api/autonomy', {'enabled': True}),
    ('/api/autonomy/goals', {'goal': '测试', 'success_criteria': '观察'}),
    ('/api/autonomy/decide', {}), ('/api/autonomy/stop', {}),
    ('/api/visual-loop/start', {'goal': '测试', 'frequency_hz': 20}),
    ('/api/visual-loop/stop', {}),
])
def test_phone_cannot_authorize_or_inject_control_goals(client_factory, path, body):
    client, _ = client_factory()
    assert client.post(path, json=body,
        headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}).status_code == 403


def test_visual_test_has_preparation_window_and_mouse_off_stops_it(client_factory, tmp_path):
    client, _ = client_factory(settings=Settings(api_key='fixture', data_dir=str(tmp_path), desktop_enabled=True))
    source = native(client, visual=True)
    result = client.post('/api/visual-loop/start', json={'goal': '只观察当前目标窗口', 'frequency_hz': 20})
    assert result.status_code == 200, result.text
    assert result.json()['running'] and result.json()['execution_permitted'] is False
    assert not result.json()['policy_ready'] and source.calls == 0
    assert client.post('/api/mouse-control', json={'enabled': False}).status_code == 200
    assert not client.get('/api/visual-loop').json()['running']
    assert source.calls == 0


def test_stop_invalidates_start_while_prior_background_cleanup_is_pending(client_factory, tmp_path, monkeypatch):
    client, _ = client_factory(settings=Settings(api_key='fixture', data_dir=str(tmp_path), desktop_enabled=True))
    source = native(client, visual=True)
    entered, release = Event(), Event()
    async def cleanup():
        entered.set()
        await asyncio.to_thread(release.wait, 3)
    monkeypatch.setattr(client.app.state.proactive, 'interrupt_and_wait', cleanup)
    with ThreadPoolExecutor(max_workers=1) as worker:
        starting = worker.submit(client.post, '/api/visual-loop/start',
            json={'goal': '不允许停止后复活', 'frequency_hz': 20})
        try:
            assert entered.wait(2)
            assert client.post('/api/visual-loop/stop', json={}).status_code == 200
        finally:
            release.set()
        assert starting.result(timeout=3).status_code == 409
    assert not client.get('/api/visual-loop').json()['running'] and source.calls == 0


def test_new_chat_preempts_both_local_background_controllers(client_factory, monkeypatch):
    client, _ = client_factory()
    calls = []
    async def visual(): calls.append('visual')
    async def autonomy(reason=''): calls.append('autonomy')
    monkeypatch.setattr(client.app.state.visual_loop, 'interrupt_and_wait', visual)
    monkeypatch.setattr(client.app.state.autonomy, 'interrupt_and_wait', autonomy)
    session = client.post('/api/sessions').json()['id']
    assert client.post('/api/chat', json={'session_id': session, 'message': '现在优先回答我'}).status_code == 200
    assert calls == ['visual', 'autonomy']


def test_update_startup_gate_blocks_new_controls_but_not_stop(client_factory, monkeypatch):
    client, _ = client_factory()
    native(client)
    monkeypatch.setattr(client.app.state.startup_gate, 'ready', lambda: False)
    assert client.post('/api/autonomy', json={'enabled': True}).status_code == 409
    assert client.post('/api/autonomy/stop', json={}).status_code == 200
    assert client.post('/api/visual-loop/stop', json={}).status_code == 200


def test_speech_preferences_route_uses_cached_installed_voices_and_strict_local_values(client_factory):
    client, network = client_factory()
    class Speech:
        def __init__(self): self.options = {}
        def voices(self): return [{'id': 'local-zh', 'name': '本机中文', 'languages': ['zh-CN'], 'source': 'installed-system'}]
        def configure(self, enabled, voice_id=None, **options): self.options = {'enabled': enabled, 'voice_id': voice_id, **options}
        def status(self): return self.options
    speech = client.app.state.speech = Speech()
    assert client.get('/api/speech/voices').json()['items'][0]['id'] == 'local-zh'
    result = client.post('/api/speech', json={'enabled': True, 'voice_id': 'local-zh', 'rate': 150, 'volume': .6})
    assert result.status_code == 200 and speech.options['rate'] == 150 and speech.options['volume'] == .6
    assert client.get('/api/speech/voices', headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}).status_code == 403
    assert not network


def test_stop_revokes_a_decision_speech_callback_delayed_in_the_executor(client_factory, tmp_path, monkeypatch):
    client, _ = client_factory()
    native(client)
    calls = []
    class Driver:
        def voices(self): return [{'id': 'zh', 'name': '中文', 'chinese': True}]
        def select(self, identifier): pass
        def preferences(self, rate, volume): pass
        def speak(self, text, cancelled):
            if not cancelled(): calls.append(text)
        def close(self): pass
    speech = client.app.state.speech = NativeSpeech(tmp_path / 'audio', driver_factory=Driver)
    assert speech.ready.wait(2)
    entered, release = Event(), Event()
    original = asyncio.to_thread
    async def delayed(function, *args, **kwargs):
        if getattr(function, '__name__', '') == 'enqueue_decision_speech':
            entered.set()
            await original(release.wait, 3)
        return await original(function, *args, **kwargs)
    monkeypatch.setattr('assistant.server.asyncio.to_thread', delayed)
    try:
        assert client.post('/api/autonomy', json={'enabled': True}).status_code == 200
        assert client.post('/api/autonomy/goals', json={'goal': '只提问', 'success_criteria': '我回答'}).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as worker:
            deciding = worker.submit(client.post, '/api/autonomy/decide', json={})
            try:
                assert entered.wait(2)
                assert client.post('/api/autonomy/stop', json={}).status_code == 200
            finally:
                release.set()
            assert deciding.result(timeout=3).status_code == 200
        assert not calls and speech.pending is None and speech.active is None
    finally:
        release.set()
        speech.close()


def test_new_goal_replaces_previous_autonomy_speech_without_waiting_for_a_new_chat(client_factory):
    from test_speech_api import SpeechStub
    client, _ = client_factory()
    native(client)
    speech = client.app.state.speech = SpeechStub()
    assert client.post('/api/autonomy', json={'enabled': True}).status_code == 200
    assert client.post('/api/autonomy/goals', json={'goal': '第一个目标', 'success_criteria': '核实'}).status_code == 200
    assert client.post('/api/autonomy/decide', json={}).status_code == 200
    assert speech.speaking
    new = client.post('/api/autonomy/goals', json={'goal': '替代目标', 'success_criteria': '新的标准'})
    assert new.status_code == 200 and not speech.speaking
    assert client.app.state.autonomy.export()['goal']['goal'] == '替代目标'
