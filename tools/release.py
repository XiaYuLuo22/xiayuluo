"""Prepare signed releases locally/CI; never upload keys or publish as a side effect."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assistant.updates import extract_windows, verify_manifest, version
from tools.build_android import validate_bundle


def keygen(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Ed25519PrivateKey.generate()
    target = directory / "release-private.pem"
    with target.open("xb") as output:
        output.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    target.chmod(0o600)
    print("Public key:", key.public_key().public_bytes_raw().hex())
    print("Keep and back up the private key securely; never package or commit it.")


def verify_versions(expected):
    version(expected)
    from assistant import __version__
    app = json.loads((ROOT / "mobile/app.json").read_text(encoding="utf-8"))["expo"]
    package = json.loads((ROOT / "mobile/package.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if {__version__, app["version"], package["version"], project["project"]["version"]} != {expected}:
        raise ValueError("Desktop/mobile/project versions must match the release tag.")
    left = json.loads((ROOT / "assistant/update-config.json").read_text(encoding="utf-8"))
    right = json.loads((ROOT / "mobile/src/update-config.json").read_text(encoding="utf-8"))
    if left != right:
        raise ValueError("Desktop/mobile release trust configuration differs.")
    return app["android"]["versionCode"]


def manifest(args):
    code = verify_versions(args.version)
    config = json.loads((ROOT / "assistant/update-config.json").read_text(encoding="utf-8"))
    # Validate binary structure before signing any release metadata.
    with tempfile.TemporaryDirectory(prefix="xiayuluo-release-check-") as work:
        extract_windows(args.windows, Path(work) / "stage")
    validate_bundle(args.android)
    pem = args.key_file.read_bytes() if args.key_file else os.environ["XIAYULUO_RELEASE_PRIVATE_KEY"].encode()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey) or key.public_key().public_bytes_raw().hex() != config["public_key"]:
        raise ValueError("Release key does not match the public key pinned in the apps.")
    assets = {}
    for platform, path in (("windows-x64", args.windows), ("android-arm64", args.android)):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        assets[platform] = {"url": f"https://github.com/{config['repository']}/releases/download/v{args.version}/{path.name}",
                            "size": path.stat().st_size, "sha256": digest}
    data = {"schema": 1, "application": "com.xiayuluo.assistant", "version": args.version,
            "android_code": code, "notes": args.notes_file.read_text(encoding="utf-8"), "assets": assets}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    envelope = json.dumps({"payload": payload, "signature": key.sign(payload.encode()).hex()}, ensure_ascii=False).encode()
    verify_manifest(envelope, config)
    with args.output.open("xb") as output:
        output.write(envelope)
    print(f"Signed update manifest: {args.output}")


def restore_android_key(directory):
    """Restore the existing Android key from CI secrets; never silently generate another."""
    key = base64.b64decode(os.environ["XIAYULUO_ANDROID_KEYSTORE_BASE64"], validate=True)
    password = os.environ["XIAYULUO_ANDROID_STORE_PASSWORD"].strip()
    if not key or len(password) < 20:
        raise ValueError("Existing Android signing secrets are required.")
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, content in (("xiayuluo-private.p12", key), ("password", password.encode())):
        path = directory / name
        with path.open("xb") as output:
            output.write(content)
        path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("keygen")
    gen.add_argument("--directory", type=Path, default=ROOT / ".release-signing")
    check = commands.add_parser("check-version")
    check.add_argument("version")
    signing = commands.add_parser("manifest")
    signing.add_argument("--version", required=True)
    signing.add_argument("--windows", type=Path, required=True)
    signing.add_argument("--android", type=Path, required=True)
    signing.add_argument("--notes-file", type=Path, required=True)
    signing.add_argument("--key-file", type=Path)
    signing.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore-android-key")
    restore.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "keygen":
        keygen(args.directory)
    elif args.command == "check-version":
        verify_versions(args.version)
    elif args.command == "restore-android-key":
        restore_android_key(args.directory)
    else:
        manifest(args)


if __name__ == "__main__":
    main()
