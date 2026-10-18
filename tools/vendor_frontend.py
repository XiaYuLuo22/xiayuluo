"""Verify or refresh the small, pinned browser bundles and their licenses.

Only listed regular files are retained. Archives are read in memory, not extracted
to disk. No package scripts run. TLS, archive SRI and per-file SHA-256 must pass.
"""
import argparse
import base64
import hashlib
import hmac
from io import BytesIO
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools/frontend-vendor.lock.json"
DESTINATION = ROOT / "assistant/static/vendor"
MAX_ARCHIVE_SIZE = 5 * 1024 * 1024


def digest_matches(data, expected):
    return hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected)


def target_path(item):
    name = item["target"]
    if Path(name).name != name:
        raise ValueError("Vendor targets must be simple file names")
    return DESTINATION / name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify local files, without network access")
    parser.add_argument("--archives-dir", type=Path, help="Use already downloaded official tarballs instead of downloading")
    args = parser.parse_args()
    packages = json.loads(LOCK.read_text(encoding="utf-8"))["packages"]
    staged = []
    for package in packages:
        if args.check:
            for item in package["files"]:
                path = target_path(item)
                if not path.is_file() or not digest_matches(path.read_bytes(), item["sha256"]):
                    raise ValueError(f"Missing or altered dependency: {path.name}")
        else:
            if args.archives_dir:
                archive_path = args.archives_dir / f"{package['name']}-{package['version']}.tgz"
                if archive_path.stat().st_size > MAX_ARCHIVE_SIZE:
                    raise ValueError("Vendor archive exceeds size limit")
                data = archive_path.read_bytes()
            else:
                if not package["url"].startswith("https://registry.npmjs.org/"):
                    raise ValueError("Only official npm HTTPS archives are supported")
                with urlopen(package["url"], timeout=30) as response:
                    if not response.url.startswith("https://registry.npmjs.org/"):
                        raise ValueError("Unexpected archive redirect")
                    data = response.read(MAX_ARCHIVE_SIZE + 1)
                if len(data) > MAX_ARCHIVE_SIZE:
                    raise ValueError("Vendor archive exceeds size limit")
            actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
            if not hmac.compare_digest(actual, package["integrity"]):
                raise ValueError(f"Archive integrity failed: {package['name']}")
            with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
                for item in package["files"]:
                    member = archive.getmember(item["source"])
                    if not member.isfile() or member.size > MAX_ARCHIVE_SIZE:
                        raise ValueError("Vendor entry must be a bounded regular file")
                    content = archive.extractfile(member).read()
                    if not digest_matches(content, item["sha256"]):
                        raise ValueError(f"File integrity failed: {item['target']}")
                    path = target_path(item)
                    if path.is_symlink():
                        raise ValueError(f"Refusing to overwrite symlink: {path.name}")
                    staged.append((path, content))
        print(f"Verified {package['name']} {package['version']} ({package['license']})")
    if not args.check:
        # Validate every archive and file before changing any retained files.
        DESTINATION.mkdir(parents=True, exist_ok=True)
        for path, content in staged:
            path.write_bytes(content)
        print("Installed browser bundles and licenses; no archives or build caches retained.")


if __name__ == "__main__":
    main()
