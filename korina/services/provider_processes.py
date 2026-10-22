"""Narrow Linux process discovery for optional local provider supervision.

Never match a whole command line with a regex: unrelated editors, scripts, and
servers can contain the same strings. Only inspect this user's processes and
match executable/argv boundaries. Port checks use the process's actual listening
socket, independent of how ``--port`` arguments were written.
"""
from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable, Iterator
from pathlib import Path

logger = logging.getLogger(__name__)
PROC_ROOT = Path('/proc')


def user_processes() -> Iterator[tuple[int, Path, list[str]]]:
    if not hasattr(os, 'getuid') or not PROC_ROOT.is_dir():
        return
    for process in PROC_ROOT.iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            executable = (process / 'exe').resolve(strict=True)
            args = (process / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
        except (OSError, ValueError):
            continue  # a process can exit while /proc is being read
        if args and args[0]:
            yield int(process.name), executable, args


def has_executable(executable: Path) -> bool:
    expected = executable.resolve()
    return any(actual == expected or Path(args[0]).resolve() == expected for _, actual, args in user_processes())


def listens_on(pid: int, port: int) -> bool:
    """Whether this PID owns a listening IPv4/IPv6 TCP socket on ``port``."""
    process = PROC_ROOT / str(pid)
    inodes = set()
    for protocol in ('tcp', 'tcp6'):
        try:
            rows = (process / 'net' / protocol).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if len(fields) > 9 and fields[3] == '0A':  # TCP_LISTEN
                if int(fields[1].rsplit(':', 1)[1], 16) == port:
                    inodes.add(fields[9])
    if not inodes:
        return False
    try:
        descriptors = list((process / 'fd').iterdir())
    except OSError:
        return False
    for descriptor in descriptors:
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        if target.startswith('socket:[') and target[8:-1] in inodes:
            return True
    return False


def terminate_matching(predicate: Callable[[int, Path, list[str]], bool]) -> None:
    for pid, executable, args in user_processes():
        if predicate(pid, executable, args):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                logger.warning('Cannot stop provider process %s', pid)


def terminate_executable(executable: Path, command: str | None = None) -> None:
    expected = executable.resolve()
    terminate_matching(lambda pid, actual, args: (
        (actual == expected or Path(args[0]).resolve() == expected)
        and (command is None or (len(args) > 1 and args[1] == command))
    ))
