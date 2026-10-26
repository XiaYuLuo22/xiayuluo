"""Explicit local training/evaluation/prediction; never installs or executes a mouse tool."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='夏鱼洛视觉动作学习原型：仅合成界面，不操作真实鼠标。')
    commands = parser.add_subparsers(dest='command', required=True)
    learning = commands.add_parser('train')
    learning.add_argument('--output', type=Path, help='可选：持久训练目录 models 下的新绝对路径')
    learning.add_argument('--data-dir', type=Path, help='可选：版本无关的持久数据根（绝对路径）')
    learning.add_argument('--train-samples', type=int, default=4096)
    learning.add_argument('--eval-samples', type=int, default=512)
    learning.add_argument('--epochs', type=int, default=20)
    learning.add_argument('--seed', type=int, default=42)
    learning.add_argument('--threads', type=int, default=2)
    learning.add_argument('--threshold', type=float, default=.45)
    evaluation = commands.add_parser('evaluate')
    evaluation.add_argument('--model', required=True, type=Path)
    evaluation.add_argument('--samples', type=int, default=512)
    evaluation.add_argument('--seed', type=int, default=3000042)
    evaluation.add_argument('--threshold', type=float, default=.45)
    evaluation.add_argument('--report', type=Path)
    evaluation.add_argument('--require-learning', action='store_true')
    prediction = commands.add_parser('predict')
    prediction.add_argument('--model', required=True, type=Path)
    prediction.add_argument('--image', required=True, type=Path)
    prediction.add_argument('--goal', required=True)
    prediction.add_argument('--threshold', type=float, default=.45)
    visualization = commands.add_parser('preview')
    visualization.add_argument('--model', required=True, type=Path)
    visualization.add_argument('--output', required=True, type=Path)
    visualization.add_argument('--seed', type=int, default=12345)
    visualization.add_argument('--threshold', type=float, default=.45)
    args = parser.parse_args(argv)
    try:
        from .model import Predictor, load_policy
        from .training import evaluate, require_learning, train
        import torch
        torch.set_num_threads(2)
        if args.command == 'train':
            from assistant.training_storage import TrainingStorage
            storage = TrainingStorage(args.data_dir)
            output = args.output or storage.model_path()
            if not output.is_absolute() or not output.resolve().is_relative_to((storage.root / 'models').resolve()):
                raise ValueError('模型输出必须位于持久训练目录的 models 下；请用 --data-dir 指定数据根，不能存进安装目录或 dist。')
            storage.ensure()
            result = train(output, train_samples=args.train_samples, eval_samples=args.eval_samples,
                epochs=args.epochs, seed=args.seed, threads=args.threads, threshold=args.threshold,
                publication_lock=storage.locked)
            result = {'model_directory': str(output), **result}
        elif args.command == 'evaluate':
            if args.report is not None and args.report.exists():
                raise ValueError('评测报告已存在，请选择新文件名。')
            model, _ = load_policy(args.model)
            result = evaluate(model, count=args.samples, seed=args.seed, threshold=args.threshold)
            if args.report is not None:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                with args.report.open('x', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            if args.require_learning:
                require_learning(result)
        elif args.command == 'preview':
            from .preview import preview
            model, _ = load_policy(args.model)
            result = preview(model, args.output, seed=args.seed, threshold=args.threshold)
        else:
            from PIL import Image
            from .data import HEIGHT, WIDTH
            model, _ = load_policy(args.model)
            with Image.open(args.image) as source:
                if source.format != 'PNG' or source.size != (WIDTH, HEIGHT):
                    raise ValueError('仅接受 128×96 合成 PNG，不自动缩放真实桌面截图。')
                result = Predictor(model, threshold=args.threshold).predict(source.convert('RGB'), args.goal).as_dict()
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except ImportError:
        print('缺少可选训练依赖，请按 VISION_LEARNING.md 安装 CPU PyTorch 和 NumPy。', file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(f'实验未完成：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
