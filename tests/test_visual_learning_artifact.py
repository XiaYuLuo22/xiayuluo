"""Synthetic checkpoints stay bounded, numeric-only, and non-executable."""
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import warnings
import zipfile

import pytest

np = pytest.importorskip('numpy')

from experiments.visual_mouse import artifact


def metadata():
    return {'model_type': 'synthetic-visual-mouse-v1', 'domain': 'synthetic-only',
            'live_execution_allowed': False, 'image_size': [128, 96],
            'training': {'seed': 7}, 'metrics': {'accuracy': .75}}


def state():
    return {'encoder.weight': np.arange(6, dtype=np.float32).reshape(2, 3),
            'head.bias': np.array([.1, -.2], dtype=np.float32)}


def make(tmp_path):
    values = state()
    target = artifact.save_artifact(tmp_path / 'model', values, metadata())
    return target, {name: value.shape for name, value in values.items()}


def manifest(target):
    return json.loads((target / 'manifest.json').read_text())


def write_manifest(target, value):
    (target / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')


def npy(value):
    output = io.BytesIO()
    np.save(output, value, allow_pickle=True)
    return output.getvalue()


def rewrite_zip(target, entries, *, mutate=None):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    raw = output.getvalue()
    if mutate:
        raw = mutate(raw)
    (target / 'weights.npz').write_bytes(raw)
    value = manifest(target)
    value['weights'].update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    write_manifest(target, value)


def normal_entries():
    return [('a000.npy', npy(state()['encoder.weight'])), ('a001.npy', npy(state()['head.bias']))]


def test_round_trip_fixed_files_float32_and_synthetic_safety_metadata(tmp_path):
    target, shapes = make(tmp_path)
    arrays, info = artifact.load_artifact(target, shapes)
    assert {item.name for item in target.iterdir()} == {'manifest.json', 'weights.npz'}
    assert info == metadata() and info['live_execution_allowed'] is False
    assert set(arrays) == set(state())
    for name, value in state().items():
        np.testing.assert_array_equal(arrays[name], value)
        assert arrays[name].dtype == np.float32
    assert not list(tmp_path.glob('.visual-mouse-staging-*'))


def test_existing_output_and_concurrently_created_output_are_preserved(tmp_path, monkeypatch):
    target, _ = make(tmp_path)
    original = (target / 'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):
        artifact.save_artifact(target, state(), metadata())
    assert (target / 'manifest.json').read_bytes() == original
    raced = tmp_path / 'raced'
    original_mkdir = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == raced and not path.exists():
            original_mkdir(path)
            (path / 'user-file').write_text('preserve')
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'mkdir', mkdir)
    with pytest.raises(FileExistsError):
        artifact.save_artifact(raced, state(), metadata())
    assert (raced / 'user-file').read_text() == 'preserve'
    assert list(raced.iterdir()) == [raced / 'user-file']
    assert not list(tmp_path.glob('.visual-mouse-staging-*'))


def test_failed_publication_cleans_only_its_new_output(tmp_path, monkeypatch):
    original = artifact.os.link
    calls = []

    def link(source, destination, **kwargs):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError('controlled publication failure')
        return original(source, destination, **kwargs)

    monkeypatch.setattr(artifact.os, 'link', link)
    with pytest.raises(OSError):
        artifact.save_artifact(tmp_path / 'failed', state(), metadata())
    assert not (tmp_path / 'failed').exists()
    assert not list(tmp_path.glob('.visual-mouse-staging-*'))


@pytest.mark.parametrize('change', [{'domain': 'desktop'}, {'live_execution_allowed': True},
    {'live_execution_allowed': 0}, {'model_type': 'unknown'}, {'image_size': [96, 128]},
    {'image_size': [128., 96.]}, {'training': {'loss': float('nan')}}])
def test_unsafe_or_nonfinite_metadata_is_rejected_before_writing(tmp_path, change):
    with pytest.raises(ValueError):
        artifact.save_artifact(tmp_path / 'unsafe', state(), {**metadata(), **change})
    assert not (tmp_path / 'unsafe').exists()


@pytest.mark.parametrize('values', [
    {'x': np.array([1.], dtype=np.float64)},
    {'x': np.array([float('inf')], dtype=np.float32)},
    {'x': np.array([object()], dtype=object)},
    {'../x': np.ones(1, dtype=np.float32)},
    {'x/y': np.ones(1, dtype=np.float32)}, {}, {'x': [1.]},
])
def test_bad_names_types_and_nonfinite_tensors_are_never_saved(tmp_path, values):
    with pytest.raises(ValueError):
        artifact.save_artifact(tmp_path / 'unsafe', values, metadata())
    assert not (tmp_path / 'unsafe').exists()


def test_size_hash_tampering_and_architecture_mismatch_fail(tmp_path):
    target, shapes = make(tmp_path)
    raw = (target / 'weights.npz').read_bytes()
    (target / 'weights.npz').write_bytes(raw + b'changed')
    with pytest.raises(ValueError, match='size/hash'):
        artifact.load_artifact(target, shapes)
    (target / 'weights.npz').write_bytes(raw)
    for expected in ({'wrong': (2, 3)}, {**shapes, 'extra': (1,)},
                     {**shapes, 'encoder.weight': (3, 2)}):
        with pytest.raises(ValueError):
            artifact.load_artifact(target, expected)


@pytest.mark.parametrize('change', [{'file': '../../outside'}, {'size': True},
    {'sha256': 'f' * 63}, {'sha256': 'not a digest'}])
def test_manifest_cannot_choose_other_files_or_unbounded_sizes(tmp_path, change):
    target, shapes = make(tmp_path)
    value = manifest(target)
    value['weights'].update(change)
    write_manifest(target, value)
    with pytest.raises(ValueError):
        artifact.load_artifact(target, shapes)


def test_duplicate_json_keys_and_live_domain_tampering_fail(tmp_path):
    target, shapes = make(tmp_path)
    original = (target / 'manifest.json').read_text()
    (target / 'manifest.json').write_text(original.replace('"schema":1', '"schema":1,"schema":1'))
    with pytest.raises(ValueError, match='Duplicate'):
        artifact.load_artifact(target, shapes)
    (target / 'manifest.json').write_text(original)
    value = manifest(target)
    value['metadata']['live_execution_allowed'] = True
    write_manifest(target, value)
    with pytest.raises(ValueError, match='synthetic-only'):
        artifact.load_artifact(target, shapes)


@pytest.mark.parametrize('kind', ['traversal', 'directory', 'duplicate', 'extra', 'symlink', 'encrypted'])
def test_archive_members_cannot_add_paths_links_duplicates_or_encryption(tmp_path, kind):
    target, shapes = make(tmp_path)
    entries = normal_entries()
    mutation = None
    if kind == 'traversal':
        entries[0] = ('../outside.npy', entries[0][1])
    elif kind == 'directory':
        entries[0] = ('a000.npy/', b'')
    elif kind == 'duplicate':
        entries[1] = entries[0]
    elif kind == 'extra':
        entries.append(('extra.npy', npy(np.ones(1, dtype=np.float32))))
    elif kind == 'symlink':
        info = zipfile.ZipInfo('a000.npy')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        entries[0] = (info, entries[0][1])
    else:
        def mutation(raw):
            modified = bytearray(raw)
            for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
                position = raw.index(signature) + offset
                flags = struct.unpack_from('<H', modified, position)[0]
                struct.pack_into('<H', modified, position, flags | 1)
            return bytes(modified)
    rewrite_zip(target, entries, mutate=mutation)
    with pytest.raises(ValueError):
        artifact.load_artifact(target, shapes)
    assert not (tmp_path / 'outside.npy').exists()


@pytest.mark.parametrize('kind', ['object', 'float64', 'huge_shape', 'oversized_header', 'fortran', 'trailing'])
def test_array_headers_are_validated_before_numpy_allocation(tmp_path, monkeypatch, kind):
    target, shapes = make(tmp_path)
    entries = normal_entries()
    if kind == 'object':
        entries[0] = ('a000.npy', npy(np.array([[object()] * 3] * 2, dtype=object)))
    elif kind == 'float64':
        entries[0] = ('a000.npy', npy(np.ones((2, 3), dtype=np.float64)))
    elif kind == 'huge_shape':
        output = io.BytesIO()
        np.lib.format.write_array_header_1_0(output, {'descr': '<f4', 'fortran_order': False,
                                                     'shape': (1_000_000_000, 1_000_000_000)})
        entries[0] = ('a000.npy', output.getvalue() + b'\0' * 4)
    elif kind == 'oversized_header':
        header = b"{'descr': '<f4', 'fortran_order': False, 'shape': (2, 3), }" + b' ' * 5000 + b'\n'
        entries[0] = ('a000.npy', b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header + b'\0' * 24)
    elif kind == 'fortran':
        entries[0] = ('a000.npy', npy(np.asfortranarray(np.ones((2, 3), dtype=np.float32))))
    else:
        entries[0] = ('a000.npy', entries[0][1] + b'trailing')
    rewrite_zip(target, entries)

    def forbidden(*args, **kwargs):
        pytest.fail('Unsafe header reached NumPy data allocation')

    monkeypatch.setattr(artifact.np, 'load', forbidden)
    with pytest.raises(ValueError):
        artifact.load_artifact(target, shapes)


def test_nonfinite_numeric_data_and_size_limits_fail(tmp_path, monkeypatch):
    target, shapes = make(tmp_path)
    entries = normal_entries()
    entries[1] = ('a001.npy', npy(np.array([float('nan'), 0], dtype=np.float32)))
    rewrite_zip(target, entries)
    with pytest.raises(ValueError, match='finite'):
        artifact.load_artifact(target, shapes)
    monkeypatch.setattr(artifact, 'MAX_UNCOMPRESSED_BYTES', 8)
    with pytest.raises(ValueError, match='size limit'):
        artifact.load_artifact(target, shapes)


def test_symlink_directory_and_weights_are_rejected(tmp_path):
    target, shapes = make(tmp_path)
    link = tmp_path / 'linked-model'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks requires local platform permission')
    with pytest.raises(ValueError, match='not a link'):
        artifact.load_artifact(link, shapes)
    weights = target / 'weights.npz'
    moved = target / 'original.npz'
    weights.rename(moved)
    weights.symlink_to(moved)
    with pytest.raises(ValueError, match='regular files'):
        artifact.load_artifact(target, shapes)


def test_manifest_rejects_oversize_and_excessive_metadata_nesting(tmp_path):
    target, shapes = make(tmp_path)
    (target / 'manifest.json').write_bytes(b' ' * (artifact.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValueError, match='size limit'):
        artifact.load_artifact(target, shapes)
    nested = {}
    for _ in range(12):
        nested = {'next': nested}
    with pytest.raises(ValueError, match='deeply'):
        artifact.save_artifact(tmp_path / 'nested', state(), {**metadata(), 'training': nested})
