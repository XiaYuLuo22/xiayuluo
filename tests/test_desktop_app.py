import importlib.util
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import httpx
import pytest

from assistant.config import Settings
from assistant.desktop_app import LocalService, configuration_directory, prepare_settings, run_window


def test_source_app_preserves_existing_config_and_resolves_data(monkeypatch, tmp_path):
    config = tmp_path / '.env'
    config.write_text('# existing user config\n', encoding='utf-8')
    seen = []
    def load(path):
        seen.append(path)
        return Settings(data_dir='memory', codex_workspace='project')
    monkeypatch.setattr(Settings, 'load', load)
    settings, selected = prepare_settings(tmp_path)
    assert selected == config
    assert seen == [config]
    assert config.read_text() == '# existing user config\n'
    assert Path(settings.data_dir) == tmp_path / 'memory'
    assert Path(settings.codex_workspace) == tmp_path / 'project'


def test_frozen_windows_uses_user_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert configuration_directory() == tmp_path / 'Xiayuluo'


def test_source_app_uses_current_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    monkeypatch.chdir(tmp_path)
    assert configuration_directory() == tmp_path


def test_new_config_is_blank_and_creation_is_repeatable(monkeypatch, tmp_path):
    monkeypatch.setattr(Settings, 'load', lambda path: Settings())
    _, config = prepare_settings(tmp_path)
    text = config.read_text(encoding='utf-8')
    assert 'OPENAI_API_KEY=\n' in text
    assert 'CODEX_WORKSPACE=\n' in text
    config.write_text('# user-edited\n', encoding='utf-8')
    prepare_settings(tmp_path)
    assert config.read_text() == '# user-edited\n'


def test_owned_service_readiness_bootstrap_and_port_release(tmp_path):
    settings = Settings(data_dir=str(tmp_path / 'memory'))
    config = tmp_path / '.env'
    service = LocalService(settings, config)
    try:
        assert service.start() is service
        with httpx.Client(base_url=service.url, trust_env=False) as client:
            assert client.get('/').status_code == 200
            bootstrap = client.get('/api/bootstrap').json()
            assert bootstrap['status']['runtime']['presentation'] == 'desktop'
            assert bootstrap['status']['runtime']['config_file'] == str(config)
            client.headers['X-Assistant-Token'] = bootstrap['token']
            assert client.post('/api/sessions').status_code == 200
    finally:
        service.stop()
    assert not service.thread.is_alive()
    assert (tmp_path / 'memory/memory.sqlite3').is_file()
    service.stop()  # Idempotent close event plus finally cleanup.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', service.port))


def test_two_app_instances_have_distinct_owned_ports(tmp_path):
    first = LocalService(Settings(data_dir=str(tmp_path / 'first')))
    second = LocalService(Settings(data_dir=str(tmp_path / 'second')))
    try:
        first.start(); second.start()
        assert first.port != second.port
        first.stop()
        assert httpx.get(second.url + '/', trust_env=False).status_code == 200
    finally:
        first.stop(); second.stop()


def test_mobile_mode_uses_configured_loopback_port(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    service = LocalService(Settings(data_dir=str(tmp_path), mobile_enabled=True, port=port))
    try:
        service.start()
        assert service.port == port
        assert service.listener.getsockname()[0] == '127.0.0.1'
        status = httpx.get(service.url + '/api/bootstrap', trust_env=False).json()['status']
        assert status['mobile']['enabled'] is True
    finally:
        service.stop()


def test_window_shown_dispatches_autostart_but_not_after_shutdown(tmp_path):
    service = LocalService(Settings(data_dir=str(tmp_path), voice_autostart=False))
    calls = []
    try:
        service.start()
        assert service.app.state.voice.autostart is False
        assert not service.app.state.voice.status()['running']
        service.app.state.voice.app_opened = lambda: calls.append('window-open')
        service.on_window_shown()
        assert calls == ['window-open']
        service.stop()
        service.on_window_shown()
        assert calls == ['window-open']
    finally:
        service.stop()


def test_window_close_cancels_active_codex_task(tmp_path):
    service = LocalService(Settings(data_dir=str(tmp_path / 'memory'), codex_workspace=str(tmp_path)))
    codex = service.app.state.codex
    codex.command_prefix = lambda: [sys.executable, str(Path(__file__).parent / 'fake_codex.py')]
    try:
        service.start()
        with httpx.Client(base_url=service.url, trust_env=False, timeout=10) as client:
            client.headers['X-Assistant-Token'] = client.get('/api/bootstrap').json()['token']
            session = client.post('/api/sessions').json()['id']
            action = client.post('/api/actions', json={'session_id': session, 'tool': 'codex_task',
                'arguments': {'prompt': 'slow'}}).json()
            with client.stream('POST', f"/api/actions/{action['id']}/approve") as response:
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        assert json.loads(line[6:])['type'] == 'status'
                        break
                service.stop()
        assert not service.thread.is_alive()
        assert not codex.lock.locked()
    finally:
        service.stop()


@pytest.mark.skipif(importlib.util.find_spec('webview') is None, reason='Optional app dependency not installed')
def test_real_window_contract_and_service_shutdown(tmp_path):
    import webview
    created = []
    observed = {}
    def create(title, url, **options):
        window = webview.create_window(title, url, **options)
        created.append(window)
        return window
    def start(**options):
        observed.update(options)
        window = created[0]
        assert window.title == '夏鱼洛'
        assert window.initial_width == 1440
        assert window._js_api is None
        assert httpx.get(window.original_url, trust_env=False).status_code == 200
        window.events.closing.set()  # Exercise the real pywebview close event contract.
    adapter = SimpleNamespace(create_window=create, start=start, settings={})
    try:
        run_window(Settings(data_dir=str(tmp_path)), tmp_path / '.env', webview_module=adapter)
        assert observed['private_mode'] is True
        assert observed['http_server'] is False
        assert adapter.settings['IGNORE_SSL_ERRORS'] is False
        assert adapter.settings['ALLOW_DOWNLOADS'] is True
        assert observed['menu'][0].title == '夏鱼洛'
    finally:
        for window in created:
            webview.windows.remove(window)


@pytest.mark.skipif(importlib.util.find_spec('webview') is None, reason='Optional app dependency not installed')
def test_renderer_failure_does_not_leave_a_service(tmp_path):
    from webview.event import Event
    urls = []
    def create(title, url, **options):
        urls.append(url)
        return SimpleNamespace(events=SimpleNamespace(closing=Event(None, should_lock=True), shown=Event(None)),
                               destroy=lambda: None, load_url=lambda url: None)
    def fail(**options):
        raise RuntimeError('renderer fixture failed')
    adapter = SimpleNamespace(create_window=create, start=fail, settings={})
    with pytest.raises(RuntimeError, match='renderer fixture failed'):
        run_window(Settings(data_dir=str(tmp_path)), tmp_path / '.env', webview_module=adapter)
    with pytest.raises(httpx.ConnectError):
        httpx.get(urls[0], trust_env=False)


def test_default_command_opens_app_and_serve_stays_explicit(monkeypatch):
    from assistant import __main__, desktop_app
    called = []
    monkeypatch.setattr(desktop_app, 'main', lambda debug=False: called.append(debug) or 0)
    assert __main__.main([]) == 0
    assert called == [False]
    assert __main__.main(['--debug']) == 0
    assert called == [False, True]


def test_windows_build_plan_keeps_user_data_out():
    source = Path(__file__).parents[1] / 'tools/build_desktop.py'
    spec = importlib.util.spec_from_file_location('xiayuluo_build_fixture', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    command = module.build_command(Path('release'), Path('temporary'))
    assert '--windowed' in command
    assert '--onedir' in command
    resources = [command[index + 1] for index, arg in enumerate(command) if arg == '--add-data']
    assert any('.env.example' in resource for resource in resources)
    assert not any('memory.sqlite3' in resource or '/.assistant' in resource for resource in resources)
    assert not any(resource.split(':')[0].endswith('/.env') for resource in resources)
