import argparse
import sys
import uvicorn

from .config import Settings
from .server import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description="夏鱼洛桌面助手")
    parser.add_argument("--serve", action="store_true", help="仅启动本地服务，供无图形环境的开发与测试使用")
    parser.add_argument("--debug", action="store_true", help="启用桌面内嵌界面的开发者工具")
    args = parser.parse_args(argv)
    if not args.serve:
        from .desktop_app import main as desktop_main
        return desktop_main(debug=args.debug)
    settings = Settings.load()
    # Frozen/windowed executables may expose a legacy cp1252 stream even when
    # stdout is redirected. This developer-only banner is not an App UI.
    if not getattr(sys, "frozen", False) and sys.stdout is not None:
        print(f"夏鱼洛开发服务已启动，回环地址 http://127.0.0.1:{settings.port}", flush=True)
    options = {"host": "127.0.0.1", "port": settings.port}
    if getattr(sys, "frozen", False) and sys.stderr is None:
        options["log_config"] = None  # Windowed builds have no stream for isatty().
    uvicorn.run(create_app(settings), **options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
