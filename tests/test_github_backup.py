import base64
import json
import os
from pathlib import Path
import struct

import httpx
import pytest

from assistant import github_backup as backup


@pytest.fixture
def fixture_files(tmp_path):
    key = tmp_path / 'private.key'
    source = tmp_path / 'consistent-export.json'
    source.write_bytes(b'{"private":"chat and training export fixture"}')
    backup.create_key(key)
    encrypted = tmp_path / 'encrypted.xybackup'
    backup.encrypt(source, encrypted, key)
    return source, key, encrypted


def test_authenticated_round_trip_is_explicit_local_only(fixture_files, tmp_path, monkeypatch):
    source, key, encrypted = fixture_files
    monkeypatch.setattr(httpx.Client, 'send', lambda *args, **kwargs: pytest.fail('Local backup must not use the network'))
    restored = tmp_path / 'recovered.json'
    result = backup.decrypt(encrypted, restored, key)
    assert restored.read_bytes() == source.read_bytes() and result['path'] == str(restored)
    assert source.read_bytes() not in encrypted.read_bytes()
    assert str(source).encode() not in encrypted.read_bytes()
    second = tmp_path / 'second.xybackup'
    backup.encrypt(source, second, key)
    assert second.read_bytes() != encrypted.read_bytes()
    if os.name != 'nt':
        assert key.stat().st_mode & 0o077 == restored.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('kind', ['key', 'encrypt', 'decrypt'])
def test_existing_outputs_are_never_replaced(fixture_files, tmp_path, kind):
    source, key, encrypted = fixture_files
    destination = tmp_path / 'existing'
    destination.write_bytes(b'previous important bytes')
    with pytest.raises(FileExistsError):
        if kind == 'key': backup.create_key(destination)
        elif kind == 'encrypt': backup.encrypt(source, destination, key)
        else: backup.decrypt(encrypted, destination, key)
    assert destination.read_bytes() == b'previous important bytes'
    assert not list(tmp_path.glob('.xiayuluo-encrypted-*'))


@pytest.mark.parametrize('location', ['metadata', 'nonce', 'ciphertext', 'tag'])
def test_all_content_is_authenticated_before_destination_is_created(fixture_files, tmp_path, location):
    source, key, encrypted = fixture_files
    content = bytearray(encrypted.read_bytes())
    metadata_size = struct.unpack('>H', content[len(backup.MAGIC):len(backup.MAGIC) + 2])[0]
    start = len(backup.MAGIC) + 2 + metadata_size
    if location == 'metadata':
        index = content.index(b'202') + 2  # Alter only a valid timestamp digit.
    elif location == 'nonce': index = start
    elif location == 'ciphertext': index = start + 12
    else: index = len(content) - 1
    content[index] ^= 1
    corrupt = tmp_path / 'corrupt.xybackup'
    corrupt.write_bytes(content)
    destination = tmp_path / 'never-created'
    with pytest.raises(ValueError):
        backup.decrypt(corrupt, destination, key)
    assert not destination.exists() and source.is_file()


def test_wrong_or_wrong_length_key_never_creates_output(fixture_files, tmp_path):
    _, _, encrypted = fixture_files
    for size in (0, 31, 32, 33):
        key = tmp_path / f'wrong-{size}.key'
        key.write_bytes(b'x' * size)
        destination = tmp_path / f'never-{size}'
        with pytest.raises(ValueError): backup.decrypt(encrypted, destination, key)
        assert not destination.exists()


def test_key_cannot_be_backup_input_destination_or_hardlink(fixture_files, tmp_path):
    source, key, encrypted = fixture_files
    with pytest.raises(ValueError): backup.encrypt(key, tmp_path / 'bad', key)
    with pytest.raises(ValueError): backup.encrypt(source, key, key)
    with pytest.raises(ValueError): backup.decrypt(encrypted, key, key)
    alias = tmp_path / 'looks-like-export.json'
    os.link(key, alias)
    with pytest.raises(ValueError): backup.encrypt(alias, tmp_path / 'bad', key)
    assert key.stat().st_size == 32


@pytest.mark.parametrize('name', ['.env', '.env.private', 'secret.pem', 'secret.pfx', 'id_ed25519'])
def test_other_credentials_are_not_backup_inputs(fixture_files, tmp_path, name):
    _, key, _ = fixture_files
    source = tmp_path / name
    source.write_bytes(b'private material')
    with pytest.raises(ValueError): backup.encrypt(source, tmp_path / 'bad', key)


def test_symlink_inputs_outputs_keys_and_ancestors_are_rejected(fixture_files, tmp_path):
    source, key, _ = fixture_files
    alias = tmp_path / 'alias'
    try: alias.symlink_to(source)
    except OSError: pytest.skip('Symbolic links unavailable')
    with pytest.raises(ValueError): backup.encrypt(alias, tmp_path / 'bad', key)
    alias.unlink()
    alias.symlink_to(key)
    with pytest.raises(ValueError): backup.encrypt(source, tmp_path / 'bad', alias)
    alias.unlink()
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError): backup.encrypt(source, alias / 'bad', key)


def test_size_limit_is_checked_before_read_or_publication(fixture_files, tmp_path):
    _, key, _ = fixture_files
    source = tmp_path / 'large.bin'
    with source.open('wb') as stream: stream.truncate(backup.MAX_PLAINTEXT + 1)
    with pytest.raises(ValueError, match='limit'): backup.encrypt(source, tmp_path / 'bad', key)
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('name', ['memory.sqlite3', 'memory.db', 'memory.db-wal', 'memory.db-shm'])
def test_live_databases_are_not_mistaken_for_consistent_exports(fixture_files, tmp_path, name):
    _, key, _ = fixture_files
    source = tmp_path / name
    source.write_bytes(b'not a consistent snapshot')
    with pytest.raises(ValueError, match='consistent database export'):
        backup.encrypt(source, tmp_path / 'bad', key)
    assert not (tmp_path / 'bad').exists()


def test_failed_publication_keeps_originals_and_no_partial_files(fixture_files, tmp_path, monkeypatch):
    source, key, encrypted = fixture_files
    previous = {path: path.read_bytes() for path in (source, key, encrypted)}
    monkeypatch.setattr(backup.os, 'link', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk unavailable')))
    destination = tmp_path / 'new-package'
    with pytest.raises(OSError): backup.encrypt(source, destination, key)
    assert not destination.exists() and not list(tmp_path.glob('.xiayuluo-encrypted-*'))
    assert all(path.read_bytes() == value for path, value in previous.items())


@pytest.mark.parametrize('content', [b'plaintext private chat', backup.MAGIC, backup.MAGIC + b'\xff\xff',
    backup.MAGIC + b'\x00\x01' + b'{', backup.MAGIC + b'\x00\x02' + b'{}' + b'x' * 28])
def test_plaintext_or_invalid_package_never_reaches_github(tmp_path, content):
    source = tmp_path / 'input'
    source.write_bytes(content)
    with pytest.raises(ValueError):
        backup.upload('Owner/repository', source, 'test-only-token', transport=httpx.MockTransport(
            lambda request: pytest.fail('Invalid package must never be uploaded')))


def test_upload_is_only_ciphertext_to_fixed_api_create_path(fixture_files):
    source, key, encrypted = fixture_files
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == 'PUT' and request.url.scheme == 'https'
        assert request.url.host == 'api.github.com'
        assert request.url.path.startswith('/repos/Owner/repository/contents/backups/xiayuluo-')
        assert request.url.path.endswith('.xybackup')
        payload = json.loads(request.content)
        assert set(payload) == {'message', 'content'}  # No SHA => cannot overwrite.
        assert base64.b64decode(payload['content']) == encrypted.read_bytes()
        assert source.read_bytes() not in request.content and key.read_bytes() not in request.content
        assert request.headers['authorization'] == 'Bearer test-only-token'
        return httpx.Response(201, json={'ignored': 'do not trust remote URLs'})
    result = backup.upload('Owner/repository', encrypted, 'test-only-token', transport=httpx.MockTransport(respond))
    assert len(calls) == 1 and result['bytes'] == encrypted.stat().st_size
    assert result['url'].startswith('https://github.com/Owner/repository/blob/HEAD/backups/')
    assert 'test-only-token' not in str(result)


@pytest.mark.parametrize('status', [200, 301, 302, 307, 401, 403, 409, 422, 500])
def test_upload_errors_and_redirects_do_not_retry_or_leak_token(fixture_files, status):
    _, _, encrypted = fixture_files
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': 'https://evil.example/steal-token'})
    with pytest.raises(ValueError) as failure:
        backup.upload('Owner/repository', encrypted, 'test-only-token', transport=httpx.MockTransport(respond))
    assert len(calls) == 1 and 'test-only-token' not in str(failure.value)


@pytest.mark.parametrize('repository', ['https://evil.example', '../bad', 'Owner/..', 'Owner/repo?secret', 'Owner/repo/extra'])
def test_invalid_repository_does_not_use_network(fixture_files, repository):
    _, _, encrypted = fixture_files
    with pytest.raises(ValueError): backup.upload(repository, encrypted, 'token')


def test_network_errors_are_sanitized(fixture_files):
    _, _, encrypted = fixture_files
    def fail(request): raise httpx.ConnectError('test-only-token should not be echoed')
    with pytest.raises(ValueError) as failure:
        backup.upload('Owner/repository', encrypted, 'test-only-token', transport=httpx.MockTransport(fail))
    assert 'test-only-token' not in str(failure.value)


def test_cli_local_commands_and_opt_in_upload(tmp_path, monkeypatch, capsys):
    key, source, encrypted, restored = (tmp_path / name for name in ('private.key', 'export.json', 'backup.xybackup', 'restored.json'))
    source.write_bytes(b'private fixture')
    assert backup.main(['keygen', '--key-file', str(key)]) == 0
    assert backup.main(['encrypt', str(source), str(encrypted), '--key-file', str(key)]) == 0
    assert backup.main(['decrypt', str(encrypted), str(restored), '--key-file', str(key)]) == 0
    assert restored.read_bytes() == source.read_bytes()
    calls = []
    def upload(repository, package, token):
        calls.append((repository, package, token))
        return {'url': 'https://github.com/Owner/repository/blob/HEAD/backups/test.xybackup'}
    monkeypatch.setattr(backup, 'upload', upload)
    monkeypatch.setenv('GITHUB_BACKUP_TOKEN', 'secret-test-only')
    assert backup.main(['upload', 'Owner/repository', str(encrypted)]) == 0
    assert calls == [('Owner/repository', encrypted, 'secret-test-only')]
    output = capsys.readouterr()
    assert 'secret-test-only' not in output.out + output.err


def test_cli_missing_token_fails_before_any_network(fixture_files, monkeypatch, capsys):
    _, _, encrypted = fixture_files
    monkeypatch.delenv('GITHUB_BACKUP_TOKEN', raising=False)
    monkeypatch.setattr(httpx.Client, 'send', lambda *args, **kwargs: pytest.fail('No token means no request'))
    assert backup.main(['upload', 'Owner/repository', str(encrypted)]) == 1
    assert 'explicit GitHub token' in capsys.readouterr().err
