"""Training snapshots are local copies and never overwrite existing user data."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import stat
from types import SimpleNamespace
import zipfile

import pytest

from assistant import training_backup as backup


@pytest.fixture
def root(tmp_path):
    root = tmp_path / 'training'
    for directory in backup.TREES:
        (root / directory).mkdir(parents=True)
    (root / 'datasets/session').mkdir()
    (root / 'datasets/session/frame.png').write_bytes(b'private training image fixture')
    (root / 'datasets/session/actions.jsonl').write_text('{"action":"click"}\n')
    (root / 'models/weights.npz').write_bytes(b'opaque numeric model fixture')
    (root / 'checkpoints/progress.json').write_text('{"epoch":12}')
    return root


def originals(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for tree in backup.TREES for path in (root / tree).rglob('*') if path.is_file()}


def rewrite_archive(path, transform):
    with zipfile.ZipFile(path) as source:
        entries = {item.filename: source.read(item) for item in source.infolist()}
    transform(entries)
    changed = path.with_name('changed-' + path.name)
    with zipfile.ZipFile(changed, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name, value in entries.items():
            output.writestr(name, value)
    return changed


def test_snapshot_is_verified_data_only_and_restore_to_new_directory(root, tmp_path):
    expected = originals(root)
    (root / '.env').write_text('not training data')
    (root / 'desktop.log').write_text('not training data')
    result = backup.snapshot(root)
    archive = Path(result['path'])
    assert archive.parent == root / 'backups'
    assert result['files'] == 4 and result['bytes'] == sum(map(len, expected.values()))
    assert result['sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    verified = backup.verify_backup(archive)
    assert verified['files'] == 4
    with zipfile.ZipFile(archive) as source:
        assert set(source.namelist()) == {backup.MANIFEST, *expected}
        manifest = source.read(backup.MANIFEST).decode()
        assert str(root) not in manifest and 'private training image fixture' not in manifest
    destination = tmp_path / 'restored'
    restored = backup.restore_to_new_directory(archive, destination)
    assert restored['files'] == 4 and originals(destination) == expected
    assert originals(root) == expected
    assert not list(tmp_path.glob('.training-restore-*'))


def test_subsequent_snapshots_do_not_include_or_delete_previous_backups(root):
    first = backup.snapshot(root)
    previous = Path(first['path']).read_bytes()
    second = backup.snapshot(root)
    assert first['path'] != second['path'] and Path(first['path']).read_bytes() == previous
    assert second['files'] == first['files'] == 4
    assert len(list((root / 'backups').glob('*.zip'))) == 2
    assert not list((root / 'backups').glob('*.tmp'))


def test_empty_training_root_can_be_backed_up_and_restored(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    result = backup.snapshot(root)
    assert result['files'] == result['bytes'] == 0
    restored = tmp_path / 'restored'
    backup.restore_to_new_directory(result['path'], restored)
    assert {path.name for path in restored.iterdir()} == set(backup.TREES)


def test_existing_restore_destination_is_never_replaced_or_merged(root, tmp_path):
    result = backup.snapshot(root)
    for populated in (False, True):
        destination = tmp_path / ('populated' if populated else 'empty')
        destination.mkdir()
        if populated:
            (destination / 'my-important-file').write_bytes(b'keep')
        with pytest.raises(FileExistsError):
            backup.restore_to_new_directory(result['path'], destination)
        assert destination.is_dir()
        if populated:
            assert (destination / 'my-important-file').read_bytes() == b'keep'


def test_disk_space_failure_does_not_touch_training_data(root, monkeypatch):
    before = originals(root)
    monkeypatch.setattr(backup.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match='space'):
        backup.snapshot(root)
    assert originals(root) == before
    assert not list((root / 'backups').glob('*'))


@pytest.mark.parametrize('max_files,max_bytes', [(1, 10000), (100, 1), (True, 10000),
    (10001, 10000), (100, -1), (100, 8 * 1024 ** 3 + 1)])
def test_resource_limits_fail_without_changing_originals(root, max_files, max_bytes):
    before = originals(root)
    with pytest.raises(ValueError):
        backup.snapshot(root, max_files=max_files, max_bytes=max_bytes)
    assert originals(root) == before


@pytest.mark.parametrize('name', ['.env', 'password', 'release-private.pem', 'signing.p12', 'cache.sqlite3', 'cache.db-wal'])
def test_private_configuration_signing_keys_and_live_databases_are_not_packaged(root, name):
    path = root / 'models' / name
    path.write_text('fixture content must remain local')
    before = originals(root)
    with pytest.raises(ValueError):
        backup.snapshot(root)
    assert originals(root) == before
    assert not (root / 'backups').exists()


def test_symlink_file_directory_root_and_destination_are_rejected(root, tmp_path):
    target = tmp_path / 'outside'
    target.mkdir()
    (target / 'secret').write_text('not training data')
    for link, value in ((root / 'datasets/link', target), (root / 'models/link', target / 'secret')):
        try:
            link.symlink_to(value, target_is_directory=value.is_dir())
        except OSError:
            pytest.skip('Symbolic links unavailable on this platform')
        with pytest.raises(ValueError, match='link|junction'):
            backup.snapshot(root)
        link.unlink()
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='link|junction'):
        backup.snapshot(alias)
    alias.unlink()
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='link|junction'):
        backup.snapshot(root, alias)
    assert (target / 'secret').read_text() == 'not training data'


def test_backup_destination_cannot_be_inside_copied_data(root):
    for path in (root / 'models', root / 'datasets/subdir/backups'):
        with pytest.raises(ValueError, match='outside'):
            backup.snapshot(root, path)


def test_changed_inventory_fails_instead_of_publishing_partial_snapshot(root, monkeypatch):
    before = originals(root)
    original = backup._inventory
    calls = []

    def changed(*args):
        result = original(*args)
        calls.append(True)
        if len(calls) == 2:
            result.pop(next(iter(result)))
        return result

    monkeypatch.setattr(backup, '_inventory', changed)
    with pytest.raises(ValueError, match='changed'):
        backup.snapshot(root)
    assert originals(root) == before
    assert list((root / 'backups').iterdir()) == []


def test_existing_snapshot_name_is_never_overwritten(root, monkeypatch):
    directory = root / 'backups'
    directory.mkdir()
    existing = directory / 'fixed.zip'
    existing.write_bytes(b'previous snapshot')
    monkeypatch.setattr(backup, '_backup_name', lambda: 'fixed.zip')
    with pytest.raises(FileExistsError):
        backup.snapshot(root)
    assert existing.read_bytes() == b'previous snapshot'
    assert list(directory.iterdir()) == [existing]


def test_tampered_data_rejected_before_any_restore_destination_exists(root, tmp_path):
    result = backup.snapshot(root)
    corrupted = rewrite_archive(Path(result['path']), lambda entries: entries.__setitem__(
        'models/weights.npz', b'x' * len(entries['models/weights.npz'])))
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='hash'):
        backup.restore_to_new_directory(corrupted, destination)
    assert not destination.exists()


@pytest.mark.parametrize('bad_name', ['../outside', '/absolute', 'models/../escape',
    'models\\escape', 'models/C:drive', 'models/AUX.png', 'models/trailing. ', '.env'])
def test_archive_traversal_and_windows_unsafe_names_are_rejected(root, tmp_path, bad_name):
    result = backup.snapshot(root)

    def change(entries):
        content = entries.pop('models/weights.npz')
        entries[bad_name] = content
        manifest = json.loads(entries[backup.MANIFEST])
        next(entry for entry in manifest['files'] if entry['path'] == 'models/weights.npz')['path'] = bad_name
        entries[backup.MANIFEST] = json.dumps(manifest).encode()

    changed = rewrite_archive(Path(result['path']), change)
    with pytest.raises(ValueError):
        backup.restore_to_new_directory(changed, tmp_path / 'restore')
    assert not (tmp_path / 'restore').exists()


def test_archive_symlink_extra_member_and_duplicate_members_are_rejected(root, tmp_path):
    result = backup.snapshot(root)
    archive = Path(result['path'])
    extra = rewrite_archive(archive, lambda entries: entries.__setitem__('unlisted', b'not allowed'))
    with pytest.raises(ValueError, match='inventory'):
        backup.verify_backup(extra)
    symbolic = tmp_path / 'symlink.zip'
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(symbolic, 'w') as target:
        for item in source.infolist():
            if item.filename == 'models/weights.npz':
                item.external_attr = (stat.S_IFLNK | 0o777) << 16
            target.writestr(item, source.read(item))
    with pytest.raises(ValueError, match='unsupported'):
        backup.verify_backup(symbolic)
    duplicate = tmp_path / 'duplicate.zip'
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(duplicate, 'w') as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        with pytest.warns(UserWarning):
            target.writestr('models/weights.npz', b'duplicate')
    with pytest.raises(ValueError, match='duplicate'):
        backup.verify_backup(duplicate)


def test_archive_expansion_limits_and_file_count_are_checked_before_restore(root, tmp_path):
    result = backup.snapshot(root)
    for limits in ({'max_bytes': 1}, {'max_files': 1}):
        with pytest.raises(ValueError):
            backup.restore_to_new_directory(result['path'], tmp_path / 'restore', **limits)
    assert not (tmp_path / 'restore').exists()


def test_restore_publication_failure_never_modifies_originals(root, tmp_path, monkeypatch):
    result = backup.snapshot(root)
    before = originals(root)
    original = backup.os.link
    calls = []

    def fail_after_first(source, destination, **kwargs):
        calls.append(True)
        if len(calls) > 1:
            raise OSError('simulated interrupted restore')
        return original(source, destination, **kwargs)

    monkeypatch.setattr(backup.os, 'link', fail_after_first)
    destination = tmp_path / 'restore'
    with pytest.raises(OSError, match='interrupted'):
        backup.restore_to_new_directory(result['path'], destination)
    assert not destination.exists() and originals(root) == before
    assert not list(tmp_path.glob('.training-restore-*'))


def test_unreadable_subdirectory_must_not_be_silently_omitted(root, monkeypatch):
    before = originals(root)

    def inaccessible(path, **options):
        options['onerror'](PermissionError('simulated unreadable data directory'))
        yield  # This is still an os.walk-shaped iterator.

    monkeypatch.setattr(backup.os, 'walk', inaccessible)
    with pytest.raises(PermissionError, match='unreadable'):
        backup.snapshot(root)
    monkeypatch.undo()
    assert originals(root) == before and not (root / 'backups').exists()


def test_case_collisions_are_rejected_for_windows_portability(root):
    (root / 'models/A.txt').write_text('first')
    (root / 'models/a.txt').write_text('second')
    if len(list((root / 'models').glob('*.txt'))) < 2:
        pytest.skip('Case-insensitive filesystem')
    with pytest.raises(ValueError, match='collide'):
        backup.snapshot(root)


def test_data_change_after_copy_invalidates_snapshot_and_keeps_latest_data(root, monkeypatch):
    actual = backup._regular
    changed = []

    @contextmanager
    def mutate_after_read(path, **options):
        with actual(path, **options) as stream:
            yield stream
        if path == root / 'models/weights.npz' and not changed:
            path.write_bytes(b'a newly completed training model')
            changed.append(True)

    monkeypatch.setattr(backup, '_regular', mutate_after_read)
    with pytest.raises(ValueError, match='inventory changed'):
        backup.snapshot(root)
    assert changed and (root / 'models/weights.npz').read_bytes() == b'a newly completed training model'
    assert not list((root / 'backups').iterdir())


def test_failed_snapshot_publication_preserves_all_originals_and_no_partial_zip(root, monkeypatch):
    before = originals(root)
    monkeypatch.setattr(backup.os, 'link', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('simulated publication failure')))
    with pytest.raises(OSError, match='publication'):
        backup.snapshot(root)
    assert originals(root) == before and not list((root / 'backups').iterdir())


def test_case_colliding_source_directories_cannot_merge_on_windows(root):
    (root / 'models/A').mkdir()
    try:
        (root / 'models/a').mkdir()
    except FileExistsError:
        pytest.skip('Case-insensitive filesystem')
    (root / 'models/A/first').write_text('one')
    (root / 'models/a/second').write_text('two')
    with pytest.raises(ValueError, match='collide'):
        backup.snapshot(root)


def test_archive_file_directory_conflicts_fail_verification(root):
    result = backup.snapshot(root)

    def change(entries):
        name, value = 'models/weights.npz/nested', b'cannot be child of a file'
        entries[name] = value
        manifest = json.loads(entries[backup.MANIFEST])
        manifest['files'].append({'path': name, 'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()})
        entries[backup.MANIFEST] = json.dumps(manifest).encode()

    changed = rewrite_archive(Path(result['path']), change)
    with pytest.raises(ValueError, match='conflict'):
        backup.verify_backup(changed)
