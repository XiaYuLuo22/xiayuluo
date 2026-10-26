import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from assistant import __version__
from assistant.updates import verify_manifest
from tools import release as publisher
from tools import verify_published_update as remote


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()  # Only a disposable test identity.
    config = {'repository': 'XiaYuLuo22/xiayuluo',
              'manifest_url': 'https://github.com/XiaYuLuo22/xiayuluo/releases/latest/download/update.json',
              'public_key': key.public_key().public_bytes_raw().hex()}
    source = tmp_path / 'source'
    (source / 'assistant').mkdir(parents=True)
    (source / 'assistant/update-config.json').write_text(json.dumps(config))
    monkeypatch.setattr(publisher, 'ROOT', source)
    monkeypatch.setattr(publisher, 'verify_versions', lambda value: 16)
    windows, android = tmp_path / 'Xiayuluo-Windows.zip', tmp_path / 'Xiayuluo-Android-arm64.apk'
    with zipfile.ZipFile(windows, 'w') as bundle:
        bundle.writestr('Xiayuluo/Xiayuluo.exe', b'MZtest')
        bundle.writestr('Xiayuluo/XiayuluoUpdater.exe', b'MZtest-helper')
        bundle.writestr('Xiayuluo/_internal/runtime', b'test runtime')
        bundle.writestr('Xiayuluo/build-info.json', json.dumps({'version': __version__, 'platform': 'windows-x64'}))
    with zipfile.ZipFile(android, 'w') as bundle:
        bundle.writestr('assets/index.android.bundle', b'test bundled JS')
        bundle.writestr('assets/licenses/THIRD_PARTY_NOTICES.md', b'test notices')
        bundle.writestr('assets/voice-model/am/final.mdl', b'test model')
    pem = tmp_path / 'fixture-private.pem'
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    notes = tmp_path / 'notes.md'; notes.write_text('Current release\n以下为 old notes')
    args = SimpleNamespace(version=__version__, windows=windows, android=android,
                           android_build_tools=tmp_path / 'public-sdk-tools', notes_file=notes,
                           key_file=pem, output=tmp_path / 'update.json')
    return args, config


def test_manifest_independently_rechecks_apk_identity_before_signing(artifacts, monkeypatch):
    args, config = artifacts
    calls = []
    def verify(path, tools, **kwargs):
        calls.append((path, tools, kwargs))
    monkeypatch.setattr(publisher, 'validate_signed_apk', verify)
    publisher.manifest(args)
    assert calls == [(args.android, args.android_build_tools, {'expected_version': __version__, 'expected_code': 16})]
    manifest = verify_manifest(args.output.read_bytes(), config)
    assert manifest['notes'] == 'Current release'
    assert manifest['assets']['android-arm64']['sha256'] == hashlib.sha256(args.android.read_bytes()).hexdigest()


@pytest.mark.parametrize('failure', ['certificate', 'package', 'version', 'code'])
def test_wrong_apk_cannot_reach_release_key_or_write_manifest(artifacts, monkeypatch, failure):
    args, _ = artifacts
    args.key_file = args.key_file.with_name('nonexistent-must-not-be-read.pem')
    def reject(*a, **k): raise ValueError('APK ' + failure + ' mismatch')
    monkeypatch.setattr(publisher, 'validate_signed_apk', reject)
    with pytest.raises(ValueError, match=failure):
        publisher.manifest(args)
    assert not args.output.exists()


def test_public_post_publish_verification_uses_anonymous_verified_asset_downloads(artifacts, monkeypatch):
    args, config = artifacts
    monkeypatch.setattr(publisher, 'validate_signed_apk', lambda *a, **k: None)
    publisher.manifest(args)
    envelope = args.output.read_bytes()
    manifest = verify_manifest(envelope, config)
    by_url = {manifest['assets'][platform]['url']: path.read_bytes()
              for platform, path in [('windows-x64', args.windows), ('android-arm64', args.android)]}
    requests = []
    def fetch(url, limit, **kwargs):
        requests.append((url, kwargs))
        if url == config['manifest_url']: return envelope
        data = by_url[url]
        assert kwargs['expected_size'] == len(data)
        assert kwargs['expected_hash'] == hashlib.sha256(data).hexdigest()
        Path(kwargs['destination']).write_bytes(data)
    monkeypatch.setattr(remote, 'fetch_bytes', fetch)
    monkeypatch.setattr(remote, 'verify_versions', lambda value: 16)
    verified = []
    monkeypatch.setattr(remote, 'validate_signed_apk', lambda *a, **k: verified.append(k))
    result = remote.verify_published(__version__, args.android_build_tools, config=config)
    assert result['anonymous_access'] and len(requests) == 3 and len(verified) == 1
    assert all('token' not in kwargs and 'headers' not in kwargs for _, kwargs in requests)


def test_release_workflow_requires_original_identity_and_reverts_failed_publication():
    workflow = (Path(__file__).parents[1] / '.github/workflows/release.yml').read_text()
    assert '--require-existing-signing' in workflow
    assert '--android-build-tools "$ANDROID_HOME/build-tools/37.0.0"' in workflow
    assert 'if ! python tools/verify_published_update.py' in workflow
    assert 'gh release edit "$GITHUB_REF_NAME" --draft=true' in workflow
