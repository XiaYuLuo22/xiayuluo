"""PyInstaller entry point; never includes user configuration or memory data."""
from multiprocessing import freeze_support
import os
from pathlib import Path
import sys
import traceback


def run():
    freeze_support()
    try:
        from assistant.__main__ import main
        return main()
    except BaseException:
        # This diagnostic is only for an isolated build smoke run. Normal user
        # launches retain the original exception behavior and create no report.
        if os.environ.get("ASSISTANT_PACKAGED_SMOKE") == "1" and "--serve" in sys.argv:
            diagnostic = traceback.format_exc()
            try:
                with (Path.cwd() / "packaged-smoke-error.txt").open("x", encoding="utf-8") as output:
                    output.write(diagnostic)
            except OSError:
                pass  # Never replace a pre-existing file or conceal the exception.
        raise


if __name__ == "__main__":
    raise SystemExit(run())
