from pathlib import Path
import zipfile

import pytest

from tools.build_android import include_licenses, signing, validate_bundle


def test_signing_key_is_private_stable_and_password_is_not_in_command(monkeypatch, tmp_path):
    commands = []
    def keytool(command, **options):
        commands.append(command)
        assert options['check'] is True
        Path(command[command.index('-keystore') + 1]).write_bytes(b'fixture-signing-key')
    monkeypatch.setattr('tools.build_android.subprocess.run', keytool)
    first = signing(tmp_path / 'keys', Path('/fixture/bin/keytool'))
    second = signing(tmp_path / 'keys', Path('/fixture/bin/keytool'))
    assert first == second
    assert len(first[1]) >= 20
    assert len(commands) == 1
    assert first[1] not in str(commands)
    assert '-storepass:file' in commands[0]
    if __import__('os').name != 'nt':
        assert first[0].stat().st_mode & 0o077 == 0
        assert (tmp_path / 'keys/password').stat().st_mode & 0o077 == 0


def test_existing_key_is_not_replaced_if_password_is_lost(tmp_path):
    (tmp_path / 'xiayuluo-private.p12').write_bytes(b'keep-this-key')
    with pytest.raises(ValueError, match='restore'):
        signing(tmp_path, Path('/fixture/keytool'))
    assert (tmp_path / 'xiayuluo-private.p12').read_bytes() == b'keep-this-key'
    assert not (tmp_path / 'password').exists()


def test_missing_bundle_and_accidental_private_data_cannot_be_distributed(tmp_path):
    def apk(name, entries):
        file = tmp_path / name
        with zipfile.ZipFile(file, 'w') as archive:
            for entry in entries:
                archive.writestr(entry, b'fixture')
        return file
    with pytest.raises(ValueError, match='embedded'):
        validate_bundle(apk('unbundled.apk', ['AndroidManifest.xml']))
    for index, name in enumerate(('.env', 'keys/private.p12', 'assets/memory.sqlite3', 'devices.sqlite3')):
        with pytest.raises(ValueError, match='private data'):
            validate_bundle(apk(f'private-{index}.apk', ['assets/index.android.bundle', name]))
    with pytest.raises(ValueError, match='license'):
        validate_bundle(apk('unlicensed.apk', ['assets/index.android.bundle', 'AndroidManifest.xml']))
    with pytest.raises(ValueError, match='model missing'):
        validate_bundle(apk('no-voice-model.apk', ['assets/index.android.bundle', 'assets/licenses/THIRD_PARTY_NOTICES.md']))
    validate_bundle(apk('bundled.apk', ['assets/index.android.bundle', 'AndroidManifest.xml', 'assets/licenses/THIRD_PARTY_NOTICES.md', 'assets/voice-model/am/final.mdl']))


def test_installed_licenses_are_preserved_in_generated_assets(monkeypatch, tmp_path):
    (tmp_path / 'THIRD_PARTY_NOTICES.md').write_text('project notices', encoding='utf-8')
    package = tmp_path / 'mobile/node_modules/fixture'
    package.mkdir(parents=True)
    (package / 'LICENSE').write_text('upstream license', encoding='utf-8')
    cache = tmp_path / 'gradle/caches/modules-2/files-2.1/fixture'
    cache.mkdir(parents=True)
    with zipfile.ZipFile(cache / 'library.jar', 'w') as archive:
        archive.writestr('META-INF/LICENSE.txt', 'native license')
    monkeypatch.setenv('GRADLE_USER_HOME', str(tmp_path / 'gradle'))
    include_licenses(tmp_path)
    target = tmp_path / 'mobile/android/app/src/main/assets/licenses'
    assert (target / 'npm/fixture/LICENSE').read_text() == 'upstream license'
    assert any(file.read_text() == 'native license' for file in (target / 'native/library.jar').iterdir())
