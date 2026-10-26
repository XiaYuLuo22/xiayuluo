"""Standalone Windows helper: wait for the old process, swap directories, roll back on failure."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from .training_storage import TRAINING_MARKER, assert_training_separate


def rename_with_retry(source, target):
    # PyInstaller's bootloader/antivirus may release executable handles slightly later.
    deadline = time.monotonic() + 10
    while True:
        try:
            source.rename(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.2)


def swap_install(install, stage, launch=subprocess.Popen, *, protected_paths=()):
    # Repeat the App's guard in the standalone helper; never trust a prior UI
    # check or an update job whose configured paths have since changed.
    assert_training_separate(install, protected_paths)
    assert_training_separate(stage, protected_paths)
    install, stage = Path(install).resolve(), Path(stage).resolve()
    user_data = {".env", ".assistant", ".venv", ".git", "training", ".vision-learning", TRAINING_MARKER}
    if (install.parent != stage.parent or not stage.name.startswith(f".{install.name}.update-")
            or not install.is_dir() or not (stage / "Xiayuluo.exe").is_file()
            or any(os.path.lexists(root / name) for root in (install, stage) for name in user_data)
            or any(item.name.casefold() in user_data for root in (install, stage) for item in root.iterdir())):
        raise ValueError("更新目录无效或含用户数据。")
    # Inno Setup's per-user uninstaller must remain at its registered path.
    # Keep only its exact metadata files, never arbitrary old application files.
    for source in install.iterdir():
        if re.fullmatch(r"unins[0-9]{3}\.(exe|dat|msg)", source.name, re.IGNORECASE):
            target = stage / source.name
            if source.is_symlink() or not source.is_file() or target.exists():
                raise ValueError("卸载器元数据冲突，保留原版。")
            shutil.copy2(source, target)
    backup = install.with_name(f".{install.name}.backup-{uuid.uuid4().hex}")
    rename_with_retry(install, backup)
    try:
        rename_with_retry(stage, install)
        launch([str(install / "Xiayuluo.exe")], cwd=install)
    except Exception:
        if install.exists():
            rename_with_retry(install, stage)
        rename_with_retry(backup, install)
        raise
    # Retain the old binary directory for manual recovery; never delete user backups.
    return backup


def wait_for_process(pid, timeout=120000):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # Already exited, not access denied.
            return
        raise OSError("无法等待旧版 App 退出。")
    try:
        if kernel.WaitForSingleObject(handle, timeout) != 0:
            raise TimeoutError("旧版 App 尚未退出，未更改安装。")
    finally:
        kernel.CloseHandle(handle)


def main():
    job = Path(sys.argv[1])
    try:
        data = json.loads(job.read_text(encoding="utf-8"))
        protected_paths = data.get("protected_paths", [])
        if (type(protected_paths) is not list or len(protected_paths) > 16
                or any(type(path) is not str or not path or len(path) > 32767 for path in protected_paths)):
            raise ValueError("更新任务的数据保护路径无效。")
        wait_for_process(int(data["pid"]))
        backup = swap_install(data["install"], data["stage"], protected_paths=protected_paths)
        job.with_suffix(".result.json").write_text(json.dumps({"ok": True, "backup": str(backup)}), encoding="utf-8")
    except Exception as error:
        job.with_suffix(".result.json").write_text(json.dumps({"ok": False, "error": str(error)}), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, "更新未完成，原版已保留。请重新打开夏鱼洛后重试。", "夏鱼洛更新", 0x10)
        return 1
    finally:
        job.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
