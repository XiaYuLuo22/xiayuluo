"""Program replacement, rollback and installer choices cannot replace training data."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant.training_storage import TRAINING_MARKER
from assistant import update_worker
from assistant.update_worker import swap_install
from assistant.updates import Updates, extract_windows, validate_install
from test_offline_updates import make_package
from test_updates import bundle, release


@pytest.fixture
def layout(tmp_path, monkeypatch):
    training = tmp_path / 'user-data' / 'training'
    for relative, value in (('datasets/demo/frame.png', b'private training screenshot'),
                            ('models/learned/weights.npz', b'learned model bytes'),
                            ('checkpoints/last.bin', b'last completed checkpoint'),
                            ('backups/manual-copy.zip', b'previous user backup')):
        target = training / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    monkeypatch.setenv('ASSISTANT_TRAINING_DIR', str(training))
    install = tmp_path / 'E-drive' / 'Xiayuluo'
    install.mkdir(parents=True)
    (install / 'Xiayuluo.exe').write_bytes(b'MZold')
    return install, training


def hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()}


def stage_for(install, tmp_path):
    archive = tmp_path / 'release.zip'
    bundle(archive)
    return extract_windows(archive, install.with_name('.Xiayuluo.update-training-test'))


@pytest.mark.parametrize('failure', [False, True])
def test_success_and_launch_failure_leave_external_datasets_models_checkpoints_unchanged(tmp_path, layout, failure):
    install, training = layout
    previous = hashes(training)
    stage = stage_for(install, tmp_path)

    def launch(*args, **kwargs):
        if failure:
            raise OSError('controlled new-program launch failure')

    if failure:
        with pytest.raises(OSError):
            swap_install(install, stage, launch=launch, protected_paths=[training])
        assert (install / 'Xiayuluo.exe').read_bytes() == b'MZold'
    else:
        backup = swap_install(install, stage, launch=launch, protected_paths=[training])
        assert (install / 'Xiayuluo.exe').read_bytes() == b'MZnew'
        assert (backup / 'Xiayuluo.exe').read_bytes() == b'MZold'
    assert hashes(training) == previous


@pytest.mark.parametrize('name', ['training', '.vision-learning', TRAINING_MARKER, 'TRAINING'])
def test_existing_installation_training_markers_refuse_update_without_changes(tmp_path, layout, name):
    install, training = layout
    marker = install / name
    marker.write_bytes(b'preserve legacy training data')
    before = hashes(install)
    with pytest.raises(ValueError):
        validate_install(install)
    stage = stage_for(install, tmp_path)
    with pytest.raises(ValueError):
        swap_install(install, stage, launch=lambda *args, **kwargs: pytest.fail('must not launch'))
    assert hashes(install) == before and marker.read_bytes() == b'preserve legacy training data'
    assert len(hashes(training)) == 4


def test_dangling_legacy_training_link_is_still_user_data(tmp_path, layout):
    install, _ = layout
    target = install / '.vision-learning'
    try:
        target.symlink_to(tmp_path / 'missing-original-data', target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation is unavailable on this host')
    assert os.path.lexists(target)
    with pytest.raises(ValueError):
        validate_install(install)
    assert target.is_symlink()


def test_installation_inside_training_root_is_refused_even_without_extra_paths(layout):
    _, training = layout
    install = training / 'program'
    install.mkdir()
    (install / 'Xiayuluo.exe').write_bytes(b'MZdo-not-replace')
    previous = hashes(training)
    with pytest.raises(ValueError):
        validate_install(install)
    assert hashes(training) == previous


def test_custom_training_root_inside_install_is_refused_by_app_and_helper(tmp_path, layout):
    install, _ = layout
    custom = install / 'not-a-known-directory-name'
    custom.mkdir()
    (custom / 'important.bin').write_bytes(b'never swap this away')
    stage = stage_for(install, tmp_path)
    before = hashes(install)
    with pytest.raises(ValueError):
        validate_install(install, protected_paths=[custom])
    with pytest.raises(ValueError):
        swap_install(install, stage, protected_paths=[custom])
    assert hashes(install) == before


def test_configured_install_child_alias_cannot_disappear_into_binary_backup(tmp_path, layout):
    install, training = layout
    alias = install / 'custom-link'
    try:
        alias.symlink_to(training, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation is unavailable on this host')
    with pytest.raises(ValueError):
        validate_install(install, protected_paths=[alias])
    assert alias.is_symlink()


def test_training_path_inside_staging_is_refused_before_replacement(tmp_path, layout):
    install, _ = layout
    stage = stage_for(install, tmp_path)
    custom = stage / 'custom-datasets'
    custom.mkdir()
    (custom / 'frame.png').write_bytes(b'preserve staged user data')
    before = hashes(stage)
    with pytest.raises(ValueError):
        swap_install(install, stage, protected_paths=[custom])
    assert hashes(stage) == before and (install / 'Xiayuluo.exe').read_bytes() == b'MZold'


@pytest.mark.parametrize('name', ['training', '.vision-learning', TRAINING_MARKER])
def test_stage_user_data_refuses_swap_even_without_declared_paths(tmp_path, layout, name):
    install, _ = layout
    stage = stage_for(install, tmp_path)
    (stage / name).write_bytes(b'not application code')
    with pytest.raises(ValueError):
        swap_install(install, stage)
    assert (stage / name).read_bytes() == b'not application code'
    assert (install / 'Xiayuluo.exe').read_bytes() == b'MZold'


@pytest.mark.parametrize('entry', ['training/datasets/frame.png', '.vision-learning/model/weights.npz',
                                  TRAINING_MARKER, 'TRAINING/datasets/frame.png'])
def test_release_archive_must_not_install_top_level_training_data(tmp_path, entry):
    archive, stage = tmp_path / 'update.zip', tmp_path / 'new-stage'
    bundle(archive, 'Xiayuluo/' + entry)
    with pytest.raises(ValueError):
        extract_windows(archive, stage)
    assert not stage.exists()


def test_generic_dependency_model_or_training_directories_are_not_user_training_roots(tmp_path):
    archive, stage = tmp_path / 'update.zip', tmp_path / 'new-stage'
    bundle(archive, 'Xiayuluo/_internal/library/models/training/utils.py')
    extract_windows(archive, stage)
    assert (stage / '_internal/library/models/training/utils.py').read_bytes() == b'bad'


def test_update_preparation_passes_custom_training_paths_to_its_guard(tmp_path, release):
    updater, package, install = make_package(tmp_path, release)
    custom = install / 'personal-demonstrations'
    custom.mkdir()
    (custom / 'sample.png').write_bytes(b'private example')
    protected = Updates(updater.cache, config=updater.config, protected_paths=[custom])
    with pytest.raises(ValueError):
        protected.prepare_local(package, install)
    assert (custom / 'sample.png').read_bytes() == b'private example'
    assert not list(tmp_path.glob('.Xiayuluo.update-*'))


@pytest.mark.parametrize('value', ['not-a-list', {}, [None], [1], [''], ['x' * 32768], ['path'] * 17])
def test_helper_rejects_malformed_protected_paths_before_waiting_or_swapping(tmp_path, monkeypatch, value):
    job = tmp_path / 'job.json'
    job.write_text(json.dumps({'pid': 123, 'install': 'unused', 'stage': 'unused', 'protected_paths': value}))
    monkeypatch.setattr(update_worker.sys, 'argv', ['helper', str(job)])
    monkeypatch.setattr(update_worker, 'wait_for_process', lambda *args: pytest.fail('must reject before waiting'))
    monkeypatch.setattr(update_worker, 'swap_install', lambda *args, **kwargs: pytest.fail('must not swap'))
    monkeypatch.setattr(update_worker.ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(MessageBoxW=lambda *args: None)), raising=False)
    assert update_worker.main() == 1
    result = json.loads(job.with_suffix('.result.json').read_text())
    assert result['ok'] is False and not job.exists()


@pytest.mark.parametrize('explicit_paths', [False, True])
def test_helper_passes_declared_protected_paths_and_supports_old_job_files(tmp_path, monkeypatch, explicit_paths):
    job = tmp_path / 'job.json'
    expected = [str(tmp_path / 'training')] if explicit_paths else []
    data = {'pid': 123, 'install': 'program', 'stage': 'stage'}
    if explicit_paths:
        data['protected_paths'] = expected
    job.write_text(json.dumps(data))
    received = []
    monkeypatch.setattr(update_worker.sys, 'argv', ['helper', str(job)])
    monkeypatch.setattr(update_worker, 'wait_for_process', lambda pid: received.append(pid))
    def swap(install, stage, *, protected_paths):
        received.append((install, stage, protected_paths))
        return tmp_path / 'old-program'
    monkeypatch.setattr(update_worker, 'swap_install', swap)
    assert update_worker.main() == 0
    assert received == [123, ('program', 'stage', expected)]
    assert json.loads(job.with_suffix('.result.json').read_text())['ok'] is True
    assert not job.exists()


def test_installer_checks_persistent_paths_without_any_data_delete_directives():
    script = (Path(__file__).parents[1] / 'tools/windows-installer.iss').read_text(encoding='utf-8')
    assert 'function PrepareToInstall(var NeedsRestart: Boolean): String;' in script
    assert 'TrainingPathsOverlap(InstallPath, DataPath)' in script
    assert "ExpandConstant('{localappdata}\\Xiayuluo')" in script
    assert "RegQueryStringValue(HKCU, 'Software\\Xiayuluo', 'TrainingDataDir', DataPath)" in script
    assert "GetEnv('ASSISTANT_TRAINING_DIR')" in script
    assert 'GetFileAttributesW@kernel32.dll stdcall' in script and '$400' in script
    for name in ('training', '.vision-learning', TRAINING_MARKER):
        assert "AddBackslash(InstallPath) + '" + name + "'" in script
    assert '[UninstallDelete]' not in script and '[InstallDelete]' not in script
