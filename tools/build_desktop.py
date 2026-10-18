"""Build a Windows onedir app, with temporary build intermediates."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def build_command(output, work):
    return [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
        "--noupx", "--name", "Xiayuluo", "--distpath", str(output),
        "--workpath", str(work / "work"), "--specpath", str(work / "spec"),
        "--paths", str(ROOT), "--collect-all", "webview", "--collect-all", "pythonnet",
        "--collect-all", "clr_loader", "--hidden-import", "clr", "--collect-submodules", "uvicorn",
        "--collect-all", "vosk", "--collect-all", "sounddevice", "--collect-all", "_sounddevice_data",
        "--collect-all", "pyttsx3", "--collect-all", "comtypes", "--hidden-import", "pyttsx3.drivers.sapi5", "--hidden-import", "pythoncom",
        "--collect-submodules", "assistant", "--hidden-import", "webview.platforms.edgechromium",
        "--add-data", f"{ROOT / 'assistant/static'}:assistant/static",
        "--add-data", f"{ROOT / 'assistant/update-config.json'}:assistant",
        "--add-data", f"{ROOT / 'assistant/voice-model'}:assistant/voice-model",
        "--add-data", f"{ROOT / '.env.example'}:.",
        "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.md'}:.", str(ROOT / "tools/desktop_entry.py")]


def collect_licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name", "unknown").replace("/", "_").replace("\\", "_")
        for item in distribution.files or []:
            base = Path(str(item)).name.lower()
            if not base.startswith(("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(item))
            if source.is_file():
                folder = destination / name
                folder.mkdir(exist_ok=True)
                shutil.copy2(source, folder / source.name)
    for candidate in [Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"]:
        if candidate.is_file():
            shutil.copy2(candidate, destination / "Python-LICENSE.txt")
            break


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/desktop")
    parser.add_argument("--dry-run", action="store_true", help="Show the build plan without writing or building")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if args.dry_run:
        print(json.dumps({"required_platform": "Windows", "output": str(output),
            "command": build_command(output, Path("<temporary-build-directory>")),
            "user_data_included": False}, ensure_ascii=False, indent=2))
        return 0
    if sys.platform != "win32":
        raise SystemExit("Windows .exe 必须在 Windows 上构建；Linux 不能交叉打包。可用 --dry-run 检查构建步骤。")
    if not (ROOT / 'assistant/voice-model/am/final.mdl').is_file():
        raise SystemExit("先运行 python tools/prepare_voice_model.py，不能把缺少离线模型的包当作语音版本分发。")
    app = output / "Xiayuluo"
    archive = output / "Xiayuluo-Windows.zip"
    if app.exists() or archive.exists():
        raise SystemExit("目标已存在。为保留已有发行包，请用 --output-dir 指定新的输出目录。")
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "tools/vendor_frontend.py"), "--check"], check=True)
    with TemporaryDirectory(prefix="xiayuluo-build-") as work:
        subprocess.run(build_command(output, Path(work)), check=True, cwd=ROOT)
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
            "--noupx", "--name", "XiayuluoUpdater", "--distpath", str(app),
            "--workpath", str(Path(work) / "updater-work"), "--specpath", str(Path(work) / "updater-spec"),
            "--paths", str(ROOT), str(ROOT / "tools/updater_entry.py")], check=True, cwd=ROOT)
    if not (app / "Xiayuluo.exe").is_file() or not (app / "XiayuluoUpdater.exe").is_file():
        raise SystemExit("构建未生成 Xiayuluo.exe，不能标记为成功。")
    collect_licenses(app / "licenses")
    shutil.copytree(ROOT / 'tools/licenses', app / 'licenses/voice', dirs_exist_ok=True)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", app / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(ROOT / "DESKTOP.md", app / "DESKTOP.md")
    shutil.make_archive(str(output / "Xiayuluo-Windows"), "zip", root_dir=output, base_dir="Xiayuluo")
    print(f"桌面 App 已构建：{archive}\n解压完整文件夹，双击 Xiayuluo.exe。用户配置和记忆未打包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
