"""Fail release preflight on missing signing secrets without printing their values."""
import os


REQUIRED = (
    'XIAYULUO_RELEASE_PRIVATE_KEY',
    'XIAYULUO_ANDROID_KEYSTORE_BASE64',
    'XIAYULUO_ANDROID_STORE_PASSWORD',
)


def main(environment=None):
    environment = os.environ if environment is None else environment
    missing = [name for name in REQUIRED if not environment.get(name, '').strip()]
    for name in missing:
        print(f'::error::Required signing secret is not configured: {name}')
    if missing:
        return 1
    print('Release signing secrets are configured; secret values are not displayed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
