"""App manager: the hub's view of the services in services.toml.

Each running service is the systemd user unit tvbox-app@<id>.service and has
its own sway workspace named <id>. Recently used services stay running in
the background; the least recently used one is stopped when memory gets low.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import aiohttp

from . import NAME, services, sway
from .app import devtools_port
from .audio import run
from .services import Service
from .util import setup_logging

log = setup_logging("hub")

HOME = "home"
UNIT = f"{NAME}-app@{{}}.service"
_UNIT_RE = re.compile(rf"{NAME}-app@([^.\s]+)\.service")
LOW_MEMORY_KB = 1536 * 1024          # stop background apps below this much available memory
MEMORY_CHECK_S = 60
PAUSE_JS = 'document.querySelectorAll("video, audio").forEach((m) => m.pause())'


def unit_of_pid(pid: int) -> str | None:
    """Service id whose unit the process belongs to, from its cgroup."""
    try:
        match = _UNIT_RE.search(Path(f"/proc/{pid}/cgroup").read_text())
    except OSError:
        return None
    return match.group(1) if match else None


def available_kb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def windows_by_workspace(tree: dict) -> dict[str, int]:
    """Number of application windows on each workspace of a sway tree."""
    counts: dict[str, int] = {}

    def walk(node: dict, workspace: str | None) -> None:
        if node.get("type") == "workspace":
            workspace = node.get("name")
            counts.setdefault(workspace, 0)
        elif workspace and node.get("pid") and node.get("type") in ("con", "floating_con"):
            counts[workspace] += 1
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            walk(child, workspace)
    walk(tree, None)
    return counts


class AppManager:
    def __init__(self, hub):
        self.hub = hub
        self.services: list[Service] = []
        self.errors: list[str] = []
        self.active: dict[str, str] = {}        # id -> "running" | "starting"
        self.last_used: dict[str, float] = {}
        self.reload()

    # -- services -----------------------------------------------------------
    def reload(self) -> None:
        self.services, self.errors = services.load_best()
        for line in self.errors:
            log.error("services: %s", line)

    def get(self, service_id: str | None) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)

    def state(self) -> list[dict]:
        return [{"id": s.id, "name": s.name, "color": s.color, "kind": s.kind,
                 "state": self.active.get(s.id, "stopped"), "focused": s.id == self.hub.app}
                for s in self.services]

    async def refresh(self) -> None:
        """Which service units are up, according to systemd."""
        code, out = await run("systemctl", "--user", "list-units", "--plain", "--no-legend",
                              "--all", f"{NAME}-app@*.service")
        if code != 0:
            return
        active = {}
        for line in out.splitlines():
            fields = line.split()
            match = _UNIT_RE.fullmatch(fields[0]) if fields else None
            if match and len(fields) >= 4 and fields[2] in ("active", "activating"):
                active[match.group(1)] = "running" if fields[3] == "running" else "starting"
        self.active = active

    # -- switching ----------------------------------------------------------
    async def launch(self, service_id: str) -> None:
        service = self.get(service_id)
        if not service:
            raise ValueError(f"no service {service_id!r}")
        self.last_used[service.id] = time.monotonic()
        await self.hub.sway_command(f"workspace {service.id}")
        await self.refresh()
        if service.id not in self.active:
            await run("systemctl", "--user", "start", "--no-block", UNIT.format(service.id))
            self.active[service.id] = "starting"
            self.hub.osd(kind="message", text=f"Starting {service.name}")
            self.hub.spawn(self.free_memory(keep=service.id))
        self.hub.push_state()

    async def stop(self, service_id: str) -> None:
        if not self.get(service_id):
            raise ValueError(f"no service {service_id!r}")
        await run("systemctl", "--user", "stop", UNIT.format(service_id))
        await self.refresh()
        self.hub.push_state()

    async def restart(self, service_id: str | None) -> bool:
        service = self.get(service_id)
        if not service:
            return False
        await run("systemctl", "--user", "restart", "--no-block", UNIT.format(service.id))
        return True

    async def focus_changed(self, old: str | None, new: str | None) -> None:
        """Called when another workspace got focus."""
        if new and self.get(new):
            self.last_used[new] = time.monotonic()
        previous = self.get(old)
        if previous and previous.pause and old != new:
            await self.pause(previous)
        await self.refresh()

    async def pause(self, service: Service) -> None:
        """Pause playback in a browser service through its DevTools port."""
        port = devtools_port(service.id) if service.kind == "browser" else None
        if not port:
            return
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"http://127.0.0.1:{port}/json") as reply:
                    pages = [p for p in await reply.json() if p.get("type") == "page"]
                for page in pages:
                    async with session.ws_connect(page["webSocketDebuggerUrl"]) as ws:
                        await ws.send_json({"id": 1, "method": "Runtime.evaluate",
                                            "params": {"expression": PAUSE_JS}})
                        await ws.receive()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError) as err:
            log.debug("pause %s: %s", service.id, err)

    # -- housekeeping -------------------------------------------------------
    async def free_memory(self, keep: str | None = None) -> None:
        """Stop least recently used background services while memory is low."""
        await self.refresh()
        candidates = sorted((s for s in self.active if s not in (keep, self.hub.app)),
                            key=lambda s: self.last_used.get(s, 0))
        for service_id in candidates:
            free = available_kb()
            if free is None or free >= LOW_MEMORY_KB:
                break
            log.warning("low memory (%d MB available): stopping %s", free // 1024, service_id)
            await run("systemctl", "--user", "stop", UNIT.format(service_id))
            await asyncio.sleep(2)
        await self.refresh()

    async def memory_watch(self) -> None:
        while True:
            await asyncio.sleep(MEMORY_CHECK_S)
            free = available_kb()
            if free is not None and free < LOW_MEMORY_KB and self.active:
                await self.free_memory()
                self.hub.push_state()

    async def window_watch(self) -> None:
        """Keep every service's windows on its own workspace, and leave a
        workspace whose last window has closed."""
        while True:
            try:
                async for event in sway.events("window"):
                    change, con = event.get("change"), event.get("container") or {}
                    if change == "new":
                        service_id = unit_of_pid(con.get("pid") or 0)
                        if service_id and sway_id(service_id):
                            await sway.command(f"[con_id={con['id']}] move container to workspace {service_id}")
                            self.active[service_id] = "running"
                            self.hub.push_state()
                    elif change == "close":
                        self.hub.spawn(self.leave_empty_workspace())
            except (ConnectionError, OSError) as err:
                log.debug("sway window events: %s", err)
            await asyncio.sleep(2)

    async def leave_empty_workspace(self) -> None:
        await asyncio.sleep(0.5)        # a restarting app opens its new window first
        try:
            current = await sway.focused_workspace()
            counts = windows_by_workspace(await sway.request(sway.GET_TREE))
        except (ConnectionError, OSError):
            return
        await self.refresh()
        if current and current != HOME and counts.get(current, 0) == 0 and current not in self.active:
            await sway.command(f"workspace {HOME}")
        self.hub.push_state()


def sway_id(name: str) -> bool:
    """Names passed inside a sway command: keep them boring."""
    return bool(name) and all(c.isalnum() or c in "-_." for c in name)
