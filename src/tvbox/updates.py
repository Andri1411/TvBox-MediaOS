"""The hub's side of updates and snapshots: talks to tvbox-updater (root,
socket-activated) and keeps the state the TV and the phone show."""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from . import NAME
from .util import setup_logging

log = setup_logging("hub")

SOCKET = Path(f"/run/{NAME}/updater.sock")
LOG_LINES = 12


class UpdaterError(Exception):
    pass


def booted_snapshot() -> int | None:
    """The snapshot this boot started from (fallback or "start once"), if any."""
    try:
        match = re.search(r"subvol=/?@snapshots/(\d+)/snapshot", Path("/proc/cmdline").read_text())
    except OSError:
        return None
    return int(match[1]) if match else None


async def call(request: dict, on_log=None, timeout: float = 3600) -> dict:
    """One request to tvbox-updater; log lines go to on_log."""
    try:
        reader, writer = await asyncio.open_unix_connection(str(SOCKET), limit=1 << 22)
    except OSError as err:
        raise UpdaterError(f"update service not available ({err.strerror or err})") from err
    try:
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        async with asyncio.timeout(timeout):
            while line := await reader.readline():
                msg = json.loads(line)
                if msg.get("event") == "log":
                    if on_log:
                        on_log(msg["line"])
                elif msg.get("event") == "error":
                    raise UpdaterError(msg.get("error", "failed"))
                elif msg.get("event") == "done":
                    return msg
    finally:
        writer.close()
    raise UpdaterError("the update service stopped answering")


class Updates:
    def __init__(self, hub):
        self.hub = hub
        self.status = "idle"            # idle | checking | available | none | applying | done | error
        self.updates: list[dict] = []
        self.download_size: int | None = None
        self.reboot_for: list[str] = []
        self.checked: float | None = None
        self.error = ""
        self.log: list[str] = []
        self.snapshots: list[dict] = []
        self.fallback: int | None = None
        self.fallback_time: int | None = None
        self.booted = booted_snapshot()
        self._task: asyncio.Task | None = None

    def state(self) -> dict:
        return {"status": self.status, "updates": self.updates, "download_size": self.download_size,
                "reboot_for": self.reboot_for, "checked": self.checked, "error": self.error,
                "log": self.log, "snapshots": self.snapshots, "fallback": self.fallback,
                "fallback_time": self.fallback_time, "booted_snapshot": self.booted}

    def _log(self, line: str) -> None:
        self.log = (self.log + [line])[-LOG_LINES:]
        self.hub.push_state()

    def busy(self) -> bool:
        return self.status in ("checking", "applying")

    def start(self, coro) -> None:
        if self.busy():
            raise ValueError("an update is already in progress")
        self._task = self.hub.spawn(coro)

    async def check(self) -> None:
        self.status, self.error, self.log = "checking", "", []
        self.hub.push_state()
        try:
            result = await call({"cmd": "check"}, self._log, timeout=300)
            self.updates = result["updates"]
            self.download_size = result.get("download_size")
            self.reboot_for = result.get("reboot_for", [])
            self.checked = time.time()
            self.status = "available" if self.updates else "none"
        except (UpdaterError, TimeoutError, ValueError) as err:
            self.status, self.error = "error", str(err)
        self.hub.push_state()

    async def apply(self) -> None:
        self.status, self.error, self.log = "applying", "", []
        self.hub.push_state()
        try:
            result = await call({"cmd": "apply"}, self._log)
            self.reboot_for = result.get("reboot_for", [])
            self.updates = []
            self.status = "done"
            log.info("update installed: %s", ", ".join(result.get("changed", [])))
        except (UpdaterError, TimeoutError, ValueError) as err:
            self.status, self.error = "error", str(err)
        await self.refresh_snapshots()
        self.hub.push_state()

    async def refresh_snapshots(self) -> None:
        try:
            result = await call({"cmd": "snapshots"}, timeout=30)
        except (UpdaterError, TimeoutError, ValueError) as err:
            log.warning("snapshots: %s", err)
            return
        self.snapshots = sorted(result["snapshots"], key=lambda s: s["number"], reverse=True)
        self.fallback, self.fallback_time = result.get("fallback"), result.get("fallback_time")
        self.booted = result.get("booted")
        self.hub.push_state()

    async def boot_once(self, number: int) -> None:
        await call({"cmd": "boot_once", "number": number}, timeout=30)

    async def rollback(self, number: int) -> None:
        await call({"cmd": "rollback", "number": number}, self._log, timeout=600)
