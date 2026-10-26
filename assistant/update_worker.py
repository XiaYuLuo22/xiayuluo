"""Standalone Windows helper: wait for the old process, swap directories, roll back on failure."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from tempfile import mkstemp

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


def stop_new_process(process):
    """Stop only this Popen's retained process handle, never a recycled PID."""
    if process is None or process.poll() is not None:
        return
    # Popen.terminate/kill use the actual Windows process HANDLE. taskkill /PID
    # could race process exit and PID reuse, affecting an unrelated application.
    # If a separate descendant keeps binaries locked, rename fails closed and
    # the old binary backup remains; never guess which other process to kill.
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_for_health(process, acknowledgement, nonce, expected_version, *, timeout=45, clock=time.monotonic, sleep=time.sleep):
    """Require a matching native-window-ready acknowledgement, not just Popen."""
    acknowledgement = Path(acknowledgement)
    deadline = clock() + timeout
    while clock() < deadline:
        if process.poll() is not None:
            raise RuntimeError("新版程序在完成启动检查前退出。")
        if acknowledgement.exists():
            if acknowledgement.is_symlink() or not acknowledgement.is_file() or acknowledgement.stat().st_size > 4096:
                raise ValueError("新版启动确认无效。")
            try:
                data = json.loads(acknowledgement.read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                raise ValueError("新版启动确认无法读取。") from error
            if (not isinstance(data, dict) or data != {"nonce": nonce, "version": expected_version, "ready": True}
                    or data.get("ready") is not True):
                raise ValueError("新版启动确认与本次更新不匹配。")
            sleep(.3)
            if process.poll() is not None:
                raise RuntimeError("新版窗口就绪后立即退出，已停止确认更新成功。")
            return
        sleep(.1)
    raise TimeoutError("新版未在限定时间内确认窗口和本地服务就绪。")


def confirm_background_start(directory, nonce, expected_version):
    """After committing the healthy install, allow its background data writers."""
    directory = Path(directory)
    descriptor, name = mkstemp(prefix='.health-accepted-', dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'nonce': nonce, 'version': expected_version, 'accepted': True}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, directory / 'health-accepted.json', follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def swap_install(install, stage, launch=subprocess.Popen, *, protected_paths=(), health_check=None,
                 launch_environment=None, restart_old=False):
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
    process = None
    try:
        rename_with_retry(stage, install)
        options = {"cwd": install}
        if launch_environment is not None:
            options["env"] = launch_environment
        process = launch([str(install / "Xiayuluo.exe")], **options)
        if health_check is not None:
            health_check(process)
    except Exception:
        if process is not None:
            stop_new_process(process)
        if install.exists():
            rename_with_retry(install, stage)
        rename_with_retry(backup, install)
        if restart_old:
            # Do not pass the one-shot probe to the old binary. A failure to
            # relaunch does not undo the restored directory or delete either.
            try:
                launch([str(install / "Xiayuluo.exe")], cwd=install)
            except Exception:
                pass
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
        expected = data.get("version")
        if expected is not None and (type(expected) is not str or not re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}", expected)):
            raise ValueError("更新任务的程序版本无效。")
        options = {}
        if expected is not None:
            nonce = secrets.token_hex(32)
            acknowledgement = job.parent / "health-ready.json"
            if os.path.lexists(acknowledgement):
                raise ValueError("更新启动确认文件已存在，不覆盖。")
            environment = dict(os.environ)
            environment["XIAYULUO_UPDATE_HEALTH"] = json.dumps({"nonce": nonce, "version": expected,
                                                              "acknowledgement": str(acknowledgement)})
            options = {"launch_environment": environment, "restart_old": True,
                       "health_check": lambda process: wait_for_health(process, acknowledgement, nonce, expected)}
        backup = swap_install(data["install"], data["stage"], protected_paths=protected_paths, **options)
        job.with_suffix(".result.json").write_text(json.dumps({"ok": True, "backup": str(backup)}), encoding="utf-8")
        if expected is not None:
            confirm_background_start(job.parent, nonce, expected)
    except Exception as error:
        job.with_suffix(".result.json").write_text(json.dumps({"ok": False, "error": str(error)}), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, "更新未完成，原程序或同级旧版备份已保留。请关闭本次新版窗口后重试；不要删除旧版备份或训练数据。", "夏鱼洛更新", 0x10)
        return 1
    finally:
        job.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
