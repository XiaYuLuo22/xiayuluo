import pytest

from assistant.config import Settings


def test_voice_autostarts_by_default_and_local_optout_is_respected(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('VOICE_AUTOSTART', raising=False)
    assert Settings.load().voice_autostart is True
    monkeypatch.setenv('VOICE_AUTOSTART', 'false')
    assert Settings.load().voice_autostart is False


def test_cloud_binding_is_used_without_exposing_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ASSISTANT_MODEL_KEY', 'cloud-test-only')
    monkeypatch.setenv('OPENAI_API_KEY', 'local-test-only')
    assert Settings.load().api_key == 'cloud-test-only'
    monkeypatch.delenv('ASSISTANT_MODEL_KEY')
    assert Settings.load().api_key == 'local-test-only'


def test_rejects_insecure_remote_provider_and_bypass_sandbox(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://remote.example/v1')
    with pytest.raises(ValueError, match='HTTPS'):
        Settings.load()
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://127.0.0.1:1234/v1')
    monkeypatch.setenv('CODEX_SANDBOX', 'danger-full-access')
    with pytest.raises(ValueError, match='CODEX_SANDBOX'):
        Settings.load()
