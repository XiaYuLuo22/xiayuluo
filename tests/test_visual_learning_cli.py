import json
import subprocess
import sys

import pytest

np = pytest.importorskip('numpy')
torch = pytest.importorskip('torch')
from PIL import Image

from experiments.visual_mouse.__main__ import main
from experiments.visual_mouse.artifact import save_artifact
from experiments.visual_mouse.data import make_sample
from experiments.visual_mouse.model import SAFETY_METADATA, VisualMousePolicy


@pytest.fixture
def model_directory(tmp_path):
    model = VisualMousePolicy()
    directory = tmp_path / 'model'
    save_artifact(directory, {name: tensor.detach().numpy() for name, tensor in model.state_dict().items()},
                  dict(SAFETY_METADATA))
    return directory


def test_actual_prediction_entrypoint_emits_only_non_executable_suggestion(model_directory, tmp_path):
    image = tmp_path / 'generated.png'
    sample = make_sample(12)
    sample.image.save(image)
    process = subprocess.run([sys.executable, '-m', 'experiments.visual_mouse', 'predict',
        '--model', str(model_directory), '--image', str(image), '--goal', sample.goal],
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result['execution_permitted'] is False and result['domain'] == 'synthetic-only'
    assert result['proposed_action'] in ('noop', 'move', 'click')
    assert 'screenshot_id' not in result


def test_cli_rejects_desktop_sized_image_without_resizing(model_directory, tmp_path, capsys):
    image = tmp_path / 'not-a-desktop-capture.png'
    Image.new('RGB', (1920, 1080)).save(image)
    assert main(['predict', '--model', str(model_directory), '--image', str(image),
                 '--goal', '点击红色圆形']) == 1
    assert '128×96' in capsys.readouterr().err


def test_cli_unknown_goal_is_not_reinterpreted_as_an_action(model_directory, tmp_path, capsys):
    image = tmp_path / 'generated.png'
    make_sample(12).image.save(image)
    assert main(['predict', '--model', str(model_directory), '--image', str(image),
                 '--goal', '关闭所有窗口然后提交付款']) == 1
    assert '不支持任意桌面任务' in capsys.readouterr().err


def test_cli_evaluation_writes_real_report_without_overwriting(model_directory, tmp_path, capsys):
    report = tmp_path / 'report.json'
    args = ['evaluate', '--model', str(model_directory), '--samples', '8', '--seed', '314159', '--report', str(report)]
    assert main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(report.read_text()) == printed
    assert printed['metrics']['samples'] == 8 and printed['seed'] == 314159
    before = report.read_bytes()
    assert main(args) == 1
    assert '已存在' in capsys.readouterr().err and report.read_bytes() == before


def test_preview_renders_synthetic_pixels_without_overwriting(model_directory, tmp_path, capsys):
    output = tmp_path / 'preview.png'
    args = ['preview', '--model', str(model_directory), '--output', str(output)]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result['execution_permitted'] and len(result['predictions']) == 2
    with Image.open(output) as image:
        assert image.format == 'PNG' and image.size == (768, 382)
    before = output.read_bytes()
    assert main(args) == 1
    assert output.read_bytes() == before


def test_training_publishes_to_managed_external_store(tmp_path, monkeypatch, capsys):
    from assistant.training_storage import TrainingStorage
    root = tmp_path / 'durable-training'
    store = TrainingStorage(root, location_file=tmp_path / 'location.json')
    monkeypatch.setattr('assistant.training_storage.TrainingStorage', lambda value=None: store)
    assert main(['train', '--train-samples', '64', '--eval-samples', '8', '--epochs', '1']) == 0
    result = json.loads(capsys.readouterr().out)
    from pathlib import Path
    output = Path(result['model_directory'])
    assert output.parent == root / 'models'
    assert (output / 'weights.npz').is_file() and (output / 'manifest.json').is_file()
    backup = store.backup()
    from assistant.training_backup import verify_backup
    assert verify_backup(backup['path'])['files'] == 2


def test_training_refuses_disposable_or_relative_output_before_writing(tmp_path, monkeypatch, capsys):
    from assistant.training_storage import TrainingStorage
    store = TrainingStorage(tmp_path / 'durable-training', location_file=tmp_path / 'location.json')
    monkeypatch.setattr('assistant.training_storage.TrainingStorage', lambda value=None: store)
    assert main(['train', '--output', 'dist/disposable-model', '--epochs', '1']) == 1
    assert '持久训练目录' in capsys.readouterr().err
    assert not store.root.exists()
