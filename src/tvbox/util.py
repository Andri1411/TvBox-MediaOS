"""Small shared helpers: runtime paths, systemd notification, inotify."""
from __future__ import annotations

import ctypes
import logging
import os
import socket
import struct
import sys
from pathlib import Path

from . import NAME

IN_ATTRIB, IN_CLOSE_WRITE, IN_MOVED_FROM, IN_MOVED_TO = 0x4, 0x8, 0x40, 0x80
IN_CREATE, IN_DELETE = 0x100, 0x200


def runtime_dir() -> Path:
    """Per-user runtime directory for sockets and state ($XDG_RUNTIME_DIR/tvbox)."""
    path = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / NAME
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def input_socket() -> Path:
    return runtime_dir() / "input.sock"


def setup_logging(name: str) -> logging.Logger:
    # journald adds timestamps and the unit name.
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("TVBOX_LOG", "INFO"),
                        format="%(levelname)s %(message)s")
    return logging.getLogger(name)


def sd_notify(state: str) -> None:
    """Tell systemd about readiness / watchdog; no-op outside a unit."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(state.encode(), addr)
    except OSError:
        pass


def watchdog_interval() -> float | None:
    """Seconds between WATCHDOG=1 pings (half of WatchdogSec), if enabled."""
    usec = os.environ.get("WATCHDOG_USEC")
    return int(usec) / 2_000_000 if usec else None


class Inotify:
    """Minimal inotify wrapper; `fd` is non-blocking for loop.add_reader()."""

    def __init__(self):
        self._libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self._libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self._paths: dict[int, Path] = {}

    def watch(self, path: Path, mask: int) -> bool:
        wd = self._libc.inotify_add_watch(self.fd, os.fsencode(path), mask)
        if wd < 0:
            return False
        self._paths[wd] = Path(path)
        return True

    def read(self) -> list[tuple[Path, int, str]]:
        """Pending events as (watched directory, mask, file name)."""
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return []
        events, pos = [], 0
        while pos + 16 <= len(data):
            wd, mask, _cookie, length = struct.unpack_from("iIII", data, pos)
            name = data[pos + 16:pos + 16 + length].split(b"\0", 1)[0].decode(errors="replace")
            pos += 16 + length
            if wd in self._paths:
                events.append((self._paths[wd], mask, name))
        return events

    def close(self) -> None:
        os.close(self.fd)
