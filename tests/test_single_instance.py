import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from assistant.single_instance import SingleInstance


class FakeKernelObjects:
    def __init__(self, *, owned=True):
        self.owned = owned
        self.signal = Event()
        self.closed = False

    def acquire(self):
        return self.owned

    def wait(self):
        ready = self.signal.wait(.02)
        self.signal.clear()
        return ready

    def close(self):
        self.closed = True


def test_duplicate_never_starts_listener_or_window():
    backend = FakeKernelObjects(owned=False)
    owner = SingleInstance(backend=backend)
    assert owner.acquire() is False
    assert owner.thread is None
    owner.close()
    assert backend.closed


def test_early_activation_waits_for_window_then_restores_and_shows():
    owner = SingleInstance(backend=FakeKernelObjects())
    owner.activate()
    calls = []
    owner.set_window(SimpleNamespace(restore=lambda: calls.append('restore'), show=lambda: calls.append('show')))
    assert calls == ['restore', 'show']
    owner.activate()
    assert calls == ['restore', 'show', 'restore', 'show']
    owner.close()


def test_named_activation_thread_only_restores_existing_window():
    backend = FakeKernelObjects()
    owner = SingleInstance(backend=backend)
    shown = Event()
    calls = []
    try:
        assert owner.acquire()
        owner.set_window(SimpleNamespace(restore=lambda: calls.append('restore'), show=shown.set))
        backend.signal.set()
        assert shown.wait(2)
        assert calls == ['restore']
    finally:
        owner.close()
    assert backend.closed and owner.thread is None


def test_real_process_lock_blocks_duplicate_and_releases_after_close(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    name = 'Local\\Xiayuluo.Test.' + uuid4().hex
    owner = SingleInstance(name=name)
    script = ('from assistant.single_instance import SingleInstance; '
              'import sys; x=SingleInstance(name=sys.argv[1]); print(x.acquire(),flush=True); x.close()')
    try:
        assert owner.acquire()
        duplicate = subprocess.run([sys.executable, '-c', script, name], cwd=Path(__file__).parents[1],
                                   capture_output=True, text=True, timeout=10, env=dict(os.environ))
        assert duplicate.returncode == 0, duplicate.stderr
        assert duplicate.stdout.strip() == 'False'
    finally:
        owner.close()
    reopened = subprocess.run([sys.executable, '-c', script, name], cwd=Path(__file__).parents[1],
                              capture_output=True, text=True, timeout=10, env=dict(os.environ))
    assert reopened.returncode == 0, reopened.stderr
    assert reopened.stdout.strip() == 'True'


def test_process_exit_without_cleanup_does_not_leave_a_stale_lock(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    name = 'Local\\Xiayuluo.Test.' + uuid4().hex
    code = ('from assistant.single_instance import SingleInstance; import os,sys; '
            'x=SingleInstance(name=sys.argv[1]); assert x.acquire(); os._exit(0)')
    exited = subprocess.run([sys.executable, '-c', code, name], timeout=10, env=dict(os.environ))
    assert exited.returncode == 0
    owner = SingleInstance(name=name)
    try:
        assert owner.acquire()
    finally:
        owner.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native activation event')
def test_real_windows_second_process_activates_existing_window():
    name = 'Local\\Xiayuluo.Test.' + uuid4().hex
    owner = SingleInstance(name=name)
    shown = Event()
    try:
        assert owner.acquire()
        owner.set_window(SimpleNamespace(restore=lambda: None, show=shown.set))
        code = ('from assistant.single_instance import SingleInstance; import sys; '
                'x=SingleInstance(name=sys.argv[1]); assert not x.acquire(); x.close()')
        duplicate = subprocess.run([sys.executable, '-c', code, name], timeout=10)
        assert duplicate.returncode == 0
        assert shown.wait(3)
    finally:
        owner.close()


def test_duplicate_entry_returns_before_config_or_service(monkeypatch):
    from assistant import desktop_app
    calls = []
    monkeypatch.setattr(desktop_app, 'SingleInstance', lambda: SimpleNamespace(
        acquire=lambda: False, close=lambda: calls.append('closed')))
    monkeypatch.setattr(desktop_app, 'prepare_settings', lambda path: pytest.fail('duplicate must not load config'))
    monkeypatch.setattr(desktop_app, 'run_window', lambda *args, **kwargs: pytest.fail('duplicate must not start service'))
    assert desktop_app.main() == 0
    assert calls == ['closed']


def test_startup_failure_releases_instance(monkeypatch):
    from assistant import desktop_app
    calls = []
    monkeypatch.setattr(desktop_app, 'SingleInstance', lambda: SimpleNamespace(
        acquire=lambda: True, close=lambda: calls.append('closed')))

    def fail(path):
        raise RuntimeError('fixture startup failed')

    monkeypatch.setattr(desktop_app, 'prepare_settings', fail)
    monkeypatch.setattr(desktop_app, 'show_error', lambda message: calls.append('error'))
    assert desktop_app.main() == 1
    assert calls == ['error', 'closed']
