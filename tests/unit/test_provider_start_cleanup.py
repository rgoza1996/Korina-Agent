"""Failed fallback startup owns its new child, never another provider process."""
from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from korina.services import provider_manager as manager


@pytest.mark.parametrize('ignore_terminate', [False, True])
def test_failed_fallback_reaps_only_new_child(monkeypatch, tmp_path, ignore_terminate):
    original_popen = subprocess.Popen
    ready = tmp_path / 'ready'
    code = (
        'import signal,time\nfrom pathlib import Path\n'
        + ('signal.signal(signal.SIGTERM, signal.SIG_IGN)\n' if ignore_terminate else '')
        + f'Path({str(ready)!r}).touch()\ntime.sleep(60)\n'
    )
    children = []
    unrelated = original_popen([sys.executable, '-c', 'import time;time.sleep(60)'])

    def spawn(args, **kwargs):
        assert args == ['ollama', 'serve']
        child = original_popen([sys.executable, '-c', code], **kwargs)
        children.append(child)
        return child

    failure = RuntimeError('fixture readiness failed')

    def fail_readiness(*args):
        deadline = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), 'owned child must enter its signal policy before failure'
        raise failure

    monkeypatch.setattr(manager.shutil, 'which', lambda name: '/fixture/ollama')
    monkeypatch.setattr(manager, '_systemctl', lambda *args: SimpleNamespace(returncode=1))
    monkeypatch.setattr(manager.subprocess, 'Popen', spawn)
    monkeypatch.setattr(manager, '_wait_for_models', fail_readiness)
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError) as caught:
            manager.start_ollama()
        assert caught.value is failure
        assert len(children) == 1 and children[0].poll() is not None
        assert unrelated.poll() is None
        assert time.monotonic() - started < 5
    finally:
        for child in [*children, unrelated]:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)


def test_early_exit_is_reported_without_signalling_exited_child(monkeypatch):
    calls = []
    process = SimpleNamespace(
        returncode=7, poll=lambda: 7,
        terminate=lambda: calls.append('terminate'), kill=lambda: calls.append('kill'),
    )
    monkeypatch.setattr(manager.shutil, 'which', lambda name: '/fixture/ollama')
    monkeypatch.setattr(manager, '_systemctl', lambda *args: SimpleNamespace(returncode=1))
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(manager, '_wait_for_models', lambda *args: None)
    with pytest.raises(RuntimeError, match='exited during startup.*7'):
        manager.start_ollama()
    assert calls == []


@pytest.mark.parametrize('systemd_started', [False, True])
def test_successful_start_does_not_cleanup_provider(monkeypatch, systemd_started):
    calls = []
    process = SimpleNamespace(
        poll=lambda: None,
        terminate=lambda: calls.append('terminate'), kill=lambda: calls.append('kill'),
    )
    monkeypatch.setattr(manager.shutil, 'which', lambda name: '/fixture/ollama')
    monkeypatch.setattr(manager, '_systemctl', lambda *args: SimpleNamespace(returncode=0 if systemd_started else 1))
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda *args, **kwargs: calls.append('spawn') or process)
    monkeypatch.setattr(manager, '_wait_for_models', lambda *args: None)
    assert manager.start_ollama().started == ['ollama']
    assert calls == ([] if systemd_started else ['spawn'])


def test_systemd_failure_never_signals_existing_service(monkeypatch):
    calls = []
    failure = RuntimeError('fixture readiness failed')
    monkeypatch.setattr(manager.shutil, 'which', lambda name: '/fixture/ollama')
    monkeypatch.setattr(manager, '_systemctl', lambda *args: SimpleNamespace(returncode=0))
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda *args, **kwargs: calls.append('spawn'))
    monkeypatch.setattr(manager, '_cleanup_failed_start', lambda process: calls.append('cleanup'))

    def fail(*args):
        raise failure

    monkeypatch.setattr(manager, '_wait_for_models', fail)
    with pytest.raises(RuntimeError) as caught:
        manager.start_ollama()
    assert caught.value is failure
    assert calls == []


def test_cleanup_error_does_not_mask_startup_error(monkeypatch):
    failure = RuntimeError('fixture readiness failed')

    def cleanup_failure():
        raise OSError('fixture signal error')

    process = SimpleNamespace(poll=lambda: None, terminate=cleanup_failure)
    monkeypatch.setattr(manager.shutil, 'which', lambda name: '/fixture/ollama')
    monkeypatch.setattr(manager, '_systemctl', lambda *args: SimpleNamespace(returncode=1))
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda *args, **kwargs: process)

    def fail(*args):
        raise failure

    monkeypatch.setattr(manager, '_wait_for_models', fail)
    with pytest.raises(RuntimeError) as caught:
        manager.start_ollama()
    assert caught.value is failure
