#!/usr/bin/python
"""Runs inside the test VM as root (see tests/qemu/session.sh).

Plugs in a fake Xbox controller through uinput (same name, ids and
capabilities as the kernel's xpad driver reports) and checks what
tvbox-inputd does with it: the keys coming out of the virtual input device
and the events on input.sock.
"""
import json
import pwd
import select
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import evdev
from evdev import AbsInfo, UInput
from evdev import ecodes as e

TV = pwd.getpwnam("tv")
RUNTIME = f"/run/user/{TV.pw_uid}"
USER_CONF = Path(TV.pw_dir) / ".config/tvbox/bindings.toml"
STICK = AbsInfo(0, -32768, 32767, 16, 128, 0)
TRIGGER = AbsInfo(0, 0, 1023, 0, 0, 0)
HAT = AbsInfo(0, -1, 1, 0, 0, 0)
failed = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"\n       {detail}" if not ok else ""), flush=True)
    if not ok:
        failed.append(name)


def tv(*cmd):
    env = ["env", f"XDG_RUNTIME_DIR={RUNTIME}"]
    return subprocess.run(["sudo", "-u", "tv", *env, *cmd], capture_output=True, text=True)


def make_pad():
    return UInput(
        {e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_X, e.BTN_Y, e.BTN_TL, e.BTN_TR,
                    e.BTN_SELECT, e.BTN_START, e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR],
         e.EV_ABS: [(e.ABS_X, STICK), (e.ABS_Y, STICK), (e.ABS_RX, STICK), (e.ABS_RY, STICK),
                    (e.ABS_Z, TRIGGER), (e.ABS_RZ, TRIGGER),
                    (e.ABS_HAT0X, HAT), (e.ABS_HAT0Y, HAT)]},
        name="Microsoft X-Box One pad", vendor=0x045E, product=0x02EA, version=0x0408,
        bustype=e.BUS_USB)


class Pad:
    def __init__(self):
        self.ui = make_pad()

    def set(self, etype, code, value):
        self.ui.write(etype, code, value)
        self.ui.syn()

    def press(self, code, hold=0.05):
        self.set(e.EV_KEY, code, 1)
        time.sleep(hold)
        self.set(e.EV_KEY, code, 0)

    def axis(self, code, value, hold=0.05, rest=0):
        self.set(e.EV_ABS, code, value)
        time.sleep(hold)
        self.set(e.EV_ABS, code, rest)

    def close(self):
        self.ui.close()


class Daemon:
    """inputd's control socket: events in, commands out."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(f"{RUNTIME}/tvbox/input.sock")
        self.buf = b""
        self.next_id = 0

    def read(self, seconds=0.3):
        """All messages arriving within `seconds`."""
        end, out = time.monotonic() + seconds, []
        while (left := end - time.monotonic()) > 0:
            if select.select([self.sock], [], [], left)[0]:
                self.buf += self.sock.recv(65536)
        *lines, self.buf = self.buf.split(b"\n")
        out += [json.loads(line) for line in lines if line]
        return out

    def call(self, **cmd):
        self.next_id += 1
        self.sock.sendall(json.dumps({"id": self.next_id, **cmd}).encode() + b"\n")
        for _ in range(20):
            for msg in self.read(0.1):
                if msg.get("reply") == self.next_id:
                    return msg
        raise TimeoutError(f"no reply to {cmd}")

    def wait_status(self, predicate, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.call(cmd="status")
            if predicate(status):
                return status
            time.sleep(0.2)
        return status


def output_device():
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.name == "tvbox virtual input":
            return dev
        dev.close()
    return None


def drain(dev, seconds=0.3):
    """Output events within `seconds` as ['KEY_ENTER:1', 'REL_X:4', ...]."""
    end, out = time.monotonic() + seconds, []
    while (left := end - time.monotonic()) > 0:
        if select.select([dev.fd], [], [], left)[0]:
            for ev in dev.read():
                if ev.type == e.EV_KEY:
                    name = e.bytype[e.EV_KEY][ev.code]
                    name = name if isinstance(name, str) else sorted(name)[0]
                    out.append(f"{name}:{ev.value}")
                elif ev.type == e.EV_REL:
                    out.append(f"{e.REL[ev.code]}:{ev.value}")
    return out


def close_menu(daemon):
    """Ask the hub (if it runs) to close the system menu again."""
    request = urllib.request.Request("http://127.0.0.1:8080/api/cmd", data=b'{"cmd": "close"}')
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except OSError:
        pass
    daemon.wait_status(lambda s: s["mode"] == "app", 5)
    daemon.read(0.2)


def neutral_workspace():
    """An empty workspace: the keys under test must not land on the home
    screen, where Enter would launch a service."""
    tv("sh", "-c", f"swaymsg -s $(ls -t {RUNTIME}/sway-ipc.*.sock | head -1) workspace scratch")


def tapped(key):
    return [f"{key}:1", f"{key}:0"]


def actions(messages):
    return [m["action"] for m in messages if m.get("event") == "action"]


def main():
    unit = tv("systemctl", "--user", "is-active", "tvbox-inputd.service").stdout.strip()
    check("tvbox-inputd.service is active", unit == "active", unit)
    out = output_device()
    check("virtual input device exists", out is not None)
    if not out or unit != "active":
        return
    inputs = tv("sh", "-c", f"swaymsg -s $(ls -t {RUNTIME}/sway-ipc.*.sock | head -1) -t get_inputs -r").stdout
    kinds = {i["type"] for i in json.loads(inputs or "[]") if i["name"] == "tvbox virtual input"}
    check("sway uses it as keyboard and pointer", {"keyboard", "pointer"} <= kinds, str(kinds))

    USER_CONF.unlink(missing_ok=True)
    neutral_workspace()
    daemon = Daemon()
    hello = daemon.read(0.5)
    check("hello on connect", hello and hello[0].get("event") == "hello", str(hello))
    check("default bindings load cleanly", hello and hello[0].get("config_errors") == [], str(hello))

    pad = Pad()
    status = daemon.wait_status(lambda s: s["devices"])
    devs = status["devices"]
    check("hot-plugged pad is detected as a grabbed gamepad",
          len(devs) == 1 and devs[0]["profile"] == "gamepad" and devs[0]["grabbed"], str(devs))
    drain(out, 0.2)
    daemon.read(0.2)

    # --- default mapping from the brief ---
    for label, code, key in (("A = Enter", e.BTN_SOUTH, "KEY_ENTER"), ("B = Back", e.BTN_EAST, "KEY_ESC"),
                             ("Start = play/pause", e.BTN_START, "KEY_SPACE"),
                             ("LB = seek back", e.BTN_TL, "KEY_LEFT"),
                             ("RB = seek forward", e.BTN_TR, "KEY_RIGHT")):
        pad.press(code)
        got = drain(out)
        check(label, got == tapped(key), str(got))

    for label, code, value, key in (("d-pad left", e.ABS_HAT0X, -1, "KEY_LEFT"),
                                    ("d-pad down", e.ABS_HAT0Y, 1, "KEY_DOWN"),
                                    ("left stick up", e.ABS_Y, -32768, "KEY_UP"),
                                    ("left stick right", e.ABS_X, 32767, "KEY_RIGHT")):
        pad.axis(code, value)
        got = drain(out)
        check(f"{label} = arrow key", got == tapped(key), str(got))

    pad.axis(e.ABS_HAT0Y, -1, hold=1.2)
    got = drain(out)
    check("holding the d-pad repeats", 8 <= got.count("KEY_UP:1") <= 14, str(got))

    pad.press(e.BTN_MODE)
    got = actions(daemon.read())
    check("Xbox button short = home", got == ["ui:home"], str(got))
    pad.set(e.EV_KEY, e.BTN_MODE, 1)
    time.sleep(0.35)
    early = actions(daemon.read(0.05))
    time.sleep(0.4)
    got = actions(daemon.read(0.1))
    pad.set(e.EV_KEY, e.BTN_MODE, 0)
    got += actions(daemon.read())
    check("Xbox button long = system menu, while still held",
          early == [] and got == ["ui:system_menu"], f"{early} {got}")
    close_menu(daemon)          # the hub has opened it; menu_test.py covers the menu itself
    neutral_workspace()         # the short press went to the home screen

    pad.axis(e.ABS_RZ, 1023)
    pad.axis(e.ABS_Z, 1023)
    pad.press(e.BTN_Y)
    got = actions(daemon.read())
    check("triggers = volume, Y = keyboard", got == ["volume:+2", "volume:-2", "ui:keyboard"], str(got))
    got = drain(out, 0.1)
    check("hub actions type nothing", got == [], str(got))

    # --- config override, reload, validation ---
    USER_CONF.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["chown", "-R", "tv:tv", str(USER_CONF.parent.parent)])
    USER_CONF.write_text('[global]\nstart = "key:p"\n[app.youtube]\nstart = "key:k"\n')
    events = [m for m in daemon.read(1.5) if m.get("event") == "config"]
    check("saving the user file reloads bindings", events and events[-1]["errors"] == [], str(events))
    pad.press(e.BTN_START)
    got = drain(out)
    check("user override applies", got == tapped("KEY_P"), str(got))
    pad.press(e.BTN_SOUTH)
    got = drain(out)
    check("other buttons keep defaults", got == tapped("KEY_ENTER"), str(got))

    tv("sh", "-c", f"swaymsg -s $(ls -t {RUNTIME}/sway-ipc.*.sock | head -1) workspace youtube")
    status = daemon.wait_status(lambda s: s["app"] == "youtube")
    check("focused workspace is the app id", status["app"] == "youtube", str(status["app"]))
    drain(out, 0.1)
    pad.press(e.BTN_START)
    got = drain(out)
    check("per-app binding applies in that app", got == tapped("KEY_K"), str(got))
    tv("sh", "-c", f"swaymsg -s $(ls -t {RUNTIME}/sway-ipc.*.sock | head -1) workspace scratch")
    daemon.wait_status(lambda s: s["app"] != "youtube")
    pad.press(e.BTN_START)
    got = drain(out)
    check("and not elsewhere", got == tapped("KEY_P"), str(got))

    daemon.read(0.1)
    USER_CONF.write_text('[global]\nstart = "key:NoSuchKey"\nhome = "none"\n')
    events = [m for m in daemon.read(1.5) if m.get("event") == "config"]
    check("a broken file is rejected with a reason",
          events and any("NoSuchKey" in err for err in events[-1]["errors"]), str(events))
    pad.press(e.BTN_START)
    got = drain(out)
    check("previous bindings stay active", got == tapped("KEY_P"), str(got))
    ctl = tv("tvbox-ctl", "check")
    check("tvbox-ctl check reports it", ctl.returncode == 1 and "NoSuchKey" in ctl.stderr, ctl.stderr)
    USER_CONF.unlink()
    events = [m for m in daemon.read(1.5) if m.get("event") == "config"]
    check("removing the file restores defaults", events and events[-1]["errors"] == [], str(events))
    pad.press(e.BTN_START)
    got = drain(out)
    check("Start is play/pause again", got == tapped("KEY_SPACE"), str(got))

    # --- modes ---
    daemon.call(cmd="mode", mode="ui")
    daemon.read(0.1)
    pad.axis(e.ABS_HAT0Y, 1)
    pad.press(e.BTN_SOUTH)
    pad.press(e.BTN_START)
    navs = [m["button"] for m in daemon.read() if m.get("event") == "nav"]
    got = drain(out, 0.1)
    check("ui mode: navigation goes to the overlay, nothing is typed",
          navs == ["down", "ok", "start"] and got == [], f"{navs} {got}")
    daemon.call(cmd="mode", mode="app")

    ctl = tv("tvbox-ctl", "action", "mouse:toggle")
    status = daemon.wait_status(lambda s: s["mode"] == "mouse")
    check("mouse mode via tvbox-ctl", ctl.returncode == 0 and status["mode"] == "mouse", ctl.stderr)
    drain(out, 0.1)
    pad.axis(e.ABS_X, 32767, hold=0.4)
    got = drain(out)
    dx = sum(int(ev.split(":")[1]) for ev in got if ev.startswith("REL_X"))
    check("mouse mode: left stick moves the pointer", dx > 100 and not any("KEY_" in ev for ev in got),
          f"dx={dx} {got[:6]}")
    pad.axis(e.ABS_RY, 32767, hold=0.4)
    got = drain(out)
    wheel = sum(int(ev.split(":")[1]) for ev in got if ev.startswith("REL_WHEEL"))
    check("mouse mode: right stick scrolls", wheel < -2, f"wheel={wheel} {got[:6]}")
    pad.press(e.BTN_SOUTH)
    got = drain(out)
    check("mouse mode: A clicks", got == tapped("BTN_LEFT"), str(got))
    daemon.call(cmd="mode", mode="app")

    # --- hotplug and restart ---
    pad.close()
    status = daemon.wait_status(lambda s: not s["devices"])
    check("unplugged pad disappears", status["devices"] == [], str(status["devices"]))
    pad = Pad()
    daemon.wait_status(lambda s: s["devices"])
    drain(out, 0.2)
    pad.press(e.BTN_SOUTH)
    got = drain(out)
    check("re-plugged pad works", got == tapped("KEY_ENTER"), str(got))

    main_pid = tv("systemctl", "--user", "show", "-p", "MainPID", "--value", "tvbox-inputd").stdout.strip()
    subprocess.run(["kill", "-9", main_pid])
    for _ in range(40):
        time.sleep(0.25)
        new = output_device()
        if new and tv("systemctl", "--user", "is-active", "tvbox-inputd").stdout.strip() == "active":
            break
    daemon = Daemon()
    daemon.wait_status(lambda s: s["devices"])
    drain(new, 0.2)
    pad.press(e.BTN_SOUTH)
    got = drain(new)
    check("killed daemon is restarted and takes the pad back", got == tapped("KEY_ENTER"), str(got))
    pad.close()


if __name__ == "__main__":
    main()
    print(f"{'FAILED: ' + ', '.join(failed) if failed else 'all input checks passed'}")
    sys.exit(1 if failed else 0)
