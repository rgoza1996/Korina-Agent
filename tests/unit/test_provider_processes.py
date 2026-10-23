from __future__ import annotations

import os
import socket
from pathlib import Path

from korina.services import provider_processes as processes


def test_listens_on_checks_socket_ownership_not_just_port(tmp_path, monkeypatch):
    monkeypatch.setattr(processes, 'PROC_ROOT', tmp_path)
    proc = tmp_path / '123'
    (proc / 'net').mkdir(parents=True)
    (proc / 'fd').mkdir()
    (proc / 'net/tcp').write_text(
        'header\n0: 0100007F:04D2 00000000:0000 0A 0 0 0 0 0 456\n'
    )
    descriptor = proc / 'fd/3'
    descriptor.symlink_to('socket:[999]')
    assert processes.listens_on(123, 1234) is False
    descriptor.unlink()
    descriptor.symlink_to('socket:[456]')
    assert processes.listens_on(123, 1234) is True
    assert processes.listens_on(123, 8080) is False


def test_listens_on_real_socket():
    if not Path('/proc/self/net/tcp').exists():
        return
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        assert processes.listens_on(os.getpid(), listener.getsockname()[1])


def test_terminate_executable_respects_binary_and_subcommand(monkeypatch):
    monkeypatch.setattr(processes, 'user_processes', lambda: iter([
        (11, Path('/usr/bin/ollama'), ['/usr/bin/ollama', 'serve']),
        (12, Path('/usr/bin/ollama'), ['/usr/bin/ollama', 'list']),
        (13, Path('/usr/bin/editor'), ['editor', '/usr/bin/ollama', 'serve']),
    ]))
    killed = []
    monkeypatch.setattr(processes.os, 'kill', lambda pid, sig: killed.append(pid))
    processes.terminate_executable(Path('/usr/bin/ollama'), command='serve')
    assert killed == [11]
