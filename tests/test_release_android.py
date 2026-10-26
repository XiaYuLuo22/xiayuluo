"""Release identity gates use public tool output, never actual signing keys."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools.build_android import ANDROID_CERTIFICATE_SHA256, validate_signed_apk


SIGNATURE = f"""Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Number of signers: 1
V2 Signer: certificate SHA-256 digest: {ANDROID_CERTIFICATE_SHA256}
"""
BADGING = """package: name='com.xiayuluo.assistant' versionCode='13' versionName='0.1.12' compileSdkVersion='37'
sdkVersion:'24'
targetSdkVersion:'36'
application-label:'夏鱼洛'
native-code: 'arm64-v8a'
"""


def tools(monkeypatch, *, signature=SIGNATURE, badging=BADGING, fail=None):
    calls = []

    def run(command, **options):
        calls.append((command, options))
        name = Path(command[0]).name
        if name == fail:
            raise subprocess.CalledProcessError(1, command, output="public verification failure")
        assert name in {"apksigner", "aapt"}
        return SimpleNamespace(stdout=signature if name == "apksigner" else badging, returncode=0)

    monkeypatch.setattr("tools.build_android.subprocess.run", run)
    return calls


def verify(**kwargs):
    return validate_signed_apk(Path("release.apk"), Path("sdk/build-tools/37.0.0"),
                               expected_version="0.1.12", expected_code=13, **kwargs)


@pytest.mark.parametrize("label", ["V2 Signer:", "Signer #1"])
def test_signed_apk_accepts_existing_identity_in_sdk36_and_sdk37_output(monkeypatch, label):
    calls = tools(monkeypatch, signature=SIGNATURE.replace("V2 Signer:", label))
    result = verify()
    assert result == {"package": "com.xiayuluo.assistant", "version": "0.1.12", "version_code": 13,
                      "minimum_sdk": 24, "architectures": ["arm64-v8a"],
                      "certificate_sha256": ANDROID_CERTIFICATE_SHA256}
    assert calls[0][0][1:] == ["verify", "--verbose", "--print-certs", "release.apk"]
    assert calls[1][0][1:] == ["dump", "badging", "release.apk"]
    assert all(options["check"] and options["capture_output"] for _, options in calls)


def test_public_verification_needs_no_keystore_password(monkeypatch):
    calls = tools(monkeypatch)
    original = {"JAVA_HOME": "jdk", "XIAYULUO_ANDROID_STORE_PASSWORD": "fixture-never-forward",
                "XIAYULUO_ANDROID_KEYSTORE": "fixture-never-open.p12"}
    verify(environment=original)
    assert original["XIAYULUO_ANDROID_STORE_PASSWORD"] == "fixture-never-forward"
    assert all(options["env"] == {"JAVA_HOME": "jdk"} for _, options in calls)
    assert not any("fixture-never" in str(command) for command, _ in calls)


def test_public_apk_tools_never_inherit_release_or_model_credentials(monkeypatch):
    calls = tools(monkeypatch)
    private_names = ('XIAYULUO_RELEASE_PRIVATE_KEY', 'XIAYULUO_ANDROID_KEYSTORE_BASE64',
                     'GH_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY', 'ASSISTANT_MODEL_KEY')
    for name in private_names: monkeypatch.setenv(name, 'private-fixture-never-forward')
    verify()
    assert all(not any(name in options['env'] for name in private_names) for _, options in calls)


@pytest.mark.parametrize("signature", [
    SIGNATURE.replace(ANDROID_CERTIFICATE_SHA256, "0" * 64),
    SIGNATURE.replace(ANDROID_CERTIFICATE_SHA256, "not-a-digest"),
    SIGNATURE.replace("Number of signers: 1", "Number of signers: 2"),
    SIGNATURE.replace("Number of signers: 1\n", ""),
    SIGNATURE + "Number of signers: 1\n",
    SIGNATURE + f"Signer #2 certificate SHA-256 digest: {'0' * 64}\n",
    SIGNATURE.replace("certificate SHA-256 digest", "public key SHA-256 digest"),
    SIGNATURE.replace("v2 scheme (APK Signature Scheme v2): true", "v2 scheme (APK Signature Scheme v2): false"),
    "",
])
def test_wrong_missing_or_multiple_signing_identity_fails_before_metadata(monkeypatch, signature):
    calls = tools(monkeypatch, signature=signature)
    with pytest.raises(ValueError, match="signing identity"):
        verify()
    assert len(calls) == 1


def test_repeated_matching_certificate_across_signature_schemes_is_one_identity(monkeypatch):
    tools(monkeypatch, signature=SIGNATURE + f"V3 Signer: certificate SHA-256 digest: {ANDROID_CERTIFICATE_SHA256.upper()}\n")
    assert verify()["certificate_sha256"] == ANDROID_CERTIFICATE_SHA256


@pytest.mark.parametrize("badging", [
    BADGING.replace("com.xiayuluo.assistant", "com.example.other"),
    BADGING.replace("versionCode='13'", "versionCode='12'"),
    BADGING.replace("versionName='0.1.12'", "versionName='0.1.11'"),
    BADGING.replace("versionCode='13'", "versionCode='13' versionCode='13'"),
    BADGING + "package: name='com.xiayuluo.assistant' versionCode='13' versionName='0.1.12'\n",
    BADGING.replace("sdkVersion:'24'", "sdkVersion:'23'"),
    BADGING.replace("sdkVersion:'24'", "sdkVersion:'25'"),
    BADGING.replace("sdkVersion:'24'\n", ""),
    BADGING + "sdkVersion:'24'\n",
    BADGING.replace("native-code: 'arm64-v8a'", "native-code: 'x86_64'"),
    BADGING.replace("native-code: 'arm64-v8a'", "native-code: 'arm64-v8a' 'x86_64'"),
    BADGING.replace("native-code: 'arm64-v8a'\n", ""),
    BADGING + "native-code: 'arm64-v8a'\n",
    "",
])
def test_compiled_manifest_not_project_declarations_decides_release(monkeypatch, badging):
    tools(monkeypatch, badging=badging)
    with pytest.raises(ValueError, match="APK"):
        verify()


@pytest.mark.parametrize("failed_tool", ["apksigner", "aapt"])
def test_failed_public_verification_tool_cannot_be_treated_as_success(monkeypatch, failed_tool):
    tools(monkeypatch, fail=failed_tool)
    with pytest.raises(subprocess.CalledProcessError):
        verify()


@pytest.mark.parametrize("expected_version,expected_code", [("invalid", 13), ("0.1.12", True),
    ("0.1.12", 0), ("0.1.12", "13")])
def test_invalid_expected_release_fails_before_any_tool(monkeypatch, expected_version, expected_code):
    calls = tools(monkeypatch)
    with pytest.raises(ValueError, match="Expected Android"):
        validate_signed_apk("release.apk", "tools", expected_version=expected_version, expected_code=expected_code)
    assert calls == []
