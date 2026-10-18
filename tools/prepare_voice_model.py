"""Fetch the official Apache-2.0 Chinese Vosk model using verified TLS.

No model is fetched on app launch. This explicit build/setup step never replaces
an existing model directory. Archive SHA-256 is pinned in tools/voice-sources.json;
upstream size/MD5 and catalog license were also checked (not a signed checksum).
--sha256 permits explicitly pinned reuse.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
from tempfile import TemporaryDirectory
import zipfile

import httpx

URL = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
MODEL = "vosk-model-small-cn-0.22"
ROOT = Path(__file__).resolve().parents[1]
MAX_ZIP = 64 * 1024 * 1024
MAX_FILES = 200 * 1024 * 1024


def unpack(archive, destination, expected=None):
    archive, destination = Path(archive), Path(destination)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected and digest != expected:
        raise ValueError("Offline model checksum mismatch")
    if destination.exists():
        raise ValueError("Model destination exists; preserve it or choose another output path")
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 1000 or sum(item.file_size for item in entries) > MAX_FILES:
            raise ValueError("Offline model archive exceeds limits")
        names = set()
        targets = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            normalized = str(path).casefold()
            if (path.is_absolute() or not path.parts or path.parts[0] != MODEL
                    or any(part in {".", ".."} or ":" in part or "\\" in part for part in path.parts)
                    or any(part.rstrip(". ") != part or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part) for part in path.parts)
                    or stat.S_ISLNK(item.external_attr >> 16) or item.filename in names or normalized in targets):
                raise ValueError("Unsafe offline model archive")
            names.add(item.filename)
            targets.add(normalized)
        if MODEL + "/am/final.mdl" not in names:
            raise ValueError("Offline model is missing acoustic data")
        destination.mkdir(parents=True)
        try:
            for item in entries:
                parts = PurePosixPath(item.filename).parts[1:]
                if not parts:
                    continue
                target = destination.joinpath(*parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
            (destination / "xiayuluo-model.json").write_text(json.dumps({"model": MODEL,
                "source": URL, "sha256": digest, "license": "Apache-2.0"}, indent=2), encoding="utf-8")
        except Exception:
            shutil.rmtree(destination)  # Only our newly created model directory.
            raise
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "assistant/voice-model")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args()
    expected = args.sha256 or json.loads((ROOT / 'tools/voice-sources.json').read_text(encoding='utf-8'))['chinese_model']['sha256']
    if not expected or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Official model SHA-256 must be pinned before download')
    if args.output.exists():
        if (args.output / "am/final.mdl").is_file() and (args.output / "xiayuluo-model.json").is_file():
            print("Existing model preserved:", args.output)
            return
        raise ValueError("An incomplete/external model exists; preserve it and choose another output")
    with TemporaryDirectory(prefix="xiayuluo-voice-model-") as directory:
        archive = args.archive or Path(directory) / "model.zip"
        if not args.archive:
            with httpx.stream("GET", URL, timeout=60, follow_redirects=False) as response:
                response.raise_for_status()
                size = 0
                with archive.open("xb") as output:
                    for chunk in response.iter_bytes(65536):
                        size += len(chunk)
                        if size > MAX_ZIP:
                            raise ValueError("Offline model download exceeds limit")
                        output.write(chunk)
        if archive.stat().st_size > MAX_ZIP:
            raise ValueError("Offline model archive exceeds limit")
        digest = unpack(archive, args.output, expected)
        print("Official offline model prepared:", args.output)
        print("SHA256:", digest)


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as error:
        print(f"官方离线模型下载失败（HTTP {error.response.status_code}）。请检查 alphacephei.com 的网络规则；不要关闭 TLS 校验。", file=sys.stderr)
        raise SystemExit(1)
    except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"离线模型准备未完成：{error}", file=sys.stderr)
        raise SystemExit(1)
