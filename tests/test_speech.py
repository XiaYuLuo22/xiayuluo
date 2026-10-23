from pathlib import Path
from queue import Queue
from threading import Event, Thread, get_ident
import json
import time

import pytest

from assistant.speech import NativeSpeech, speech_text


class Driver:
    def __init__(self, *, blocking=False, chinese=True, failing=False):
        self.blocking, self.chinese, self.failing = blocking, chinese, failing
        self.started = Queue()
        self.calls = []
        self.release = Event()

    def record(self, name, *args):
        self.calls.append((name, get_ident(), args))

    def voices(self):
        self.record('voices')
        return [{'id': 'local-zh', 'name': '本机中文声音', 'chinese': self.chinese},
                {'id': 'local-other', 'name': '其他本机声音', 'chinese': False}]

    def select(self, voice_id):
        self.record('select', voice_id)

    def speak(self, text, cancelled):
        self.record('speak', text)
        self.started.put(text)
        if self.failing:
            raise RuntimeError('fake sensitive driver error must not surface')
        while self.blocking and not self.release.wait(.005):
            if cancelled():
                break

    def close(self):
        self.record('close')


def speech(tmp_path, **driver_options):
    driver = Driver(**driver_options)
    speaker = NativeSpeech(tmp_path, driver_factory=lambda: driver)
    assert speaker.ready.wait(3)
    return speaker, driver


def test_plain_speech_removes_code_markdown_links_and_bounds_length():
    value, truncated = speech_text('## 你好\n\n- **世界**\n[文档](https://example.com/a)\n```python\nprint("密钥")\n```\nhttps://secret.invalid/token\n<img src="private">')
    assert '你好' in value and '世界' in value and '文档' in value
    assert 'print' not in value and '密钥' not in value and 'https' not in value and 'private' not in value
    assert not any(mark in value for mark in ('#', '*', '<', '>'))
    assert not truncated
    value, truncated = speech_text('长' * 3001)
    assert truncated and value.endswith('剩余内容请查看文字。')


def test_native_speech_is_independent_of_microphone_and_all_driver_calls_share_one_thread(tmp_path):
    speaker, driver = speech(tmp_path)
    try:
        assert speaker.status()['enabled'] and speaker.status()['available']
        assert speaker.status()['engine'] == 'system'
        ticket = speaker.say('你好，世界。', expected_generation=0)
        assert ticket.done.wait(3)
        assert not ticket.cancelled and not ticket.error
        assert driver.started.get(timeout=1) == '你好，世界。'
    finally:
        speaker.close()
    assert not speaker.thread.is_alive()
    assert len({call[1] for call in driver.calls}) == 1
    assert driver.calls[0][1] != get_ident()
    assert driver.calls[-1][0] == 'close'
    assert not any(name in vars(speaker) for name in ('model', 'microphone', 'voice_token'))


def test_latest_reply_wins_and_stop_never_replays_cancelled_queue(tmp_path):
    speaker, driver = speech(tmp_path, blocking=True)
    try:
        first = speaker.say('第一条')
        assert driver.started.get(timeout=1) == '第一条'
        second = speaker.say('旧排队')
        newest = speaker.say('最新回复')
        assert second.done.wait(1) and second.cancelled
        assert driver.started.get(timeout=3) == '最新回复'
        speaker.stop()
        assert first.done.wait(3) and newest.done.wait(3)
        assert first.cancelled and newest.cancelled
        assert driver.started.empty()
        assert speaker.status()['generation'] == 1
    finally:
        speaker.close()


def test_stop_and_mute_reject_delayed_reply_generation_and_preserve_preference(tmp_path):
    speaker, driver = speech(tmp_path)
    try:
        old = speaker.status()['generation']
        speaker.stop()
        with pytest.raises(ValueError, match='旧回复'):
            speaker.say('延迟回复', expected_generation=old)
        speaker.configure(False)
        with pytest.raises(ValueError, match='关闭'):
            speaker.say('不要朗读')
        speaker.configure(True, 'local-other')
        assert speaker.status()['voice_id'] == 'local-other'
        assert json.loads((tmp_path / 'speech.json').read_text(encoding='utf-8')) == {
            'schema': 1, 'enabled': True, 'voice_id': 'local-other'}
        for generation in (True, -1, old):
            with pytest.raises(ValueError, match='旧回复'):
                speaker.say('旧回复', expected_generation=generation)
        ticket = speaker.say('新的回复', expected_generation=speaker.status()['generation'])
        assert ticket.done.wait(3)
        assert ('local-other',) in [args for name, _, args in driver.calls if name == 'select']
    finally:
        speaker.close()
    restored, _ = speech(tmp_path)
    try:
        assert restored.status()['enabled'] and restored.status()['voice_id'] == 'local-other'
    finally:
        restored.close()


def test_microphone_stop_only_cancels_voice_not_chat_reply(tmp_path):
    speaker, driver = speech(tmp_path, blocking=True)
    try:
        chat = speaker.say('普通文字回复', source='chat')
        driver.started.get(timeout=1)
        before = speaker.status()['generation']
        speaker.stop(source='voice')
        assert not chat.cancelled and speaker.status()['generation'] == before
        speaker.stop()
        assert chat.done.wait(3) and chat.cancelled
    finally:
        speaker.close()


def test_missing_or_failed_system_voice_reports_reason_without_affecting_text(tmp_path):
    speaker, _ = speech(tmp_path, chinese=False)
    try:
        assert not speaker.status()['available'] and '中文声音' in speaker.status()['reason']
        with pytest.raises(ValueError, match='中文声音'):
            speaker.say('不会说出')
    finally:
        speaker.close()
    failed, _ = speech(tmp_path, failing=True)
    try:
        ticket = failed.say('设备失败')
        assert ticket.done.wait(3)
        assert '系统朗读失败' in ticket.error
        assert 'sensitive' not in str(failed.status())
    finally:
        failed.close()


def test_corrupt_config_fails_closed_and_unknown_voice_does_not_overwrite(tmp_path):
    (tmp_path / 'speech.json').write_text('{"enabled":"true"}', encoding='utf-8')
    speaker, _ = speech(tmp_path)
    try:
        assert not speaker.status()['enabled'] and '配置损坏' in speaker.status()['message']
        original = (tmp_path / 'speech.json').read_text()
        with pytest.raises(ValueError, match='未找到'):
            speaker.configure(True, 'https://not-a-system-voice')
        assert (tmp_path / 'speech.json').read_text() == original
        with pytest.raises(ValueError, match='布尔'):
            speaker.configure('yes')
        speaker.configure(False)
        assert '损坏' not in speaker.status()['message']
    finally:
        speaker.close()


def test_configuration_failure_preserves_current_state(tmp_path, monkeypatch):
    speaker, _ = speech(tmp_path)
    try:
        before = speaker.status()

        def fail(*args):
            raise PermissionError('fixture write failure')

        monkeypatch.setattr(speaker, '_save', fail)
        with pytest.raises(ValueError, match='原设置保持'):
            speaker.configure(False)
        assert speaker.status()['enabled'] == before['enabled']
        assert speaker.status()['generation'] == before['generation']
    finally:
        speaker.close()


def test_cloud_constructor_never_initializes_system_audio(tmp_path):
    speaker = NativeSpeech(tmp_path, platform='linux')
    assert speaker.thread is None
    assert not speaker.status()['available']
    assert '云端' in speaker.status()['reason']
    speaker.close()


def test_activity_gate_is_set_before_playback_and_cleared_after_cancel(tmp_path):
    speaker, driver = speech(tmp_path, blocking=True)
    activity = []
    speaker.set_activity_callback(lambda active: activity.append(active))
    try:
        ticket = speaker.say('夏鱼洛不会听到自己的声音')
        driver.started.get(timeout=1)
        assert activity == [True]
        speaker.stop()
        assert ticket.done.wait(3)
        assert activity == [True, False]
    finally:
        speaker.close()


def test_close_cancels_active_and_pending_without_late_playback(tmp_path):
    speaker, driver = speech(tmp_path, blocking=True)
    first = speaker.say('正在说')
    driver.started.get(timeout=1)
    second = speaker.say('不应播报')
    speaker.close()
    assert first.done.is_set() and first.cancelled
    assert second.done.is_set() and second.cancelled
    assert driver.started.empty()
    with pytest.raises(ValueError, match='关闭'):
        speaker.say('已经退出')


def test_empty_and_oversized_speech_cannot_enter_queue(tmp_path):
    speaker, _ = speech(tmp_path)
    try:
        for text in (' ', 'https://example.com/token', 'x' * 100001):
            with pytest.raises(ValueError):
                speaker.say(text)
        ticket = speaker.say('长' * 3500)
        assert ticket.truncated and speaker.status()['truncated']
        assert ticket.done.wait(3)
    finally:
        speaker.close()
