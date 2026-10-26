"""Training storage must survive application updates without changing location."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid

import pytest

from assistant import training_backup
from assistant import training_storage as storage


@pytest.fixture
def location(tmp_path):
    return tmp_path / 'preferences' / 'training-location.json'


@pytest.fixture
def store(tmp_path, location):
    return storage.TrainingStorage(tmp_path / 'private-training', location_file=location)


def registration(location, root):
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps({'schema': 1, 'root': str(root)}), encoding='utf-8')


def symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip('Symbolic links unavailable on this platform')


@pytest.mark.parametrize('platform,environment,expected', [
    ('win32', {'LOCALAPPDATA': '/profile/local'}, '/profile/local/Xiayuluo'),
    ('win32', {}, '/profile/home/AppData/Local/Xiayuluo'),
    ('darwin', {}, '/profile/home/Library/Application Support/Xiayuluo'),
    ('linux', {'XDG_DATA_HOME': '/profile/data'}, '/profile/data/Xiayuluo'),
    ('linux', {}, '/profile/home/.local/share/Xiayuluo'),
])
def test_user_data_paths_do_not_include_application_version(platform, environment, expected):
    assert storage.user_data_base(platform=platform, environment=environment,
                                  home='/profile/home') == Path(expected)


def test_ensure_registers_a_managed_store_and_retains_existing_bytes(store, location):
    assert store.ensure() == store.root
    assert set(storage.AREAS) <= {path.name for path in store.root.iterdir()}
    assert json.loads(location.read_text()) == {'schema': 1, 'root': str(store.root)}
    sample = store.root / 'datasets' / 'private-frame.png'
    sample.write_bytes(b'private fixture retained exactly')
    next_start = storage.TrainingStorage(location_file=location)
    assert next_start.root == store.root
    next_start.ensure()
    assert sample.read_bytes() == b'private fixture retained exactly'
    assert next_start.status()['ready'] and next_start.status()['has_data']


def test_registered_location_is_stable_across_cwd_and_source_or_frozen_mode(store, location,
                                                                         monkeypatch, tmp_path):
    store.ensure()
    other_install = tmp_path / 'app-version-123'
    other_install.mkdir()
    monkeypatch.chdir(other_install)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(other_install / 'Xiayuluo.exe'))
    frozen = storage.TrainingStorage(location_file=location)
    assert frozen.root == store.root
    frozen.ensure()
    monkeypatch.setattr(sys, 'frozen', False)
    assert storage.TrainingStorage(location_file=location).root == store.root


@pytest.mark.skipif(sys.platform != 'win32', reason='Exercises real Windows per-user registry')
def test_windows_registry_roundtrip_uses_an_isolated_test_key(tmp_path, monkeypatch):
    import winreg

    isolated_key = r'Software\Xiayuluo-Test-' + uuid.uuid4().hex
    assert isolated_key.startswith('Software\\Xiayuluo-Test-')
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, isolated_key)
    monkeypatch.setattr(storage, 'REGISTRY_KEY', isolated_key)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local-app-data'))
    monkeypatch.delenv('ASSISTANT_TRAINING_DIR', raising=False)
    root = tmp_path / 'registered-private-training'
    try:
        first = storage.TrainingStorage(root)
        first.ensure()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, isolated_key) as key:
            value, kind = winreg.QueryValueEx(key, storage.REGISTRY_VALUE)
        assert value == str(root) and kind == winreg.REG_SZ
        restarted = storage.TrainingStorage()
        assert restarted.root == root
        restarted.ensure()
        with first.locked():
            with pytest.raises(ValueError, match='锁'):
                with restarted.locked(timeout=.05):
                    pytest.fail('Windows byte lock allowed a second writer')
        other = storage.TrainingStorage(tmp_path / 'must-not-replace-original')
        with pytest.raises(ValueError, match='迁移'):
            other.ensure()
        assert not other.root.exists()
        assert storage.TrainingStorage().root == root
    finally:
        # Only this unpredictable, previously absent test key is ever removed.
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, isolated_key)
        except FileNotFoundError:
            pass


def test_environment_and_explicit_absolute_paths_are_resolved_without_cwd_dependencies(tmp_path,
                                                                                    location):
    custom = tmp_path / 'E-drive' / 'my-private-training'
    environment = {'ASSISTANT_TRAINING_DIR': str(custom)}
    assert storage.resolve_training_root(environment=environment, location_file=location) == custom
    explicit = tmp_path / 'explicit'
    assert storage.resolve_training_root(explicit, environment=environment,
                                         location_file=location) == explicit
    for value in ('relative/training', 'C:relative', 'bad\0path'):
        with pytest.raises(ValueError):
            storage.resolve_training_root(value, location_file=location)


def test_default_path_does_not_depend_on_frozen_executable_or_cwd(tmp_path, monkeypatch):
    preferences = tmp_path / 'profile'
    monkeypatch.setenv('XDG_DATA_HOME', str(preferences))
    monkeypatch.setenv('LOCALAPPDATA', str(preferences))
    monkeypatch.delenv('ASSISTANT_TRAINING_DIR', raising=False)
    location = preferences / 'training-location.json'
    expected = storage.resolve_training_root(environment=dict(storage.os.environ),
                                             location_file=location)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'version-999' / 'Xiayuluo.exe'))
    monkeypatch.chdir(tmp_path)
    actual = storage.resolve_training_root(environment=dict(storage.os.environ),
                                           location_file=location)
    assert actual == expected and 'version-999' not in str(actual)


def test_configuration_change_never_silently_switches_to_an_empty_store(store, location, tmp_path):
    store.ensure()
    sample = store.root / 'models' / 'weights.npz'
    sample.write_bytes(b'keep numeric weight bytes')
    original_registration = location.read_bytes()
    changed_root = tmp_path / 'different-training'
    changed = storage.TrainingStorage(changed_root, location_file=location)
    assert not changed.status()['ready']
    with pytest.raises(ValueError, match='迁移'):
        changed.ensure()
    assert not changed_root.exists()
    assert location.read_bytes() == original_registration
    assert sample.read_bytes() == b'keep numeric weight bytes'


def test_missing_registered_disk_is_not_replaced_by_a_new_empty_root(store, location, tmp_path):
    store.ensure()
    sample = store.root / 'datasets' / 'sample.json'
    sample.write_bytes(b'{"private":"fixture"}')
    disconnected = tmp_path / 'disconnected-volume'
    store.root.rename(disconnected)
    restarted = storage.TrainingStorage(location_file=location)
    assert not restarted.status()['ready']
    for operation in (restarted.ensure, lambda: restarted.backup(only_if_data=True)):
        with pytest.raises(ValueError, match='不可用'):
            operation()
        assert not restarted.root.exists()
    assert (disconnected / 'datasets' / 'sample.json').read_bytes() == b'{"private":"fixture"}'


@pytest.mark.parametrize('invalid', [
    '', '{bad json', '[]', 'null', 'true', '1', '"text"',
    '{"schema":true,"root":"/tmp/fixture"}',
    '{"schema":2,"root":"/tmp/fixture"}',
    '{"schema":1,"root":"relative"}',
    '{"schema":1,"root":"/tmp/a","root":"/tmp/b"}',
    '{"schema":1,"root":"/tmp/fixture","extra":1}',
    '{"schema":1}', ' ' * 4097,
])
def test_invalid_registry_fails_closed_without_replacing_it(tmp_path, location, invalid):
    location.parent.mkdir(parents=True)
    location.write_text(invalid)
    before = location.read_bytes()
    with pytest.raises((OSError, ValueError)):
        storage.TrainingStorage(location_file=location)
    explicit = storage.TrainingStorage(tmp_path / 'must-not-be-created', location_file=location)
    with pytest.raises((OSError, ValueError)):
        explicit.ensure()
    assert not explicit.root.exists() and location.read_bytes() == before


@pytest.mark.parametrize('invalid', [
    '{"schema":true,"application":"com.xiayuluo.training"}',
    '{"schema":2,"application":"com.xiayuluo.training"}',
    '{"schema":1,"schema":1,"application":"com.xiayuluo.training"}',
    '{"schema":1,"application":"com.xiayuluo.training","extra":1}',
    '{"schema":1,"application":"another-app"}', '[]', 'null', ' ' * 4097,
])
def test_invalid_store_marker_does_not_overwrite_existing_data(store, invalid):
    store.ensure()
    sample = store.root / 'models' / 'weights.npz'
    sample.write_bytes(b'original weights')
    marker = store.root / storage.TRAINING_MARKER
    marker.write_text(invalid)
    with pytest.raises((OSError, ValueError)):
        store.ensure()
    assert sample.read_bytes() == b'original weights' and marker.read_text() == invalid
    assert not store.status()['ready']


def test_unmanaged_nonempty_directory_is_not_adopted_or_cleaned(store):
    store.root.mkdir()
    original = store.root / 'my-document'
    original.write_bytes(b'do not adopt or delete')
    with pytest.raises(ValueError, match='未管理'):
        store.ensure()
    assert list(store.root.iterdir()) == [original]
    assert original.read_bytes() == b'do not adopt or delete'


def test_symlink_root_ancestor_and_location_record_are_rejected(tmp_path, location):
    target = tmp_path / 'outside'
    target.mkdir()
    alias = tmp_path / 'alias'
    symlink(alias, target, directory=True)
    for root in (alias, alias / 'nested-data'):
        candidate = storage.TrainingStorage(root, location_file=location)
        with pytest.raises(ValueError):
            candidate.ensure()
    assert list(target.iterdir()) == []
    record = tmp_path / 'record.json'
    registration(record, target)
    location.parent.mkdir(parents=True, exist_ok=True)
    symlink(location, record)
    with pytest.raises(ValueError):
        storage.TrainingStorage(location_file=location)
    assert location.is_symlink()


def test_registry_parent_link_and_broken_registry_link_fail_closed(tmp_path):
    target = tmp_path / 'outside'
    target.mkdir()
    alias = tmp_path / 'alias'
    symlink(alias, target, directory=True)
    linked_location = alias / 'training-location.json'
    candidate = storage.TrainingStorage(tmp_path / 'data', location_file=linked_location)
    with pytest.raises(ValueError):
        candidate.ensure()
    assert not candidate.root.exists() and list(target.iterdir()) == []
    broken = tmp_path / 'broken-record.json'
    symlink(broken, tmp_path / 'missing-record.json')
    with pytest.raises(ValueError):
        storage.TrainingStorage(location_file=broken)


def test_managed_area_link_and_marker_link_are_rejected(store, tmp_path):
    store.ensure()
    outside = tmp_path / 'outside'
    outside.mkdir()
    for name in storage.AREAS:
        area = store.root / name
        moved = store.root / ('original-' + name)
        area.rename(moved)
        symlink(area, outside, directory=True)
        with pytest.raises(ValueError):
            store.ensure()
        area.unlink()
        moved.rename(area)
    marker = store.root / storage.TRAINING_MARKER
    original = tmp_path / 'original-marker.json'
    marker.rename(original)
    symlink(marker, original)
    with pytest.raises(ValueError):
        store.ensure()
    assert list(outside.iterdir()) == []


def test_broad_roots_and_running_source_or_install_are_rejected(tmp_path, location, monkeypatch):
    for root in (Path(tmp_path.anchor), Path.home(), Path(storage.__file__).resolve().parents[1],
                 Path(storage.__file__).resolve().parent):
        with pytest.raises(ValueError):
            storage.TrainingStorage(root, location_file=location)
    install = tmp_path / 'installed-app'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(install / 'Xiayuluo.exe'))
    for root in (install, install / 'training', tmp_path):
        with pytest.raises(ValueError):
            storage.TrainingStorage(root, location_file=location)
    assert not install.exists()


def test_concurrent_initialization_of_same_root_succeeds_for_both_instances(store, location):
    barrier = threading.Barrier(2)
    second = storage.TrainingStorage(store.root, location_file=location)

    def initialize(candidate):
        barrier.wait(timeout=5)
        return candidate.ensure()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(initialize, [store, second])) == [store.root, store.root]
    assert json.loads(location.read_text())['root'] == str(store.root)


def test_concurrent_different_roots_cannot_replace_first_registration(tmp_path, location):
    barrier = threading.Barrier(2)
    candidates = [storage.TrainingStorage(tmp_path / name, location_file=location)
                  for name in ('first', 'second')]

    def initialize(candidate):
        barrier.wait(timeout=5)
        try:
            return candidate.ensure(), None
        except ValueError as error:
            return None, str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(initialize, candidates))
    winners = [root for root, error in outcomes if error is None]
    assert len(winners) == 1
    assert json.loads(location.read_text())['root'] == str(winners[0])
    assert not next(item.root for item in candidates if item.root != winners[0]).exists()


def test_write_lock_excludes_another_instance_and_is_released_after_exception(store, location):
    second = storage.TrainingStorage(store.root, location_file=location)
    with pytest.raises(RuntimeError, match='fixture failure'):
        with store.locked():
            with pytest.raises(ValueError, match='锁'):
                with second.locked(timeout=.05):
                    pytest.fail('Two writers held the training lock')
            raise RuntimeError('fixture failure')
    with second.locked(timeout=.05):
        assert second.root == store.root


def test_write_lock_excludes_an_independent_python_process(store, location):
    program = '''
from pathlib import Path
import sys
from assistant.training_storage import TrainingStorage
storage = TrainingStorage(Path(sys.argv[1]), location_file=Path(sys.argv[2]))
try:
    with storage.locked(timeout=.05):
        raise SystemExit(3)
except ValueError:
    raise SystemExit(0)
'''
    with store.locked():
        result = subprocess.run([sys.executable, '-c', program, str(store.root), str(location)],
                                cwd=Path(storage.__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_reparse_junction_attribute_is_rejected_without_requiring_windows_link_privileges(
        store, monkeypatch):
    store.ensure()
    real_reparse = storage._reparse
    junction = store.root / 'datasets'
    monkeypatch.setattr(storage, '_reparse', lambda path: path == junction or real_reparse(path))
    with pytest.raises(ValueError, match='链接'):
        store.ensure()


def test_lock_link_never_modifies_the_link_target(store, tmp_path):
    store.ensure()
    outside = tmp_path / 'unrelated-file'
    outside.write_bytes(b'keep untouched')
    symlink(store.root / '.training.lock', outside)
    with pytest.raises(ValueError):
        with store.locked():
            pytest.fail('Linked lock file was accepted')
    assert outside.read_bytes() == b'keep untouched'


def test_backup_holds_the_same_writer_lock_and_keeps_previous_snapshots(store, location,
                                                                     monkeypatch):
    store.ensure()
    sample = store.root / 'datasets' / 'sample.jsonl'
    sample.write_bytes(b'{"frame":"synthetic fixture"}\n')
    real_snapshot = training_backup.snapshot
    second = storage.TrainingStorage(store.root, location_file=location)
    invocations = []

    def snapshot_while_locked(root):
        with pytest.raises(ValueError, match='锁'):
            with second.locked(timeout=.05):
                pytest.fail('Backup did not exclude a training publisher')
        invocations.append(root)
        return real_snapshot(root)

    monkeypatch.setattr(training_backup, 'snapshot', snapshot_while_locked)
    first = store.backup(only_if_data=True)
    previous_bytes = Path(first['path']).read_bytes()
    second_backup = store.backup()
    assert invocations == [store.root, store.root]
    assert Path(first['path']).read_bytes() == previous_bytes
    assert first['path'] != second_backup['path']
    verified = training_backup.verify_backup(second_backup['path'])
    assert verified['files'] == 1
    assert sample.read_bytes() == b'{"frame":"synthetic fixture"}\n'


def test_backup_only_if_data_skips_empty_store_but_full_backup_is_verified(store):
    assert store.backup(only_if_data=True) is None
    assert not store.root.exists()
    store.ensure()
    assert store.backup(only_if_data=True) is None
    result = store.backup()
    assert training_backup.verify_backup(result['path'])['files'] == 0
    assert not store.has_data()


def test_model_paths_are_unique_under_managed_data_not_application_directory(store):
    first = store.model_path()
    second = store.model_path()
    assert first != second and first.parent == second.parent == store.root / 'models'
    assert first.name.startswith('run-') and not first.exists() and not second.exists()


def test_overlap_policy_handles_both_containment_directions_and_resolved_aliases(tmp_path):
    parent = tmp_path / 'app'
    nested = parent / 'training'
    assert storage.paths_overlap(parent, nested) and storage.paths_overlap(nested, parent)
    assert not storage.paths_overlap(parent, tmp_path / 'unrelated')
    parent.mkdir()
    alias = tmp_path / 'alias'
    symlink(alias, parent, directory=True)
    assert storage.paths_overlap(alias, nested)
