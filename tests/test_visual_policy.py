import json
from pathlib import Path

from PIL import Image
import pytest

from assistant.visual_policy import OfflineShadowPolicy


def model_directory(tmp_path):
    root = tmp_path / 'training'
    directory = root / 'models' / 'fixture-model'
    directory.mkdir(parents=True)
    return root, directory


def test_scope_requires_existing_absolute_child_of_selected_models(tmp_path):
    root, directory = model_directory(tmp_path)
    policy = OfflineShadowPolicy(directory, training_root=root)
    assert policy.model is None and policy.metadata is None and policy.ready is False
    for candidate in (root, root / 'models', tmp_path, Path('relative-model')):
        with pytest.raises(ValueError):
            OfflineShadowPolicy(candidate, training_root=root)
    with pytest.raises(ValueError):
        OfflineShadowPolicy(directory, training_root=Path('relative-root'))
    with pytest.raises(FileNotFoundError):
        OfflineShadowPolicy(root / 'models' / 'missing', training_root=root)


@pytest.mark.parametrize('value', [True, -.1, 1.1, float('nan')])
def test_threshold_is_finite_and_bounded(tmp_path, value):
    root, directory = model_directory(tmp_path)
    with pytest.raises(ValueError):
        OfflineShadowPolicy(directory, training_root=root, threshold=value)


def test_symlinks_are_rejected_without_loading_or_writing(tmp_path):
    root, directory = model_directory(tmp_path)
    link = root / 'models' / 'linked'
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip('This Windows account cannot create symbolic links.')
    with pytest.raises(ValueError):
        OfflineShadowPolicy(link, training_root=root)


@pytest.fixture
def artifact(tmp_path):
    np = pytest.importorskip('numpy')
    torch = pytest.importorskip('torch')
    from experiments.visual_mouse.artifact import save_artifact
    from experiments.visual_mouse.demonstration_policy import ARCHITECTURE, MODEL_TYPE, DemonstrationPolicy
    root, unused = model_directory(tmp_path)
    unused.rmdir()
    model = DemonstrationPolicy().eval()
    metadata = {'model_type': MODEL_TYPE, 'domain': 'user-demonstrations-offline',
                'live_execution_allowed': False, 'image_size': [128, 96],
                'architecture': ARCHITECTURE, 'goals': ['点击测试按钮']}
    save_artifact(unused, {name: value.detach().cpu().numpy().astype(np.float32)
                          for name, value in model.state_dict().items()}, metadata)
    return root, unused


def test_existing_offline_model_can_predict_ram_image_without_saving_screens(artifact):
    root, directory = artifact
    before = set(directory.iterdir())
    policy = OfflineShadowPolicy(directory, training_root=root)
    result = policy.predict(Image.new('RGB', (256, 192), 'red'), '点击测试按钮')
    assert policy.ready is True
    assert result['execution_permitted'] is False and result['domain'] == 'user-demonstrations-offline'
    assert result['proposed_action'] in ('click', 'move', 'noop')
    assert set(directory.iterdir()) == before and not list(root.rglob('*.png'))
    assert json.loads((directory / 'manifest.json').read_text())['metadata']['live_execution_allowed'] is False
    policy.close()
    assert policy.model is policy.metadata is None and policy.ready is False


def test_unknown_goal_is_noop_not_inferred_or_executed(artifact):
    root, directory = artifact
    result = OfflineShadowPolicy(directory, training_root=root).predict(
        Image.new('RGB', (80, 80)), '未注册的任务')
    assert result['proposed_action'] == 'noop' and result['normalized_xy'] is None
    assert result['confidence'] == 0 and result['execution_permitted'] is False


def test_invalid_image_does_not_load_checkpoint(tmp_path):
    root, directory = model_directory(tmp_path)
    policy = OfflineShadowPolicy(directory, training_root=root)
    for image in (Image.new('L', (80, 80)), Image.new('RGB', (8, 8)), object()):
        with pytest.raises(ValueError):
            policy.predict(image, 'fixture')
    assert policy.model is None and policy.metadata is None
