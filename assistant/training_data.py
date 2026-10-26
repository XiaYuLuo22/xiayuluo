"""Explicit local training backup/recovery utility. Never collects or executes data."""
import argparse
import json
from pathlib import Path
import sys

from .training_backup import restore_to_new_directory, verify_backup
from .training_storage import TrainingStorage


def main(argv=None):
    parser = argparse.ArgumentParser(description='夏鱼洛训练数据：状态、备份、校验和恢复副本；不删除原文件。')
    parser.add_argument('--root', type=Path, help='可选：已配置的持久训练数据根，必须为绝对路径')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    commands.add_parser('backup')
    verification = commands.add_parser('verify')
    verification.add_argument('archive', type=Path)
    recovery = commands.add_parser('restore')
    recovery.add_argument('archive', type=Path)
    recovery.add_argument('--to', required=True, type=Path, help='必须是不存在的新绝对目录，不覆盖原目录或切换登记')
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            result = verify_backup(args.archive)
        elif args.command == 'restore':
            if not args.to.is_absolute():
                raise ValueError('恢复目标必须是新的绝对目录。')
            result = restore_to_new_directory(args.archive, args.to)
        else:
            storage = TrainingStorage(args.root)
            result = storage.status() if args.command == 'status' else storage.backup()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ready', True) else 1
    except (OSError, ValueError) as error:
        print(f'训练数据操作未完成，原文件保留：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
