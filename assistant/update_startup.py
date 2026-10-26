"""Hold background writes until the updater accepts this native startup."""
import json
import os
from pathlib import Path
from tempfile import gettempdir

from . import __version__


class StartupBackgroundGate:
    def __init__(self):
        raw = os.environ.get('XIAYULUO_UPDATE_HEALTH')
        self.accepted = raw is None
        self.probe = None
        if raw is None or len(raw) > 8192:
            return
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict) or set(data) != {'nonce', 'version', 'acknowledgement'}
                    or data['version'] != __version__ or not isinstance(data['nonce'], str)
                    or len(data['nonce']) != 64 or any(c not in '0123456789abcdef' for c in data['nonce'])):
                return
            ack = Path(data['acknowledgement'])
            if (not ack.is_absolute() or ack.name != 'health-ready.json'
                    or not ack.parent.name.startswith('xiayuluo-update-')
                    or not ack.parent.resolve().is_relative_to(Path(gettempdir()).resolve())):
                return
            self.probe = (ack.parent / 'health-accepted.json', data['nonce'])
        except (ValueError, TypeError, OSError, RuntimeError):
            pass

    def ready(self):
        if self.accepted:
            return True
        if self.probe is None:
            return False
        path, nonce = self.probe
        try:
            for item in (path, *path.parents):
                if os.path.lexists(item):
                    info = item.lstat()
                    if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                        return False
            with path.open('rb') as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                return False
            value = json.loads(raw)
            self.accepted = (isinstance(value, dict) and value == {
                'nonce': nonce, 'version': __version__, 'accepted': True} and value.get('accepted') is True)
        except (OSError, ValueError, TypeError, RuntimeError):
            return False
        return self.accepted
