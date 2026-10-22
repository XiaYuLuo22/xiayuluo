"""Update cleanup must never remove files/directories it did not create."""
import hashlib
from types import SimpleNamespace

import httpx
import pytest

from assistant.updates import fetch_bytes
from test_offline_updates import make_package
from test_updates import release


FIXED_ID = '0123456789abcdef0123456789abcdef'
DOWNLOAD_URL = 'https://github.com/XiaYuLuo22/xiayuluo/releases/download/v0.2.0/Xiayuluo-Windows.zip'


def route_online_download(updater, content, monkeypatch):
    metadata = {'available': True, 'version': '0.2.0', 'assets': {'windows-x64': {
        'url': DOWNLOAD_URL, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}}}
    monkeypatch.setattr(updater, 'check', lambda: metadata)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)))
    monkeypatch.setattr('assistant.updates.fetch_bytes',
        lambda *args, **kwargs: fetch_bytes(*args, **kwargs, client=client))
    return client


@pytest.mark.parametrize('mode', ['offline', 'online'])
@pytest.mark.parametrize('collision', ['cache', 'stage'])
def test_update_name_collisions_preserve_existing_targets(tmp_path, release, monkeypatch, mode, collision):
    updater, package, install = make_package(tmp_path, release)
    monkeypatch.setattr('assistant.updates.uuid.uuid4', lambda: SimpleNamespace(hex=FIXED_ID))
    cache_file = updater.cache / f'{FIXED_ID}.zip'
    stage = install.parent / f'.{install.name}.update-{FIXED_ID}'
    if collision == 'cache':
        updater.cache.mkdir()
        cache_file.write_bytes(b'earlier cached archive: preserve')
    else:
        stage.mkdir()
        (stage / '.env').write_text('existing user file: preserve', encoding='utf-8')
    with route_online_download(updater, (tmp_path / 'windows.zip').read_bytes(), monkeypatch):
        with pytest.raises(FileExistsError):
            if mode == 'offline':
                updater.prepare_local(package, install)
            else:
                updater.prepare(install)
    if collision == 'cache':
        assert cache_file.read_bytes() == b'earlier cached archive: preserve'
        assert not stage.exists()
    else:
        assert (stage / '.env').read_text(encoding='utf-8') == 'existing user file: preserve'
        assert not cache_file.exists()
    assert (install / 'Xiayuluo.exe').read_bytes() == b'MZold'
    assert package.is_file()
    assert not updater.lock.locked()


@pytest.mark.parametrize('failure', ['hash', 'size', 'limit', 'network'])
def test_failed_fetch_removes_only_its_new_exclusive_file(tmp_path, failure):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * 70000
            raise httpx.ReadError('controlled transport failure')
    destination = tmp_path / 'new-download.zip'
    unrelated = tmp_path / 'already-existing.zip'
    unrelated.write_bytes(b'preserve unrelated file')
    options = {'limit': 100000}
    if failure == 'hash': options['expected_hash'] = '0' * 64
    if failure == 'size': options['expected_size'] = 4
    if failure == 'limit': options['limit'] = 2
    def response(request):
        return httpx.Response(200, stream=BrokenStream()) if failure == 'network' else httpx.Response(200, content=b'new')
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        with pytest.raises((ValueError, httpx.ReadError)):
            fetch_bytes(DOWNLOAD_URL, destination=destination, client=client, **options)
    assert not destination.exists()
    assert unrelated.read_bytes() == b'preserve unrelated file'


def test_fetch_refuses_existing_destination_without_unlinking_it(tmp_path):
    destination = tmp_path / 'existing-download.zip'
    destination.write_bytes(b'preserve original')
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'new'))) as client:
        with pytest.raises(FileExistsError):
            fetch_bytes(DOWNLOAD_URL, 100, destination=destination, client=client)
    assert destination.read_bytes() == b'preserve original'


def test_online_prepare_failed_hash_cleans_new_download_and_releases_lock(tmp_path, release, monkeypatch):
    updater, package, install = make_package(tmp_path, release)
    monkeypatch.setattr('assistant.updates.uuid.uuid4', lambda: SimpleNamespace(hex=FIXED_ID))
    with route_online_download(updater, b'controlled response', monkeypatch):
        metadata = updater.check()
        metadata['assets']['windows-x64']['sha256'] = '0' * 64
        with pytest.raises(ValueError, match='校验失败'):
            updater.prepare(install)
    assert not list(updater.cache.iterdir())
    assert not list(tmp_path.glob('.Xiayuluo.update-*'))
    assert (install / 'Xiayuluo.exe').read_bytes() == b'MZold'
    assert package.is_file() and not updater.lock.locked()
