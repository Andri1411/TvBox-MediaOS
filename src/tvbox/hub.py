"""tvbox-hub: the control centre between input, the on-screen UI and the system.

Phase 2 scope: the system menu and volume/OSD backend. It listens to
tvbox-inputd (input.sock) for actions and overlay navigation, serves the web
UI to tvbox-shell and keeps it up to date over a WebSocket.

HTTP (loopback only until the phone remote adds authentication):
    GET  /overlay        the overlay page (system menu, OSD)
    GET  /ws             WebSocket: {"type": "state"|"nav"|"osd", ...} to the UI,
                         {"cmd": ...} from the UI
    GET  /api/state      current state as JSON
    POST /api/cmd        {"cmd": ...}, same commands as over the WebSocket
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from pathlib import Path

from aiohttp import WSMsgType, web

from . import NAME, audio, sway
from .util import input_socket, runtime_dir, sd_notify, setup_logging, watchdog_interval

log = setup_logging("hub")

PORT = 8080
HOME_WORKSPACE = "home"
SWAY_UI_MODE = "tvbox-ui"                # binding mode defined in the sway config


def data_dir() -> Path:
    return Path(os.environ.get("TVBOX_DATA_DIR", f"/usr/share/{NAME}"))


def release_version() -> str:
    try:
        for line in (data_dir() / "release").read_text().splitlines():
            if line.startswith("VERSION="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return "dev"


def lan_address() -> str:
    """The address other devices on the LAN reach the box at (no traffic is sent)."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    return ""


class Hub:
    def __init__(self):
        self.overlay: str | None = None       # None | "menu"
        self.view = "main"                    # view the menu opens with
        self.base_mode = "app"                # input mode outside the overlay: app | mouse
        self.app: str | None = None           # focused app (workspace)
        self.apps: list[dict] = []
        self.volume: int | None = None
        self.muted = False
        self.sinks: list[dict] = []
        self.config_errors: list[str] = []
        self.input_connected = False
        self._input: asyncio.StreamWriter | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self._volume_pending = 0
        self._volume_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # -- state --------------------------------------------------------------
    def state(self) -> dict:
        return {"type": "state", "overlay": self.overlay, "view": self.view,
                "mouse": self.base_mode == "mouse", "app": self.app, "apps": self.apps,
                "volume": self.volume, "muted": self.muted, "sinks": self.sinks,
                "config_errors": self.config_errors, "input_connected": self.input_connected,
                "version": release_version(), "hostname": socket.gethostname(),
                "address": lan_address()}

    def send(self, message: dict) -> None:
        for ws in list(self._clients):
            self._spawn(self._send_one(ws, message))

    async def _send_one(self, ws: web.WebSocketResponse, message: dict) -> None:
        with contextlib.suppress(ConnectionError, RuntimeError):
            await ws.send_json(message)

    def push_state(self) -> None:
        self.send(self.state())

    def osd(self, **fields) -> None:
        self.send({"type": "osd", **fields})

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- link to inputd -----------------------------------------------------
    def input_send(self, **cmd) -> None:
        if self._input:
            self._input.write(json.dumps(cmd).encode() + b"\n")

    async def input_link(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(str(input_socket()))
                self._input, self.input_connected = writer, True
                while line := await reader.readline():
                    await self.on_input(json.loads(line))
            except (OSError, ValueError) as err:
                log.debug("inputd link: %s", err)
            if self.input_connected:
                log.warning("lost tvbox-inputd, reconnecting")
            self._input, self.input_connected = None, False
            await asyncio.sleep(1)

    async def on_input(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "hello":
            self.config_errors = msg.get("config_errors", [])
            self.app = msg.get("app")
            if msg.get("mode") == "mouse":
                self.base_mode = "mouse"
            # Either side may have restarted: make inputd's mode match ours.
            self.input_send(cmd="mode", mode="ui" if self.overlay else self.base_mode)
            self.push_state()
        elif event == "nav":
            self.send({"type": "nav", "button": msg["button"]})
        elif event == "action":
            await self.on_action(msg["action"])
        elif event == "mode":
            if msg["mode"] in ("app", "mouse") and msg["mode"] != self.base_mode:
                self.base_mode = msg["mode"]
                self.osd(kind="message", text=f"Mouse mode {'on' if self.base_mode == 'mouse' else 'off'}")
                self.push_state()
        elif event == "focus":
            self.app = msg.get("app")
            await self.refresh_apps()
            self.push_state()
        elif event == "config":
            self.config_errors = msg.get("errors", [])
            if self.config_errors:
                self.osd(kind="message", text="Bindings file has errors, keeping the previous bindings")
            self.push_state()

    async def on_action(self, action: str) -> None:
        kind, _, arg = action.partition(":")
        if action == "ui:system_menu":
            await (self.close_overlay() if self.overlay else self.open_overlay("main"))
        elif action == "ui:app_switcher":
            if self.overlay and self.view == "apps":
                await self.close_overlay()
            else:
                await self.open_overlay("apps")
        elif action == "ui:home":
            await self.command({"cmd": "home"})
        elif action == "ui:keyboard":
            self.osd(kind="message", text="The on-screen keyboard arrives in a later version")
        elif kind == "volume":
            await self.command({"cmd": "mute"} if arg == "mute" else {"cmd": "volume", "delta": int(arg)})
        elif action == "audio:next_output":
            await self.next_output()
        elif action == "app:restart":
            await self.command({"cmd": "restart_app"})
        elif kind == "app" and arg.startswith("launch:"):
            await self.command({"cmd": "switch_app", "id": arg.removeprefix("launch:")})
        else:
            log.info("action %s is not handled yet", action)

    # -- overlay ------------------------------------------------------------
    async def open_overlay(self, view: str) -> None:
        if not self._clients:
            # Without the shell nothing would be drawn, and ui mode would
            # swallow the controller: stay in app mode.
            log.warning("system menu requested but tvbox-shell is not connected")
            return
        self.overlay, self.view = "menu", view
        self.input_send(cmd="mode", mode="ui")
        self.push_state()
        await self.sway_command(f"mode {SWAY_UI_MODE}")     # keyboard navigation
        await asyncio.gather(self.refresh_audio(), self.refresh_apps())
        self.push_state()

    async def close_overlay(self) -> None:
        if self.overlay:
            self.overlay = None
            self.input_send(cmd="mode", mode=self.base_mode)
            self.push_state()
            await self.sway_command("mode default")

    # -- system -------------------------------------------------------------
    async def refresh_audio(self) -> None:
        volume, self.sinks = await asyncio.gather(audio.get_volume(), audio.list_sinks())
        if volume:
            self.volume, self.muted = volume
        else:
            self.volume = None

    async def refresh_apps(self) -> None:
        try:
            workspaces = await sway.request(sway.GET_WORKSPACES)
        except (ConnectionError, OSError):
            return
        self.apps = [{"id": ws["name"], "name": ws["name"], "focused": ws.get("focused", False)}
                     for ws in workspaces if ws["name"] != HOME_WORKSPACE]

    async def _apply_volume(self) -> None:
        # Held triggers arrive faster than wpctl runs: apply what piled up.
        while self._volume_pending:
            delta, self._volume_pending = self._volume_pending, 0
            await audio.change_volume(delta)
            volume = await audio.get_volume()
            if volume:
                self.volume, self.muted = volume
            self.volume_changed()

    def volume_changed(self) -> None:
        if self.volume is None:
            self.osd(kind="message", text="No audio output")
        elif not self.overlay:
            self.osd(kind="volume", volume=self.volume, muted=self.muted)
        self.push_state()

    async def next_output(self) -> None:
        await self.refresh_audio()
        if not self.sinks:
            self.osd(kind="message", text="No audio output")
            return
        current = next((i for i, s in enumerate(self.sinks) if s["default"]), -1)
        await self.command({"cmd": "audio_output", "id": self.sinks[(current + 1) % len(self.sinks)]["id"]})

    async def sway_command(self, cmd: str) -> bool:
        try:
            return await sway.command(cmd)
        except (ConnectionError, OSError) as err:
            log.warning("sway: %s", err)
            return False

    async def command(self, msg: dict) -> dict:
        """Commands from the UI (and from actions). Raises ValueError/KeyError
        on bad input."""
        cmd = msg.get("cmd")
        if cmd == "close":
            await self.close_overlay()
        elif cmd == "home":
            await self.close_overlay()
            await self.sway_command(f"workspace {HOME_WORKSPACE}")
        elif cmd == "switch_app":
            app = str(msg["id"])
            if not sway_safe(app):
                raise ValueError(f"bad app id {app!r}")
            await self.close_overlay()
            await self.sway_command(f"workspace {app}")
        elif cmd == "volume":
            delta = int(msg["delta"])
            if not -100 <= delta <= 100:
                raise ValueError("delta out of range")
            self._volume_pending += delta
            if not (self._volume_task and not self._volume_task.done()):
                self._volume_task = self._spawn(self._apply_volume())
        elif cmd == "mute":
            await audio.toggle_mute()
            volume = await audio.get_volume()
            self.volume, self.muted = volume if volume else (None, False)
            self.volume_changed()
        elif cmd == "audio_output":
            await audio.set_default_sink(int(msg["id"]))
            await self.refresh_audio()
            name = next((s["name"] for s in self.sinks if s["default"]), None)
            self.osd(kind="message", text=f"Audio: {name}" if name else "No audio output")
            self.push_state()
        elif cmd == "mouse_toggle":
            self.base_mode = "app" if self.base_mode == "mouse" else "mouse"
            self.osd(kind="message", text=f"Mouse mode {'on' if self.base_mode == 'mouse' else 'off'}")
            if self.overlay:
                await self.close_overlay()
            else:
                self.input_send(cmd="mode", mode=self.base_mode)
                self.push_state()
        elif cmd == "restart_app":
            unit = f"{NAME}-app@{self.app}.service"
            if self.app and (await audio.run("systemctl", "--user", "is-active", "-q", unit))[0] == 0:
                await self.close_overlay()
                await audio.run("systemctl", "--user", "restart", "--no-block", unit)
                self.osd(kind="message", text=f"Restarting {self.app}")
            else:
                self.osd(kind="message", text="No app to restart here")
        elif cmd == "restart_session":
            # greetd logs in automatically only once per boot, so sway must be
            # restarted by tvbox-session's loop: the flag tells it this exit
            # is not a logout.
            (runtime_dir() / "restart-session").touch()
            await self.sway_command("exit")
        elif cmd == "reboot":
            await audio.run("systemctl", "reboot")
        else:
            raise ValueError(f"unknown command {cmd!r}")
        return {"ok": True}

    # -- HTTP ---------------------------------------------------------------
    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self._clients.add(ws)
        await ws.send_json(self.state())
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    await self.command(json.loads(message.data))
                except (ValueError, KeyError, TypeError) as err:
                    await ws.send_json({"type": "error", "error": str(err)})
        finally:
            self._clients.discard(ws)
            if not self._clients:
                await self.close_overlay()      # nobody left to draw the menu
        return ws

    async def api_state(self, _request: web.Request) -> web.Response:
        return web.json_response(self.state() | {"ui_clients": len(self._clients)})

    async def api_cmd(self, request: web.Request) -> web.Response:
        try:
            msg = await request.json()
            if not isinstance(msg, dict):
                raise ValueError("expected a JSON object")
            if msg.get("cmd") == "action":       # same as a bound button
                await self.on_action(str(msg["action"]))
                return web.json_response({"ok": True})
            return web.json_response(await self.command(msg))
        except (ValueError, KeyError, TypeError) as err:
            return web.json_response({"ok": False, "error": str(err)}, status=400)

    def app_factory(self) -> web.Application:
        webroot = data_dir() / "web"
        app = web.Application()

        async def overlay(_request):
            return web.FileResponse(webroot / "overlay.html")
        app.add_routes([web.get("/overlay", overlay), web.get("/ws", self.ws_handler),
                        web.get("/api/state", self.api_state), web.post("/api/cmd", self.api_cmd),
                        web.static("/static", webroot)])
        return app

    async def run(self, host: str = "127.0.0.1", port: int = PORT) -> None:
        runner = web.AppRunner(self.app_factory(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        self._spawn(self.input_link())
        self._spawn(self.refresh_audio())
        self._spawn(self.sway_command("mode default"))   # in case a previous hub died mid-menu
        sd_notify("READY=1")
        log.info("listening on http://%s:%d", host, port)
        interval = watchdog_interval()
        try:
            while True:
                sd_notify("WATCHDOG=1")
                await asyncio.sleep(interval or 3600)
        finally:
            await runner.cleanup()


def sway_safe(name: str) -> bool:
    """Workspace names are passed inside a sway command: keep them boring."""
    return bool(name) and all(c.isalnum() or c in "-_." for c in name)


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(Hub().run())


if __name__ == "__main__":
    main()
