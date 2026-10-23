import json
from pathlib import Path
import zipfile
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace

import httpx
import pytest

from assistant.voice import DesktopVoice, WakeGate
from tools.prepare_voice_model import MODEL, unpack


def test_window_open_autostarts_once_and_stop_wins(monkeypatch):
    created = []
    class Thread:
        def __init__(self, **options): self.running = False; created.append(self)
        def start(self): self.running = True
        def is_alive(self): return self.running
    monkeypatch.setattr('assistant.voice.Thread', Thread)
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture', platform='win32')
    voice.available = lambda: (True, 'fixture')
    assert not voice.status()['running']  # Construction/headless readiness never opens a mic.
    assert voice.app_opened()['running']
    assert len(created) == 1
    voice.app_opened()
    assert len(created) == 1
    voice.stop(); created[0].running = False
    assert not voice.app_opened()['running']
    assert len(created) == 1
    voice.start()
    assert len(created) == 2
    voice.stop()


def test_stop_before_window_show_prevents_delayed_autostart(monkeypatch):
    def unexpected(**options): raise AssertionError('microphone worker must not start')
    monkeypatch.setattr('assistant.voice.Thread', unexpected)
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture', platform='win32')
    voice.available = lambda: (True, 'fixture')
    voice.stop()
    assert not voice.app_opened()['running']


def test_autostart_optout_unavailable_and_cloud_do_not_listen(monkeypatch):
    def unexpected(**options): raise AssertionError('microphone worker must not start')
    monkeypatch.setattr('assistant.voice.Thread', unexpected)
    off = DesktopVoice('http://127.0.0.1:12345', 'fixture', platform='win32', autostart=False)
    off.available = lambda: (True, 'fixture')
    assert not off.app_opened()['running']
    missing = DesktopVoice('http://127.0.0.1:12345', 'fixture', platform='win32')
    missing.available = lambda: (False, 'fixture missing model')
    assert not missing.app_opened()['running']
    assert 'fixture missing model' in missing.status()['message']
    assert missing.autostart_attempted
    cloud = DesktopVoice('http://127.0.0.1:12345', 'fixture', platform='linux')
    assert not cloud.app_opened()['running']


def test_wake_gate_discards_ambient_and_only_forwards_post_wake_text():
    now = [100.0]
    gate = WakeGate(clock=lambda: now[0])
    assert gate.feed('这是环境里的私人对话') == ('ignore', '')
    assert gate.feed('刚刚那句话不要发送 夏 鱼 洛，今天星期几') == ('chat', '今天星期几')
    assert gate.feed('再介绍一下你自己') == ('chat', '再介绍一下你自己')
    now[0] += 31
    assert gate.feed('这句已超时不会发送') == ('ignore', '')
    assert gate.feed('夏雨洛') == ('wake', '')
    assert gate.feed('结束对话') == ('sleep', '')
    assert gate.feed('睡眠后不发送') == ('ignore', '')
    assert gate.feed('夏鱼洛停止监听') == ('stop', '')
    assert gate.feed('关闭后不发送') == ('ignore', '')


def test_wake_gate_resumes_window_only_after_answer_and_bounds_input():
    now = [10.0]
    gate = WakeGate(clock=lambda: now[0])
    gate.feed('夏鱼洛')
    now[0] += 50
    gate.resume()
    assert gate.feed('请继续') == ('chat', '请继续')
    assert len(gate.feed('x' * 9000)[1]) == 2000


def test_voice_is_opt_in_and_unavailable_on_cloud():
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture', platform='linux')
    assert not voice.status()['running']
    assert not voice.status()['available']
    with pytest.raises(ValueError, match='仅 Windows'):
        voice.start()
    voice.stop()
    assert voice.stop_event.is_set()


def test_voice_reuses_speaker_and_muted_speech_never_stops_microphone():
    calls = []

    class Muted:
        def set_activity_callback(self, callback):
            self.callback = callback

        def say(self, text, *, source):
            calls.append((text, source))
            raise ValueError('回复朗读已关闭。')

        def stop(self, source=None):
            calls.append(('stop', source))

    shared = Muted()
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture', speech=shared)
    voice._speak('只显示文字')
    assert not voice.stop_event.is_set()
    assert calls == [('只显示文字', 'voice')]
    voice.stop()
    assert calls[-1] == ('stop', 'voice')


def test_external_chat_playback_pauses_and_flushes_asr_then_resumes():
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture')
    samples = Queue()
    samples.put(b'old buffered audio')
    paused, finished = Event(), Event()
    calls = []
    stream = SimpleNamespace(stop=lambda: (calls.append('stop'), paused.set()), start=lambda: calls.append('start'))
    recognizer = SimpleNamespace(Reset=lambda: calls.append('reset'))
    voice._speech_activity(True)
    worker = Thread(target=lambda: (voice._pause_for_playback(stream, samples, recognizer), finished.set()))
    worker.start()
    assert paused.wait(2)
    assert samples.empty()
    samples.put(b'possible speaker tail')
    voice._speech_activity(False)
    assert finished.wait(2)
    worker.join(2)
    assert samples.empty() and calls == ['stop', 'reset', 'reset', 'start']
    assert voice.phase == 'listening' and not voice.stop_event.is_set()


def test_stopping_microphone_during_playback_never_restarts_it():
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture')
    calls = []
    paused = Event()
    stream = SimpleNamespace(stop=lambda: paused.set(), start=lambda: calls.append('unexpected restart'))
    recognizer = SimpleNamespace(Reset=lambda: None)
    voice._speech_activity(True)
    worker = Thread(target=lambda: voice._pause_for_playback(stream, Queue(), recognizer))
    worker.start()
    assert paused.wait(2)
    voice.stop()
    worker.join(2)
    voice._speech_activity(False)
    assert not worker.is_alive() and not calls


def test_voice_routes_auth_confirmation_and_no_mobile_access(client_factory):
    client, _ = client_factory()
    app = client.app
    assert client.get('/api/voice', headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.post('/api/voice/start', json={'confirm': False}).status_code == 400
    assert client.post('/api/voice/start', json={'confirm': True}).status_code == 409
    assert client.get('/api/voice').json()['running'] is False
    class Voice:
        running = False
        def status(self): return {'available': True, 'running': self.running}
        def start(self): self.running = True; return self.status()
        def stop(self): self.running = False
    app.state.voice = Voice()
    assert client.post('/api/voice/start', json={'confirm': True}).json()['running']
    app.state.updating = True
    assert client.post('/api/voice/start', json={'confirm': True}).status_code == 409
    assert client.post('/api/voice/stop').json()['stopped']
    assert app.state.voice.running is False


def test_voice_chat_reuses_original_chat_and_never_approves_actions():
    calls = []
    def transport(request):
        calls.append(request)
        if request.url.path.endswith('/sessions'):
            return httpx.Response(200, json={'id': 'a' * 32})
        assert request.url.path == '/api/chat'
        assert json.loads(request.content)['mode'] == 'chat'
        packets = [{'type': 'text', 'text': '需要批准'}, {'type': 'action', 'action': {'id': 'fixture'}}, {'type': 'done'}]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(p) + '\n\n' for p in packets))
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture')
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert '批准' in voice._chat(client, '你好')
    assert len(calls) == 2
    assert not any('actions' in request.url.path for request in calls)


def test_voice_chat_rejects_incomplete_stream_without_retry():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(200, text='data: {"type":"text","text":"partial"}\n\n')
    voice = DesktopVoice('http://127.0.0.1:12345', 'fixture')
    voice.session = 'a' * 32
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ValueError, match='中断'):
            voice._chat(client, '你好')
    assert len(calls) == 1


def test_offline_model_extraction_hash_and_preservation(tmp_path):
    archive = tmp_path / 'model.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(MODEL + '/am/final.mdl', b'fixture-only-not-a-model')
    with pytest.raises(ValueError, match='checksum'):
        unpack(archive, tmp_path / 'wrong', '0' * 64)
    assert not (tmp_path / 'wrong').exists()
    digest = unpack(archive, tmp_path / 'model')
    assert json.loads((tmp_path / 'model/xiayuluo-model.json').read_text())['sha256'] == digest
    with pytest.raises(ValueError, match='exists'):
        unpack(archive, tmp_path / 'model')


@pytest.mark.parametrize('entry', [MODEL + '/../escape', '/absolute', MODEL + '/x\\bad', MODEL + '/bad:ads', MODEL + '/CON', MODEL + '/trailing.'])
def test_model_archive_rejects_unsafe_paths(tmp_path, entry):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(MODEL + '/am/final.mdl', 'fixture')
        info = zipfile.ZipInfo()
        info.filename = entry  # ZipInfo(name) would normalize backslashes on Windows.
        bundle.writestr(info, 'bad')
    with pytest.raises(ValueError, match='Unsafe'):
        unpack(archive, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()
