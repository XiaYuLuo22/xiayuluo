"""Version-independent training storage. No model imports, collection or execution.

Normal updates replace application binaries, never these user-owned directories.
Location registration survives installation paths and application versions.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import time


TRAINING_MARKER = '.training-store.json'
AREAS = ('datasets', 'models', 'checkpoints', 'backups')
REGISTRY_KEY = r'Software\Xiayuluo'
REGISTRY_VALUE = 'TrainingDataDir'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('重复的数据位置或格式字段。')
        result[key] = value
    return result


def user_data_base(*, platform=None, environment=None, home=None):
    platform = sys.platform if platform is None else platform
    environment = os.environ if environment is None else environment
    home = Path.home() if home is None else Path(home)
    if platform == 'win32':
        parent = Path(environment.get('LOCALAPPDATA') or home / 'AppData/Local')
        return parent / 'Xiayuluo'
    if platform == 'darwin':
        return home / 'Library/Application Support/Xiayuluo'
    parent = Path(environment.get('XDG_DATA_HOME') or home / '.local/share')
    return parent / 'Xiayuluo'


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or '\0' in str(path):
        raise ValueError('训练数据目录必须是绝对路径，不能依赖安装目录或当前工作目录。')
    return path.absolute()


def _registered_root(*, platform=None, location_file=None):
    platform = sys.platform if platform is None else platform
    if platform == 'win32' and location_file is None:
        if os.name != 'nt':  # Pure path-policy tests may simulate Windows on Linux.
            return None
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
                value, kind = winreg.QueryValueEx(key, REGISTRY_VALUE)
        except FileNotFoundError:
            return None
        if kind != winreg.REG_SZ or not isinstance(value, str):
            raise ValueError('训练数据位置登记无效；不会自动改为空目录。')
        return _absolute(value)
    file = Path(location_file) if location_file is not None else user_data_base() / 'training-location.json'
    _plain_ancestors(file)
    if not file.exists():
        if file.is_symlink():
            raise ValueError('训练目录位置记录是失效链接。')
        return None
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 4096:
        raise ValueError('训练目录位置记录无效；不会自动创建替代目录。')
    try:
        data = json.loads(file.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
        if set(data) != {'schema', 'root'} or type(data['schema']) is not int or data['schema'] != 1:
            raise ValueError()
        return _absolute(data['root'])
    except (KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError('训练目录位置记录损坏，请恢复原记录；不会自动改为空目录。') from error


def resolve_training_root(configured=None, *, platform=None, environment=None, home=None, location_file=None):
    environment = os.environ if environment is None else environment
    value = configured or environment.get('ASSISTANT_TRAINING_DIR')
    if value:
        return _absolute(value)
    if location_file is None and (platform or sys.platform) != 'win32':
        location_file = user_data_base(platform=platform, environment=environment, home=home) / 'training-location.json'
    registered = _registered_root(platform=platform, location_file=location_file)
    if registered is not None:
        return registered
    return _absolute(user_data_base(platform=platform, environment=environment, home=home) / 'training')


def paths_overlap(first, second):
    """Check lexical and resolved aliases, in both containment directions."""
    def aliases(value):
        path = Path(value)
        return {os.path.normcase(os.path.abspath(path)), os.path.normcase(str(path.resolve()))}
    for left in aliases(first):
        for right in aliases(second):
            try:
                if os.path.commonpath((left, right)) in (left, right):
                    return True
            except ValueError:  # Different Windows drives.
                continue
    return False


def assert_training_separate(install, protected_paths=()):
    if not isinstance(protected_paths, (tuple, list)) or len(protected_paths) > 16:
        raise ValueError('训练数据保护路径无效。')
    roots = [user_data_base() / 'training', resolve_training_root()]
    registered = _registered_root()
    if registered is not None:
        roots.append(registered)
    for value in protected_paths:
        if not isinstance(value, (str, os.PathLike)):
            raise ValueError('训练数据保护路径无效。')
        roots.append(_absolute(value))
    if any(paths_overlap(install, root) for root in roots):
        raise ValueError('程序安装目录与训练或用户数据目录重叠，已停止更新以保留原数据。')


def _reparse(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _plain_ancestors(path):
    for item in (path, *path.parents):
        if os.path.lexists(item) and _reparse(item):
            raise ValueError('训练数据和位置记录不能通过链接或目录联接保存。')


@contextmanager
def _file_lock(path, *, timeout=5):
    _plain_ancestors(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    stream = os.fdopen(descriptor, 'r+b')
    acquired = False
    started = time.monotonic()
    try:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('训练写锁不是普通文件。')
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'\0')
            stream.flush()
        while not acquired:
            try:
                stream.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() - started >= timeout:
                    raise ValueError('训练数据正在写入或备份，未能取得锁；请稍后再试。')
                time.sleep(.03)
        yield
    finally:
        try:
            if acquired:
                stream.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


class TrainingStorage:
    def __init__(self, root=None, *, location_file=None, platform=None):
        self.platform = sys.platform if platform is None else platform
        self.location_file = Path(location_file) if location_file is not None else None
        self.root = resolve_training_root(root, platform=self.platform, location_file=self.location_file)
        if self.root == self.root.parent or self.root.resolve() == Path.home().resolve():
            raise ValueError('不能将磁盘根目录或整个用户目录作为训练数据目录。')
        install = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
        if paths_overlap(self.root, install):
            raise ValueError('训练数据必须存放在程序安装或源码目录之外。')

    def _location_conflict(self):
        registered = _registered_root(platform=self.platform, location_file=self.location_file)
        if registered is not None and registered.resolve() != self.root.resolve():
            # Never silently switch an existing user's data to a new empty path.
            raise ValueError('已登记的训练目录与新配置不同；原数据保留，请先明确迁移而不是创建空目录。')
        if registered is not None and not self.root.exists():
            raise ValueError('已登记的训练目录不可用；请接回原磁盘或恢复数据，不会创建空目录代替。')

    def _location_target(self):
        return self.location_file or user_data_base(platform=self.platform) / 'training-location.json'

    def _register(self):
        self._location_conflict()
        if self.platform == 'win32' and self.location_file is None:
            import winreg
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, REGISTRY_VALUE, 0, winreg.REG_SZ, str(self.root))
            return
        target = self._location_target()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(prefix='.training-location-', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 1, 'root': str(self.root)}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if target.is_symlink():
                raise ValueError('不能覆盖训练目录位置链接。')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _validate(self):
        _plain_ancestors(self.root)
        if not self.root.is_dir() or _reparse(self.root):
            raise ValueError('训练数据根必须是本机普通目录，不能是链接。')
        marker = self.root / TRAINING_MARKER
        if not marker.is_file() or _reparse(marker) or marker.stat().st_size > 4096:
            raise ValueError('训练目录没有有效的数据标记；原文件不会删除或覆盖，请先导入原数据。')
        try:
            data = json.loads(marker.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
            if data != {'schema': 1, 'application': 'com.xiayuluo.training'} or type(data.get('schema')) is not int:
                raise ValueError()
        except (ValueError, TypeError) as error:
            raise ValueError('训练存储格式不兼容，已停止写入，原数据保留。') from error
        for area in AREAS:
            path = self.root / area
            if not path.is_dir() or _reparse(path):
                raise ValueError('训练数据子目录缺失或含链接，已停止写入。')

    def ensure(self):
        # Serialize initialization and location registration, even for two different
        # requested roots. A second process must not silently replace the first root.
        target = self._location_target()
        with _file_lock(target.with_name('.training-location.lock')):
            return self._ensure_locked()

    def _ensure_locked(self):
        self._location_conflict()
        _plain_ancestors(self.root)
        marker = self.root / TRAINING_MARKER
        if self.root.exists():
            if not self.root.is_dir() or _reparse(self.root):
                raise ValueError('训练数据目录不是本机普通目录。')
            if not marker.exists() and any(self.root.iterdir()):
                raise ValueError('所选目录已有未管理的数据；不会自动覆盖、删除或迁移。')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not marker.exists():
            for area in AREAS:
                (self.root / area).mkdir(mode=0o700, exist_ok=True)
            try:
                with marker.open('x', encoding='utf-8') as stream:
                    json.dump({'schema': 1, 'application': 'com.xiayuluo.training'}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
        self._validate()
        self._register()
        return self.root

    @contextmanager
    def locked(self, *, timeout=5):
        """Cross-process advisory write lock, shared by publishers and backups."""
        self.ensure()
        with _file_lock(self.root / '.training.lock', timeout=timeout):
            self._validate()
            yield self

    def model_path(self):
        self.ensure()
        return self.root / 'models' / ('run-' + secrets.token_hex(12))

    def has_data(self):
        self._validate()
        return any(any((self.root / area).iterdir()) for area in AREAS if area != 'backups')

    def status(self):
        result = {'root': str(self.root), 'exists': self.root.exists(),
                  'areas': {area: str(self.root / area) for area in AREAS},
                  'stable_location': True, 'has_data': False}
        try:
            self._location_conflict()
            if self.root.exists():
                result['has_data'] = self.has_data()
            result['ready'] = True
            result['message'] = '训练数据与程序分开保存；普通更新不会清理此目录。'
        except (OSError, ValueError):
            result['ready'] = False
            result['message'] = '训练目录无法核实或位置配置冲突；原数据保留，更新前需处理。'
        return result

    def backup(self, *, only_if_data=False):
        self._location_conflict()
        if not self.root.exists() and only_if_data:
            return None
        with self.locked():
            if only_if_data and not self.has_data():
                return None
            from .training_backup import snapshot
            return snapshot(self.root)
