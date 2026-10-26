"""Explicit small encrypted backups; importing this module never reads or uploads data.

The public repository receives ciphertext only, never its local key. This is
backup transport, not realtime synchronization. A lost key cannot be recovered
from GitHub; retain an independent private copy. Windows permissions inherit
the selected local directory's ACL; POSIX files are created owner-only.
"""
import base64
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import httpx


MAGIC = b'XIAYULUO-ENCRYPTED-BACKUP\x00\x01'
MAX_PLAINTEXT = 16 * 1024 * 1024
MAX_METADATA = 512
MAX_PACKAGE = len(MAGIC) + 2 + MAX_METADATA + 12 + MAX_PLAINTEXT + 16
_REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z')
_SECRET_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.jks'}
_DATABASE_SUFFIXES = {'.sqlite', '.sqlite3', '.db'}


def _path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Backup paths must be absolute without parent traversal.')
    return path


def _no_links(path):
    for current in (path, *path.parents):
        if os.path.lexists(current):
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Encrypted backup paths must not contain links or junctions.')


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(path, maximum):
    path = _path(path)
    _no_links(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError('Backup input is not a regular file or exceeds the 16 MiB limit.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(descriptor)
        # Windows Python 3.12 path/handle ctime semantics differ. Check the
        # stable object across APIs and each API's full signature over time.
        if _signature(opened)[:4] != _signature(before)[:4] or not stat.S_ISREG(opened.st_mode):
            raise ValueError('Backup input changed before reading.')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            content = stream.read(maximum + 1)
        if (len(content) != before.st_size or _signature(path.lstat()) != _signature(before)
                or _signature(os.fstat(descriptor)) != _signature(opened)):
            raise ValueError('Backup input changed while reading.')
        _no_links(path)
        return content
    finally:
        os.close(descriptor)


def _publish(path, content):
    path = _path(path)
    _no_links(path)
    if os.path.lexists(path):
        raise FileExistsError('Backup output already exists; nothing was overwritten.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _no_links(path.parent)
    descriptor, name = tempfile.mkstemp(prefix='.xiayuluo-encrypted-', dir=path.parent)
    temporary = Path(name)
    owned = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
        _no_links(temporary)
        if _signature(temporary.lstat())[:4] != _signature(written)[:4]:
            raise ValueError('Backup output changed before publication.')
        _no_links(path)
        os.link(temporary, path, follow_symlinks=False)  # Exclusive publication.
    finally:
        try:
            info = temporary.lstat()
            if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (owned.st_dev, owned.st_ino):
                temporary.unlink()
        except FileNotFoundError:
            pass
    return {'path': str(path), 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}


def create_key(path):
    """Explicitly create a new local 32-byte key, never replace an existing key."""
    result = _publish(path, AESGCM.generate_key(bit_length=256))
    # A key's hash is not needed by callers and should not become backup metadata.
    return {'path': result['path'], 'bytes': 32}


def _key(path):
    value = _read(path, 32)
    if len(value) != 32:
        raise ValueError('The local backup key must contain exactly 32 bytes.')
    return value


def _separate_key(key_file, *paths):
    key_file = _path(key_file)
    for value in paths:
        path = _path(value)
        if path == key_file or (path.exists() and key_file.exists() and os.path.samefile(path, key_file)):
            raise ValueError('The private key cannot be encrypted, overwritten or uploaded as a backup.')


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate encrypted backup metadata.')
        value[key] = item
    return value


def _package(content):
    if (len(content) > MAX_PACKAGE or len(content) < len(MAGIC) + 2 + 12 + 16
            or not content.startswith(MAGIC)):
        raise ValueError('Only a bounded Xiayuluo encrypted backup is accepted; plaintext is refused.')
    size = struct.unpack('>H', content[len(MAGIC):len(MAGIC) + 2])[0]
    if not 1 <= size <= MAX_METADATA:
        raise ValueError('Invalid encrypted backup metadata size.')
    offset = len(MAGIC) + 2 + size
    try:
        info = json.loads(content[len(MAGIC) + 2:offset], object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Invalid encrypted backup metadata.') from error
    if (not isinstance(info, dict) or set(info) != {'schema', 'algorithm', 'plaintext_bytes', 'created_at'}
            or type(info['schema']) is not int or info['schema'] != 1
            or info['algorithm'] != 'AES-256-GCM' or type(info['plaintext_bytes']) is not int
            or not 0 <= info['plaintext_bytes'] <= MAX_PLAINTEXT
            or not isinstance(info['created_at'], str) or len(info['created_at']) > 40
            or len(content) != offset + 12 + info['plaintext_bytes'] + 16):
        raise ValueError('Unsupported or truncated encrypted backup.')
    return content[:offset], content[offset:offset + 12], content[offset + 12:]


def encrypt(source, destination, key_file):
    """Encrypt an explicitly selected consistent export or verified training ZIP."""
    _separate_key(key_file, source, destination)
    source = _path(source)
    if (source.suffix.casefold() in _SECRET_SUFFIXES or source.name.casefold().startswith('.env')
            or source.name.casefold() in {'id_rsa', 'id_ed25519'}):
        raise ValueError('Private keys and environment configuration are not backup inputs.')
    if source.suffix.casefold() in _DATABASE_SUFFIXES or source.name.casefold().endswith(('-wal', '-shm', '-journal')):
        raise ValueError('Use a consistent database export, not a live SQLite file or journal.')
    key, plaintext = _key(key_file), _read(source, MAX_PLAINTEXT)
    metadata = json.dumps({'schema': 1, 'algorithm': 'AES-256-GCM',
        'plaintext_bytes': len(plaintext), 'created_at': datetime.now(timezone.utc).isoformat()},
        separators=(',', ':')).encode('utf-8')
    aad = MAGIC + struct.pack('>H', len(metadata)) + metadata
    nonce = os.urandom(12)
    package = aad + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)
    return _publish(destination, package)


def decrypt(package, destination, key_file):
    """Authenticate the whole package before creating any recovery output."""
    _separate_key(key_file, package, destination)
    content, key = _read(package, MAX_PACKAGE), _key(key_file)
    aad, nonce, ciphertext = _package(content)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as error:
        raise ValueError('Backup authentication failed: wrong key or changed package; no files were restored.') from error
    return _publish(destination, plaintext)


def upload(repository, encrypted_file, token, *, transport=None):
    """Explicit opt-in upload of ciphertext only. No redirects, retries or overwrite.

    Structural validation is not authentication without the local key; callers
    should use encrypt() and keep the resulting hash. At most 16 MiB plaintext
    fits one package. Large datasets need a separate private storage design.
    """
    if (not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository)
            or repository.rsplit('/', 1)[-1] in {'.', '..'}):
        raise ValueError('Invalid GitHub owner/repository.')
    if not isinstance(token, str) or not token or len(token) > 4096 or any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError('An explicit GitHub token is required; it is never persisted.')
    content = _read(encrypted_file, MAX_PACKAGE)
    _package(content)
    name = 'xiayuluo-' + uuid.uuid4().hex + '.xybackup'
    url = f'https://api.github.com/repos/{repository}/contents/backups/{name}'
    payload = {'message': 'Add encrypted Xiayuluo backup', 'content': base64.b64encode(content).decode('ascii')}
    try:
        with httpx.Client(transport=transport, timeout=30, follow_redirects=False, trust_env=False) as client:
            request = client.build_request('PUT', url, json=payload, headers={
                'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28'})
            # Stream only response headers. No unbounded response body or remote
            # URL is needed to report success; errors never include credentials.
            response = client.send(request, stream=True, follow_redirects=False)
            try:
                if response.status_code != 201:
                    raise ValueError('GitHub did not create the encrypted backup; no redirect or overwrite was allowed.')
            finally:
                response.close()
    except httpx.HTTPError:
        # Do not chain a transport exception: a third-party transport may have
        # included authentication material in its error message.
        raise ValueError('Encrypted GitHub backup upload failed; the local files were retained.') from None
    return {'url': f'https://github.com/{repository}/blob/HEAD/backups/{name}',
            'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description='夏鱼洛小文件加密备份；仅显式 upload 联网，不自动上传聊天或训练数据。')
    commands = parser.add_subparsers(dest='command', required=True)
    keygen = commands.add_parser('keygen')
    keygen.add_argument('--key-file', required=True, type=Path)
    for command in ('encrypt', 'decrypt'):
        item = commands.add_parser(command)
        item.add_argument('source', type=Path)
        item.add_argument('destination', type=Path)
        item.add_argument('--key-file', required=True, type=Path)
    sending = commands.add_parser('upload')
    sending.add_argument('repository')
    sending.add_argument('package', type=Path)
    sending.add_argument('--token-env', default='GITHUB_BACKUP_TOKEN',
                         help='读取本机环境变量中的GitHub令牌；不接受命令行明文令牌')
    args = parser.parse_args(argv)
    try:
        if args.command == 'keygen':
            result = create_key(args.key_file)
        elif args.command in {'encrypt', 'decrypt'}:
            result = (encrypt if args.command == 'encrypt' else decrypt)(args.source, args.destination, args.key_file)
        else:
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', args.token_env):
                raise ValueError('Invalid token environment variable name.')
            result = upload(args.repository, args.package, os.getenv(args.token_env, ''))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f'加密备份操作未完成，原文件保留：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
