import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from cryptography.hazmat.primitives import serialization
import pytest

from assistant import __version__
from assistant.updates import Updates, verify_windows_package
from assistant.update_worker import swap_install
from test_updates import bundle, release
from tools.build_desktop import installer_command
from tools.release import windows_package


def make_package(tmp_path, release, *, change=None, corrupt=False, extra=False, build_version='0.2.0'):
    config, _, sign = release
    archive = tmp_path / 'windows.zip'
    bundle(archive)
    with zipfile.ZipFile(archive, 'a') as files:
        files.writestr('Xiayuluo/build-info.json', json.dumps({'version': build_version, 'platform': 'windows-x64'}))
    content = archive.read_bytes()
    data = {'schema': 2, 'application': 'com.xiayuluo.assistant', 'platform': 'windows-x64',
            'version': '0.2.0', 'notes': '私人更新',
            'asset': {'filename': 'Xiayuluo-Windows.zip', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}}
    if change:
        change(data)
    package = tmp_path / 'private.xyupdate'
    with zipfile.ZipFile(package, 'w') as files:
        files.writestr('update.json', sign(data))
        files.writestr('Xiayuluo-Windows.zip', content[:-1] + b'X' if corrupt else content)
        if extra:
            files.writestr('../escape', 'bad')
    install = tmp_path / 'Xiayuluo'
    install.mkdir(exist_ok=True)
    (install / 'Xiayuluo.exe').write_bytes(b'MZold')
    return Updates(tmp_path / 'cache', config=config), package, install


def test_offline_import_verifies_signature_hash_version_and_never_uses_network(tmp_path, release, monkeypatch):
    def network(*args, **kwargs): raise AssertionError('Offline imports must not use network')
    monkeypatch.setattr('assistant.updates.fetch_bytes', network)
    updater, package, install = make_package(tmp_path, release)
    stage = updater.prepare_local(package, install)
    assert (stage / 'Xiayuluo.exe').read_bytes() == b'MZnew'
    assert (install / 'Xiayuluo.exe').read_bytes() == b'MZold'
    assert package.is_file() and not list(updater.cache.iterdir()) and not updater.lock.locked()


@pytest.mark.parametrize('problem', ['hash', 'size', 'downgrade', 'wrong-app', 'platform', 'filename', 'notes', 'schema', 'extra', 'build-version', 'user-data', 'bad-signature'])
def test_offline_failures_preserve_installation_and_cleanup_owned_files(tmp_path, release, problem):
    def change(data):
        if problem == 'size': data['asset']['size'] += 1
        if problem == 'downgrade': data['version'] = __version__
        if problem == 'wrong-app': data['application'] = 'other.app'
        if problem == 'platform': data['platform'] = 'android-arm64'
        if problem == 'filename': data['asset']['filename'] = '../windows.zip'
        if problem == 'notes': data['notes'] = 'x' * 4001
        if problem == 'schema': data['schema'] = 1
    updater, package, install = make_package(tmp_path, release, change=change, corrupt=problem == 'hash',
        extra=problem == 'extra', build_version='0.1.0' if problem == 'build-version' else '0.2.0')
    if problem == 'user-data': (install / '.env').write_text('preserve')
    if problem == 'bad-signature': updater.config = {**updater.config, 'public_key': 'a' * 64}
    with pytest.raises((ValueError, FileNotFoundError)):
        updater.prepare_local(package, install)
    assert (install / 'Xiayuluo.exe').read_bytes() == b'MZold'
    assert not list(tmp_path.glob('.Xiayuluo.update-*'))
    assert not updater.cache.exists() or not list(updater.cache.iterdir())
    assert not updater.lock.locked()


def test_offline_import_api_requires_local_authorization_and_no_client_paths(client_factory):
    client, _ = client_factory()
    assert not client.get('/api/status').json()['runtime']['update_supported']
    assert client.post('/api/updates/import', json={'confirm': True}).status_code == 409
    calls = []
    client.app.state.install_local_update = lambda updater: calls.append('dialog') or False
    assert client.post('/api/updates/import', json={'confirm': True}, headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.post('/api/updates/import', json={'confirm': True, 'path': 'C:/private'}).status_code == 422
    assert client.post('/api/updates/import', json={}).status_code == 409
    result = client.post('/api/updates/import', json={'confirm': True})
    assert result.json() == {'restarting': False, 'cancelled': True}
    assert calls == ['dialog'] and not client.app.state.updating
    client.app.state.updating = True
    assert client.post('/api/updates/import', json={'confirm': True}).status_code == 409


def test_offline_import_error_never_leaks_path_and_resets_state(client_factory):
    client, _ = client_factory()
    def fail(updater): raise OSError('private path / secret configuration')
    client.app.state.install_local_update = fail
    result = client.post('/api/updates/import', json={'confirm': True})
    assert result.status_code == 409 and 'private path' not in result.text
    assert not client.app.state.updating
    client.app.state.install_local_update = lambda updater: True
    assert client.post('/api/updates/import', json={'confirm': True}).json()['restarting']
    assert client.app.state.updating


def test_installer_targets_built_app_not_cmd_or_python(tmp_path):
    command = installer_command(tmp_path / 'Xiayuluo', tmp_path / 'output', compiler='ISCC.exe')
    assert command[0] == 'ISCC.exe' and f'/DAppVersion={__version__}' in command
    script = (Path(__file__).parents[1] / 'tools/windows-installer.iss').read_text(encoding='utf-8')
    assert 'PrivilegesRequired=lowest' in script and "DirExists('E:\\')" in script
    assert '{autodesktop}' in script and '{autoprograms}' in script
    assert 'Filename: "{app}\\Xiayuluo.exe"' in script
    assert 'start.cmd' not in script and 'python.exe' not in script and '[UninstallDelete]' not in script


def test_local_swap_keeps_installer_uninstaller_and_direct_exe_launch(tmp_path, release):
    updater, package, install = make_package(tmp_path, release)
    (install / 'unins000.exe').write_bytes(b'MZuninstall')
    (install / 'unins000.dat').write_bytes(b'uninstall metadata')
    stage = updater.prepare_local(package, install)
    launched = []
    backup = swap_install(install, stage, launch=lambda command, **kwargs: launched.append(command))
    assert (install / 'unins000.exe').read_bytes() == b'MZuninstall'
    assert (install / 'unins000.dat').read_bytes() == b'uninstall metadata'
    assert launched == [[str(install / 'Xiayuluo.exe')]]
    assert backup.is_dir()


def test_windows_package_signing_never_uploads_and_refuses_overwrite(tmp_path, release, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import tools.release as publisher
    root = tmp_path / 'source'
    (root / 'assistant').mkdir(parents=True)
    key = Ed25519PrivateKey.generate()
    config = {'public_key': key.public_key().public_bytes_raw().hex()}
    (root / 'assistant/update-config.json').write_text(json.dumps(config))
    pem = tmp_path / 'test-key.pem'
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    monkeypatch.setattr(publisher, 'ROOT', root)
    monkeypatch.setattr(publisher, 'verify_versions', lambda value: None)
    updater, _, _ = make_package(tmp_path, release, build_version=__version__)
    notes = tmp_path / 'notes.md'
    notes.write_text('私人更新\n以下为 历史版本：\n旧内容', encoding='utf-8')
    args = SimpleNamespace(version=__version__, windows=tmp_path / 'windows.zip', notes_file=notes,
                           key_file=pem, output=tmp_path / 'signed.xyupdate')
    windows_package(args)
    with zipfile.ZipFile(args.output) as files:
        assert set(files.namelist()) == {'update.json', 'Xiayuluo-Windows.zip'}
        assert verify_windows_package(files.read('update.json'), config)['notes'] == '私人更新'
    before = args.output.read_bytes()
    with pytest.raises(FileExistsError): windows_package(args)
    assert args.output.read_bytes() == before
