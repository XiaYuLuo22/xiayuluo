"""Personalization stays local, bounded and on the speech driver's owned thread."""
import json
from threading import Thread, get_ident

import pytest

from assistant.speech import DEFAULT_RATE, DEFAULT_VOLUME, NativeSpeech
from test_speech import Driver


class PersonalDriver(Driver):
    def preferences(self, rate, volume):
        self.record('preferences', rate, volume)


def speaker(tmp_path, driver=None):
    driver = driver or PersonalDriver()
    result = NativeSpeech(tmp_path, driver_factory=lambda: driver)
    assert result.ready.wait(3)
    return result, driver


def test_voice_enumeration_is_cached_validated_and_copied(tmp_path):
    class Voices(PersonalDriver):
        def voices(self):
            self.record('voices')
            return [
                {'id': 'installed-zh', 'name': '  系统声音  ', 'languages': [b'\x05zh_CN', 'en-US']},
                {'id': 'installed-zh', 'name': 'duplicate', 'chinese': True},
                {'id': 'unsafe\nidentifier', 'name': 'bad', 'chinese': True},
                {'id': '', 'name': 'empty', 'chinese': True},
                {'id': 'other', 'name': '', 'chinese': True},
                {'id': 123, 'name': 'not a string', 'chinese': True},
            ]
    output, driver = speaker(tmp_path, Voices())
    try:
        assert output.status()['available']
        voices = output.voices()
        assert voices == [{'id': 'installed-zh', 'name': '系统声音', 'languages': ['zh-CN', 'en-US'],
                           'chinese': True, 'local': True, 'source': 'installed-system'}]
        voices[0]['languages'].append('mutated')
        voices[0]['name'] = 'mutated'
        assert output.voices()[0]['name'] == '系统声音'
        assert output.status()['voices'][0]['languages'] == ['zh-CN', 'en-US']
        assert [name for name, _, _ in driver.calls].count('voices') == 1
        assert driver.calls[0][1] != get_ident()
        assert not (tmp_path / 'speech.json').exists()
        # An installed third-party SAPI voice is not a verified network policy.
        assert 'offline_verified' not in output.voices()[0]
        assert 'network_required' not in output.voices()[0]
    finally:
        output.close()


def test_rate_and_volume_are_saved_restored_and_applied_only_on_worker(tmp_path):
    output, driver = speaker(tmp_path)
    try:
        initial = output.status()
        assert initial['rate'] == DEFAULT_RATE and initial['volume'] == DEFAULT_VOLUME
        assert initial['personalization_available']
        changed = output.configure(True, 'local-zh', rate=145, volume=.65)
        assert changed['rate'] == 145 and changed['volume'] == .65
        assert changed['generation'] == initial['generation'] + 1
        assert not any(name == 'preferences' for name, _, _ in driver.calls)
        ticket = output.say('自定义系统声音。', expected_generation=changed['generation'])
        assert ticket.done.wait(3) and not ticket.error
        assert ('preferences', (145, .65)) in [(name, args) for name, _, args in driver.calls]
        assert len({identity for _, identity, _ in driver.calls}) == 1
        assert driver.calls[0][1] != get_ident()
    finally:
        output.close()
    config = tmp_path / 'speech.json'
    assert json.loads(config.read_text(encoding='utf-8')) == {
        'schema': 1, 'enabled': True, 'voice_id': 'local-zh', 'rate': 145, 'volume': .65}
    original = config.read_bytes()
    restored, _ = speaker(tmp_path)
    try:
        assert restored.status()['rate'] == 145 and restored.status()['volume'] == .65
        assert config.read_bytes() == original
    finally:
        restored.close()


def test_old_preferences_load_without_migration_or_audio(tmp_path):
    path = tmp_path / 'speech.json'
    raw = b'{"schema":1,"enabled":false,"voice_id":"local-zh"}'
    path.write_bytes(raw)
    output, driver = speaker(tmp_path)
    try:
        state = output.status()
        assert not state['enabled'] and state['rate'] == DEFAULT_RATE and state['volume'] == DEFAULT_VOLUME
        assert path.read_bytes() == raw
        assert [name for name, _, _ in driver.calls] == ['voices']
        output.configure(True)
        assert set(json.loads(path.read_text())) == {'schema', 'enabled', 'voice_id'}
    finally:
        output.close()


@pytest.mark.parametrize('rate', [True, False, 79, 301, 180.5, '180', float('nan'), float('inf')])
def test_invalid_rates_do_not_change_state_or_save(tmp_path, rate):
    output, _ = speaker(tmp_path)
    try:
        before = output.status()
        with pytest.raises(ValueError, match='语速'):
            output.configure(True, rate=rate)
        assert output.status() == before
        assert not output.path.exists()
    finally:
        output.close()


@pytest.mark.parametrize('volume', [True, False, -.01, 1.01, '.5', float('nan'), float('inf')])
def test_invalid_volumes_do_not_change_state_or_save(tmp_path, volume):
    output, _ = speaker(tmp_path)
    try:
        before = output.status()
        with pytest.raises(ValueError, match='音量'):
            output.configure(True, volume=volume)
        assert output.status() == before
        assert not output.path.exists()
    finally:
        output.close()


def test_driver_without_personalization_does_not_claim_support(tmp_path):
    output, driver = speaker(tmp_path, Driver())
    try:
        assert output.status()['available'] and not output.status()['personalization_available']
        before = output.status()
        with pytest.raises(ValueError, match='不支持'):
            output.configure(True, rate=140, volume=.5)
        assert output.status() == before and not output.path.exists()
        ticket = output.say('原有朗读仍可用。')
        assert ticket.done.wait(3) and not ticket.error
        assert driver.started.get(timeout=1) == '原有朗读仍可用。'
    finally:
        output.close()


def test_saved_preferences_require_a_capable_driver_without_rewriting(tmp_path):
    path = tmp_path / 'speech.json'
    raw = b'{"schema":1,"enabled":true,"voice_id":"local-zh","rate":150,"volume":0.7}'
    path.write_bytes(raw)
    output, driver = speaker(tmp_path, Driver())
    try:
        assert not output.status()['available'] and '无法应用' in output.status()['reason']
        assert path.read_bytes() == raw and driver.started.empty()
    finally:
        output.close()


def test_changing_preferences_cancels_old_playback_and_generation(tmp_path):
    output, driver = speaker(tmp_path, PersonalDriver(blocking=True))
    try:
        old = output.say('旧声音')
        assert driver.started.get(timeout=1) == '旧声音'
        old_generation = output.status()['generation']
        state = output.configure(True, rate=220, volume=.4)
        assert old.done.wait(3) and old.cancelled
        assert state['generation'] == old_generation + 1
        with pytest.raises(ValueError, match='旧回复'):
            output.say('延迟旧声音', expected_generation=old_generation)
        new = output.say('新声音', expected_generation=state['generation'])
        assert driver.started.get(timeout=3) == '新声音'
        assert ('preferences', (220, .4)) in [(name, args) for name, _, args in driver.calls]
        output.stop()
        assert new.done.wait(3) and new.cancelled
    finally:
        output.close()


def test_corrupt_settings_are_preserved_before_explicit_repair(tmp_path):
    path = tmp_path / 'speech.json'
    original = b'{"schema":1,"enabled":true,"voice_id":"","rate":false}'
    path.write_bytes(original)
    output, _ = speaker(tmp_path)
    try:
        assert not output.status()['enabled'] and '损坏' in output.status()['message']
        assert path.read_bytes() == original and not list(tmp_path.glob('speech.invalid-*'))
        output.configure(False, rate=150, volume=.8)
        backups = list(tmp_path.glob('speech.invalid-*.json'))
        assert len(backups) == 1 and backups[0].read_bytes() == original
        assert json.loads(path.read_text())['rate'] == 150
        output.configure(True)
        assert len(list(tmp_path.glob('speech.invalid-*.json'))) == 1
    finally:
        output.close()


def test_failed_repair_preserves_original_backup_and_logical_settings(tmp_path, monkeypatch):
    path = tmp_path / 'speech.json'
    original = b'not json; keep me'
    path.write_bytes(original)
    output, _ = speaker(tmp_path)
    try:
        before = output.status()
        def fail(*args):
            raise PermissionError('local-write-fixture')
        monkeypatch.setattr('assistant.speech.os.replace', fail)
        with pytest.raises(ValueError, match='原设置保持'):
            output.configure(True, rate=150)
        assert path.read_bytes() == original and output.status() == before
        assert [entry.read_bytes() for entry in tmp_path.glob('speech.invalid-*.json')] == [original]
        assert not list(tmp_path.glob('.speech-*.tmp'))
    finally:
        output.close()


def test_concurrent_settings_are_serialized_and_persist_a_complete_pair(tmp_path):
    output, driver = speaker(tmp_path)
    errors = []
    def configure(rate, volume):
        try:
            output.configure(True, rate=rate, volume=volume)
        except Exception as error:
            errors.append(error)
    try:
        threads = [Thread(target=configure, args=(rate, volume)) for rate, volume in ((120, .3), (240, .9))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=3)
        assert not errors and not any(thread.is_alive() for thread in threads)
        config = json.loads(output.path.read_text())
        assert (config['rate'], config['volume']) in {(120, .3), (240, .9)}
        assert (output.status()['rate'], output.status()['volume']) == (config['rate'], config['volume'])
        assert [name for name, _, _ in driver.calls] == ['voices']
        assert not list(tmp_path.glob('.speech-*.tmp'))
    finally:
        output.close()


@pytest.mark.parametrize('rate,volume', [(80, 0), (300, 1)])
def test_boundary_preferences_are_valid_without_starting_audio(tmp_path, rate, volume):
    output, driver = speaker(tmp_path)
    try:
        state = output.configure(True, rate=rate, volume=volume)
        assert state['rate'] == rate and state['volume'] == float(volume)
        assert driver.started.empty()
    finally:
        output.close()


def test_preference_driver_failure_is_sanitized_and_releases_asr_gate(tmp_path):
    class Failure(PersonalDriver):
        def preferences(self, rate, volume):
            self.record('preferences', rate, volume)
            raise RuntimeError('private-device-error-must-not-leak')
    output, driver = speaker(tmp_path, Failure())
    gate = []
    output.set_activity_callback(gate.append)
    try:
        output.configure(True, rate=150, volume=.5)
        ticket = output.say('设备失败不应影响文字。')
        assert ticket.done.wait(3)
        assert '系统朗读失败' in ticket.error and 'private-device' not in str(output.status())
        assert driver.started.empty() and gate == [True, False]
        assert not output.status()['speaking']
    finally:
        output.close()


def test_backup_failure_never_replaces_corrupt_configuration(tmp_path, monkeypatch):
    path = tmp_path / 'speech.json'
    original = b'preserve-broken-source'
    path.write_bytes(original)
    output, _ = speaker(tmp_path)
    try:
        before = output.status()
        def fail(*args, **kwargs):
            raise PermissionError('cannot-create-private-backup')
        monkeypatch.setattr('assistant.speech.os.link', fail)
        with pytest.raises(ValueError, match='原设置保持'):
            output.configure(True, rate=150)
        assert path.read_bytes() == original and output.status() == before
        assert not list(tmp_path.glob('speech.invalid-*.json'))
        assert not list(tmp_path.glob('.speech-*.tmp'))
    finally:
        output.close()
