"""Local data-only training snapshots, independent of installed program versions.

Callers must quiesce their training/data writers while snapshotting. Files and
the inventory are also rechecked so an external edit makes the snapshot fail
instead of being reported as complete. Nothing loads a model or runs dataset
contents. Backups are not encrypted and must remain in the owner's private
storage; a second disk is needed to survive loss of the original disk.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile


TREES = ('datasets', 'models', 'checkpoints')
MANIFEST = 'training-backup.json'
MAX_FILES = 10000
MAX_BYTES = 8 * 1024 ** 3
MAX_MANIFEST_BYTES = 4 * 1024 ** 2
CHUNK = 1024 * 1024
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_RESERVED = re.compile(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z', re.I)
_PRIVATE_NAMES = {'.env', 'password', 'id_rsa', 'id_ed25519'}
_PRIVATE_SUFFIXES = {'.pem', '.p12', '.pfx', '.jks', '.key'}
_DATABASE_SUFFIXES = {'.sqlite', '.sqlite3', '.db'}


def _limits(max_files, max_bytes):
    if type(max_files) is not int or not 1 <= max_files <= MAX_FILES:
        raise ValueError('Invalid training backup file limit.')
    if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_BYTES:
        raise ValueError('Invalid training backup byte limit.')


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _same_open_file(path_info, descriptor_info):
    # CPython 3.12 on Windows exposes creation time as lstat().st_ctime,
    # but change time as fstat().st_ctime. They are not interchangeable.
    # Match the actual object and contents' metadata across the two APIs;
    # _regular still checks EACH API's full signature before/after reading.
    return _identity(path_info)[:4] == _identity(descriptor_info)[:4]


def _path(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Training backup paths must not contain parent traversal.')
    return path.absolute()


def _no_links(path):
    for current in reversed((path, *path.parents)):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if _linked(info):
            raise ValueError('Training backup paths must not use symbolic links or junctions.')


def _relative(name):
    if not isinstance(name, str) or len(name) > 2048 or '\\' in name or ':' in name:
        raise ValueError('Invalid training backup member path.')
    value = PurePosixPath(name)
    if (value.is_absolute() or value.as_posix() != name or len(value.parts) < 2
            or value.parts[0] not in TREES):
        raise ValueError('Backup member is outside the training data trees.')
    for part in value.parts:
        if (part in {'.', '..'} or part.endswith((' ', '.')) or _RESERVED.fullmatch(part)
                or any(ord(char) < 32 or ord(char) == 127 for char in part)):
            raise ValueError('Unsafe training backup member name.')
    basename = value.name.casefold()
    if (basename in _PRIVATE_NAMES or basename.startswith('.env.')
            or value.suffix.casefold() in _PRIVATE_SUFFIXES):
        raise ValueError('Signing material or private configuration is not training data.')
    # A plain file copy cannot promise a consistent live SQLite database.
    if value.suffix.casefold() in _DATABASE_SUFFIXES or basename.endswith(('-wal', '-shm', '-journal')):
        raise ValueError('Training databases require a database-aware export before backup.')
    return value


def _portable_paths(names):
    files, spellings = {name.casefold() for name in names}, {}
    for name in names:
        parts = PurePosixPath(name).parts
        for length in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:length])
            folded = prefix.casefold()
            if folded in spellings and spellings[folded] != prefix:
                raise ValueError('Training paths collide on Windows.')
            spellings[folded] = prefix
            if length < len(parts) and folded in files:
                raise ValueError('Training backup has a file/directory path conflict.')


@contextmanager
def _regular(path, expected=None, maximum=None):
    _no_links(path)
    before = path.lstat()
    if _linked(before) or not stat.S_ISREG(before.st_mode):
        raise ValueError('Training backup inputs must be regular files.')
    if expected is not None and _identity(before) != expected:
        raise ValueError('Training data changed while preparing its backup.')
    if maximum is not None and before.st_size > maximum:
        raise ValueError('Training backup input exceeds its size limit.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(descriptor)
        if not _same_open_file(before, opened) or not stat.S_ISREG(opened.st_mode) or _linked(opened):
            raise ValueError('Training backup input identity changed.')
        _no_links(path)
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            yield stream
        after_descriptor, after_path = os.fstat(descriptor), path.lstat()
        if (_identity(after_descriptor) != _identity(opened) or _identity(after_path) != _identity(before)
                or not stat.S_ISREG(after_descriptor.st_mode) or _linked(after_descriptor)
                or not stat.S_ISREG(after_path.st_mode) or _linked(after_path)):
            raise ValueError('Training data changed while reading its backup.')
        _no_links(path)
    finally:
        os.close(descriptor)


def _inventory(root, max_files, max_bytes):
    result, folded, total = {}, set(), 0
    for tree in TREES:
        base = root / tree
        if not base.exists() and not base.is_symlink():
            continue
        _no_links(base)
        if not base.is_dir():
            raise ValueError('Training data trees must be directories.')
        def unreadable(error):
            raise error

        for directory, subdirectories, files in os.walk(base, followlinks=False, onerror=unreadable):
            for name in subdirectories:
                item = Path(directory) / name
                if _linked(item.lstat()):
                    raise ValueError('Training backups do not follow directory links or junctions.')
            for name in files:
                item = Path(directory) / name
                relative = item.relative_to(root).as_posix()
                _relative(relative)
                info = item.lstat()
                if _linked(info):
                    raise ValueError('Training backups do not follow file links or junctions.')
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Training backups only include regular data files.')
                if relative.casefold() in folded:
                    raise ValueError('Training filenames collide on Windows.')
                folded.add(relative.casefold())
                total += info.st_size
                if len(result) >= max_files or total > max_bytes:
                    raise ValueError('Training data exceeds the snapshot resource limits.')
                result[relative] = _identity(info)
    _portable_paths(result)
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate training backup manifest field.')
        result[key] = value
    return result


def _manifest(archive, max_files, max_bytes):
    items = archive.infolist()
    if len(items) > max_files + 1 or len({item.filename for item in items}) != len(items):
        raise ValueError('Training backup has duplicate or excessive members.')
    manifest_items = [item for item in items if item.filename == MANIFEST]
    if len(manifest_items) != 1 or manifest_items[0].file_size > MAX_MANIFEST_BYTES:
        raise ValueError('Training backup manifest is missing or oversized.')
    for item in items:
        mode = stat.S_IFMT(item.external_attr >> 16)
        if (item.is_dir() or item.flag_bits & 1 or mode not in (0, stat.S_IFREG)
                or item.external_attr & 0x10
                or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise ValueError('Training backup contains an unsupported archive member.')
    with archive.open(manifest_items[0]) as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('Training backup manifest is oversized.')
    try:
        data = json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite backup metadata.')))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Invalid training backup manifest.') from error
    if (not isinstance(data, dict) or set(data) != {'schema', 'application', 'created_at', 'trees', 'files'}
            or type(data['schema']) is not int or data['schema'] != 1
            or data['application'] != 'xiayuluo-training-backup' or data['trees'] != list(TREES)
            or not isinstance(data['created_at'], str) or len(data['created_at']) > 40
            or not isinstance(data['files'], list) or len(data['files']) > max_files):
        raise ValueError('Unsupported training backup manifest.')
    expected, folded, total = {}, set(), 0
    for entry in data['files']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}:
            raise ValueError('Invalid training backup file metadata.')
        name = entry['path']
        _relative(name)
        if name in expected or name.casefold() in folded:
            raise ValueError('Training backup paths collide.')
        if (type(entry['size']) is not int or entry['size'] < 0
                or not isinstance(entry['sha256'], str) or not _DIGEST.fullmatch(entry['sha256'])):
            raise ValueError('Invalid training backup file size or digest.')
        total += entry['size']
        if total > max_bytes:
            raise ValueError('Training backup exceeds its expanded size limit.')
        expected[name] = entry
        folded.add(name.casefold())
    _portable_paths(expected)
    if {item.filename for item in items} != {MANIFEST, *expected}:
        raise ValueError('Training backup inventory does not match its manifest.')
    for item in items:
        if item.filename != MANIFEST and item.file_size != expected[item.filename]['size']:
            raise ValueError('Training backup member size does not match its manifest.')
    return data, expected, total


def _transfer(archive, entry, output=None):
    digest, size = hashlib.sha256(), 0
    with archive.open(entry['path']) as source:
        while chunk := source.read(CHUNK):
            size += len(chunk)
            if size > entry['size']:
                raise ValueError('Training backup member expands beyond its declared size.')
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
    if size != entry['size'] or digest.hexdigest() != entry['sha256']:
        raise ValueError('Training backup member failed its size/hash check.')


def verify_backup(archive, *, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """Validate all data bytes before any restore destination is touched."""
    _limits(max_files, max_bytes)
    archive = _path(archive)
    maximum = max_bytes + max_bytes // 100 + MAX_MANIFEST_BYTES
    try:
        with _regular(archive, maximum=maximum) as source, zipfile.ZipFile(source) as bundle:
            data, expected, total = _manifest(bundle, max_files, max_bytes)
            for entry in expected.values():
                _transfer(bundle, entry)
    except (zipfile.BadZipFile, EOFError, RuntimeError, OverflowError) as error:
        raise ValueError('Invalid training backup archive.') from error
    return {'schema': 1, 'files': len(expected), 'bytes': total,
            'created_at': data['created_at'], 'entries': list(expected.values())}


def _backup_name():
    return 'training-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex + '.zip'


def snapshot(root, backup_dir=None, *, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """Copy training data into a new verified local snapshot; never prune history."""
    _limits(max_files, max_bytes)
    root = _path(root)
    _no_links(root)
    if not root.is_dir():
        raise ValueError('Training root must already exist.')
    destination = _path(backup_dir) if backup_dir is not None else root / 'backups'
    for name in TREES:
        if destination == root / name or destination.is_relative_to(root / name):
            raise ValueError('Training backups must be outside the data trees being copied.')
    _no_links(destination)
    before = _inventory(root, max_files, max_bytes)
    total = sum(signature[2] for signature in before.values())
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    _no_links(destination)
    if not destination.is_dir() or shutil.disk_usage(destination).free < total + 16 * 1024 ** 2:
        raise ValueError('Not enough space for a complete training backup; originals are unchanged.')
    final = destination / _backup_name()
    descriptor, temporary = tempfile.mkstemp(prefix='.training-backup-', suffix='.tmp', dir=destination)
    temporary = Path(temporary)
    own_identity = os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino
    entries = []
    try:
        with os.fdopen(descriptor, 'w+b') as output:
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as bundle:
                for name, signature in sorted(before.items()):
                    digest, size = hashlib.sha256(), 0
                    info = zipfile.ZipInfo(name)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    with _regular(root / name, expected=signature) as source, bundle.open(info, 'w', force_zip64=True) as target:
                        while chunk := source.read(CHUNK):
                            size += len(chunk)
                            if size > signature[2]:
                                raise ValueError('Training data changed during its backup.')
                            digest.update(chunk)
                            target.write(chunk)
                    if size != signature[2]:
                        raise ValueError('Training data changed during its backup.')
                    entries.append({'path': name, 'size': size, 'sha256': digest.hexdigest()})
                if _inventory(root, max_files, max_bytes) != before:
                    raise ValueError('Training data inventory changed during its backup.')
                manifest = {'schema': 1, 'application': 'xiayuluo-training-backup',
                            'created_at': datetime.now(timezone.utc).isoformat(), 'trees': list(TREES), 'files': entries}
                raw = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ValueError('Training backup manifest is too large.')
                bundle.writestr(MANIFEST, raw)
            output.flush()
            os.fsync(output.fileno())
        verified = verify_backup(temporary, max_files=max_files, max_bytes=max_bytes)
        with _regular(temporary) as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        os.link(temporary, final, follow_symlinks=False)  # Atomic, never replaces an existing snapshot.
        return {'path': str(final), 'files': verified['files'], 'bytes': verified['bytes'], 'sha256': digest}
    finally:
        try:
            info = temporary.lstat()
            if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == own_identity:
                temporary.unlink()
        except FileNotFoundError:
            pass


def restore_to_new_directory(archive, destination, *, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """Verify and restore to a NEW directory only; never merge or overwrite data."""
    archive = _path(archive)
    original_archive = _identity(archive.lstat())
    verified = verify_backup(archive, max_files=max_files, max_bytes=max_bytes)
    if _identity(archive.lstat()) != original_archive:
        raise ValueError('Training backup archive changed before restoration.')
    destination = _path(destination)
    _no_links(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Restore destination already exists; original training data is never overwritten.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    _no_links(destination.parent)
    if shutil.disk_usage(destination.parent).free < verified['bytes'] + 16 * 1024 ** 2:
        raise ValueError('Not enough space to restore training data.')
    published, directories, identity = [], [], None
    with tempfile.TemporaryDirectory(prefix='.training-restore-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        for tree in TREES:
            (stage / tree).mkdir(mode=0o700)
        try:
            with _regular(archive, expected=original_archive,
                          maximum=max_bytes + max_bytes // 100 + MAX_MANIFEST_BYTES) as source, zipfile.ZipFile(source) as bundle:
                _, expected, total = _manifest(bundle, max_files, max_bytes)
                for entry in expected.values():
                    relative = _relative(entry['path'])
                    target = stage.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with target.open('xb') as output:
                        _transfer(bundle, entry, output)
                        output.flush()
                        os.fsync(output.fileno())
                    target.chmod(0o600)
            destination.mkdir(mode=0o700)
            info = destination.lstat()
            identity = info.st_dev, info.st_ino
            for directory, subdirectories, files in os.walk(stage):
                relative = Path(directory).relative_to(stage)
                parent = destination / relative
                if relative.parts:
                    _no_links(parent.parent)
                    parent.mkdir(mode=0o700)
                    directories.append(parent)
                for name in files:
                    _no_links(parent)
                    source, target = Path(directory) / name, parent / name
                    os.link(source, target, follow_symlinks=False)
                    published.append((source, target))
            # Check the published data too, before telling a caller it is safe
            # to select the restored root. The original tree is never touched.
            for entry in expected.values():
                target = destination.joinpath(*_relative(entry['path']).parts)
                with _regular(target, maximum=entry['size']) as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                        raise ValueError('Restored training data failed its final hash check.')
        except BaseException:
            # Clean only files still linked to this invocation's staging files;
            # foreign concurrently-created files/directories are never deleted.
            if identity is not None and destination.is_dir() and not destination.is_symlink():
                info = destination.lstat()
                if (info.st_dev, info.st_ino) == identity:
                    for source, target in reversed(published):
                        try:
                            _no_links(target)
                            if os.path.samefile(source, target):
                                target.unlink()
                        except (FileNotFoundError, ValueError):
                            pass
                    for directory in reversed(directories):
                        try:
                            _no_links(directory)
                            directory.rmdir()
                        except (FileNotFoundError, OSError, ValueError):
                            pass
                    try:
                        destination.rmdir()
                    except OSError:
                        pass
            raise
    return {'path': str(destination), 'files': len(expected), 'bytes': total}
