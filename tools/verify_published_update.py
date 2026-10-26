"""Check a newly published update anonymously, using exactly the App trust root."""
import argparse
import json
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assistant.updates import CONFIG, MAX_MANIFEST, extract_windows, fetch_bytes, verify_manifest
from tools.build_android import validate_bundle, validate_signed_apk
from tools.release import verify_versions


def verify_published(version, android_build_tools, *, config=None):
    code = verify_versions(version)
    config = config or json.loads(CONFIG.read_text(encoding='utf-8'))
    # No Authorization header or token is passed, including for redirected assets.
    release = verify_manifest(fetch_bytes(config['manifest_url'], MAX_MANIFEST), config)
    if release['version'] != version or release['android_code'] != code:
        raise ValueError('The public latest manifest does not match this release.')
    with TemporaryDirectory(prefix='xiayuluo-public-update-check-') as directory:
        root = Path(directory)
        for platform, name in (('windows-x64', 'windows.zip'), ('android-arm64', 'android.apk')):
            asset = release['assets'][platform]
            fetch_bytes(asset['url'], asset['size'], destination=root / name,
                        expected_size=asset['size'], expected_hash=asset['sha256'])
        stage = extract_windows(root / 'windows.zip', root / 'stage')
        if json.loads((stage / 'build-info.json').read_text(encoding='utf-8')) != {
                'version': version, 'platform': 'windows-x64'}:
            raise ValueError('The publicly downloaded Windows package has the wrong version.')
        validate_bundle(root / 'android.apk')
        validate_signed_apk(root / 'android.apk', android_build_tools, expected_version=version, expected_code=code)
    return {'version': version, 'anonymous_access': True, 'assets_verified': ['windows-x64', 'android-arm64']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--android-build-tools', required=True, type=Path)
    args = parser.parse_args(argv)
    # GitHub's latest-release redirect may take a few seconds to propagate.
    # Retry the whole verification; never relax a hash, signature or version.
    for attempt in range(5):
        try:
            result = verify_published(args.version, args.android_build_tools)
            print(json.dumps(result))
            return 0
        except (httpx.HTTPError, OSError, ValueError):
            if attempt == 4:
                raise SystemExit('Anonymous public update verification failed; do not advertise this release as ready.') from None
            time.sleep(3)


if __name__ == '__main__':
    main()
