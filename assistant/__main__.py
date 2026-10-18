import argparse
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
    print(f"夏鱼洛开发服务已启动，回环地址 http://127.0.0.1:{settings.port}", flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
