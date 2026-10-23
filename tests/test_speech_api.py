"""Native reply playback is local output, not a microphone or model permission."""
import pytest

from assistant.config import Settings
from conftest import events
from test_mobile import pair, phone


class SpeechStub:
    def __init__(self):
        self.enabled = True
        self.speaking = False
        self.generation = 0
        self.voice_id = 'system-zh'
        self.calls = []
        self.fail = None

    def status(self):
        return {'available': True, 'enabled': self.enabled, 'speaking': self.speaking,
                'generation': self.generation, 'engine': 'system', 'voice_id': self.voice_id,
                'phase': 'speaking' if self.speaking else 'idle', 'message': '系统中文声音',
                'reason': '', 'voices': [{'id': 'system-zh', 'name': '系统中文'}], 'truncated': False}

    def configure(self, enabled, voice_id=None):
        if voice_id is not None and voice_id != 'system-zh':
            raise ValueError('请选择系统已安装的声音。')
        self.enabled, self.speaking = enabled, False
        self.generation += 1
        return self.status()

    def say(self, text, *, source='chat', expected_generation=None):
        if self.fail:
            raise self.fail
        if not self.enabled or expected_generation != self.generation:
            raise ValueError('朗读已停止或授权代次已改变。')
        self.calls.append((text, source, expected_generation))
        self.speaking = True
        return object()  # The API must not wait for the playback ticket to finish.

    def stop(self, source=None):
        self.generation += 1
        self.speaking = False
        return self.status()

    def close(self):
        self.stop()


def attach(client):
    native = SpeechStub()
    client.app.state.speech = native
    return native


def test_headless_speech_reports_unavailable_without_opening_audio(client_factory):
    client, records = client_factory()
    status = client.get('/api/speech')
    assert status.status_code == 200
    assert status.json()['available'] is False
    assert status.json()['speaking'] is False
    assert client.post('/api/speech', json={'enabled': True}).status_code == 409
    assert client.post('/api/speech/speak', json={'text': '你好', 'generation': 0}).status_code == 409
    assert records == []


@pytest.mark.parametrize('method,path,body', [('get', '/api/speech', None),
    ('post', '/api/speech', {'enabled': False}), ('post', '/api/speech/stop', {}),
    ('post', '/api/speech/speak', {'text': '你好', 'generation': 0})])
def test_speech_routes_require_local_authorization(client_factory, method, path, body):
    client, _ = client_factory()
    native = attach(client)
    request = getattr(client, method)
    kwargs = {'json': body} if method == 'post' else {}
    assert request(path, headers={'X-Assistant-Token': ''}, **kwargs).status_code == 403
    assert native.calls == [] and native.enabled


def test_speech_sends_only_local_output_and_can_be_stopped_without_microphone(client_factory):
    client, records = client_factory()
    native = attach(client)
    assert client.app.state.voice is None
    result = client.post('/api/speech/speak', json={'text': '你好，今天一起开始吧。', 'generation': 0})
    assert result.status_code == 200, result.text
    assert result.json()['speaking']
    assert native.calls == [('你好，今天一起开始吧。', 'chat', 0)]
    result = client.post('/api/speech/stop')
    assert result.status_code == 200
    assert not result.json()['speaking']
    assert result.json()['generation'] > 0
    assert not records and client.app.state.voice is None


def test_stop_and_disable_reject_delayed_old_playback(client_factory):
    client, _ = client_factory()
    native = attach(client)
    old = client.get('/api/speech').json()['generation']
    assert client.post('/api/speech/stop').status_code == 200
    assert client.post('/api/speech/speak', json={'text': '旧回复', 'generation': old}).status_code == 409
    response = client.post('/api/speech', json={'enabled': False})
    assert response.status_code == 200 and response.json()['enabled'] is False
    assert client.post('/api/speech/speak', json={'text': '关闭后不播报',
        'generation': response.json()['generation']}).status_code == 409
    assert native.calls == []


@pytest.mark.parametrize('body', [{'enabled': 'true'}, {'enabled': 1}, {'enabled': True, 'engine': 'cloud'}])
def test_speech_configuration_is_strict_and_cannot_choose_network_engines(client_factory, body):
    client, _ = client_factory()
    attach(client)
    assert client.post('/api/speech', json=body).status_code == 422


@pytest.mark.parametrize('body', [{'text': '你好', 'generation': -1}, {'text': '你好', 'generation': True},
    {'text': '你好', 'generation': 0.5}, {'text': '你好', 'generation': '0'},
    {'text': '你好', 'generation': 0, 'source': 'voice'}, {'text': '', 'generation': 0},
    {'text': 'x' * 100001, 'generation': 0}])
def test_speech_rejects_bad_payloads_before_native_dispatch(client_factory, body):
    client, _ = client_factory()
    native = attach(client)
    assert client.post('/api/speech/speak', json=body).status_code == 422
    assert native.calls == []


def test_updates_block_new_speech_but_never_block_stop_or_disable(client_factory):
    client, _ = client_factory()
    native = attach(client)
    client.app.state.updating = True
    assert client.post('/api/speech/speak', json={'text': '不应播放', 'generation': 0}).status_code == 409
    assert client.post('/api/speech', json={'enabled': True}).status_code == 409
    assert client.post('/api/speech/stop').status_code == 200
    assert client.post('/api/speech', json={'enabled': False}).status_code == 200
    assert native.calls == [] and native.enabled is False


@pytest.mark.parametrize('method,path,body', [('get', '/api/speech', None),
    ('post', '/api/speech', {'enabled': False}), ('post', '/api/speech/stop', {}),
    ('post', '/api/speech/speak', {'text': '不允许远程发声', 'generation': 0})])
def test_phone_cannot_remote_control_computer_speaker(client_factory, tmp_path, method, path, body):
    client, _ = client_factory(settings=Settings(api_key='fixture', data_dir=str(tmp_path), mobile_enabled=True))
    native = attach(client)
    headers = phone(client, pair(client)['token'])
    request = getattr(client, method)
    kwargs = {'json': body} if method == 'post' else {}
    assert request('https://phone.example/mobile/v1/' + path.removeprefix('/api/'), headers=headers,
                   **kwargs).status_code == 403
    # Forwarded device identity must also be rejected at the local route boundary.
    assert request(path, headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}, **kwargs).status_code == 403
    assert native.calls == [] and native.enabled


def test_tts_failure_is_sanitized_and_does_not_mark_the_chat_incomplete(client_factory):
    client, _ = client_factory()
    native = attach(client)
    session = client.post('/api/sessions').json()['id']
    result = events(client.post('/api/chat', json={'session_id': session, 'message': '你好'}))
    assert result[-1]['type'] == 'done'
    native.fail = RuntimeError('fixture-private-engine-diagnostic')
    failed = client.post('/api/speech/speak', json={'text': '完整回复', 'generation': 0})
    assert failed.status_code == 409
    assert 'fixture-private-engine-diagnostic' not in failed.text
    saved = client.get(f'/api/sessions/{session}').json()['messages']
    assert saved[-1]['role'] == 'assistant' and '[回复未完成]' not in saved[-1]['content']
    assert client.post('/api/speech', json={'enabled': True, 'voice_id': 'https://invalid.example/audio'}).status_code == 409
