"""Stream a CI build normally and expose a failing tail as a check annotation."""
import argparse
import subprocess
import sys


def main(command, *, title='Windows build command failed'):
    tail = ''
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding='utf-8', errors='replace') as process:
        for line in process.stdout:
            print(line, end='', flush=True)
            tail = (tail + line)[-20000:]
        code = process.wait()
    if code:
        # Check annotations are truncated by GitHub. Keep the actual final error,
        # rather than the beginning of a much longer PyInstaller analysis tail.
        tail = tail.encode('utf-8')[-3000:].decode('utf-8', errors='replace')
        escaped = tail.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        safe_title = title.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A').replace(',', '%2C').replace(':', '%3A')
        print(f'::error title={safe_title}::{escaped}', flush=True)
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--annotation-title', default='Windows build command failed')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if not arguments.command:
        parser.error('a command is required')
    raise SystemExit(main(arguments.command, title=arguments.annotation_title))
