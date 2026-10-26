import json
from pathlib import Path

import pytest

from assistant import __version__
from assistant.desktop_app import acknowledge_update_health
from assistant.update_worker import stop_new_process, swap_install, wait_for_health
from test_offline_updates import make_package
from test_update_ownership import route_online_download
from test_updates import release


@pytest.mark.parametrize('build_version', ['0.2.0', '0.1.0'])
def test_online_prepare_verifies_actual_build_and_preserves_install(tmp_path, release, monkeypatch, build_version):
    updater, package, install = make_package(tmp_path, release, build_version=build_version)
    original = (install / 'Xiayuluo.exe').read_bytes()
    with route_online_download(updater, (tmp_path / 'windows.zip').read_bytes(), monkeypatch):
        if build_version != '0.2.0':
            with pytest.raises(ValueError, match='版本不符'):
                updater.prepare(install)
            assert updater.progress()['phase'] == 'failed'
            assert not list(tmp_path.glob('.Xiayuluo.update-*'))
        else:
            stage = updater.prepare(install)
            assert json.loads((stage / 'build-info.json').read_text())['version'] == '0.2.0'
            progress = updater.progress()
            assert progress['phase'] == 'ready' and progress['received_bytes'] == progress['total_bytes']
            assert '尚未确认' in progress['message']
            progress['phase'] = 'tampered'
            assert updater.progress()['phase'] == 'ready'
    assert package.is_file() and (install / 'Xiayuluo.exe').read_bytes() == original
    assert not updater.lock.locked() and not list(updater.cache.iterdir())


class Process:
    def __init__(self, code=None):
        self.code = code
        self.calls = []
    def poll(self):
        return self.code
    def terminate(self):
        self.calls.append('terminate'); self.code = -1
    def wait(self, timeout=None):
        self.calls.append('wait'); return self.code
    def kill(self):
        self.calls.append('kill'); self.code = -2


@pytest.mark.parametrize('problem', ['nonce', 'version', 'not-boolean', 'exited', 'timeout'])
def test_health_check_requires_this_nonce_version_and_live_process(tmp_path, problem):
    ack = tmp_path / 'health-ready.json'
    content = {'nonce': 'current', 'version': '0.2.0', 'ready': True}
    if problem == 'nonce': content['nonce'] = 'previous'
    if problem == 'version': content['version'] = '0.1.0'
    if problem == 'not-boolean': content['ready'] = 1
    if problem != 'timeout': ack.write_text(json.dumps(content))
    process = Process(1 if problem == 'exited' else None)
    clock = [0.0]
    def sleep(delay): clock[0] += delay
    with pytest.raises((ValueError, RuntimeError, TimeoutError)):
        wait_for_health(process, ack, 'current', '0.2.0', timeout=.2, clock=lambda: clock[0], sleep=sleep)


def test_health_check_accepts_matching_ready_only_while_process_is_alive(tmp_path):
    ack = tmp_path / 'health-ready.json'
    ack.write_text(json.dumps({'nonce': 'current', 'version': '0.2.0', 'ready': True}))
    wait_for_health(Process(), ack, 'current', '0.2.0', sleep=lambda delay: None)


def test_health_failure_stops_only_owned_process_and_restores_old_install(tmp_path, monkeypatch):
    install, stage = tmp_path / 'Xiayuluo', tmp_path / '.Xiayuluo.update-test'
    install.mkdir(); stage.mkdir()
    (install / 'Xiayuluo.exe').write_bytes(b'old')
    (stage / 'Xiayuluo.exe').write_bytes(b'new')
    training = tmp_path / 'training'
    training.mkdir(); (training / 'weights').write_bytes(b'private weights preserved')
    process, launches = Process(), []
    def launch(*args, **kwargs): launches.append((args, kwargs)); return process
    def fail(child): assert child is process; raise TimeoutError('no healthy window')
    monkeypatch.setattr('assistant.update_worker.subprocess.run', lambda *a, **k: pytest.fail('Never kill by PID'))
    with pytest.raises(TimeoutError):
        swap_install(install, stage, launch, protected_paths=[training], health_check=fail, restart_old=True,
                     launch_environment={'XIAYULUO_UPDATE_HEALTH': 'one-shot'})
    assert process.calls == ['terminate', 'wait']
    assert len(launches) == 2 and 'env' not in launches[1][1]
    assert (install / 'Xiayuluo.exe').read_bytes() == b'old'
    assert (stage / 'Xiayuluo.exe').read_bytes() == b'new'
    assert (training / 'weights').read_bytes() == b'private weights preserved'


def test_private_health_ack_is_one_shot_atomic_and_never_overwrites(tmp_path, monkeypatch):
    directory = tmp_path / 'xiayuluo-update-fixture'
    directory.mkdir(); ack = directory / 'health-ready.json'
    monkeypatch.setattr('assistant.desktop_app.gettempdir', lambda: str(tmp_path))
    probe = json.dumps({'nonce': 'a' * 64, 'version': __version__, 'acknowledgement': str(ack)})
    monkeypatch.setenv('XIAYULUO_UPDATE_HEALTH', probe)
    assert acknowledge_update_health()
    original = ack.read_bytes()
    assert json.loads(original)['ready'] is True
    assert not acknowledge_update_health()
    monkeypatch.setenv('XIAYULUO_UPDATE_HEALTH', probe)
    assert not acknowledge_update_health() and ack.read_bytes() == original
    assert [p.name for p in directory.iterdir()] == ['health-ready.json']


def test_update_progress_api_is_owner_only_and_reports_backup_failure(client_factory, monkeypatch):
    client, _ = client_factory()
    assert client.get('/api/updates/progress').json()['phase'] == 'idle'
    assert client.get('/api/updates/progress', headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.get('/api/updates/progress', headers={'X-Xiayuluo-Voice-Device': 'phone:fixture'}).status_code == 403
    client.app.state.install_update = lambda updater: pytest.fail('Backup failure must prevent update')
    def fail(**kwargs): raise ValueError('private backup details')
    monkeypatch.setattr(client.app.state.training_storage, 'backup', fail)
    assert client.post('/api/updates/install', json={'confirm': True}).status_code == 409
    progress = client.get('/api/updates/progress').json()
    assert progress['phase'] == 'error' and not progress['updating']
    assert 'private backup details' not in str(progress)
