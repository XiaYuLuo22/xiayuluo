"""Build a private, bundled, independently signed Android APK without EAS/cloud upload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET


ANDROID_PACKAGE = "com.xiayuluo.assistant"
# Public certificate identity used by all existing private-test installations.
# Never replace this fingerprint to accommodate a different CI signing key.
ANDROID_CERTIFICATE_SHA256 = "bef9a3315531035f91362a72fd3012198304fd83d21d07009d67fd1df90ac96a"


def signing(directory, keytool):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    keystore, password_file = directory / "xiayuluo-private.p12", directory / "password"
    if not password_file.exists():
        if keystore.exists():
            raise ValueError("Signing password is missing; restore it, do not replace the existing signing key.")
        with password_file.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(36))
        password_file.chmod(0o600)
    password = password_file.read_text(encoding="utf-8").strip()
    if len(password) < 20:
        raise ValueError("Signing password file is invalid.")
    if not keystore.exists():
        subprocess.run([str(keytool), "-genkeypair", "-storetype", "PKCS12", "-keystore", str(keystore),
            "-storepass:file", str(password_file), "-keypass:file", str(password_file), "-alias", "xiayuluo",
            "-keyalg", "RSA", "-keysize", "3072", "-validity", "10000",
            "-dname", "CN=Xiayuluo Private Test,O=Xiayuluo,C=CN"], check=True)
        keystore.chmod(0o600)
    return keystore, password


def include_licenses(root):
    """Accompany binary distributions with unmodified installed dependency notices."""
    root = Path(root)
    target = root / "mobile/android/app/src/main/assets/licenses"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "THIRD_PARTY_NOTICES.md", target / "THIRD_PARTY_NOTICES.md")
    source_licenses = root / 'tools/licenses'
    if source_licenses.is_dir():
        shutil.copytree(source_licenses, target / 'voice', dirs_exist_ok=True)
    modules = root / "mobile/node_modules"
    for directory, _, files in os.walk(modules):
        for name in files:
            lower = name.lower()
            if lower in {"license", "notice", "copying", "copyright"} or lower.startswith(("license.", "notice.", "copying.")):
                source = Path(directory) / name
                if source.is_file():
                    destination = target / "npm" / source.relative_to(modules)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
    cache = Path(os.environ.get("GRADLE_USER_HOME", Path.home() / ".gradle")) / "caches/modules-2/files-2.1"
    inventory = []
    for source in cache.rglob("*") if cache.is_dir() else []:
        if source.suffix == ".pom":
            try:
                pom = ET.parse(source).getroot()
                licenses = [{"name": item.findtext("{*}name"), "url": item.findtext("{*}url")}
                            for item in pom.findall("{*}licenses/{*}license")]
                if licenses:
                    inventory.append({"group": pom.findtext("{*}groupId"), "artifact": pom.findtext("{*}artifactId"),
                                      "version": pom.findtext("{*}version"), "licenses": licenses})
            except (ET.ParseError, OSError):
                continue
        if source.suffix not in {".jar", ".aar"}:
            continue
        try:
            with zipfile.ZipFile(source) as archive:
                for entry in archive.infolist():
                    basename = Path(entry.filename).name
                    if entry.is_dir() or entry.file_size > 524288 or not basename.lower().startswith(("license", "notice", "copying", "copyright")):
                        continue
                    identifier = hashlib.sha256((str(source.relative_to(cache)) + entry.filename).encode()).hexdigest()[:12]
                    destination = target / "native" / source.name / (identifier + "-" + basename)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(entry))
        except (OSError, zipfile.BadZipFile):
            continue
    (target / "native-license-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_bundle(apk):
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if "assets/index.android.bundle" not in names:
            raise ValueError("No embedded JS bundle. Do not distribute an APK that requires Metro.")
        if any(name.endswith((".env", ".p12", ".jks", "release-private.pem", "memory.sqlite3", "devices.sqlite3")) for name in names):
            raise ValueError("Unexpected private data in APK; refusing distribution.")
        if "assets/licenses/THIRD_PARTY_NOTICES.md" not in names:
            raise ValueError("Third-party license notices are missing from APK.")
        if "assets/voice-model/am/final.mdl" not in names:
            raise ValueError("Offline Chinese voice model missing; do not distribute a nonfunctional voice build.")


def validate_signed_apk(apk, build_tools, *, expected_version, expected_code, environment=None):
    """Fail closed on a different signing identity or compiled release metadata.

    Verification reads only the APK's public certificate and manifest. It does
    not open a keystore or require its password. SDK 36 and 37 print different
    signer labels, both of which identify the same pinned certificate.
    """
    if not isinstance(expected_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", expected_version):
        raise ValueError("Expected Android version is invalid.")
    if type(expected_code) is not int or not 1 <= expected_code <= 2100000000:
        raise ValueError("Expected Android versionCode is invalid.")
    build_tools = Path(build_tools)
    # These values are needed for Gradle signing, not public APK verification.
    tool_environment = None if environment is None else {
        name: value for name, value in environment.items()
        if name not in {"XIAYULUO_ANDROID_STORE_PASSWORD", "XIAYULUO_ANDROID_KEYSTORE"}
    }
    options = {"env": tool_environment, "check": True, "capture_output": True,
               "text": True, "encoding": "utf-8", "errors": "replace"}
    signature = subprocess.run([str(build_tools / "apksigner"), "verify", "--verbose", "--print-certs", str(apk)],
                               **options).stdout
    counts = re.findall(r"^Number of signers:\s*([0-9]+)\s*$", signature, re.MULTILINE)
    certificates = re.findall(r"^(?:Signer #[0-9]+|V[0-9.]+ Signer):? certificate SHA-256 digest:\s*([0-9a-fA-F]{64})\s*$",
                              signature, re.MULTILINE)
    if (counts != ["1"] or not certificates
            or {value.lower() for value in certificates} != {ANDROID_CERTIFICATE_SHA256}
            or not re.search(r"^Verified using v2 scheme \(APK Signature Scheme v2\): true\s*$", signature, re.MULTILINE)):
        raise ValueError("APK signing identity does not match existing installations; refusing release.")
    badging = subprocess.run([str(build_tools / "aapt"), "dump", "badging", str(apk)], **options).stdout
    packages = re.findall(r"^package: (.+)$", badging, re.MULTILINE)
    if len(packages) != 1:
        raise ValueError("APK package metadata is missing or ambiguous.")
    attributes = re.findall(r"([A-Za-z][A-Za-z0-9]*)='([^']*)'", packages[0])
    package = dict(attributes)
    if (len(attributes) != len(package) or package.get("name") != ANDROID_PACKAGE
            or package.get("versionName") != expected_version or package.get("versionCode") != str(expected_code)):
        raise ValueError("APK package/version does not match the intended release.")
    if re.findall(r"^sdkVersion:'([0-9]+)'\s*$", badging, re.MULTILINE) != ["24"]:
        raise ValueError("APK minimum Android SDK must remain 24.")
    architectures = re.findall(r"^native-code:\s*(.+)$", badging, re.MULTILINE)
    if len(architectures) != 1 or architectures[0].strip() != "'arm64-v8a'":
        raise ValueError("APK must contain only the arm64-v8a architecture.")
    return {"package": ANDROID_PACKAGE, "version": expected_version, "version_code": expected_code,
            "minimum_sdk": 24, "architectures": ["arm64-v8a"], "certificate_sha256": ANDROID_CERTIFICATE_SHA256}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--java", required=True, type=Path)
    parser.add_argument("--java17", type=Path, help="JDK 17 needed by the React Native Gradle plugin")
    parser.add_argument("--gradle", type=Path)
    parser.add_argument("--signing-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--architectures", default="arm64-v8a")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    mobile = root / "mobile"
    if args.output.exists():
        raise ValueError("Output already exists; choose a new output file to preserve it.")
    if not (mobile / "android/gradlew").is_file():
        raise ValueError("Run npm ci and expo prebuild --platform android --no-install first.")
    java = args.java.resolve()
    if args.java17 and not (args.java17 / "bin/javac").is_file():
        raise ValueError("The specified JDK 17 is missing. Install it before starting Gradle.")
    sdk = args.sdk.resolve()
    build_tools = sdk / "build-tools/37.0.0"
    for tool in (java / "bin/javac", build_tools / "apksigner", build_tools / "aapt"):
        if not tool.is_file():
            raise ValueError(f"Missing build tool: {tool}")
    keystore, password = signing(args.signing_dir, java / "bin/keytool")
    include_licenses(root)
    environment = dict(os.environ)
    environment.update(JAVA_HOME=str(java), ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk),
        EXPO_NO_TELEMETRY="1", CI="1", XIAYULUO_ANDROID_KEYSTORE=str(keystore),
        XIAYULUO_ANDROID_STORE_PASSWORD=password)
    gradle = str(args.gradle.resolve()) if args.gradle else str(mobile / "android/gradlew")
    toolchain_args = [f"-Porg.gradle.java.installations.paths={java},{args.java17.resolve()}"] if args.java17 else []
    subprocess.run([gradle, "--no-daemon", "--max-workers=3", "--console=plain", "--init-script",
        str(root / "tools/android-personal.init.gradle"), "app:assembleRelease",
        f"-PreactNativeArchitectures={args.architectures}",
        "-Dorg.gradle.jvmargs=-Xmx4096m -XX:MaxMetaspaceSize=1024m", *toolchain_args],
        cwd=mobile / "android", env=environment, check=True)
    apk = mobile / "android/app/build/outputs/apk/release/app-release.apk"
    app = json.loads((mobile / "app.json").read_text(encoding="utf-8"))["expo"]
    validate_signed_apk(apk, build_tools, expected_version=app["version"],
                        expected_code=app["android"]["versionCode"], environment=environment)
    validate_bundle(apk)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with apk.open("rb") as source, args.output.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    print(f"APK: {args.output}")
    print(f"SHA256: {hashlib.sha256(args.output.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Android build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
