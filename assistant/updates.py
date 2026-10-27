"""Pinned Ed25519 release metadata, bounded HTTPS downloads and safe Windows staging."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from threading import Lock
from urllib.parse import urlsplit
import uuid
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import httpx

from . import __version__
from .training_storage import TRAINING_MARKER, assert_training_separate

MAX_MANIFEST = 32768
MAX_DOWNLOAD = 512 * 1024 * 1024
CONFIG = Path(__file__).with_name("update-config.json")


def verify_windows_package(raw, config):
    """Separate, offline-only signed format; never supplies a URL to fetch."""
    if len(raw) > MAX_MANIFEST:
        raise ValueError("更新清单过大。")
    try:
        envelope = json.loads(raw)
        payload, signature = envelope["payload"], bytes.fromhex(envelope["signature"])
        if not isinstance(payload, str) or len(signature) != 64:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(config["public_key"])).verify(signature, payload.encode("utf-8"))
        data = json.loads(payload)
        version(data["version"])
        if data["schema"] != 2 or data["application"] != "com.xiayuluo.assistant" or data["platform"] != "windows-x64":
            raise ValueError()
        if not isinstance(data["notes"], str) or len(data["notes"]) > 4000:
            raise ValueError()
        asset = data["asset"]
        if (asset["filename"] != "Xiayuluo-Windows.zip" or type(asset["size"]) is not int
                or not 1 <= asset["size"] <= MAX_DOWNLOAD or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])):
            raise ValueError()
        return data
    except (KeyError, TypeError, ValueError, InvalidSignature) as error:
        raise ValueError("离线更新包签名或内容无效，已拒绝更新。") from error


def validate_install(install, protected_paths=()):
    assert_training_separate(install, protected_paths)
    install = Path(install).resolve()
    if install.parent == install or not (install / "Xiayuluo.exe").is_file():
        raise ValueError("无法确定桌面 App 安装目录。")
    user_data = {".env", ".assistant", ".venv", ".git", "training", ".vision-learning", TRAINING_MARKER}
    if (any(os.path.lexists(install / name) for name in user_data)
            or any(item.name.casefold() in user_data for item in install.iterdir())):
        raise ValueError("安装目录有用户数据，请先迁移至配置目录后再更新。")
    return install


def download_pages(repository):
    if (not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository)
            or repository.rsplit("/", 1)[-1] in {".", ".."}):
        raise ValueError("下载仓库配置无效。")
    root = f"https://github.com/{repository}"
    return {"windows_url": root + "/actions/workflows/windows-app.yml", "android_url": root + "/releases"}


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})", value):
        raise ValueError("更新版本号无效。")
    return tuple(map(int, value.split(".")))


def trusted_url(value, repository=None):
    url = urlsplit(value)
    hosts = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
    if url.scheme != "https" or url.hostname not in hosts or url.username or url.password or url.port not in (None, 443) or url.fragment:
        raise ValueError("更新地址不在受信任的 HTTPS 发布源中。")
    if repository and (url.hostname != "github.com" or not url.path.startswith(f"/{repository}/releases/") or url.query):
        raise ValueError("更新地址不属于固定发布仓库。")
    return value


def verify_manifest(raw, config):
    if len(raw) > MAX_MANIFEST:
        raise ValueError("更新清单过大。")
    try:
        envelope = json.loads(raw)
        payload, signature = envelope["payload"], bytes.fromhex(envelope["signature"])
        if not isinstance(payload, str) or len(signature) != 64:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(config["public_key"])).verify(signature, payload.encode("utf-8"))
        data = json.loads(payload)
        version(data["version"])
        if data["schema"] != 1 or data["application"] != "com.xiayuluo.assistant":
            raise ValueError()
        if not isinstance(data["notes"], str) or len(data["notes"]) > 4000:
            raise ValueError()
        if type(data["android_code"]) is not int or not 1 <= data["android_code"] <= 2100000000:
            raise ValueError()
        for platform, suffix in (("windows-x64", ".zip"), ("android-arm64", ".apk")):
            asset = data["assets"][platform]
            if type(asset["size"]) is not int or not 1 <= asset["size"] <= MAX_DOWNLOAD:
                raise ValueError()
            if not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]):
                raise ValueError()
            trusted_url(asset["url"], config["repository"])
            expected = f"/{config['repository']}/releases/download/v{data['version']}/"
            parsed = urlsplit(asset["url"])
            if not parsed.path.startswith(expected) or not re.fullmatch(r"[A-Za-z0-9._-]+" + re.escape(suffix), parsed.path[len(expected):]):
                raise ValueError()
        return data
    except (KeyError, TypeError, ValueError, InvalidSignature) as error:
        raise ValueError("更新清单签名或内容无效，已拒绝更新。") from error


def response_stream(client, url):
    """Check every redirect before following it; never forward credentials."""
    for _ in range(6):
        trusted_url(url)
        request = client.build_request("GET", url, headers={"Accept-Encoding": "identity"})
        response = client.send(request, stream=True, follow_redirects=False)
        if response.is_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise ValueError("更新重定向无效。")
            url = str(httpx.URL(url).join(location))
            continue
        try:
            response.raise_for_status()
        except Exception:
            response.close()
            raise
        return response
    raise ValueError("更新重定向过多。")


def fetch_bytes(url, limit, *, destination=None, expected_hash=None, expected_size=None, client=None, progress=None):
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30, read=60), follow_redirects=False)
    response = None
    output = None
    complete = False
    digest, result, count = hashlib.sha256(), bytearray(), 0
    try:
        response = response_stream(client, url)
        if int(response.headers.get("content-length", 0)) > limit:
            raise ValueError("更新文件超过大小限制。")
        if destination:
            output = Path(destination).open("xb")
        if progress:
            progress(0, expected_size)
        for chunk in response.iter_bytes(chunk_size=65536):
            count += len(chunk)
            if count > limit:
                raise ValueError("更新文件超过大小限制。")
            digest.update(chunk)
            if output:
                output.write(chunk)
            else:
                result.extend(chunk)
            if progress:
                progress(count, expected_size)
        if expected_size is not None and count != expected_size:
            raise ValueError("更新下载不完整，请重新下载。")
        if expected_hash and digest.hexdigest() != expected_hash:
            raise ValueError("更新文件校验失败，已拒绝安装。")
        complete = True
        return bytes(result)
    finally:
        if output:
            output.close()
            if not complete:
                Path(destination).unlink(missing_ok=True)  # Only our successfully opened exclusive file.
        if response:
            response.close()
        if owned:
            client.close()


def extract_windows(archive, stage, *, protected_paths=()):
    """Stage exclusively into a fresh sibling directory. Never overlay user files."""
    stage = Path(stage)
    assert_training_separate(stage, protected_paths)
    stage.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) > 20000 or sum(item.file_size for item in entries) > 2 * 1024**3:
                raise ValueError("更新包解压大小超限。")
            seen = set()
            for item in entries:
                # ZipInfo.filename normalizes backslashes on Windows; validate the
                # unmodified archive name so platform aliases cannot evade policy.
                original = item.orig_filename
                path = PurePosixPath(original)
                parts = path.parts
                lower = [part.lower() for part in parts]
                if (not parts or parts[0] != "Xiayuluo" or path.is_absolute() or ".." in parts
                        or "\\" in original or "\0" in original or any(":" in part or part.endswith((".", " ")) for part in parts)
                        or stat.S_ISLNK(item.external_attr >> 16)
                        or any(part in {".env", ".assistant", ".venv", ".git", ".release-signing", ".android-signing", "release-private.pem"} for part in lower)
                        or (len(lower) > 1 and lower[1] in {"training", ".vision-learning", TRAINING_MARKER})
                        or any(re.fullmatch(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?", part) for part in lower)):
                    raise ValueError("更新包含不安全路径或用户数据。")
                key = "/".join(lower)
                if key in seen:
                    raise ValueError("更新包有重复路径。")
                seen.add(key)
                if len(parts) == 1:
                    continue
                target = stage.joinpath(*parts[1:])
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
        for name in ("Xiayuluo.exe", "XiayuluoUpdater.exe"):
            with (stage / name).open("rb") as stream:
                if stream.read(2) != b"MZ":
                    raise ValueError("更新包缺少 Windows 程序。")
        if not (stage / "_internal").is_dir():
            raise ValueError("更新包缺少运行依赖。")
    except Exception:
        shutil.rmtree(stage)  # Only this newly created, validated staging directory.
        raise
    return stage


class Updates:
    def __init__(self, cache, *, config=None, protected_paths=()):
        self.config = config or json.loads(CONFIG.read_text(encoding="utf-8"))
        self.cache = Path(cache)
        self.protected_paths = tuple(protected_paths)
        self.lock = Lock()
        self.progress_lock = Lock()
        self._progress = {"phase": "idle", "version": None, "received_bytes": 0, "total_bytes": None,
                          "message": "尚未开始更新。"}

    def progress(self):
        with self.progress_lock:
            return dict(self._progress)

    def _state(self, phase, message, **fields):
        with self.progress_lock:
            self._progress.update(phase=phase, message=message, **fields)

    def _download_progress(self, received, total):
        self._state("downloading", "正在下载已签名的更新包，当前版本尚未修改。",
                    received_bytes=received, total_bytes=total)

    @staticmethod
    def _validate_build(stage, expected):
        info_path = stage / "build-info.json"
        if not info_path.is_file() or info_path.stat().st_size > 4096:
            raise ValueError("更新包缺少有效的程序版本标识。")
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ValueError("更新包程序版本标识无效。") from error
        if info != {"version": expected, "platform": "windows-x64"}:
            raise ValueError("更新包程序版本不符。")

    def downloads(self):
        return {"current": __version__, "mode": "browser-download",
                **download_pages(self.config["repository"]),
                "message": "私有仓库需在系统浏览器登录 GitHub。只有成功构建/已发布的安装包才能下载；打开页面不等于安装完成。"}

    def check(self):
        trusted_url(self.config["manifest_url"], self.config["repository"])
        if not re.fullmatch(r"[0-9a-f]{64}", self.config.get("public_key", "")):
            raise ValueError("尚未配置更新签名公钥。")
        raw = fetch_bytes(self.config["manifest_url"], MAX_MANIFEST)
        data = verify_manifest(raw, self.config)
        return {**data, "current": __version__, "available": version(data["version"]) > version(__version__)}

    def prepare(self, install):
        if not self.lock.acquire(blocking=False):
            raise ValueError("已有更新正在处理。")
        archive = None
        archive_owned = False
        stage = None
        stage_owned = False
        try:
            self._state("checking", "正在检查签名清单。", received_bytes=0, total_bytes=None, version=None)
            data = self.check()  # Do not accept client-supplied URLs or a stale cached manifest.
            if not data["available"]:
                raise ValueError("没有更高版本，不允许降级或重装。")
            install = validate_install(install, self.protected_paths)
            assert_training_separate(self.cache, self.protected_paths)
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            identifier = uuid.uuid4().hex
            archive = self.cache / f"{identifier}.zip"
            asset = data["assets"]["windows-x64"]
            self._state("downloading", "正在下载更新包。", version=data["version"], total_bytes=asset["size"])
            fetch_bytes(asset["url"], asset["size"], destination=archive,
                        expected_hash=asset["sha256"], expected_size=asset["size"], progress=self._download_progress)
            archive_owned = True
            self._state("staging", "下载校验通过，正在检查程序内容；当前版本尚未修改。")
            stage = install.parent / f".{install.name}.update-{identifier}"
            extract_windows(archive, stage, protected_paths=self.protected_paths)
            stage_owned = True
            self._validate_build(stage, data["version"])
            self._state("ready", "更新包已验证，准备重启；尚未确认新版已启动。")
            return stage
        except Exception:
            if stage_owned and stage.is_dir():
                shutil.rmtree(stage)
            self._state("failed", "更新准备失败，原程序和用户数据保留。")
            raise
        finally:
            if archive_owned and archive.is_file():
                archive.unlink()
            self.lock.release()

    def prepare_local(self, package, install):
        """Import a user-selected signed file, with no networking or source checkout edits."""
        if not self.lock.acquire(blocking=False):
            raise ValueError("已有更新正在处理。")
        archive, stage = None, None
        archive_owned, stage_owned = False, False
        try:
            self._state("checking", "正在校验本地签名更新包。", received_bytes=0, total_bytes=None, version=None)
            install = validate_install(install, self.protected_paths)
            assert_training_separate(self.cache, self.protected_paths)
            package = Path(package)
            if not package.is_file() or package.suffix.lower() != ".xyupdate" or package.stat().st_size > MAX_DOWNLOAD + MAX_MANIFEST + 65536:
                raise ValueError("请选择有效的 .xyupdate 签名更新包。")
            with zipfile.ZipFile(package) as bundle:
                entries = bundle.infolist()
                if (len(entries) != 2 or {item.orig_filename for item in entries} != {"update.json", "Xiayuluo-Windows.zip"}
                        or any(stat.S_ISLNK(item.external_attr >> 16) for item in entries)
                        or bundle.getinfo("update.json").file_size > MAX_MANIFEST):
                    raise ValueError("离线更新包结构无效。")
                data = verify_windows_package(bundle.read("update.json"), self.config)
                if version(data["version"]) <= version(__version__):
                    raise ValueError("没有更高版本，不允许降级或重装。")
                asset = data["asset"]
                self._state("verifying", "正在读取并校验本地更新包。", version=data["version"], total_bytes=asset["size"])
                if bundle.getinfo(asset["filename"]).file_size != asset["size"]:
                    raise ValueError("更新文件大小不符。")
                self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
                identifier = uuid.uuid4().hex
                archive = self.cache / f"{identifier}.zip"
                digest, count = hashlib.sha256(), 0
                with bundle.open(asset["filename"]) as source, archive.open("xb") as output:
                    archive_owned = True
                    while chunk := source.read(65536):
                        count += len(chunk)
                        if count > asset["size"]:
                            raise ValueError("更新文件大小超限。")
                        digest.update(chunk)
                        output.write(chunk)
                        self._state("verifying", "正在读取并校验本地更新包。", received_bytes=count)
                if count != asset["size"] or digest.hexdigest() != asset["sha256"]:
                    raise ValueError("更新文件校验失败。")
            stage = install.parent / f".{install.name}.update-{identifier}"
            self._state("staging", "签名与文件校验通过，正在检查程序内容。")
            extract_windows(archive, stage, protected_paths=self.protected_paths)
            stage_owned = True
            self._validate_build(stage, data["version"])
            self._state("ready", "更新包已验证，准备重启；尚未确认新版已启动。")
            return stage
        except Exception:
            if stage_owned and stage.is_dir():
                shutil.rmtree(stage)  # Only the newly created staging directory.
            self._state("failed", "更新准备失败，原程序和用户数据保留。")
            raise
        finally:
            if archive_owned and archive.is_file():
                archive.unlink()
            self.lock.release()
