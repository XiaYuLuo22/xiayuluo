"""Learning is offline and goal-conditioned; predicted actions are never executed."""
import inspect

import pytest

np = pytest.importorskip('numpy')
torch = pytest.importorskip('torch')
from PIL import Image

from experiments.visual_mouse.artifact import save_artifact
from experiments.visual_mouse.data import make_counterfactuals, make_sample
from experiments.visual_mouse.model import (NULL_LOCATION, SAFETY_METADATA, Predictor,
    VisualMousePolicy, decode_predictions, image_tensor, load_policy, location_index, location_xy)
from experiments.visual_mouse.training import evaluate, metrics, point_hit, require_learning, tensors, train


@pytest.fixture(autouse=True)
def cpu_budget():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def test_pixels_and_goal_produce_bounded_heads_and_receive_supervised_gradients():
    torch.manual_seed(12)
    model = VisualMousePolicy()
    samples = [make_sample(10), make_sample(11)]
    images, goals, location_labels, action_labels = tensors(samples)
    location, action = model(images, goals)
    assert location.shape == (2, 193) and action.shape == (2, 3)
    before = model.location.weight.detach().clone()
    loss = torch.nn.functional.cross_entropy(location, location_labels) + torch.nn.functional.cross_entropy(action, action_labels)
    loss.backward()
    assert model.color.weight.grad is not None and torch.count_nonzero(model.color.weight.grad)
    optimizer = torch.optim.SGD(model.parameters(), lr=.01)
    optimizer.step()
    assert not torch.equal(model.location.weight, before)


def test_same_pixels_are_conditioned_on_goal_not_generator_metadata():
    first, second = make_counterfactuals(10)
    model = VisualMousePolicy().eval()
    image = image_tensor(first.image).unsqueeze(0).repeat(2, 1, 1, 1)
    goals = torch.tensor([first.goal_tokens, second.goal_tokens], dtype=torch.int64)
    with torch.inference_mode():
        location, _ = model(image, goals)
    assert not torch.equal(location[0], location[1])
    assert list(inspect.signature(Predictor.predict).parameters) == ['self', 'image', 'goal']


@pytest.mark.parametrize('image', [Image.new('RGB', (1920, 1080)), Image.new('L', (128, 96)), 'screen.png'])
def test_no_silent_real_desktop_resizing(image):
    with pytest.raises(ValueError, match='128×96'):
        image_tensor(image)


def test_prediction_rejects_unsupported_or_multi_action_goals_before_model_use():
    class Forbidden:
        def eval(self):
            return self
        def __call__(self, *args):
            pytest.fail('unsupported goals must not invoke a model')
    predictor = Predictor(Forbidden())
    for goal in ['打开浏览器搜索今天的新闻', '点击付款', '点击红色圆形然后删除文件']:
        with pytest.raises(ValueError):
            predictor.predict(Image.new('RGB', (128, 96)), goal)


def test_decoding_missing_low_confidence_and_invalid_outputs_never_suggest_clicks():
    locations = torch.zeros((1, 193))
    actions = torch.tensor([[10., 0., 0.]])
    result = decode_predictions(locations, actions)[0]
    assert result.proposed_action == 'noop' and result.normalized_xy is None
    locations[0, NULL_LOCATION] = 20
    assert decode_predictions(locations, actions)[0].proposed_action == 'noop'
    locations[0, 0] = float('nan')
    with pytest.raises(ValueError, match='有限'):
        decode_predictions(locations, actions)
    for threshold in [-1, 1.1, float('nan')]:
        with pytest.raises(ValueError):
            decode_predictions(torch.zeros((1, 193)), actions, threshold=threshold)


def test_decoded_coordinates_are_only_synthetic_suggestions():
    locations = torch.full((1, 193), -30.)
    locations[0, 44] = 30
    result = decode_predictions(locations, torch.tensor([[20., 0., -20.]]))[0]
    assert result.proposed_action == 'click' and result.normalized_xy == location_xy(44)
    assert result.as_dict()['execution_permitted'] is False
    assert result.as_dict()['domain'] == 'synthetic-only'


def test_location_coordinate_convention_matches_pixels_at_grid_boundaries():
    for index in range(192):
        assert location_index(location_xy(index)) == index
    assert location_index((63.5 / 127, 44 / 95)) % 16 == 7
    assert location_xy(NULL_LOCATION) is None and location_index(None) == NULL_LOCATION


def test_shape_metric_does_not_count_circle_or_triangle_box_corners():
    for shape in (0, 2):
        sample = make_sample(21, present_probability=1, goal_tokens=(0, shape, 0))
        l, t, r, b = sample.target_box
        assert point_hit(sample, sample.target_xy)
        assert not point_hit(sample, ((l + .1) / 127, (t + .1) / 95))


def test_abstention_is_not_counted_as_success_for_present_targets():
    present = make_sample(12, present_probability=1)
    absent = make_sample(12, present_probability=0)
    result = decode_predictions(torch.zeros((2, 193)), torch.zeros((2, 3)))
    report = metrics([present, absent], result)
    assert report['present_success_rate'] == 0 and report['absent_abstention_rate'] == 1
    assert report['suggestion_precision'] is None


def test_missing_predictions_are_not_silently_dropped_from_metrics(monkeypatch):
    sample = make_sample(12)
    with pytest.raises(ValueError, match='一一对应'):
        metrics([sample], [])
    with pytest.raises(ValueError, match='一一对应'):
        metrics([], [])
    original = __import__('experiments.visual_mouse.training', fromlist=['predictions']).predictions

    def short(model, samples, **options):
        results = original(model, samples, **options)
        return results[:-1] if len(samples) == 4 else results

    monkeypatch.setattr('experiments.visual_mouse.training.predictions', short)
    with pytest.raises(ValueError, match='反事实'):
        evaluate(VisualMousePolicy(), count=8, counterfactual_pairs=2)


def test_regression_gate_rejects_abstention_only_goal_independent_or_low_precision_results():
    from copy import deepcopy
    report = {'domain': 'synthetic-only', 'metrics': {'samples': 512, 'present': 400, 'absent': 112,
        'present_success_rate': .95, 'suggestion_precision': .98, 'absent_abstention_rate': .98},
        'counterfactual_pairs': 64, 'counterfactual_both_correct_rate': .9,
        'wrong_goal_metrics': {'present_success_rate': .02}}
    require_learning(report)
    bad = deepcopy(report)
    bad['metrics']['present_success_rate'] = 0
    bad['metrics']['suggestion_precision'] = None
    with pytest.raises(ValueError, match='未通过'):
        require_learning(bad)
    for field in ('counterfactual_both_correct_rate', 'counterfactual_pairs'):
        bad = deepcopy(report)
        bad[field] = 0
        with pytest.raises(ValueError):
            require_learning(bad)
    bad = deepcopy(report)
    bad['wrong_goal_metrics']['present_success_rate'] = .95
    with pytest.raises(ValueError):
        require_learning(bad)


def test_artifact_round_trip_restores_predictions_without_torch_pickle(tmp_path, monkeypatch):
    model = VisualMousePolicy().eval()
    monkeypatch.setattr(torch, 'load', lambda *args, **kwargs: pytest.fail('no pickle loader'))
    directory = tmp_path / 'model'
    save_artifact(directory, {name: value.detach().numpy() for name, value in model.state_dict().items()}, dict(SAFETY_METADATA))
    restored, metadata = load_policy(directory)
    sample = make_sample(13)
    assert Predictor(restored).predict(sample.image, sample.goal) == Predictor(model).predict(sample.image, sample.goal)
    assert metadata['live_execution_allowed'] is False


def test_small_real_training_pipeline_saves_evaluated_offline_artifact(tmp_path):
    directory = tmp_path / 'learned'
    metadata = train(directory, train_samples=64, eval_samples=16, epochs=2, batch_size=32, threads=1)
    assert len(metadata['training']['losses']) == 2
    assert metadata['training']['losses'][1] < metadata['training']['losses'][0]
    assert metadata['baseline']['seed'] == metadata['validation']['seed'] == 1000042
    assert metadata['live_execution_allowed'] is False
    model, _ = load_policy(directory)
    report = evaluate(model, count=8, seed=314159, counterfactual_pairs=2)
    assert report['metrics']['samples'] == 8 and report['counterfactual_pairs'] == 2
    with pytest.raises(ValueError, match='已存在'):
        train(directory, train_samples=64, eval_samples=16, epochs=1)


@pytest.mark.parametrize('kwargs', [{'train_samples': 1}, {'epochs': 10000}, {'seed': -1},
    {'learning_rate': float('nan')}, {'threads': 20}, {'train_samples': True}])
def test_training_rejects_invalid_or_unbounded_resource_requests(tmp_path, kwargs):
    with pytest.raises(ValueError):
        train(tmp_path / 'new', **kwargs)
    assert not (tmp_path / 'new').exists()
