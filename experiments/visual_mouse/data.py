"""Small, reproducible screen-and-goal imitation tasks without desktop access.

The supported language is deliberately finite: four colors, three shapes, and
click/move goals. Rendering metadata supplies *training labels only*. A policy
must receive the RGB image and goal tokens, never object bounds or labels.
Nothing in this module opens files, captures a screen, or controls an input
device. These generated shapes are not evidence of real desktop proficiency.
"""
from dataclasses import dataclass
import random
import re

from PIL import Image, ImageDraw


WIDTH, HEIGHT = 128, 96
COLORS = ('red', 'green', 'blue', 'yellow')
SHAPES = ('circle', 'square', 'triangle')
ACTIONS = ('click', 'move')
COLOR_ZH = ('红色', '绿色', '蓝色', '黄色')
SHAPE_ZH = ('圆形', '方形', '三角形')
COLOR_RGB = ((220, 58, 55), (40, 176, 88), (46, 105, 225), (230, 191, 42))
_ATTRIBUTES = tuple((color, shape) for color in range(4) for shape in range(3))


@dataclass(frozen=True)
class Goal:
    color_id: int
    shape_id: int
    action_id: int

    def __post_init__(self):
        for value, limit in ((self.color_id, 4), (self.shape_id, 3), (self.action_id, 2)):
            if type(value) is not int or not 0 <= value < limit:
                raise ValueError('目标超出实验支持的颜色、形状或动作词表。')

    @property
    def tokens(self):
        return self.color_id, self.shape_id, self.action_id


@dataclass(frozen=True)
class Sample:
    image: Image.Image
    goal: str
    goal_tokens: tuple[int, int, int]
    action: str
    # Coordinates use x/(WIDTH-1), y/(HEIGHT-1); None means no action.
    target_xy: tuple[float, float] | None
    # Inclusive pixel bounds used only to assess synthetic training/evaluation.
    target_box: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class _Object:
    color_id: int
    shape_id: int
    box: tuple[int, int, int, int]


def _goal(value):
    if isinstance(value, Goal):
        return value
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError('实验目标必须是三个整数编号。')
    return Goal(*value)


def goal_text(tokens: tuple[int, int, int] | Goal, language='zh'):
    """Canonical descriptions; this is not an unrestricted language model."""
    goal = _goal(tokens)
    color, shape, action = goal.tokens
    if language == 'zh':
        prefix = '点击' if action == 0 else '移动到'
        return prefix + COLOR_ZH[color] + SHAPE_ZH[shape] + '按钮'
    if language == 'en':
        prefix = 'click' if action == 0 else 'move to'
        return f'{prefix} the {COLORS[color]} {SHAPES[shape]} button'
    raise ValueError('实验目标仅支持 zh 或 en 模板。')


def parse_goal(text: str) -> Goal:
    """Accept only documented whole-string templates; ambiguous goals fail closed."""
    if not isinstance(text, str) or not 1 <= len(text) <= 120:
        raise ValueError('请输入实验支持的单个颜色、形状和点击/移动目标。')
    value = re.sub(r'\s+', ' ', text).strip().rstrip('。.!！').strip()
    chinese = re.fullmatch(
        r'(?:请)?(点击|移动到|把鼠标移到)(红色|绿色|蓝色|黄色)(圆形|方形|三角形)(?:按钮)?', value)
    if chinese:
        action, color, shape = chinese.groups()
        return Goal(COLOR_ZH.index(color), SHAPE_ZH.index(shape), 0 if action == '点击' else 1)
    english = re.fullmatch(
        r'(?:please )?(click|move to|move mouse to) (?:the )?'
        r'(red|green|blue|yellow) (circle|square|triangle)(?: button)?', value.casefold())
    if english:
        action, color, shape = english.groups()
        return Goal(COLORS.index(color), SHAPES.index(shape), 0 if action == 'click' else 1)
    raise ValueError('目标不在实验词表内；支持如“点击红色圆形按钮”，不支持任意桌面任务。')


def _rng(seed):
    if type(seed) is not int or not 0 <= seed < 2 ** 63:
        raise ValueError('实验种子必须是非负的 63 位整数。')
    return random.Random(seed)


def _render(rng, attributes):
    # A 3x2 coarse layout makes overlap impossible; object sizes, cell occupancy,
    # pixel positions, colors and background are independently jittered.
    background = rng.randint(225, 248)
    tint = tuple(max(0, min(255, background + rng.randint(-4, 4))) for _ in range(3))
    image = Image.new('RGB', (WIDTH, HEIGHT), tint)
    draw = ImageDraw.Draw(image)
    for row in range(HEIGHT):
        shift = int((row / (HEIGHT - 1) - .5) * rng.randint(0, 6))
        draw.line((0, row, WIDTH - 1, row), fill=tuple(max(0, min(255, value + shift)) for value in tint))
    objects = []
    cells = rng.sample(range(6), len(attributes))
    for (color_id, shape_id), cell in zip(attributes, cells):
        col, row = cell % 3, cell // 3
        size = rng.randint(24, 30)
        left = col * 40 + rng.randint(4, 36 - size)
        top = row * 44 + rng.randint(5, 39 - size)
        box = left, top, left + size, top + size
        fill = tuple(max(0, min(255, value + rng.randint(-10, 10))) for value in COLOR_RGB[color_id])
        outline = tuple(max(0, value - 25) for value in fill)
        if shape_id == 0:
            draw.ellipse(box, fill=fill, outline=outline, width=1)
        elif shape_id == 1:
            draw.rectangle(box, fill=fill, outline=outline, width=1)
        else:
            draw.polygon(((left + size / 2, top), (left + size, top + size), (left, top + size)),
                         fill=fill, outline=outline, width=1)
        objects.append(_Object(color_id, shape_id, box))
    return image, objects


def _sample(image, objects, goal, language):
    target = next((item for item in objects
                   if (item.color_id, item.shape_id) == goal.tokens[:2]), None)
    if target is None:
        return Sample(image, goal_text(goal, language), goal.tokens, 'noop', None, None)
    left, top, right, bottom = target.box
    xy = ((left + right) / 2 / (WIDTH - 1), (top + bottom) / 2 / (HEIGHT - 1))
    return Sample(image, goal_text(goal, language), goal.tokens, ACTIONS[goal.action_id], xy, target.box)


def make_sample(seed: int, *, present_probability=.8,
                goal_tokens: tuple[int, int, int] | Goal | None = None, language='zh') -> Sample:
    """Create a deterministic task with four to six unique non-overlapping objects.

    A missing target is labeled noop, not a click on a similarly colored object.
    Different seed ranges can be used for training, validation and final testing.
    """
    if (type(present_probability) not in (int, float)
            or not 0 <= present_probability <= 1):
        raise ValueError('目标出现概率必须介于 0 和 1。')
    rng = _rng(seed)
    goal = _goal(goal_tokens) if goal_tokens is not None else Goal(rng.randrange(4), rng.randrange(3), rng.randrange(2))
    requested = goal.tokens[:2]
    visible = rng.random() < present_probability
    count = rng.randint(4, 6)
    other = rng.sample([value for value in _ATTRIBUTES if value != requested], count - int(visible))
    attributes = other + ([requested] if visible else [])
    rng.shuffle(attributes)
    image, objects = _render(rng, attributes)
    return _sample(image, objects, goal, language)


def make_counterfactuals(seed: int, *, language='zh') -> tuple[Sample, Sample]:
    """One identical image, two present click goals and distinct ground-truth targets."""
    rng = _rng(seed)
    attributes = rng.sample(_ATTRIBUTES, rng.randint(4, 6))
    image, objects = _render(rng, attributes)
    first, second = rng.sample(objects, 2)
    return (_sample(image, objects, Goal(first.color_id, first.shape_id, 0), language),
            _sample(image, objects, Goal(second.color_id, second.shape_id, 0), language))
