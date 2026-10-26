"""A separate Codex adapter for explicit cloud tasks; never forward service keys."""
import os

from .tools import Codex


class CloudTaskCodex(Codex):
    PRIVATE_ENVIRONMENT = {
        'CLOUD_SYNC_DEVICE_TOKEN', 'XIAYULUO_CLOUD_ADMIN_TOKEN',
        'XIAYULUO_RELEASE_PRIVATE_KEY', 'XIAYULUO_ANDROID_KEYSTORE_BASE64',
        'XIAYULUO_ANDROID_STORE_PASSWORD', 'GITHUB_TOKEN', 'GH_TOKEN',
        'GITHUB_BACKUP_TOKEN', 'XIAYULUO_UPDATE_HEALTH',
    }

    def execution_environment(self):
        return {name: value for name, value in os.environ.items() if name not in self.PRIVATE_ENVIRONMENT}
