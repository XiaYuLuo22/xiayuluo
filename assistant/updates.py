"""Pinned Ed25519 release metadata, bounded HTTPS downloads and safe Windows staging."""
import hashlib
import json
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

MAX_MANIFEST = 32768
MAX_DOWNLOAD = 512 * 1024 * 1024
CONFIG = Path(__file__).with_name("update-config.json")


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
    except (KeyError, TypeError, ValueError, InvalidSignature, json.JSONDecodeError) as error:
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


def fetch_bytes(url, limit, *, destination=None, expected_hash=None, expected_size=None, client=None):
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30, read=60), follow_redirects=False)
    response = None
    output = None
    digest, result, count = hashlib.sha256(), bytearray(), 0
    try:
        response = response_stream(client, url)
        if int(response.headers.get("content-length", 0)) > limit:
            raise ValueError("更新文件超过大小限制。")
        if destination:
            output = Path(destination).open("xb")
        for chunk in response.iter_bytes(chunk_size=65536):
            count += len(chunk)
            if count > limit:
                raise ValueError("更新文件超过大小限制。")
            digest.update(chunk)
            if output:
                output.write(chunk)
            else:
                result.extend(chunk)
        if expected_size is not None and count != expected_size:
            raise ValueError("更新下载不完整，请重新下载。")
        if expected_hash and digest.hexdigest() != expected_hash:
            raise ValueError("更新文件校验失败，已拒绝安装。")
        return bytes(result)
    finally:
        if output:
            output.close()
        if response:
            response.close()
        if owned:
            client.close()


def extract_windows(archive, stage):
    """Stage exclusively into a fresh sibling directory. Never overlay user files."""
    stage = Path(stage)
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
    def __init__(self, cache, *, config=None):
        self.config = config or json.loads(CONFIG.read_text(encoding="utf-8"))
        self.cache = Path(cache)
        self.lock = Lock()

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
        stage = None
        try:
            data = self.check()  # Do not accept client-supplied URLs or a stale cached manifest.
            if not data["available"]:
                raise ValueError("没有更高版本，不允许降级或重装。")
            install = Path(install).resolve()
            if install.parent == install or not (install / "Xiayuluo.exe").is_file():
                raise ValueError("无法确定桌面 App 安装目录。")
            # Frozen apps keep user data outside installation. Refuse legacy/portable data.
            if any((install / name).exists() for name in (".env", ".assistant", ".venv", ".git")):
                raise ValueError("安装目录有用户数据，请先迁移至配置目录后再更新。")
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            identifier = uuid.uuid4().hex
            archive = self.cache / f"{identifier}.zip"
            asset = data["assets"]["windows-x64"]
            fetch_bytes(asset["url"], asset["size"], destination=archive,
                        expected_hash=asset["sha256"], expected_size=asset["size"])
            stage = install.parent / f".{install.name}.update-{identifier}"
            extract_windows(archive, stage)
            return stage
        finally:
            if archive and archive.is_file():
                archive.unlink()
            self.lock.release()
