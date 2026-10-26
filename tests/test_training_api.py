from dataclasses import replace

import pytest

from assistant.config import Settings
from assistant.desktop_app import update_location_safe
from assistant.training_backup import verify_backup
from assistant.training_storage import TrainingStorage


@pytest.fixture
def training_client(client_factory, tmp_path):
    root = tmp_path / 'durable-training'
    client, _ = client_factory(settings=Settings(data_dir=str(tmp_path / 'memory'), training_dir=str(root)))
    storage = TrainingStorage(root, location_file=tmp_path / 'location.json')
    client.app.state.training_storage = storage
    return client, storage


def populate(storage):
    storage.ensure()
    for area in ('datasets', 'models', 'checkpoints'):
        (storage.root / area / 'original.bin').write_bytes(area.encode() + b'\x00\xff')


def test_status_is_read_only_and_owner_only(training_client):
    client, storage = training_client
    status = client.get('/api/training/status').json()
    assert status['ready'] and not status['exists'] and status['last_backup'] is None
    assert status['root'] == str(storage.root)
    assert not storage.root.exists() and not storage.location_file.exists()
    assert client.get('/api/training/status', headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.get('/api/training/status', headers={'x-xiayuluo-voice-device': 'phone:test'}).status_code == 403


def test_manual_backup_verifies_bytes_without_overwriting_originals(training_client):
    client, storage = training_client
    populate(storage)
    before = {str(p): p.read_bytes() for p in storage.root.glob('*/original.bin')}
    response = client.post('/api/training/backup', json={})
    assert response.status_code == 200
    status = response.json()
    backup = status['last_backup']
    assert backup['files'] == 3 and verify_backup(backup['path'])['files'] == 3
    assert before == {str(p): p.read_bytes() for p in storage.root.glob('*/original.bin')}
    assert client.get('/api/training/status').json()['last_backup'] == backup
    assert client.post('/api/training/backup', json={'path': '/other'}).status_code == 422
    assert client.post('/api/training/backup', json={}, headers={'x-xiayuluo-voice-device': 'phone:test'}).status_code == 403
    client.app.state.updating = True
    assert client.post('/api/training/backup', json={}).status_code == 409


@pytest.mark.parametrize('kind', ['install', 'import'])
def test_update_takes_verified_backup_before_native_callback(training_client, kind):
    client, storage = training_client
    populate(storage)
    calls = []
    def apply(updater):
        backup = client.app.state.last_training_backup
        assert verify_backup(backup['path'])['files'] == 3
        assert storage.root in updater.protected_paths
        calls.append('update')
        return True
    setattr(client.app.state, 'install_update' if kind == 'install' else 'install_local_update', apply)
    result = client.post('/api/updates/' + kind, json={'confirm': True})
    assert result.status_code == 200 and result.json()['restarting']
    assert calls == ['update']
    assert all((storage.root / area / 'original.bin').is_file() for area in ('datasets', 'models', 'checkpoints'))


@pytest.mark.parametrize('kind', ['install', 'import'])
@pytest.mark.parametrize('problem', ['disk-full', 'write-lock', 'missing-registered-root'])
def test_failed_backup_blocks_update_and_preserves_data(training_client, monkeypatch, tmp_path, kind, problem):
    client, storage = training_client
    populate(storage)
    calls = []
    setattr(client.app.state, 'install_update' if kind == 'install' else 'install_local_update', lambda updater: calls.append('MUST NOT RUN'))
    if problem == 'missing-registered-root':
        storage.root.rename(tmp_path / 'original-preserved')
    else:
        def fail(**kwargs):
            raise (OSError if problem == 'disk-full' else ValueError)('private path and error')
        monkeypatch.setattr(storage, 'backup', fail)
    result = client.post('/api/updates/' + kind, json={'confirm': True})
    assert result.status_code == 409 and '备份失败' in result.text
    assert 'private path' not in result.text and not calls and not client.app.state.updating
    if problem == 'missing-registered-root':
        assert not storage.root.exists()
        assert (tmp_path / 'original-preserved/models/original.bin').is_file()
    else:
        assert (storage.root / 'models/original.bin').is_file()


def test_update_without_training_does_not_create_empty_store(training_client):
    client, storage = training_client
    client.app.state.install_update = lambda updater: None
    assert client.post('/api/updates/install', json={'confirm': True}).status_code == 200
    assert not storage.root.exists() and not storage.location_file.exists()


def test_native_location_guard_protects_training_in_both_directions(tmp_path):
    install = tmp_path / 'app'
    settings = Settings(data_dir=str(tmp_path / 'memory'), training_dir=str(tmp_path / 'training'))
    config = tmp_path / 'config/.env'
    assert update_location_safe(install, config, settings)
    assert not update_location_safe(install, config, replace(settings, training_dir=str(install / 'training')))
    assert not update_location_safe(install, config, replace(settings, training_dir=str(tmp_path)))
