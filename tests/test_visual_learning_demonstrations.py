"""Offline owner-provided demonstration pipeline. Fixtures are generated, not user captures."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

np = pytest.importorskip('numpy')
torch = pytest.importorskip('torch')
from PIL import Image, ImageDraw

from assistant.training_storage import TrainingStorage
from experiments.visual_mouse.artifact import load_artifact, save_artifact
from experiments.visual_mouse.demonstrations import (DOMAIN, image_bytes, import_dataset,
                                                    load_dataset)
from experiments.visual_mouse.demonstration_policy import (ARCHITECTURE, MODEL_TYPE, DemonstrationPolicy,
    evaluate_demonstrations, load_demonstration_policy, predict_demonstration, train_demonstrations)
from experiments.visual_mouse.__main__ import main


@pytest.fixture
def dataset_path(tmp_path):
    source = tmp_path / 'provided'
    (source / 'images').mkdir(parents=True)
    entries = []
    for split_id, split in enumerate(('train', 'validation', 'test')):
        # Different pixels across splits, same actual image with two tasks within
        # each split: label/goal changes never become inputs to the policy.
        image = Image.new('RGB', (256, 192), (20 + split_id * 20, 20, 20))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 35, 75, 80), fill='red')
        draw.rectangle((165, 105, 210, 150), fill='green')
        path = source / 'images' / (split + '.png')
        image.save(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for goal, xy in (('点击保存按钮', [.2, .3]), ('点击搜索按钮', [.75, .7])):
            entries.append({'episode': 'session-' + split, 'split': split, 'image': 'images/' + path.name,
                            'sha256': digest, 'goal': goal, 'action': 'click', 'xy': xy})
    manifest = {'schema': 1, 'domain': DOMAIN, 'owner_provided': True,
                'goals': ['点击保存按钮', '点击搜索按钮'], 'samples': entries}
    path = source / 'dataset.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return path


def rewrite(path, update):
    data = json.loads(path.read_text(encoding='utf-8'))
    update(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def test_dataset_has_exact_goals_labels_and_episode_holdouts(dataset_path):
    dataset = load_dataset(dataset_path)
    assert dataset.goals == ('点击保存按钮', '点击搜索按钮')
    assert dataset.summary()['splits'] == {'train': 2, 'validation': 2, 'test': 2}
    assert dataset.samples[0].image.size == (128, 96)
    assert dataset.samples[0].image.mode == 'RGB'
    assert dataset.samples[0].pixel_hash == dataset.samples[1].pixel_hash
    assert dataset.samples[0].goal_id != dataset.samples[1].goal_id
    assert dataset.samples[0].xy != dataset.samples[1].xy
    assert dataset.summary()['execution_permitted'] is False


@pytest.mark.parametrize('change,match', [
    (lambda d: d.update(owner_provided=False), '本人'),
    (lambda d: d.update(schema=True), 'schema'),
    (lambda d: d.update(goals=['重复', '重复']), '任务表'),
    (lambda d: d['goals'].append('没有示范的任务'), '训练示范'),
    (lambda d: d['samples'][0].update(episode='../outside'), '会话'),
    (lambda d: d['samples'][2].update(episode='session-train'), 'episode'),
    (lambda d: d['samples'][0].update(action='keyboard'), '动作'),
    (lambda d: d['samples'][0].update(xy=[float('nan'), .5]), '坐标'),
    (lambda d: d['samples'][0].update(xy=[True, .5]), '坐标'),
    (lambda d: d['samples'][0].update(action='noop'), 'noop'),
    (lambda d: d['samples'][0].update(sha256='0' * 64), 'SHA-256'),
    (lambda d: d['samples'][0].update(goal='未注册任意任务'), '任务'),
    (lambda d: d['samples'][0].update(extra='hidden label'), '未知字段'),
    (lambda d: d['samples'].append(copy.deepcopy(d['samples'][0])), '重复'),
    (lambda d: d.update(samples=d['samples'][:4]), '分区'),
])
def test_dataset_rejects_bad_metadata_and_leakage(dataset_path, change, match):
    rewrite(dataset_path, change)
    with pytest.raises(ValueError, match=match):
        load_dataset(dataset_path)


@pytest.mark.parametrize('image', ['../outside.png', '/absolute.png', 'images\\file.png',
                                  'C:/drive.png', 'images/AUX.png', 'images/trailing. /a.png'])
def test_image_paths_cannot_escape_or_use_windows_unsafe_names(dataset_path, image):
    rewrite(dataset_path, lambda data: data['samples'][0].update(image=image))
    with pytest.raises(ValueError, match='路径'):
        load_dataset(dataset_path)


def test_same_pixels_cannot_leak_across_splits_even_if_different_png_bytes(dataset_path):
    train = dataset_path.parent / 'images/train.png'
    validation = dataset_path.parent / 'images/validation.png'
    with Image.open(train) as source:
        source.save(validation, compress_level=0)
    assert train.read_bytes() != validation.read_bytes()
    digest = hashlib.sha256(validation.read_bytes()).hexdigest()
    rewrite(dataset_path, lambda data: [item.update(sha256=digest) for item in data['samples']
                                        if item['split'] == 'validation'])
    with pytest.raises(ValueError, match='内容泄漏'):
        load_dataset(dataset_path)


def test_dataset_noop_labels_and_unknown_partition(dataset_path):
    rewrite(dataset_path, lambda data: data['samples'][0].update(action='noop', xy=None))
    dataset = load_dataset(dataset_path)
    assert dataset.samples[0].xy is None
    with pytest.raises(ValueError):
        dataset.partition('unknown')


def test_images_reject_links_and_non_png(dataset_path, tmp_path):
    image = dataset_path.parent / 'images/train.png'
    original = image.read_bytes()
    linked = tmp_path / 'linked.png'
    try:
        linked.symlink_to(image)
    except OSError:
        pass
    else:
        with pytest.raises(ValueError, match='链接'):
            image_bytes(linked)
    image.write_bytes(b'not image')
    with pytest.raises(ValueError):
        image_bytes(image)
    image.write_bytes(original)


def test_duplicate_json_fields_cannot_redefine_owner_or_splits(dataset_path):
    original = dataset_path.read_text(encoding='utf-8')
    dataset_path.write_text(original.replace('"schema": 1', '"schema": 1, "schema": 1'), encoding='utf-8')
    with pytest.raises(ValueError, match='重复字段'):
        load_dataset(dataset_path)


def test_image_and_total_read_limits_are_checked_before_training(dataset_path, monkeypatch):
    monkeypatch.setattr('experiments.visual_mouse.demonstrations.MAX_TOTAL_BYTES', 1)
    with pytest.raises(ValueError, match='总读取量'):
        load_dataset(dataset_path)


def test_import_copies_private_data_to_managed_root_never_overwrites(dataset_path, tmp_path):
    store = TrainingStorage(tmp_path / 'durable', location_file=tmp_path / 'location.json')
    original = {str(path): path.read_bytes() for path in dataset_path.parent.rglob('*') if path.is_file()}
    result = import_dataset(dataset_path, store, 'my-demo')
    imported = Path(result['dataset'])
    assert imported == store.root / 'datasets/my-demo/dataset.json'
    assert load_dataset(imported).summary()['splits'] == result['splits']
    assert len(list(imported.parent.joinpath('images').glob('*.png'))) == 3
    assert original == {str(path): path.read_bytes() for path in dataset_path.parent.rglob('*') if path.is_file()}
    before = imported.read_bytes()
    with pytest.raises(FileExistsError):
        import_dataset(dataset_path, store, 'my-demo')
    assert imported.read_bytes() == before
    with pytest.raises(ValueError):
        import_dataset(dataset_path, store, '../unsafe')


def test_new_policy_uses_only_pixels_and_registered_goal_ids(dataset_path):
    dataset = load_dataset(dataset_path)
    model = DemonstrationPolicy()
    from experiments.visual_mouse.model import image_tensor
    image = image_tensor(dataset.samples[0].image)
    with torch.inference_mode():
        first, _ = model(image.unsqueeze(0), torch.tensor([0]))
        second, _ = model(image.unsqueeze(0), torch.tensor([1]))
    assert not torch.equal(first, second)
    with pytest.raises(ValueError):
        model(image.unsqueeze(0), torch.tensor([32]))
    with pytest.raises(ValueError):
        model(image.unsqueeze(0), torch.tensor([[0, 1, 2]]))


def test_training_real_format_and_data_only_loading_stay_offline(dataset_path, tmp_path):
    output = tmp_path / 'model'
    entered = []
    from contextlib import contextmanager
    @contextmanager
    def lock():
        entered.append('locked-publication')
        yield
    metadata = train_demonstrations(dataset_path, output, epochs=1, threads=1, publication_lock=lock)
    assert entered == ['locked-publication']
    assert metadata['model_type'] == MODEL_TYPE
    assert metadata['domain'] == DOMAIN and metadata['live_execution_allowed'] is False
    assert metadata['baseline']['split'] == metadata['validation']['split'] == 'validation'
    assert metadata['training']['train_samples'] == 2
    model, restored = load_demonstration_policy(output)
    assert restored == metadata
    report = evaluate_demonstrations(model, load_dataset(dataset_path))
    assert report['split'] == 'test' and report['metrics']['samples'] == 2
    assert report['counterfactual_pairs'] == 1
    assert report['execution_permitted'] is False
    result = predict_demonstration(model, restored, dataset_path.parent / 'images/test.png', '点击保存按钮')
    assert result['domain'] == DOMAIN and not result['execution_permitted']
    assert result['original_image_size'] == [256, 192]
    with pytest.raises(ValueError, match='未在本模型中注册'):
        predict_demonstration(model, restored, dataset_path.parent / 'images/test.png', '任意未知指令')
    with pytest.raises(FileExistsError):
        train_demonstrations(dataset_path, output, epochs=1)
    with pytest.raises(ValueError):
        evaluate_demonstrations(model, load_dataset(dataset_path), split='train')


def test_cli_import_train_evaluate_and_predict(dataset_path, tmp_path, monkeypatch, capsys):
    store = TrainingStorage(tmp_path / 'durable', location_file=tmp_path / 'location.json')
    monkeypatch.setattr('assistant.training_storage.TrainingStorage', lambda value=None: store)
    assert main(['import-demo', '--manifest', str(dataset_path), '--name', 'example']) == 0
    imported = json.loads(capsys.readouterr().out)['dataset']
    assert main(['train-demo', '--dataset', imported, '--epochs', '1', '--threads', '1']) == 0
    trained = json.loads(capsys.readouterr().out)
    output = trained['model_directory']
    assert Path(output).parent == store.root / 'models'
    report = tmp_path / 'test-report.json'
    args = ['evaluate-demo', '--model', output, '--dataset', imported, '--report', str(report)]
    assert main(args) == 0
    measured = json.loads(capsys.readouterr().out)
    assert measured['split'] == 'test' and measured['domain'] == DOMAIN
    assert json.loads(report.read_text()) == measured
    assert main(args) == 1
    assert '已存在' in capsys.readouterr().err
    assert main(['predict-demo', '--model', output, '--image', str(dataset_path.parent / 'images/test.png'),
                 '--goal', '点击保存按钮']) == 0
    assert json.loads(capsys.readouterr().out)['execution_permitted'] is False
    rewrite(Path(imported), lambda data: data['samples'][0].update(xy=[.5, .5]))
    assert main(['evaluate-demo', '--model', output, '--dataset', imported]) == 1
    assert '清单改变' in capsys.readouterr().err


def test_cli_training_refuses_unmanaged_dataset(dataset_path, tmp_path, monkeypatch, capsys):
    store = TrainingStorage(tmp_path / 'durable', location_file=tmp_path / 'location.json')
    monkeypatch.setattr('assistant.training_storage.TrainingStorage', lambda value=None: store)
    assert main(['train-demo', '--dataset', str(dataset_path), '--epochs', '1']) == 1
    assert 'import-demo' in capsys.readouterr().err


def test_all_noop_predictions_are_not_reported_as_coordinate_success(dataset_path):
    from experiments.visual_mouse.model import Prediction
    from experiments.visual_mouse.demonstration_policy import _metrics
    samples = load_dataset(dataset_path).partition('test')
    result = _metrics(samples, [Prediction('noop', None, .99, 'abstain') for sample in samples])
    assert result['label_coordinate_success_rate'] == 0
    assert result['suggestion_precision'] is None and result['suggestions'] == 0


def test_demo_and_synthetic_loaders_do_not_mix_model_domains(tmp_path):
    from experiments.visual_mouse.model import SAFETY_METADATA, VisualMousePolicy, load_policy
    synthetic = VisualMousePolicy()
    metadata = {'model_type': MODEL_TYPE, 'domain': DOMAIN, 'live_execution_allowed': False,
                'image_size': [128, 96], 'architecture': ARCHITECTURE, 'goals': ['点击保存按钮']}
    path = save_artifact(tmp_path / 'mislabelled', {name: tensor.detach().numpy()
                        for name, tensor in synthetic.state_dict().items()}, metadata)
    with pytest.raises(ValueError, match='synthetic-only'):
        load_policy(path)


@pytest.mark.parametrize('field,value', [('live_execution_allowed', True), ('domain', 'desktop-live'),
                                       ('goals', ['repeated', 'repeated']), ('goals', ['a'] * 33)])
def test_demo_model_metadata_cannot_enable_execution(tmp_path, field, value):
    model = DemonstrationPolicy()
    metadata = {'model_type': MODEL_TYPE, 'domain': DOMAIN, 'live_execution_allowed': False,
                'image_size': [128, 96], 'architecture': ARCHITECTURE, 'goals': ['点击保存按钮']}
    metadata[field] = value
    with pytest.raises(ValueError):
        save_artifact(tmp_path / 'unsafe', {name: tensor.detach().numpy()
                      for name, tensor in model.state_dict().items()}, metadata)
