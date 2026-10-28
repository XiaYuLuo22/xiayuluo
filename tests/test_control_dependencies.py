"""Local control dependencies are optional, pinned, and packaged with notices."""
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from tools import build_desktop
from tools.bootstrap_windows import PROBE


ROOT = Path(__file__).resolve().parents[1]


def test_control_extra_is_pinned_and_keeps_training_out_of_normal_app():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    extra = project['project']['optional-dependencies']['control']
    assert extra == ['transitions==0.9.3', 'mss==10.2.0',
                     'opencv-python-headless==4.14.0.94', 'numpy==2.4.2']
    constraints = (ROOT / 'requirements.lock').read_text(encoding='utf-8').splitlines()
    assert all(item in constraints for item in extra)
    assert 'six==1.17.0' in constraints
    assert not any('torch' in item or 'transformers' in item for item in extra)
    assert 'mss' in PROBE and 'cv2' in PROBE and 'transitions' in PROBE
    assert 'mss.mss(' not in PROBE and 'screenshot(' not in PROBE


def test_build_collects_control_backends_without_training_weights():
    command = build_desktop.build_command(Path('release'), Path('temporary'))
    hidden = [command[index + 1] for index, value in enumerate(command) if value == '--hidden-import']
    collected = [command[index + 1] for index, value in enumerate(command) if value == '--collect-all']
    assert {'transitions', 'transitions.extensions.asyncio', 'cv2'} <= set(hidden)
    assert 'mss' in collected
    excluded = [command[index + 1] for index, value in enumerate(command) if value == '--exclude-module']
    assert {'torch', 'experiments'} <= set(excluded)
    assert not any(value in hidden + collected for value in ('torch', 'transformers', 'experiments'))


def test_license_collection_preserves_distinct_same_named_notices(tmp_path, monkeypatch):
    installed = tmp_path / 'installed'
    for name, text in [('package/LICENSE', 'component A'),
                       ('fixture.dist-info/licenses/subcomponent/LICENSE', 'component B')]:
        target = installed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    files = [Path('package/LICENSE'), Path('fixture.dist-info/licenses/subcomponent/LICENSE')]
    dist = SimpleNamespace(metadata={'Name': 'fixture'}, files=files,
                           locate_file=lambda relative: installed / relative)
    monkeypatch.setattr(build_desktop.metadata, 'distributions', lambda: [dist])
    destination = tmp_path / 'collected'
    build_desktop.collect_licenses(destination)
    assert (destination / 'fixture/package/LICENSE').read_text() == 'component A'
    assert (destination / 'fixture/fixture.dist-info/licenses/subcomponent/LICENSE').read_text() == 'component B'


def test_actual_control_wheel_licenses_include_opencv_third_party_text(tmp_path, monkeypatch):
    names = ('transitions', 'mss', 'opencv-python-headless', 'numpy', 'six')
    try:
        distributions = [metadata.distribution(name) for name in names]
    except metadata.PackageNotFoundError:
        pytest.skip('Actual wheel notices require the optional control dependency group')
    monkeypatch.setattr(build_desktop.metadata, 'distributions', lambda: distributions)
    build_desktop.collect_licenses(tmp_path)
    expected = tmp_path / 'opencv-python-headless/opencv_python_headless-4.14.0.94.dist-info'
    text = (expected / 'LICENSE-3RD-PARTY.txt').read_text(encoding='utf-8')
    assert 'FFmpeg' in text and 'GNU LESSER GENERAL PUBLIC LICENSE' in text
    assert (expected / 'LICENSE.txt').is_file()
    assert (tmp_path / 'transitions/transitions-0.9.3.dist-info/licenses/LICENSE').is_file()
    assert (tmp_path / 'mss/mss-10.2.0.dist-info/licenses/LICENSE.txt').is_file()
    # Distinct NumPy component licenses with the same basename all survive.
    assert len(list((tmp_path / 'numpy').rglob('LICENSE*'))) > 5
