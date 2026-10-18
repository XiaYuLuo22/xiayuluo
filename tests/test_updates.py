import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
import pytest

from assistant import __version__
from assistant.update_worker import swap_install
from assistant.desktop_app import update_location_safe
from assistant.updates import Updates, download_pages, extract_windows, fetch_bytes, trusted_url, verify_manifest, version


@pytest.fixture
def release():
    key = Ed25519PrivateKey.generate()
    config = {"repository": "XiaYuLuo22/xiayuluo", "public_key": key.public_key().public_bytes_raw().hex(),
              "manifest_url": "https://github.com/XiaYuLuo22/xiayuluo/releases/latest/download/update.json"}
    data = {"schema": 1, "application": "com.xiayuluo.assistant", "version": "0.2.0", "android_code": 3,
            "notes": "修复更新", "assets": {}}
    for target, name in (("windows-x64", "Windows.zip"), ("android-arm64", "Android.apk")):
        data["assets"][target] = {"url": f"https://github.com/{config['repository']}/releases/download/v0.2.0/{name}",
                                  "size": 123, "sha256": "a" * 64}
    def sign(value=data):
        payload = json.dumps(value, ensure_ascii=False)
        return json.dumps({"payload": payload, "signature": key.sign(payload.encode()).hex()}).encode()
    return config, data, sign


def test_release_signature_and_monotonic_version(release, tmp_path, monkeypatch):
    config, data, sign = release
    assert verify_manifest(sign(), config) == data
    monkeypatch.setattr('assistant.updates.fetch_bytes', lambda *args: sign())
    updater = Updates(tmp_path, config=config)
    assert updater.check()['available'] is True
    previous = '0.2.0'
    for candidate in [__version__, "0.0.1"]:
        data['version'] = candidate
        for asset in data['assets'].values():
            asset['url'] = asset['url'].replace('/v' + previous + '/', '/v' + candidate + '/')
        assert updater.check()['available'] is False
        previous = candidate


def test_signature_tampering_wrong_key_and_oversize(release):
    config, data, sign = release
    for raw in (sign().replace(b'0.2.0', b'9.9.9'), sign() + b' ' * 33000, b'{}', b'[]'):
        with pytest.raises(ValueError):
            verify_manifest(raw, config)
    with pytest.raises(ValueError):
        verify_manifest(sign(), {**config, 'public_key': 'a' * 64})


@pytest.mark.parametrize('bad', ['https://evil.example/a.apk', 'http://github.com/XiaYuLuo22/xiayuluo/releases/download/v0.2.0/a.apk',
                                    'https://github.com/other/repo/releases/download/v0.2.0/a.apk',
                                    'https://user@github.com/XiaYuLuo22/xiayuluo/releases/download/v0.2.0/a.apk',
                                    'https://github.com/XiaYuLuo22/xiayuluo/releases/download/v0.1.0/a.apk'])
def test_signed_but_invalid_urls_rejected(release, bad):
    config, data, sign = release
    data['assets']['android-arm64']['url'] = bad
    with pytest.raises(ValueError):
        verify_manifest(sign(), config)


@pytest.mark.parametrize('bad', ['1.0', '01.0.1', '1.2.3-beta', '999999.0.0', None])
def test_invalid_versions(bad):
    with pytest.raises(ValueError):
        version(bad)


def test_download_hash_length_redirect_and_bounds(tmp_path):
    url = 'https://github.com/a/b/releases/download/v1.0.0/a.zip'
    def handler(request):
        if request.url.host == 'github.com':
            return httpx.Response(302, headers={'Location': 'https://release-assets.githubusercontent.com/a'})
        return httpx.Response(200, content=b'verified')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        target = tmp_path / 'download'
        fetch_bytes(url, 8, destination=target, expected_size=8, expected_hash=hashlib.sha256(b'verified').hexdigest(), client=client)
        assert target.read_bytes() == b'verified'
        for kwargs in ({'limit': 7}, {'limit': 8, 'expected_size': 7}, {'limit': 8, 'expected_hash': '0' * 64}):
            with pytest.raises(ValueError):
                fetch_bytes(url, client=client, **kwargs)
    visited = []
    def redirect(request):
        visited.append(request.url)
        return httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'})
    with httpx.Client(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(ValueError):
            fetch_bytes(url, 100, client=client)
    assert len(visited) == 1


def bundle(path, extra=None):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('Xiayuluo/Xiayuluo.exe', b'MZnew')
        archive.writestr('Xiayuluo/XiayuluoUpdater.exe', b'MZhelper')
        archive.writestr('Xiayuluo/_internal/runtime', b'dependency')
        if extra:
            archive.writestr(extra, b'bad')


@pytest.mark.parametrize('name', ['Xiayuluo/../../escape', '/absolute', 'Xiayuluo/.env', 'Xiayuluo/.assistant/memory.sqlite3', 'Xiayuluo/release-private.pem',
                                  'Xiayuluo/C:/escape', 'Xiayuluo/a\\escape', 'Xiayuluo/CON.txt', 'Xiayuluo/test.'])
def test_zip_paths_user_data_and_windows_aliases_rejected(tmp_path, name):
    archive = tmp_path / 'update.zip'
    bundle(archive, name)
    stage = tmp_path / 'stage'
    with pytest.raises(ValueError):
        extract_windows(archive, stage)
    assert not stage.exists()
    assert not (tmp_path.parent / 'escape').exists()


def test_symlink_duplicate_and_missing_exe_rejected(tmp_path):
    path = tmp_path / 'bad.zip'
    bundle(path)
    with zipfile.ZipFile(path, 'a') as archive:
        entry = zipfile.ZipInfo('Xiayuluo/link')
        entry.external_attr = 0o120777 << 16
        archive.writestr(entry, 'outside')
    with pytest.raises(ValueError):
        extract_windows(path, tmp_path / 'link-stage')
    bundle(path, 'Xiayuluo/XIAYULUO.EXE')
    with pytest.raises(ValueError):
        extract_windows(path, tmp_path / 'duplicate-stage')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('Xiayuluo/_internal/data', 'no exe')
    with pytest.raises(FileNotFoundError):
        extract_windows(path, tmp_path / 'missing-stage')


def test_install_retains_path_backup_and_unrelated_user_data(tmp_path):
    install = tmp_path / 'E-drive' / 'Xiayuluo'
    install.mkdir(parents=True)
    (install / 'Xiayuluo.exe').write_bytes(b'MZold')
    memory = tmp_path / 'AppData' / 'memory.sqlite3'
    memory.parent.mkdir(); memory.write_bytes(b'keep memory')
    archive = tmp_path / 'release.zip'; bundle(archive)
    stage = extract_windows(archive, install.with_name('.Xiayuluo.update-test'))
    launched = []
    backup = swap_install(install, stage, launch=lambda *args, **kwargs: launched.append((args, kwargs)))
    assert (install / 'Xiayuluo.exe').read_bytes() == b'MZnew'
    assert (backup / 'Xiayuluo.exe').read_bytes() == b'MZold'
    assert memory.read_bytes() == b'keep memory'
    assert launched[0][0][0] == [str(install / 'Xiayuluo.exe')]


def test_install_rollback_when_launch_or_swap_fails(tmp_path):
    install = tmp_path / 'Xiayuluo'; install.mkdir()
    (install / 'Xiayuluo.exe').write_bytes(b'MZold')
    stage = tmp_path / '.Xiayuluo.update-test'; stage.mkdir()
    (stage / 'Xiayuluo.exe').write_bytes(b'MZnew')
    def fail(*args, **kwargs):
        raise OSError('fixture failed to start')
    with pytest.raises(OSError):
        swap_install(install, stage, launch=fail)
    assert (install / 'Xiayuluo.exe').read_bytes() == b'MZold'
    assert (stage / 'Xiayuluo.exe').read_bytes() == b'MZnew'
    (install / '.env').write_text('keep private')
    with pytest.raises(ValueError):
        swap_install(install, stage)
    assert (install / '.env').read_text() == 'keep private'


def test_update_api_requires_local_token_confirmation_and_idle(client_factory, monkeypatch):
    client, _ = client_factory()
    monkeypatch.setattr(Updates, 'check', lambda self: {'available': True, 'version': '0.2.0'})
    assert client.get('/api/updates', headers={'X-Assistant-Token': ''}).status_code == 403
    assert client.get('/api/updates').json()['install_supported'] is False
    assert client.post('/api/updates/install', json={}).status_code == 409
    assert client.post('/api/updates/install', json={'confirm': True}).status_code == 409
    seen = []
    client.app.state.install_update = lambda updater: seen.append(updater)
    client.app.state.updating = True
    assert client.post('/api/updates/install', json={'confirm': True}).status_code == 409
    session = client.post('/api/sessions').json()['id']
    assert client.post('/api/chat', json={'session_id': session, 'message': 'do not start during update'}).status_code == 409
    client.app.state.updating = False
    assert client.post('/api/updates/install', json={'confirm': True, 'url': 'https://evil.example'}).status_code == 422
    assert client.post('/api/updates/install', json={'confirm': True}).json()['restarting'] is True
    assert len(seen) == 1


def test_failed_update_resets_state_and_does_not_touch_memories(client_factory, monkeypatch):
    client, _ = client_factory()
    client.post('/api/memories', json={'tier': 'permanent', 'content': '保留记忆'})
    def fail(updater):
        raise zipfile.BadZipFile('fixture')
    client.app.state.install_update = fail
    assert client.post('/api/updates/install', json={'confirm': True}).status_code == 409
    assert client.app.state.updating is False
    assert client.get('/api/memories').json()['items'][0]['content'] == '保留记忆'


def test_no_release_is_not_reported_as_up_to_date(client_factory, monkeypatch):
    client, _ = client_factory()
    def fail(self):
        response = httpx.Response(404, request=httpx.Request('GET', 'https://github.com'))
        response.raise_for_status()
    monkeypatch.setattr(Updates, 'check', fail)
    result = client.get('/api/updates')
    assert result.status_code == 409
    assert '尚未发布' in result.json()['detail']
    assert '下载更新' in result.json()['detail']


def test_release_versions_and_pinned_configs_match():
    from tools.release import verify_versions
    assert verify_versions(__version__) == 4


def test_private_download_pages_do_not_need_network_and_require_local_token(client_factory, monkeypatch):
    client, _ = client_factory()
    def no_network(*args, **kwargs):
        raise AssertionError('Download links must not fetch private artifacts anonymously')
    monkeypatch.setattr('assistant.updates.fetch_bytes', no_network)
    assert client.get('/api/updates/downloads', headers={'X-Assistant-Token': ''}).status_code == 403
    info = client.get('/api/updates/downloads').json()
    assert info['mode'] == 'browser-download'
    assert info['windows_url'] == 'https://github.com/XiaYuLuo22/xiayuluo/actions/workflows/windows-app.yml'
    assert info['android_url'] == 'https://github.com/XiaYuLuo22/xiayuluo/releases'
    assert '登录 GitHub' in info['message']
    assert 'token=' not in json.dumps(info)


@pytest.mark.parametrize('repository', ['../other', 'owner/..', 'owner/.', 'owner/repo?token=secret',
    'https://evil.example/repo', 'owner/repo/extra', 'owner/repo#fragment', 'owner/repo\\other'])
def test_manual_download_repository_validation(repository):
    with pytest.raises(ValueError):
        download_pages(repository)


def test_install_cannot_replace_config_data_or_codex_workspace(tmp_path):
    from assistant.config import Settings
    install = tmp_path / 'Xiayuluo'
    config = tmp_path / 'AppData' / '.env'
    data = tmp_path / 'AppData' / 'memories'
    assert update_location_safe(install, config, Settings(data_dir=str(data)))
    assert not update_location_safe(install, install / '.env', Settings(data_dir=str(data)))
    assert not update_location_safe(install, config, Settings(data_dir=str(install / 'memory')))
    assert not update_location_safe(install, config, Settings(data_dir=str(data), codex_workspace=str(install / 'project')))
