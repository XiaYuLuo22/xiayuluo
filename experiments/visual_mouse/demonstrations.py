"""Explicitly supplied, local screenshot demonstrations; no desktop hooks.

A dataset contains pixels, exact registered task strings and supervision labels.
Episode/split declarations and normalized pixel hashes prevent direct holdout
leakage. They cannot detect every near-duplicate or authenticate a data author.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

from PIL import Image, UnidentifiedImageError

from .data import HEIGHT, WIDTH


MAX_SAMPLES = 4096
MAX_MANIFEST = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
SPLITS = ('train', 'validation', 'test')
DOMAIN = 'user-demonstrations-offline'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_RESERVED = re.compile(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z', re.I)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('示范数据含重复字段。')
        result[key] = value
    return result


def validate_goals(goals):
    if (not isinstance(goals, list) or not 1 <= len(goals) <= 32
            or any(not isinstance(goal, str) or not goal.strip() or goal != goal.strip()
                   or len(goal) > 128 or any(ord(char) < 32 for char in goal) for goal in goals)
            or len(set(goals)) != len(goals)):
        raise ValueError('任务表必须包含 1–32 个不重复、精确匹配的简短任务文本。')
    return tuple(goals)


def no_links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('示范数据路径不能使用链接或目录联接。')


@contextmanager
def regular_file(path, maximum):
    no_links(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError('示范输入必须是大小受限的普通文件。')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError('读取示范数据时文件身份改变。')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            yield stream
        after = path.lstat()
        finished = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('读取示范数据时原文件改变。')
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns):
            raise ValueError('读取示范数据时打开的文件改变。')
    finally:
        os.close(descriptor)


def _image_path(value):
    if not isinstance(value, str) or len(value) > 512 or '\\' in value or ':' in value:
        raise ValueError('截图必须是数据清单旁的相对 PNG 路径。')
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or not path.parts
            or path.suffix.lower() != '.png'
            or any(part in ('.', '..') or part.endswith((' ', '.')) or _RESERVED.fullmatch(part)
                   or any(ord(char) < 32 for char in part) for part in path.parts)):
        raise ValueError('截图路径无效或不能跨平台安全保存。')
    return path


def image_bytes(path, expected_hash=None):
    with regular_file(Path(path), MAX_IMAGE_BYTES) as source:
        raw = source.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('截图文件超过 16 MiB 上限。')
    if expected_hash is not None and hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('示范截图 SHA-256 与清单不一致。')
    try:
        with Image.open(BytesIO(raw)) as source:
            if (source.format != 'PNG' or source.mode not in ('RGB', 'RGBA', 'L')
                    or getattr(source, 'n_frames', 1) != 1 or source.width < 16 or source.height < 16
                    or source.width * source.height > MAX_PIXELS):
                raise ValueError('仅支持单帧 PNG，最小 16×16，最大 16 百万像素。')
            source.load()
            image = source.convert('RGB').resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
            original_size = source.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise ValueError('示范截图不是可解码的有界 PNG。') from error
    return raw, image, original_size


@dataclass(frozen=True)
class Demonstration:
    image: Image.Image
    goal_id: int
    action: str
    xy: tuple | None
    episode: str
    split: str
    image_path: str
    sha256: str
    pixel_hash: str


@dataclass(frozen=True)
class Dataset:
    manifest: dict
    goals: tuple
    samples: tuple
    digest: str

    def partition(self, split):
        if split not in SPLITS:
            raise ValueError('未知示范分区。')
        return [sample for sample in self.samples if sample.split == split]

    def summary(self):
        return {'domain': DOMAIN, 'execution_permitted': False,
                'samples': len(self.samples), 'goals': list(self.goals), 'sha256': self.digest,
                'splits': {split: len(self.partition(split)) for split in SPLITS},
                'episodes': len({sample.episode for sample in self.samples}),
                'preprocessing': 'RGB bilinear resize to 128x96; normalized whole-image coordinates',
                'scope': 'exact registered goals; offline single-step coordinate/action imitation only'}


def load_dataset(manifest_path):
    path = Path(manifest_path).absolute()
    with regular_file(path, MAX_MANIFEST) as source:
        raw = source.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise ValueError('示范清单过大。')
    try:
        data = json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('示范清单必须是有效 JSON。') from error
    if (not isinstance(data, dict) or set(data) != {'schema', 'domain', 'owner_provided', 'goals', 'samples'}
            or type(data['schema']) is not int or data['schema'] != 1
            or data['domain'] != DOMAIN or data['owner_provided'] is not True):
        raise ValueError('需要本人明确提供的离线示范清单（schema 1）。')
    goals = validate_goals(data['goals'])
    entries = data['samples']
    if not isinstance(entries, list) or not 3 <= len(entries) <= MAX_SAMPLES:
        raise ValueError('示范样本数必须介于 3 和 4096。')
    result, episodes, content_splits, seen, total = [], {}, {}, set(), 0
    raw_hashes = {}
    for item in entries:
        if not isinstance(item, dict) or set(item) != {'episode', 'split', 'image', 'sha256', 'goal', 'action', 'xy'}:
            raise ValueError('示范记录字段不完整或含未知字段。')
        if (not isinstance(item['episode'], str) or not _ID.fullmatch(item['episode'])
                or item['split'] not in SPLITS or item['goal'] not in goals
                or item['action'] not in ('click', 'move', 'noop')
                or not isinstance(item['sha256'], str) or not _HASH.fullmatch(item['sha256'])):
            raise ValueError('示范会话、分区、任务、动作或图片哈希无效。')
        episode, split = item['episode'], item['split']
        if episode in episodes and episodes[episode] != split:
            raise ValueError('同一示范 episode 不能跨训练、验证或测试分区。')
        episodes[episode] = split
        xy = item['xy']
        if item['action'] == 'noop':
            if xy is not None:
                raise ValueError('noop 示范不能包含鼠标坐标。')
        elif (not isinstance(xy, list) or len(xy) != 2 or any(
                type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in xy)):
            raise ValueError('点击/移动标签必须是两个有限归一化坐标。')
        relative = _image_path(item['image'])
        image_path = path.parent.joinpath(*relative.parts)
        if item['image'] in raw_hashes and raw_hashes[item['image']] != item['sha256']:
            raise ValueError('同一截图路径被声明为不同内容。')
        raw_hashes[item['image']] = item['sha256']
        image_raw, image, _ = image_bytes(image_path, item['sha256'])
        total += len(image_raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('示范截图总读取量超过 512 MiB 上限。')
        pixel_hash = hashlib.sha256(image.tobytes()).hexdigest()
        if pixel_hash in content_splits and content_splits[pixel_hash] != split:
            raise ValueError('训练/验证/测试出现相同预处理画面，拒绝直接内容泄漏。')
        content_splits[pixel_hash] = split
        key = (episode, pixel_hash, item['goal'])
        if key in seen:
            raise ValueError('同一 episode 的画面和任务不能重复或给出矛盾标签。')
        seen.add(key)
        result.append(Demonstration(image, goals.index(item['goal']), item['action'],
                                    tuple(xy) if xy is not None else None, episode, split,
                                    item['image'], item['sha256'], pixel_hash))
    if set(episodes.values()) != set(SPLITS):
        raise ValueError('必须预先提供互不重叠的 train、validation、test 会话分区。')
    train_goals = {sample.goal_id for sample in result if sample.split == 'train'}
    if train_goals != set(range(len(goals))):
        raise ValueError('每个注册任务都必须有训练示范，不能把未知任务冒充已学会。')
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return Dataset(data, goals, tuple(result), hashlib.sha256(canonical).hexdigest())


def import_dataset(source, storage, name):
    """Copy an explicitly provided dataset; no collection, overwrites or migration."""
    if not isinstance(name, str) or not _ID.fullmatch(name):
        raise ValueError('数据集名称只允许 1–64 位字母、数字、下划线或连字符。')
    dataset = load_dataset(source)
    source = Path(source).absolute()
    with storage.locked():
        parent = storage.root / 'datasets'
        destination = parent / name
        if os.path.lexists(destination):
            raise FileExistsError('数据集已存在；不会覆盖旧数据，请选择新名称。')
        with tempfile.TemporaryDirectory(prefix='.demo-import-', dir=parent) as temporary:
            stage = Path(temporary)
            (stage / 'images').mkdir(mode=0o700)
            entries, copied = [], set()
            for item in dataset.manifest['samples']:
                filename = item['sha256'] + '.png'
                if filename not in copied:
                    raw, _, _ = image_bytes(source.parent.joinpath(*_image_path(item['image']).parts), item['sha256'])
                    with (stage / 'images' / filename).open('xb') as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
                    copied.add(filename)
                entries.append({**item, 'image': 'images/' + filename})
            manifest = {**dataset.manifest, 'samples': entries}
            with (stage / 'dataset.json').open('x', encoding='utf-8') as output:
                json.dump(manifest, output, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            imported = load_dataset(stage / 'dataset.json')
            # mkdir is exclusive; publish immutable files and manifest last. A
            # crash may leave an incomplete NEW directory, never replace old data.
            destination.mkdir(mode=0o700)
            (destination / 'images').mkdir(mode=0o700)
            for filename in sorted(copied):
                os.link(stage / 'images' / filename, destination / 'images' / filename, follow_symlinks=False)
            os.link(stage / 'dataset.json', destination / 'dataset.json', follow_symlinks=False)
    return {**imported.summary(), 'dataset': str(destination / 'dataset.json')}
