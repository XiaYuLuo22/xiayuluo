"""A newly authored, small image-and-goal policy; never a desktop executor.

This first experiment learns one action on synthetic shapes. It has no OCR,
object-box input, pretrained weights, language model or temporal-training claim.
"""
from dataclasses import dataclass
import math

import numpy as np
from PIL import Image
import torch
from torch import nn

from .data import HEIGHT, WIDTH, parse_goal


GRID_W, GRID_H = 16, 12
NULL_LOCATION = GRID_W * GRID_H
ACTION_NAMES = ('click', 'move', 'noop')
SAFETY_METADATA = {
    'model_type': 'synthetic-visual-mouse-v1', 'domain': 'synthetic-only',
    'live_execution_allowed': False, 'image_size': [WIDTH, HEIGHT],
}


class VisualMousePolicy(nn.Module):
    """Pixels plus finite goal tokens -> location distribution and action class."""
    def __init__(self):
        super().__init__()
        self.color = nn.Embedding(4, 8)
        self.shape = nn.Embedding(3, 8)
        self.intent = nn.Embedding(2, 4)
        self.encoder = nn.Sequential(
            nn.AvgPool2d(2),
            nn.Conv2d(3, 12, 5, stride=2, padding=2), nn.ReLU(),
            nn.Conv2d(12, 24, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(24, 24, 3, padding=1), nn.ReLU(),
        )
        self.fusion = nn.Sequential(nn.Conv2d(44, 32, 1), nn.ReLU(),
                                    nn.Conv2d(32, 32, 1), nn.ReLU())
        self.location = nn.Conv2d(32, 1, 1)
        self.missing = nn.Sequential(nn.Linear(84, 32), nn.ReLU(), nn.Linear(32, 1))
        self.action = nn.Sequential(nn.Linear(84, 32), nn.ReLU(), nn.Linear(32, 3))

    def forward(self, images, goals):
        if images.ndim != 4 or tuple(images.shape[1:]) != (3, HEIGHT, WIDTH):
            raise ValueError('实验只接受 128×96 RGB 图像张量。')
        if goals.ndim != 2 or goals.shape != (images.shape[0], 3) or goals.dtype != torch.int64:
            raise ValueError('实验目标必须是每张图三个整数词表编号。')
        features = self.encoder(images)
        goal = torch.cat((self.color(goals[:, 0]), self.shape(goals[:, 1]),
                          self.intent(goals[:, 2])), dim=1)
        conditioned = self.fusion(torch.cat((features, goal[:, :, None, None].expand(
            -1, -1, GRID_H, GRID_W)), dim=1))
        summary = torch.cat((conditioned.mean(dim=(2, 3)), conditioned.amax(dim=(2, 3)), goal), dim=1)
        locations = torch.cat((self.location(conditioned).flatten(1), self.missing(summary)), dim=1)
        return locations, self.action(summary)


def image_tensor(image):
    if not isinstance(image, Image.Image) or image.size != (WIDTH, HEIGHT) or image.mode != 'RGB':
        raise ValueError('实验只接受 128×96 RGB 图像；不会自动缩放真实桌面。')
    array = np.array(image, dtype=np.float32, copy=True) / 255.0
    return torch.from_numpy(array.transpose(2, 0, 1).copy())


def location_index(xy):
    if xy is None:
        return NULL_LOCATION
    x, y = xy
    if not all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 for value in xy):
        raise ValueError('训练位置必须是有限归一化坐标。')
    return min(GRID_H - 1, int(y * (HEIGHT - 1) / 8)) * GRID_W + min(GRID_W - 1, int(x * (WIDTH - 1) / 8))


def location_xy(index):
    if type(index) is not int or not 0 <= index <= NULL_LOCATION:
        raise ValueError('预测位置编号无效。')
    if index == NULL_LOCATION:
        return None
    return ((index % GRID_W + .5) * 8 / (WIDTH - 1),
            (index // GRID_W + .5) * 8 / (HEIGHT - 1))


@dataclass(frozen=True)
class Prediction:
    proposed_action: str
    normalized_xy: tuple[float, float] | None
    confidence: float
    reason: str

    def as_dict(self):
        return {'domain': 'synthetic-only', 'execution_permitted': False,
                'proposed_action': self.proposed_action, 'normalized_xy': self.normalized_xy,
                'confidence': self.confidence, 'reason': self.reason}


def decode_predictions(location_logits, action_logits, *, threshold=.45):
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('预测置信度门槛必须介于 0 和 1。')
    if not torch.isfinite(location_logits).all() or not torch.isfinite(action_logits).all():
        raise ValueError('模型输出非有限数值，拒绝预测。')
    if (location_logits.ndim != 2 or location_logits.shape[1] != NULL_LOCATION + 1
            or action_logits.shape != (location_logits.shape[0], 3)):
        raise ValueError('模型输出维度不匹配。')
    location_prob = torch.softmax(location_logits, dim=1)
    action_prob = torch.softmax(action_logits, dim=1)
    loc_conf, loc_index = location_prob.max(dim=1)
    act_conf, act_index = action_prob.max(dim=1)
    result = []
    for location, action, confidence in zip(loc_index.tolist(), act_index.tolist(),
            torch.minimum(loc_conf, act_conf).tolist()):
        if location == NULL_LOCATION or action == 2:
            result.append(Prediction('noop', None, confidence, '模型预测目标缺失或不操作。'))
        elif confidence < threshold:
            result.append(Prediction('noop', None, confidence, '模型不够确定，放弃动作建议。'))
        else:
            result.append(Prediction(ACTION_NAMES[action], location_xy(location), confidence,
                                     '仅为合成界面实验预测，禁止用于真实鼠标执行。'))
    return result


class Predictor:
    """Dry-run API deliberately accepts no Sample, label, target box or driver."""
    def __init__(self, model, *, threshold=.45):
        self.model = model.eval()
        self.threshold = threshold

    def predict(self, image, goal):
        tokens = parse_goal(goal).tokens
        with torch.inference_mode():
            locations, actions = self.model(image_tensor(image).unsqueeze(0),
                torch.tensor([tokens], dtype=torch.int64))
            return decode_predictions(locations, actions, threshold=self.threshold)[0]


def load_policy(directory):
    from .artifact import load_artifact
    model = VisualMousePolicy()
    shapes = {name: tuple(tensor.shape) for name, tensor in model.state_dict().items()}
    arrays, metadata = load_artifact(directory, shapes)
    model.load_state_dict({name: torch.from_numpy(array) for name, array in arrays.items()}, strict=True)
    return model.eval(), metadata
