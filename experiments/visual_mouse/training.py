"""CPU imitation experiment and held-out evaluation, without desktop access."""
import math
from contextlib import nullcontext
import random
import sys
import time

import numpy as np
import torch
from torch import nn

from .artifact import save_artifact
from .data import HEIGHT, WIDTH, make_counterfactuals, make_sample
from .model import (ACTION_NAMES, SAFETY_METADATA, VisualMousePolicy,
                    decode_predictions, image_tensor, location_index)


def tensors(samples):
    return (torch.stack([image_tensor(sample.image) for sample in samples]),
            torch.tensor([sample.goal_tokens for sample in samples], dtype=torch.int64),
            torch.tensor([location_index(sample.target_xy) for sample in samples], dtype=torch.int64),
            torch.tensor([ACTION_NAMES.index(sample.action) for sample in samples], dtype=torch.int64))


def point_hit(sample, xy):
    """Evaluation labels only: require actual shape interior, not just its box."""
    if sample.target_box is None or xy is None:
        return False
    x, y = xy[0] * (WIDTH - 1), xy[1] * (HEIGHT - 1)
    left, top, right, bottom = sample.target_box
    if not left < x < right or not top < y < bottom:
        return False
    shape = sample.goal_tokens[1]
    if shape == 0:
        return ((x - (left + right) / 2) / ((right - left) / 2)) ** 2 + (
            (y - (top + bottom) / 2) / ((bottom - top) / 2)) ** 2 < 1
    if shape == 2:
        return abs(x - (left + right) / 2) < (right - left) / 2 * (y - top) / (bottom - top)
    return True


def predictions(model, samples, *, threshold=.45, goals=None, batch_size=64):
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ValueError('预测批量超出实验资源上限。')
    if goals is not None and len(goals) != len(samples):
        raise ValueError('每张图像必须恰好对应一个任务目标。')
    model.eval()
    result = []
    with torch.inference_mode():
        for start in range(0, len(samples), batch_size):
            batch = samples[start:start + batch_size]
            images = torch.stack([image_tensor(sample.image) for sample in batch])
            token_batch = goals[start:start + batch_size] if goals is not None else [sample.goal_tokens for sample in batch]
            locations, actions = model(images, torch.tensor(token_batch, dtype=torch.int64))
            result.extend(decode_predictions(locations, actions, threshold=threshold))
    return result


def metrics(samples, results):
    if not samples or len(samples) != len(results):
        raise ValueError('评测样本与预测必须非空且一一对应。')
    present = sum(sample.action != 'noop' for sample in samples)
    absent = len(samples) - present
    suggested = sum(result.proposed_action != 'noop' for result in results)
    correct = sum(sample.action != 'noop' and result.proposed_action == sample.action
                  and point_hit(sample, result.normalized_xy) for sample, result in zip(samples, results))
    abstained = sum(sample.action == 'noop' and result.proposed_action == 'noop'
                    for sample, result in zip(samples, results))
    return {'samples': len(samples), 'present': present, 'absent': absent,
            'suggestions': suggested, 'correct_suggestions': correct,
            'present_success_rate': correct / present if present else None,
            'suggestion_precision': correct / suggested if suggested else None,
            'absent_abstention_rate': abstained / absent if absent else None}


def evaluate(model, *, count=512, seed=1000042, threshold=.45, counterfactual_pairs=64):
    if type(count) is not int or not 1 <= count <= 4096 or type(seed) is not int or not 0 <= seed < 2 ** 63 - 5000000:
        raise ValueError('评测样本数或种子超出范围。')
    if type(counterfactual_pairs) is not int or not 0 <= counterfactual_pairs <= 512:
        raise ValueError('反事实评测样本数超出范围。')
    samples = [make_sample(seed + index) for index in range(count)]
    results = predictions(model, samples, threshold=threshold)
    raw = predictions(model, samples, threshold=0)
    # Wrong-goal ablation: same images, different goal tokens; original labels
    # remain evaluation-only. A real goal-conditioned policy should get worse.
    wrong_goals = [((sample.goal_tokens[0] + 1) % 4, sample.goal_tokens[1], sample.goal_tokens[2]) for sample in samples]
    wrong = predictions(model, samples, threshold=threshold, goals=wrong_goals)
    pairs = [make_counterfactuals(seed + 2000000 + index) for index in range(counterfactual_pairs)]
    flat = [sample for pair in pairs for sample in pair]
    pair_results = predictions(model, flat, threshold=threshold) if flat else []
    if len(pair_results) != len(flat):
        raise ValueError('反事实预测数量不完整，不能计为成功。')
    both = sum(all(result.proposed_action == sample.action and point_hit(sample, result.normalized_xy)
                   for sample, result in zip(flat[index:index + 2], pair_results[index:index + 2]))
               for index in range(0, len(flat), 2))
    return {'domain': 'synthetic-only', 'seed': seed, 'threshold': threshold,
            'metrics': metrics(samples, results), 'ungated_metrics': metrics(samples, raw),
            'wrong_goal_metrics': metrics(samples, wrong),
            'counterfactual_pairs': counterfactual_pairs,
            'counterfactual_both_correct_rate': both / counterfactual_pairs if counterfactual_pairs else None}


def require_learning(report):
    """Synthetic regression gate, not permission or proof of desktop proficiency."""
    observed = report['metrics']
    checks = (
        report.get('domain') == 'synthetic-only',
        observed['samples'] >= 256 and observed['present'] >= 100 and observed['absent'] >= 30,
        (observed['present_success_rate'] or 0) >= .90,
        (observed['suggestion_precision'] or 0) >= .95,
        (observed['absent_abstention_rate'] or 0) >= .90,
        report['counterfactual_pairs'] >= 32 and (report['counterfactual_both_correct_rate'] or 0) >= .80,
        report['wrong_goal_metrics']['present_success_rate'] is not None
            and report['wrong_goal_metrics']['present_success_rate'] <= .20,
    )
    if not all(checks):
        raise ValueError('合成学习基准未通过；请查看报告，不得冒称已学会任务。')


def train(output, *, train_samples=4096, eval_samples=512, epochs=20, seed=42,
          batch_size=64, learning_rate=.003, threshold=.45, threads=2, publication_lock=None):
    if any(type(value) is not int for value in (train_samples, eval_samples, epochs, seed, batch_size, threads)):
        raise ValueError('训练计数和种子必须为整数。')
    if not (64 <= train_samples <= 16384 and 1 <= eval_samples <= 4096 and 1 <= epochs <= 100
            and 0 <= seed < 2 ** 63 - 6000000 and 8 <= batch_size <= 256 and 1 <= threads <= 4):
        raise ValueError('训练参数超出 CPU 实验资源上限。')
    if epochs * math.ceil(train_samples / batch_size) > 10000:
        raise ValueError('训练步骤过多，超过本次实验上限。')
    if not math.isfinite(learning_rate) or not 0 < learning_rate <= .1:
        raise ValueError('学习率无效。')
    # Fail before expensive training rather than replace an earlier experiment.
    from pathlib import Path
    if Path(output).exists():
        raise ValueError('实验输出目录已存在，请保留原模型并选择新目录。')
    random.seed(seed)
    np.random.seed(seed % (2 ** 32))
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(True)
    model = VisualMousePolicy()
    eval_seed = seed + 1000000
    baseline = evaluate(model, count=eval_samples, seed=eval_seed, threshold=threshold)
    samples = [make_sample(seed + index) for index in range(train_samples)]
    images, goals, locations, actions = tensors(samples)
    del samples
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    loss_function = nn.CrossEntropyLoss()
    started = time.monotonic()
    losses = []
    for epoch in range(epochs):
        model.train()
        permutation = torch.randperm(train_samples)
        total = 0.0
        for start in range(0, train_samples, batch_size):
            indexes = permutation[start:start + batch_size]
            optimizer.zero_grad()
            location_logits, action_logits = model(images[indexes], goals[indexes])
            loss = loss_function(location_logits, locations[indexes]) + .5 * loss_function(action_logits, actions[indexes])
            if not torch.isfinite(loss):
                raise ValueError('训练损失非有限，已停止且未发布模型。')
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            total += loss.item() * len(indexes)
        losses.append(total / train_samples)
        print(f'epoch {epoch + 1}/{epochs} loss={losses[-1]:.5f}', file=sys.stderr, flush=True)
    validation = evaluate(model, count=eval_samples, seed=eval_seed, threshold=threshold)
    metadata = {**SAFETY_METADATA,
        'architecture': 'small-cnn-goal-embeddings-spatial-policy-v1',
        'training': {'seed': seed, 'train_samples': train_samples, 'eval_samples': eval_samples,
                     'eval_seed': eval_seed, 'epochs': epochs, 'batch_size': batch_size,
                     'learning_rate': learning_rate, 'threads': threads,
                     'seconds': round(time.monotonic() - started, 3), 'losses': losses,
                     'torch': torch.__version__, 'numpy': np.__version__},
        'baseline': baseline, 'validation': validation,
        'scope': 'single synthetic image and finite color/shape/action goal; no real desktop validation'}
    with publication_lock() if publication_lock is not None else nullcontext():
        save_artifact(output, {name: tensor.detach().cpu().numpy().astype(np.float32)
                              for name, tensor in model.state_dict().items()}, metadata)
    return metadata
