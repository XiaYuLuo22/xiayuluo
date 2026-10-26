"""Bounded, data-only checkpoints for the synthetic visual-mouse experiment.

No pickle, Torch deserialization, imports from an artifact, or desktop execution.
The SHA-256 detects accidental changes; it does not authenticate an untrusted
author. A loaded checkpoint remains a synthetic-only research model.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

import numpy as np


MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARRAYS = 128
MAX_HEADER_BYTES = 4096
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]{0,127}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _shape(value):
    if not isinstance(value, (list, tuple)) or len(value) > 8:
        raise ValueError('Invalid tensor shape.')
    if any(type(size) is not int or size < 1 or size > 1_000_000 for size in value):
        raise ValueError('Invalid tensor shape.')
    result = tuple(value)
    if math.prod(result) * 4 > MAX_UNCOMPRESSED_BYTES:
        raise ValueError('Tensor exceeds the checkpoint size limit.')
    return result


def _names_and_shapes(expected):
    if not isinstance(expected, dict) or not 1 <= len(expected) <= MAX_ARRAYS:
        raise ValueError('Invalid checkpoint tensor inventory.')
    result = {}
    for name, shape in expected.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or '..' in name:
            raise ValueError('Invalid tensor name.')
        result[name] = _shape(shape)
    if sum(math.prod(shape) * 4 for shape in result.values()) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError('Checkpoint exceeds the total tensor size limit.')
    return result


def _json_value(value, depth=0):
    if depth > 8:
        raise ValueError('Checkpoint metadata is nested too deeply.')
    if value is None or type(value) in (bool, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Checkpoint metadata must contain finite numbers.')
        return
    if isinstance(value, str):
        if len(value) > 8192:
            raise ValueError('Checkpoint metadata string is too long.')
        return
    if isinstance(value, list):
        if len(value) > 512:
            raise ValueError('Checkpoint metadata list is too long.')
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 256 or any(not isinstance(key, str) or len(key) > 128 for key in value):
            raise ValueError('Checkpoint metadata object is invalid.')
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError('Checkpoint metadata must be plain JSON data.')


def _metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError('Checkpoint metadata must be an object.')
    _json_value(metadata)
    supported = {
        'synthetic-visual-mouse-v1': 'synthetic-only',
        'demonstration-visual-mouse-v1': 'user-demonstrations-offline',
    }
    if (not isinstance(metadata.get('model_type'), str) or metadata.get('model_type') not in supported
            or metadata.get('domain') != supported.get(metadata.get('model_type'))
            or metadata.get('live_execution_allowed') is not False
            or metadata.get('image_size') != [128, 96]
            or any(type(size) is not int for size in metadata.get('image_size', []))):
        raise ValueError('Checkpoint is not an approved offline-only model (synthetic-only or user-demonstrations-offline).')
    if metadata.get('model_type') == 'demonstration-visual-mouse-v1':
        goals = metadata.get('goals')
        if (not isinstance(goals, list) or not 1 <= len(goals) <= 32
                or any(not isinstance(goal, str) or not goal.strip() or goal != goal.strip()
                       or len(goal) > 128 or any(ord(char) < 32 for char in goal) for goal in goals)
                or len(set(goals)) != len(goals)):
            raise ValueError('Demonstration model requires a bounded exact-match goal vocabulary.')
    encoded = json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_MANIFEST_BYTES // 2:
        raise ValueError('Checkpoint metadata is too large.')
    return json.loads(encoded)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate checkpoint metadata field.')
        result[key] = value
    return result


@contextmanager
def _regular_file(path, maximum):
    """Open fixed local files, refusing symlinks and changed file identities."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('Checkpoint inputs must be regular files, not links.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or opened.st_size > maximum):
            raise ValueError('Checkpoint file changed or exceeds its size limit.')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            yield stream
    finally:
        os.close(descriptor)


def _manifest(value, expected):
    if (not isinstance(value, dict) or set(value) != {'schema', 'metadata', 'weights', 'tensors'}
            or type(value['schema']) is not int or value['schema'] != 1):
        raise ValueError('Unsupported checkpoint manifest.')
    metadata = _metadata(value['metadata'])
    weights = value['weights']
    if (not isinstance(weights, dict) or set(weights) != {'file', 'size', 'sha256'}
            or weights['file'] != 'weights.npz' or type(weights['size']) is not int
            or not 1 <= weights['size'] <= MAX_FILE_BYTES
            or not isinstance(weights['sha256'], str) or not _DIGEST.fullmatch(weights['sha256'])):
        raise ValueError('Invalid checkpoint weights descriptor.')
    inventory = value['tensors']
    if not isinstance(inventory, list) or len(inventory) != len(expected):
        raise ValueError('Checkpoint tensor inventory does not match the architecture.')
    entries, names = {}, set()
    for index, tensor in enumerate(inventory):
        if not isinstance(tensor, dict) or set(tensor) != {'name', 'entry', 'shape', 'dtype'}:
            raise ValueError('Invalid checkpoint tensor descriptor.')
        name = tensor['name']
        if not isinstance(name, str) or name not in expected or name in names:
            raise ValueError('Checkpoint tensor names do not match the architecture.')
        shape = _shape(tensor['shape'])
        if shape != expected[name] or tensor['dtype'] != 'float32' or tensor['entry'] != f'a{index:03d}.npy':
            raise ValueError('Checkpoint tensor shape/type does not match the architecture.')
        names.add(name)
        entries[tensor['entry']] = (name, shape)
    if names != set(expected):
        raise ValueError('Checkpoint tensor inventory is incomplete.')
    return metadata, weights, entries


def save_artifact(directory, state_dict, metadata):
    """Publish a new data-only checkpoint; never replace an existing directory.

    We prepare files in a sibling temporary directory, reserve the output with
    exclusive mkdir, and link the complete manifest last. Concurrent readers can
    never accept an incompletely written checkpoint; existing outputs survive.
    """
    metadata = _metadata(metadata)
    if not isinstance(state_dict, dict):
        raise ValueError('Checkpoint state must be a tensor mapping.')
    if any(not isinstance(value, np.ndarray) for value in state_dict.values()):
        raise ValueError('Convert checkpoint tensors to NumPy float32 arrays first.')
    expected = _names_and_shapes({name: tuple(value.shape) for name, value in state_dict.items()})
    arrays, inventory = {}, []
    for index, name in enumerate(sorted(expected)):
        value = state_dict[name]
        if value.dtype != np.dtype('float32') or not np.isfinite(value).all():
            raise ValueError('Checkpoint tensors must be finite float32 arrays.')
        arrays[f'a{index:03d}'] = np.array(value, dtype=np.float32, order='C', copy=True)
        inventory.append({'name': name, 'entry': f'a{index:03d}.npy',
                          'shape': list(expected[name]), 'dtype': 'float32'})
    destination = Path(directory).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Checkpoint output already exists; choose a new directory.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    published, created, identity = [], False, None
    with tempfile.TemporaryDirectory(prefix='.visual-mouse-staging-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        weights_path = stage / 'weights.npz'
        with weights_path.open('xb') as output:
            np.savez_compressed(output, **arrays)
            output.flush()
            os.fsync(output.fileno())
        weights_path.chmod(0o600)
        if weights_path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Checkpoint compressed weights exceed the size limit.')
        with zipfile.ZipFile(weights_path) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError('Checkpoint uncompressed weights exceed the size limit.')
        with weights_path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        manifest = {'schema': 1, 'metadata': metadata,
                    'weights': {'file': 'weights.npz', 'size': weights_path.stat().st_size, 'sha256': digest},
                    'tensors': inventory}
        encoded = json.dumps(manifest, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise ValueError('Checkpoint manifest is too large.')
        manifest_path = stage / 'manifest.json'
        with manifest_path.open('xb') as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        manifest_path.chmod(0o600)
        try:
            destination.mkdir(mode=0o700)
            created = True
            info = destination.stat()
            identity = (info.st_dev, info.st_ino)
            for name in ('weights.npz', 'manifest.json'):
                os.link(stage / name, destination / name, follow_symlinks=False)
                published.append(name)
        except BaseException:
            # Only remove the exact new files/directory this call published.
            if created and destination.is_dir() and not destination.is_symlink():
                info = destination.stat()
                if (info.st_dev, info.st_ino) == identity:
                    for name in published:
                        target = destination / name
                        if target.is_file() and not target.is_symlink() and os.path.samefile(stage / name, target):
                            target.unlink()
                    try:
                        destination.rmdir()
                    except OSError:
                        pass
            raise
    return destination


def load_artifact(directory, expected_shapes):
    """Load only fixed-shape numeric arrays; all headers are checked before allocation."""
    expected = _names_and_shapes(expected_shapes)
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Checkpoint directory must be a local directory, not a link.')
    with _regular_file(directory / 'manifest.json', MAX_MANIFEST_BYTES) as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('Checkpoint manifest exceeds its size limit.')
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique_object,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON value.')))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Invalid checkpoint manifest JSON.') from error
    metadata, weights, entries = _manifest(manifest, expected)
    result = {}
    with _regular_file(directory / 'weights.npz', MAX_FILE_BYTES) as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) != weights['size'] or hashlib.sha256(raw).hexdigest() != weights['sha256']:
        raise ValueError('Checkpoint weights failed their size/hash check.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            if (len(items) != len(entries) or len({item.filename for item in items}) != len(items)
                    or {item.filename for item in items} != set(entries)
                    or sum(item.file_size for item in items) > MAX_UNCOMPRESSED_BYTES):
                raise ValueError('Checkpoint archive members are unexpected or oversized.')
            for item in items:
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (item.is_dir() or item.flag_bits & 1 or mode not in (0, stat.S_IFREG)
                        or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or not 1 <= item.file_size <= MAX_UNCOMPRESSED_BYTES):
                    raise ValueError('Checkpoint archive must contain only numeric tensor files.')
                name, shape = entries[item.filename]
                with archive.open(item) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version == (1, 0):
                        actual_shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=MAX_HEADER_BYTES)
                    elif version == (2, 0):
                        actual_shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream, max_header_size=MAX_HEADER_BYTES)
                    else:
                        raise ValueError('Unsupported checkpoint array format.')
                    if (actual_shape != shape or dtype != np.dtype('float32') or fortran
                            or item.file_size - stream.tell() != math.prod(shape) * 4):
                        raise ValueError('Checkpoint array header does not match the architecture.')
                # NumPy only sees data whose complete inventory, headers, exact
                # byte lengths and aggregate allocation limits are already known.
            with np.load(io.BytesIO(raw), allow_pickle=False, max_header_size=MAX_HEADER_BYTES) as arrays:
                for entry, (name, shape) in entries.items():
                    value = arrays[entry[:-4]]
                    if value.dtype != np.dtype('float32') or value.shape != shape or not np.isfinite(value).all():
                        raise ValueError('Checkpoint tensors must be finite float32 arrays.')
                    result[name] = np.array(value, copy=True)
    except (zipfile.BadZipFile, EOFError, KeyError, OverflowError, OSError) as error:
        raise ValueError('Invalid checkpoint numeric archive.') from error
    return result, metadata
