"""Per-login-session desktop ownership, independent of the installation path.

Windows kernel objects disappear after the last owning process exits; no stale
PID files, public HTTP endpoints, or credentials are used to activate a window.
"""
import ctypes
import os
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time


class _WindowsObjects:
    def __init__(self, name):
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for method, result, args in (
            ('CreateMutexW', wintypes.HANDLE, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]),
            ('CreateEventW', wintypes.HANDLE, [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]),
            ('OpenEventW', wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]),
            ('SetEvent', wintypes.BOOL, [wintypes.HANDLE]),
            ('WaitForSingleObject', wintypes.DWORD, [wintypes.HANDLE, wintypes.DWORD]),
            ('CloseHandle', wintypes.BOOL, [wintypes.HANDLE]),
        ):
            function = getattr(kernel, method)
            function.restype, function.argtypes = result, args
        self.kernel, self.name = kernel, name
        self.mutex = self.event = None

    def acquire(self):
        self.mutex = self.kernel.CreateMutexW(None, False, self.name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None
            # The first process may still be between mutex and event creation.
            deadline = time.monotonic() + 2
            while True:
                event = self.kernel.OpenEventW(0x0002, False, self.name + '.Activate')
                if event:
                    try:
                        self.kernel.SetEvent(event)
                    finally:
                        self.kernel.CloseHandle(event)
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(.025)
            return False
        self.event = self.kernel.CreateEventW(None, False, False, self.name + '.Activate')
        if not self.event:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        return True

    def wait(self):
        result = self.kernel.WaitForSingleObject(self.event, 200)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def close(self):
        for name in ('event', 'mutex'):
            handle = getattr(self, name)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)


class SingleInstance:
    def __init__(self, *, name=r'Local\Xiayuluo.Desktop.v1', backend=None):
        self.backend = backend if backend is not None else (_WindowsObjects(name) if sys.platform == 'win32' else None)
        self.file = None
        self.window = None
        self.pending = False
        self.lock = Lock()
        self.stopping = Event()
        self.thread = None

    def acquire(self):
        if self.backend:
            if not self.backend.acquire():
                return False
            self.thread = Thread(target=self._listen, name='xiayuluo-window-activation', daemon=True)
            self.thread.start()
            return True
        # Developer GUI on Unix also has one owner; Windows is the distributed App.
        import fcntl
        root = Path(os.getenv('XDG_RUNTIME_DIR') or Path.home() / '.cache') / 'xiayuluo'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = (root / 'desktop.lock').open('a+b')
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            self.file = None
            return False
        return True

    def _listen(self):
        while not self.stopping.is_set():
            try:
                if self.backend.wait():
                    self.activate()
            except OSError:
                return

    def activate(self):
        with self.lock:
            window = self.window
            if window is None:
                self.pending = True
                return
        try:
            # pywebview marshals these calls onto the native UI thread.
            window.restore()
            window.show()  # Windows adapter also calls Activate().
        except Exception:
            pass  # A concurrent close must never open a second window.

    def set_window(self, window):
        with self.lock:
            self.window = window
            pending, self.pending = self.pending, False
        if pending:
            self.activate()

    def close(self):
        self.stopping.set()
        with self.lock:
            self.window = None
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
        if self.backend:
            self.backend.close()
        if self.file:
            self.file.close()  # Advisory lock released, including on process exit.
            self.file = None
