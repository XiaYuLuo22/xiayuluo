"""Explicit local training/evaluation/prediction; never installs or executes a mouse tool."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='夏鱼洛视觉动作离线学习：合成实验与本人明确提供的示范，不操作真实鼠标。')
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
    demonstration = commands.add_parser('import-demo', help='校验并复制本人提供的 PNG＋任务＋动作标签，不采集屏幕')
    demonstration.add_argument('--manifest', required=True, type=Path)
    demonstration.add_argument('--name', required=True)
    demonstration.add_argument('--data-dir', type=Path)
    demo_training = commands.add_parser('train-demo', help='用持久数据区的示范训练新的离线模型')
    demo_training.add_argument('--dataset', required=True, type=Path)
    demo_training.add_argument('--data-dir', type=Path)
    demo_training.add_argument('--output', type=Path)
    demo_training.add_argument('--epochs', type=int, default=20)
    demo_training.add_argument('--seed', type=int, default=42)
    demo_training.add_argument('--threads', type=int, default=2)
    demo_training.add_argument('--threshold', type=float, default=.45)
    demo_evaluation = commands.add_parser('evaluate-demo', help='单独在示范 validation/test 分区评测，不执行动作')
    demo_evaluation.add_argument('--model', required=True, type=Path)
    demo_evaluation.add_argument('--dataset', required=True, type=Path)
    demo_evaluation.add_argument('--split', choices=('validation', 'test'), default='test')
    demo_evaluation.add_argument('--threshold', type=float, default=.45)
    demo_evaluation.add_argument('--report', type=Path)
    demo_prediction = commands.add_parser('predict-demo', help='仅预测明确提供的 PNG 和已注册任务')
    demo_prediction.add_argument('--model', required=True, type=Path)
    demo_prediction.add_argument('--image', required=True, type=Path)
    demo_prediction.add_argument('--goal', required=True)
    demo_prediction.add_argument('--threshold', type=float, default=.45)
    args = parser.parse_args(argv)
    try:
        if args.command == 'import-demo':
            # Validation/copy does not need Torch; training remains optional.
            from assistant.training_storage import TrainingStorage
            from .demonstrations import import_dataset
            result = import_dataset(args.manifest, TrainingStorage(args.data_dir), args.name)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 0
        from .model import Predictor, load_policy
        from .training import evaluate, require_learning, train
        import torch
        torch.set_num_threads(2)
        if args.command == 'train-demo':
            from assistant.training_storage import TrainingStorage
            from .demonstration_policy import train_demonstrations
            storage = TrainingStorage(args.data_dir)
            output = args.output or storage.model_path()
            if (not args.dataset.is_absolute()
                    or not args.dataset.resolve().is_relative_to((storage.root / 'datasets').resolve())):
                raise ValueError('请先 import-demo 到持久训练数据区，再指定其 dataset.json 训练。')
            if not output.is_absolute() or not output.resolve().is_relative_to((storage.root / 'models').resolve()):
                raise ValueError('示范模型输出必须是持久训练目录 models 下的新绝对路径。')
            storage.ensure()
            result = train_demonstrations(args.dataset, output, epochs=args.epochs, seed=args.seed,
                threads=args.threads, threshold=args.threshold, publication_lock=storage.locked)
            result = {'model_directory': str(output), **result}
        elif args.command == 'evaluate-demo':
            from .demonstrations import load_dataset
            from .demonstration_policy import evaluate_demonstrations, load_demonstration_policy
            if args.report is not None and args.report.exists():
                raise ValueError('评测报告已存在，请选择新文件名。')
            model, metadata = load_demonstration_policy(args.model)
            dataset = load_dataset(args.dataset)
            if metadata.get('dataset_sha256') != dataset.digest or metadata['goals'] != list(dataset.goals):
                raise ValueError('评测必须使用训练时记录的完整示范清单及留出分区；清单改变后不能冒称原留出测试。')
            result = evaluate_demonstrations(model, dataset, split=args.split, threshold=args.threshold)
            if args.report is not None:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                with args.report.open('x', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        elif args.command == 'predict-demo':
            from .demonstration_policy import load_demonstration_policy, predict_demonstration
            model, metadata = load_demonstration_policy(args.model)
            result = predict_demonstration(model, metadata, args.image, args.goal, threshold=args.threshold)
        elif args.command == 'train':
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
