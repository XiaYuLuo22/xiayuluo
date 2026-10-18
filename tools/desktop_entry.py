"""PyInstaller entry point; never includes user configuration or memory data."""
from multiprocessing import freeze_support

from assistant.__main__ import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
