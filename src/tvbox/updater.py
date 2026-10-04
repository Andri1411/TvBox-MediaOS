"""tvbox-updater: the root helper behind Settings → Updates and Snapshots.

Socket-activated on /run/tvbox/updater.sock (group tv). One request per
connection, as a JSON line; the answer is a stream of JSON lines ending with
{"event": "done", ...} or {"event": "error", "error": "..."}.

    {"cmd": "check"}                   available updates, download size, reboot needed
    {"cmd": "apply"}                   pacman -Syu; progress lines; records the boot fallback
    {"cmd": "snapshots"}               snapper snapshots, the fallback, the booted one
    {"cmd": "boot_once", "number": n}  next boot (only) starts snapshot n
    {"cmd": "rollback", "number": n}   make snapshot n the system (takes effect on reboot)

Nothing here runs on a timer: every action starts from the user.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

from . import NAME
from .util import sd_notify, setup_logging

log = setup_logging("updater")

GRUBENV = Path(f"/efi/{NAME}/grubenv")
CHECK_DB = Path(f"/var/lib/{NAME}/checkupdates-db")
TOP_MOUNT = Path(f"/run/{NAME}/btrfs-top")
SNAPSHOTS = Path("/.snapshots")
IDLE_EXIT_S = 600
# Package changes after which the box should be rebooted: kernel, firmware,
# core libraries, the graphics stack, the session and our own software.
REBOOT_PACKAGES = re.compile(
    r"^(linux(-lts)?(-headers)?|linux-firmware.*|intel-ucode|glibc|systemd(-libs)?|dbus.*|mesa|"
    r"vulkan-intel|intel-media-driver|libva|wlroots.*|sway|greetd|pipewire.*|wireplumber|"
    r"gtk4|webkitgtk-6.0|chromium|python|" + NAME + r"-(core|session|base|browser))$")


class Failed(Exception):
    pass


async def run(*argv: str, env: dict | None = None, timeout: float = 600) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "LC_ALL": "C", **(env or {})})
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise Failed(f"{argv[0]} took too long")
    return proc.returncode or 0, out.decode(errors="replace")


def needs_reboot(names) -> list[str]:
    return sorted(n for n in names if REBOOT_PACKAGES.match(n))


def parse_checkupdates(text: str) -> list[dict]:
    """`name old -> new` lines."""
    updates = []
    for line in text.splitlines():
        match = re.fullmatch(r"(\S+) (\S+) -> (\S+)", line.strip())
        if match:
            updates.append({"name": match[1], "old": match[2], "new": match[3]})
    return updates


def grubenv_get() -> dict[str, str]:
    try:
        lines = GRUBENV.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


async def grubenv_set(**values) -> None:
    """Set (or with None, unset) variables in the boot environment on the ESP
    (GRUB can't write to btrfs; FAT is fine)."""
    if not GRUBENV.exists():
        GRUBENV.parent.mkdir(parents=True, exist_ok=True)
        await run("grub-editenv", str(GRUBENV), "create")
    sets = [f"{k}={v}" for k, v in values.items() if v is not None]
    unsets = [k for k, v in values.items() if v is None]
    if sets:
        code, out = await run("grub-editenv", str(GRUBENV), "set", *sets)
        if code:
            raise Failed(f"grub-editenv: {out.strip()}")
    if unsets:
        await run("grub-editenv", str(GRUBENV), "unset", *unsets)


def booted_snapshot() -> int | None:
    match = re.search(r"subvol=/?@snapshots/(\d+)/snapshot", Path("/proc/cmdline").read_text())
    return int(match[1]) if match else None


def snapshot_list(root: Path = SNAPSHOTS) -> list[dict]:
    """Snapper's snapshots, read from their info.xml. (The snapper command
    refuses to work when the box was started from a snapshot: / is then an
    overlay, not a btrfs subvolume.)"""
    snapshots = []
    for info in root.glob("*/info.xml"):
        try:
            xml = ElementTree.parse(info).getroot()
            number = int(xml.findtext("num", "0"))
        except (ElementTree.ParseError, ValueError, OSError):
            continue
        if number == 0 or not (info.parent / "snapshot").is_dir():
            continue
        pre = xml.findtext("pre_num")
        snapshots.append({"number": number, "type": xml.findtext("type", "single"),
                          "pre": int(pre) if pre and pre.isdigit() else None,
                          "date": xml.findtext("date", ""),          # UTC, "YYYY-MM-DD HH:MM:SS"
                          "description": xml.findtext("description", "")})
    return sorted(snapshots, key=lambda s: s["number"])


async def snapper_list() -> list[dict]:
    return snapshot_list()


class Updater:
    def __init__(self):
        self.busy = asyncio.Lock()
        self.last_activity = time.monotonic()

    async def check(self, emit) -> dict:
        CHECK_DB.mkdir(parents=True, exist_ok=True)
        emit(event="log", line="Checking for updates…")
        # checkupdates syncs a private copy of the package databases, so this
        # never leaves the system half-updated (no partial upgrade).
        code, out = await run("checkupdates", "--nocolor", env={"CHECKUPDATES_DB": str(CHECK_DB)})
        if code == 2:
            return {"updates": [], "download_size": 0, "reboot_for": []}
        if code != 0:
            raise Failed("could not check for updates: " + (out.strip().splitlines() or ["no network?"])[-1])
        updates = parse_checkupdates(out)
        code, sizes = await run("pacman", "-Sup", "--dbpath", str(CHECK_DB), "--print-format", "%s")
        size = sum(int(s) for s in sizes.split() if s.isdigit()) if code == 0 else None
        return {"updates": updates, "download_size": size,
                "reboot_for": needs_reboot(u["name"] for u in updates)}

    async def stream(self, emit, *argv: str) -> tuple[int, list[str]]:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "LC_ALL": "C"})
        lines = []
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                lines.append(line)
                emit(event="log", line=line)
                self.last_activity = time.monotonic()
        return await proc.wait(), lines

    async def apply(self, emit) -> dict:
        if Path("/var/lib/pacman/db.lck").exists():
            raise Failed("another package operation is running")
        before = max((s["number"] for s in await snapper_list()), default=0)
        changed: list[str] = []
        # The keyring first, so packages signed with new keys can be checked.
        for step in (["pacman", "-Sy", "--needed", "--noconfirm", "archlinux-keyring"],
                     ["pacman", "-Su", "--noconfirm"]):
            code, lines = await self.stream(emit, *step)
            changed += [m[1] for line in lines for m in [re.match(r"(?:upgrading|installing) (\S+?)\.\.\.", line)] if m]
            if code != 0:
                raise Failed(f"{' '.join(step[:2])} failed (exit {code}); the system is unchanged "
                             "or can be rolled back from Settings → Snapshots")
        # snap-pac took a pre snapshot of each transaction: the first one is
        # the state before this update, the boot fallback.
        pre = [s["number"] for s in await snapper_list() if s["number"] > before and s["type"] == "pre"]
        if pre:
            await grubenv_set(tvbox_fallback=min(pre), tvbox_fallback_time=int(time.time()))
        return {"changed": sorted(set(changed)), "reboot_for": needs_reboot(changed),
                "fallback": min(pre) if pre else None}

    async def snapshots(self, _emit) -> dict:
        env = grubenv_get()
        return {"snapshots": await snapper_list(), "fallback": _int(env.get("tvbox_fallback")),
                "fallback_time": _int(env.get("tvbox_fallback_time")), "booted": booted_snapshot()}

    async def _existing(self, number) -> int:
        if not isinstance(number, int) or not any(s["number"] == number for s in await snapper_list()):
            raise Failed(f"no snapshot {number!r}")
        return number

    async def boot_once(self, _emit, number) -> dict:
        number = await self._existing(number)
        await grubenv_set(tvbox_once=number)
        return {"next_boot": number}

    async def rollback(self, emit, number) -> dict:
        """Replace @ with a writable copy of snapshot n. The old @ is kept as
        @rollback-<time> until the next rollback. Takes effect on reboot."""
        number = await self._existing(number)
        code, source = await run("findmnt", "-no", "SOURCE", "/home")
        device = source.strip().split("[")[0]
        if code or not device.startswith("/dev/"):
            raise Failed("cannot find the btrfs device")
        TOP_MOUNT.mkdir(parents=True, exist_ok=True)
        code, out = await run("mount", "-o", "subvolid=5", device, str(TOP_MOUNT))
        if code:
            raise Failed(f"mount: {out.strip()}")
        try:
            top = TOP_MOUNT
            snapshot = top / "@snapshots" / str(number) / "snapshot"
            new, old = top / "@.new", top / f"@rollback-{time.strftime('%Y%m%d-%H%M%S')}"
            if new.exists():
                await run("btrfs", "subvolume", "delete", str(new))
            emit(event="log", line=f"Copying snapshot {number}…")
            code, out = await run("btrfs", "subvolume", "snapshot", str(snapshot), str(new))
            if code:
                raise Failed(f"btrfs snapshot: {out.strip()}")
            for previous in sorted(top.glob("@rollback-*")):       # keep only the newest old system
                # -R: systemd creates subvolumes inside / (var/lib/portables, …)
                await run("btrfs", "subvolume", "delete", "-R", str(previous))
            os.rename(top / "@", old)
            os.rename(new, top / "@")
            emit(event="log", line=f"Snapshot {number} is now the system; the previous one is kept as {old.name}")
        finally:
            await run("umount", str(TOP_MOUNT))
        # The user decided: the fallback has done its job.
        await grubenv_set(tvbox_tries=None, tvbox_once=None, tvbox_fallback=None, tvbox_fallback_time=None)
        return {"rolled_back_to": number, "kept": old.name}

    async def handle(self, reader, writer) -> None:
        def emit(**message):
            writer.write(json.dumps(message).encode() + b"\n")
        self.last_activity = time.monotonic()
        try:
            request = json.loads(await reader.readline())
            cmd = request.get("cmd")
            handlers = {"check": self.check, "apply": self.apply, "snapshots": self.snapshots,
                        "boot_once": self.boot_once, "rollback": self.rollback}
            if cmd not in handlers:
                raise Failed(f"unknown command {cmd!r}")
            args = [request["number"]] if cmd in ("boot_once", "rollback") else []
            if cmd in ("apply", "rollback", "boot_once"):
                if self.busy.locked():
                    raise Failed("an update or rollback is already running")
                async with self.busy:
                    log.info("%s %s", cmd, args)
                    result = await handlers[cmd](emit, *args)
            else:
                result = await handlers[cmd](emit, *args)
            emit(event="done", **result)
        except (Failed, ValueError, KeyError, TypeError, AttributeError, OSError) as err:
            log.error("%s", err)
            emit(event="error", error=str(err))
        finally:
            with contextlib.suppress(ConnectionError):
                await writer.drain()
            writer.close()
            self.last_activity = time.monotonic()


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def serve() -> None:
    updater = Updater()
    if os.environ.get("LISTEN_FDS") == "1":          # socket activation
        sock = socket.socket(fileno=3)
    else:
        path = Path(f"/run/{NAME}/updater.sock")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(path))
    server = await asyncio.start_unix_server(updater.handle, sock=sock)
    sd_notify("READY=1")
    async with server:
        # Exit when idle; systemd starts us again on the next connection.
        while time.monotonic() - updater.last_activity < IDLE_EXIT_S or updater.busy.locked():
            await asyncio.sleep(30)


def main() -> int:
    if os.geteuid() != 0:
        print("tvbox-updater must run as root (it is started by systemd)", file=sys.stderr)
        return 1
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
