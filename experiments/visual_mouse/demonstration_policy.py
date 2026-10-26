"""Offline single-step imitation of owner-provided screenshot demonstrations.

The exact-match task embedding is not a language model. Images are resized to
128x96, so this small policy is not evidence of precise real desktop control.
Nothing here captures a screen, listens to input, or executes an action.
"""
import math
from pathlib import Path
import random
import sys
import time

import numpy as np
import torch
from torch import nn

from .artifact import load_artifact, save_artifact
from .demonstrations import DOMAIN, image_bytes, load_dataset, validate_goals
from .model import (ACTION_NAMES, GRID_H, GRID_W, VisualMousePolicy,
                    decode_predictions, image_tensor, location_index)


MODEL_TYPE = 'demonstration-visual-mouse-v1'
ARCHITECTURE = 'small-cnn-exact-goal-embedding-spatial-policy-v1'


class DemonstrationPolicy(nn.Module):
    """Only image tensors and task IDs enter the forward pass, never labels."""
    def __init__(self):
        super().__init__()
        base = VisualMousePolicy()
        self.task = nn.Embedding(32, 20)
        self.encoder = base.encoder
        self.fusion = base.fusion
        self.location = base.location
        self.missing = base.missing
        self.action = base.action

    def forward(self, images, goals):
        if images.ndim != 4 or tuple(images.shape[1:]) != (3, 96, 128):
            raise ValueError('示范策略仅接受预处理的 128×96 RGB 图像张量。')
        if (goals.shape != (images.shape[0],) or goals.dtype != torch.int64
                or torch.any(goals < 0) or torch.any(goals >= 32)):
            raise ValueError('示范策略任务编号无效。')
        goal = self.task(goals)
        features = self.encoder(images)
        conditioned = self.fusion(torch.cat((features, goal[:, :, None, None].expand(
            -1, -1, GRID_H, GRID_W)), dim=1))
        summary = torch.cat((conditioned.mean(dim=(2, 3)), conditioned.amax(dim=(2, 3)), goal), dim=1)
        return (torch.cat((self.location(conditioned).flatten(1), self.missing(summary)), dim=1),
                self.action(summary))


def predict_batch(model, samples, *, threshold=.45, goal_ids=None):
    if goal_ids is not None and len(goal_ids) != len(samples):
        raise ValueError('每张示范画面需要一个任务编号。')
    model.eval()
    results = []
    with torch.inference_mode():
        for offset in range(0, len(samples), 64):
            batch = samples[offset:offset + 64]
            images = torch.stack([image_tensor(sample.image) for sample in batch])
            goals = (goal_ids[offset:offset + 64] if goal_ids is not None
                     else [sample.goal_id for sample in batch])
            locations, actions = model(images, torch.tensor(goals, dtype=torch.int64))
            results.extend(decode_predictions(locations, actions, threshold=threshold))
    return results


def _hit(sample, result, radius=.06):
    return (sample.action != 'noop' and result.proposed_action == sample.action
            and result.normalized_xy is not None
            and math.dist(sample.xy, result.normalized_xy) <= radius)


def _metrics(samples, results):
    present = sum(sample.action != 'noop' for sample in samples)
    absent = len(samples) - present
    suggestions = sum(result.proposed_action != 'noop' for result in results)
    correct = sum(_hit(sample, result) for sample, result in zip(samples, results))
    abstained = sum(sample.action == result.proposed_action == 'noop' for sample, result in zip(samples, results))
    return {'samples': len(samples), 'present': present, 'absent': absent, 'suggestions': suggestions,
            'correct_suggestions': correct,
            'label_coordinate_success_rate': correct / present if present else None,
            'suggestion_precision': correct / suggestions if suggestions else None,
            'noop_abstention_rate': abstained / absent if absent else None,
            'coordinate_radius': .06,
            'success_definition': 'matching action within normalized Euclidean radius of owner label; not verified button bounds'}


def evaluate_demonstrations(model, dataset, *, split='test', threshold=.45):
    if split not in ('validation', 'test'):
        raise ValueError('只能在 validation 或 test 分区报告留出评测。')
    samples = dataset.partition(split)
    results = predict_batch(model, samples, threshold=threshold)
    raw = predict_batch(model, samples, threshold=0)
    wrong = None
    if len(dataset.goals) > 1:
        wrong_goals = [(sample.goal_id + 1) % len(dataset.goals) for sample in samples]
        wrong = _metrics(samples, predict_batch(model, samples, threshold=threshold, goal_ids=wrong_goals))
    # Same actual pixels, different goals and labels; labels remain evaluation-only.
    groups = {}
    for index, sample in enumerate(samples):
        groups.setdefault(sample.pixel_hash, []).append(index)
    pair_count = both = 0
    for indexes in groups.values():
        for left_index, left in enumerate(indexes):
            for right in indexes[left_index + 1:]:
                first, second = samples[left], samples[right]
                if (first.goal_id != second.goal_id and first.action != 'noop' and second.action != 'noop'
                        and first.xy != second.xy):
                    pair_count += 1
                    both += _hit(first, results[left]) and _hit(second, results[right])
    return {'domain': DOMAIN, 'execution_permitted': False, 'split': split,
            'dataset_sha256': dataset.digest, 'threshold': threshold,
            'episodes': len({sample.episode for sample in samples}),
            'metrics': _metrics(samples, results), 'ungated_metrics': _metrics(samples, raw),
            'wrong_goal_original_label_metrics': wrong,
            'counterfactual_pairs': pair_count,
            'counterfactual_both_correct_rate': both / pair_count if pair_count else None,
            'limitations': 'exact task vocabulary; single-step imitation; no real execution, temporal learning or desktop proficiency certification'}


def train_demonstrations(dataset_path, output, *, epochs=20, seed=42, threads=2,
                         threshold=.45, publication_lock=None):
    if (type(epochs) is not int or not 1 <= epochs <= 100 or type(seed) is not int
            or not 0 <= seed < 2 ** 63 - 1 or type(threads) is not int or not 1 <= threads <= 4):
        raise ValueError('示范训练参数超出 CPU 资源上限。')
    if Path(output).exists() or Path(output).is_symlink():
        raise FileExistsError('模型目录已存在，不能覆盖旧模型。')
    dataset = load_dataset(dataset_path)
    samples = dataset.partition('train')
    if epochs * math.ceil(len(samples) / 32) > 10000:
        raise ValueError('示范训练步骤超过资源上限。')
    random.seed(seed)
    np.random.seed(seed % (2 ** 32))
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(True)
    model = DemonstrationPolicy()
    baseline = evaluate_demonstrations(model, dataset, split='validation', threshold=threshold)
    images = torch.stack([image_tensor(sample.image) for sample in samples])
    goals = torch.tensor([sample.goal_id for sample in samples], dtype=torch.int64)
    locations = torch.tensor([location_index(sample.xy) for sample in samples], dtype=torch.int64)
    actions = torch.tensor([ACTION_NAMES.index(sample.action) for sample in samples], dtype=torch.int64)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.003)
    criterion = nn.CrossEntropyLoss()
    started, losses = time.monotonic(), []
    for epoch in range(epochs):
        model.train()
        total = 0.
        permutation = torch.randperm(len(samples))
        for start in range(0, len(samples), 32):
            indexes = permutation[start:start + 32]
            optimizer.zero_grad()
            location_logits, action_logits = model(images[indexes], goals[indexes])
            loss = criterion(location_logits, locations[indexes]) + .5 * criterion(action_logits, actions[indexes])
            if not torch.isfinite(loss):
                raise ValueError('示范训练损失非有限数值，停止且不发布模型。')
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.)
            optimizer.step()
            total += loss.item() * len(indexes)
        losses.append(total / len(samples))
        print(f'demonstration epoch {epoch + 1}/{epochs} loss={losses[-1]:.5f}', file=sys.stderr, flush=True)
    validation = evaluate_demonstrations(model, dataset, split='validation', threshold=threshold)
    metadata = {'model_type': MODEL_TYPE, 'domain': DOMAIN, 'live_execution_allowed': False,
                'image_size': [128, 96], 'architecture': ARCHITECTURE, 'goals': list(dataset.goals),
                'dataset_sha256': dataset.digest,
                'training': {'seed': seed, 'epochs': epochs, 'train_samples': len(samples), 'threads': threads,
                             'seconds': round(time.monotonic() - started, 3), 'losses': losses},
                'baseline': baseline, 'validation': validation,
                'preprocessing': 'RGB bilinear resize to 128x96, whole-image normalized coordinates',
                'scope': 'owner-supplied screenshots, exact task IDs, single-step offline imitation; test split not used for optimization'}
    # A managed CLI supplies this lock so publication cannot race its backups.
    from contextlib import nullcontext
    with publication_lock() if publication_lock is not None else nullcontext():
        save_artifact(output, {name: tensor.detach().cpu().numpy().astype(np.float32)
                              for name, tensor in model.state_dict().items()}, metadata)
    return metadata


def load_demonstration_policy(directory):
    model = DemonstrationPolicy()
    shapes = {name: tuple(tensor.shape) for name, tensor in model.state_dict().items()}
    arrays, metadata = load_artifact(directory, shapes)
    if (metadata['model_type'] != MODEL_TYPE or metadata['domain'] != DOMAIN
            or metadata.get('architecture') != ARCHITECTURE):
        raise ValueError('不是兼容的离线示范模型。')
    validate_goals(metadata.get('goals'))
    model.load_state_dict({name: torch.from_numpy(array) for name, array in arrays.items()}, strict=True)
    return model.eval(), metadata


def predict_demonstration(model, metadata, image_path, goal, *, threshold=.45):
    goals = validate_goals(metadata.get('goals'))
    if not isinstance(goal, str) or goal not in goals:
        raise ValueError('任务未在本模型中注册；不会猜测或执行未训练指令。')
    _, image, size = image_bytes(Path(image_path))
    with torch.inference_mode():
        locations, actions = model(image_tensor(image).unsqueeze(0),
                                  torch.tensor([goals.index(goal)], dtype=torch.int64))
        result = decode_predictions(locations, actions, threshold=threshold)[0].as_dict()
    return {**result, 'domain': DOMAIN, 'execution_permitted': False, 'original_image_size': list(size),
            'reason': '仅离线单步建议；低分辨率、未知界面或不确定结果不能用于真实鼠标执行。'}
