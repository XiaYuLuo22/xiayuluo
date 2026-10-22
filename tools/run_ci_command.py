"""Stream a CI build normally and expose a failing tail as a check annotation."""
import subprocess
import sys


def main(command):
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
        print(f'::error title=Windows build command failed::{escaped}', flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
