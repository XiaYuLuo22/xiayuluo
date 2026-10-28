"""Read-only adapter for existing offline demonstration models, never execution.

Only fixed-schema numeric checkpoints below the selected training root/models
are accepted. Screens remain in RAM; no screenshot PNG or model is uploaded.
"""
import math
from pathlib import Path


class OfflineShadowPolicy:
    def __init__(self, model_dir, *, training_root, threshold=.45):
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('本地影子策略阈值必须在0–1之间。')
        self.directory, self.training_root = Path(model_dir), Path(training_root)
        if not self.directory.is_absolute() or not self.training_root.is_absolute():
            raise ValueError('本地模型和训练根必须是已选择的绝对路径。')
        from .training_storage import _plain_ancestors
        _plain_ancestors(self.directory)
        _plain_ancestors(self.training_root)
        root = self.training_root.resolve(strict=True)
        directory = self.directory.resolve(strict=True)
        if (not directory.is_dir() or directory == root / 'models'
                or not directory.is_relative_to(root / 'models')):
            raise ValueError('影子模型必须是所选训练根models下的现有独立目录。')
        self.directory, self.training_root, self.threshold = directory, root, float(threshold)
        self.model = self.metadata = None

    @property
    def ready(self):
        return self.model is not None and self.metadata is not None

    def _load(self):
        if self.model is None:
            from .training_storage import _plain_ancestors
            _plain_ancestors(self.directory)
            # Existing loader verifies bounded JSON/NPZ, exact architecture and
            # live_execution_allowed=false; no pickle or downloaded code runs.
            from experiments.visual_mouse.demonstration_policy import load_demonstration_policy
            self.model, self.metadata = load_demonstration_policy(self.directory)

    def predict(self, image, goal):
        from PIL import Image
        if (not isinstance(image, Image.Image) or image.mode != 'RGB'
                or image.width < 16 or image.height < 16 or image.width * image.height > 16_000_000
                or not isinstance(goal, str) or not goal.strip() or len(goal) > 128):
            raise ValueError('影子策略要求有界RGB画面及明确注册目标。')
        self._load()
        if goal not in self.metadata['goals']:
            return {'proposed_action': 'noop', 'normalized_xy': None, 'confidence': 0.,
                    'execution_permitted': False, 'reason': '目标不在本模型注册表中；不会猜测或执行。'}
        import torch
        from experiments.visual_mouse.model import decode_predictions, image_tensor
        resized = image.resize((128, 96), Image.Resampling.BILINEAR)
        with torch.inference_mode():
            locations, actions = self.model(image_tensor(resized).unsqueeze(0),
                torch.tensor([self.metadata['goals'].index(goal)], dtype=torch.int64))
            result = decode_predictions(locations, actions, threshold=self.threshold)[0].as_dict()
        return {**result, 'domain': 'user-demonstrations-offline', 'execution_permitted': False,
                'reason': '仅低分辨率离线示范策略的影子建议，不允许真实鼠标执行。'}

    def close(self):
        self.model = self.metadata = None
