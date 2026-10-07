#!/usr/bin/python
"""Runs inside the test VM as root (see tests/qemu/session.sh), after
input_test.py: drives the system menu with the fake Xbox controller and checks
the hub's state, the input mode and what reaches the focused app.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

from evdev import ecodes as e
from input_test import RUNTIME, Daemon, Pad, check, drain, failed, output_device, tapped, tv

HUB = "http://127.0.0.1:8080"
SERVICES = "/etc/tvbox/services.toml"
# Two light "apps" (terminals) so the tests do not depend on the internet.
# foot exits with an error when its window is closed; the wrapper makes that
# a clean exit, as when an app is quit from its own menu.
TEST_SERVICES = '''
order = ["alpha", "beta"]
[[service]]
id = "alpha"
name = "Alpha"
kind = "native"
exec = ["sh", "-c", "foot; true"]
icon = "foot"                       # the icon setting (no .desktop file runs "sh")
[[service]]
id = "beta"
name = "Beta"
kind = "native"
exec = ["sh", "-c", "foot; true"]
'''


def state():
    with urllib.request.urlopen(f"{HUB}/api/state", timeout=5) as r:
        return json.load(r)


def api(**cmd):
    request = urllib.request.Request(f"{HUB}/api/cmd", data=json.dumps(cmd).encode())
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as err:
        return json.load(err)


def ui_ready(st):
    """Both pages of the shell and inputd are connected to the hub."""
    return {"home", "overlay"} <= set(st.get("ui_clients", [])) and st.get("input_connected")


def service(st, service_id):
    return next((s for s in st.get("services", []) if s["id"] == service_id), {})


def install_test_services():
    with open(SERVICES, "w") as f:
        f.write(TEST_SERVICES)
    return wait_state(lambda s: service(s, "beta"), 5)


def wait_state(predicate, timeout=10):
    end = time.monotonic() + timeout
    st = {}
    while time.monotonic() < end:
        try:
            st = state()
            if predicate(st):
                break
        except OSError:
            pass
        time.sleep(0.2)
    return st


def swaymsg(*args):
    return tv("sh", "-c", f'swaymsg -s $(ls -t {RUNTIME}/sway-ipc.*.sock | head -1) "$@"', "sh", *args)


def sway_pid():
    return subprocess.run(["pgrep", "-u", "tv", "-x", "sway"], capture_output=True, text=True).stdout.strip()


def long_press(pad, code):
    pad.press(code, hold=0.8)
    time.sleep(0.3)


def step(pad, *moves):
    """d-pad moves: 'up' 'down' 'left' 'right', or a button code."""
    hat = {"up": (e.ABS_HAT0Y, -1), "down": (e.ABS_HAT0Y, 1),
           "left": (e.ABS_HAT0X, -1), "right": (e.ABS_HAT0X, 1)}
    for move in moves:
        if move in hat:
            pad.axis(*hat[move])
        else:
            pad.press(move)
        time.sleep(0.25)


def main():
    for unit in ("tvbox-hub", "tvbox-shell"):
        active = tv("systemctl", "--user", "is-active", unit).stdout.strip()
        check(f"{unit}.service is active", active == "active", active)
    st = wait_state(ui_ready, 30)
    check("shell (home + overlay) and inputd are connected to the hub", ui_ready(st), str(st)[:300])
    if failed:
        return

    install_test_services()
    api(cmd="launch", id="alpha")
    wait_state(lambda s: service(s, "alpha").get("state") == "running")
    api(cmd="launch", id="beta")
    st = wait_state(lambda s: s["app"] == "beta" and service(s, "beta").get("state") == "running")
    check("two test apps are running", st["app"] == "beta" and service(st, "alpha").get("state") == "running",
          str(st.get("services"))[:300])
    time.sleep(1)

    out = output_device()
    daemon = Daemon()
    pad = Pad()
    daemon.wait_status(lambda s: s["devices"])
    drain(out, 0.3)

    # --- open, navigate, close ---
    long_press(pad, e.BTN_MODE)
    st = wait_state(lambda s: s["overlay"] == "menu")
    mode = daemon.call(cmd="status")["mode"]
    check("Xbox long press opens the system menu", st["overlay"] == "menu" and mode == "ui", f"{st['overlay']} {mode}")
    check("menu knows volume and audio outputs", st["volume"] is not None and len(st["sinks"]) >= 1,
          f"{st['volume']} {st['sinks']}")
    output = st["sinks"][0]["id"] if st["sinks"] else ""
    reply = api(cmd="audio_output", id=output)
    st = wait_state(lambda s: any(o["default"] for o in s["sinks"]), 5)
    check("choosing an audio output makes it the current one", reply.get("ok") is not False
          and [o["id"] for o in st["sinks"] if o["default"]] == [output], f"{output} {st['sinks']} {reply}")
    volume = st["volume"]
    drain(out, 0.2)

    step(pad, "down", "down", "right" if volume <= 90 else "left")       # Volume row
    expect = volume + 5 if volume <= 90 else volume - 5
    st = wait_state(lambda s: s["volume"] == expect, 5)
    check("menu: volume row adjusts with left/right", st["volume"] == expect, f"{volume} -> {st['volume']}")
    step(pad, "down", e.BTN_SOUTH)                                        # Mute row
    st = wait_state(lambda s: s["muted"], 5)
    check("menu: A toggles mute", st["muted"] is True, str(st["muted"]))
    step(pad, e.BTN_SOUTH)
    st = wait_state(lambda s: not s["muted"], 5)
    check("menu: and back", st["muted"] is False, str(st["muted"]))
    step(pad, e.BTN_START, e.BTN_TL)
    got = drain(out, 0.3)
    check("nothing is typed into the app while the menu is open", got == [], str(got))

    step(pad, e.BTN_EAST)
    st = wait_state(lambda s: s["overlay"] is None, 5)
    mode = daemon.call(cmd="status")["mode"]
    check("B closes the menu", st["overlay"] is None and mode == "app", f"{st['overlay']} {mode}")
    drain(out, 0.2)
    pad.press(e.BTN_SOUTH)
    got = drain(out)
    check("the controller drives the app again", got == tapped("KEY_ENTER"), str(got))

    long_press(pad, e.BTN_MODE)
    wait_state(lambda s: s["overlay"] == "menu")
    long_press(pad, e.BTN_MODE)
    st = wait_state(lambda s: s["overlay"] is None, 5)
    check("Xbox long press again closes it", st["overlay"] is None, str(st["overlay"]))

    # --- volume outside the menu ---
    volume = state()["volume"]
    pad.axis(e.ABS_Z if volume > 50 else e.ABS_RZ, 1023, hold=0.1)
    expect = volume + (-2 if volume > 50 else 2)
    st = wait_state(lambda s: s["volume"] == expect, 5)
    check("triggers change the volume", st["volume"] == expect, f"{volume} -> {st['volume']}")

    # --- app switcher ---
    pad.press(e.BTN_SELECT)
    st = wait_state(lambda s: s["overlay"] == "menu", 5)
    # the switcher lists running services first, in the configured order
    order = [s["id"] for s in sorted(st["services"], key=lambda s: s["state"] == "stopped")]
    check("View button opens the app switcher", st["view"] == "apps" and order[:2] == ["alpha", "beta"],
          f"{st['view']} {order}")
    moves = (order.index("alpha") - order.index("beta")) % len(order)
    step(pad, *["down"] * moves, e.BTN_SOUTH)
    status = daemon.wait_status(lambda s: s["app"] == "alpha")
    st = wait_state(lambda s: s["overlay"] is None, 5)
    check("selecting an app switches to it and closes the menu",
          status["app"] == "alpha" and st["overlay"] is None, f"{status['app']} {st['overlay']}")

    pad.press(e.BTN_MODE)
    status = daemon.wait_status(lambda s: s["app"] == "home")
    check("Xbox short press goes home", status["app"] == "home", str(status["app"]))

    # --- mouse mode from the menu ---
    long_press(pad, e.BTN_MODE)
    wait_state(lambda s: s["overlay"] == "menu")
    step(pad, *["down"] * 7, e.BTN_SOUTH)                            # Mouse mode
    status = daemon.wait_status(lambda s: s["mode"] == "mouse")
    st = state()
    check("menu: mouse mode on", status["mode"] == "mouse" and st["mouse"] and st["overlay"] is None,
          f"{status['mode']} {st['mouse']} {st['overlay']}")
    long_press(pad, e.BTN_MODE)
    st = wait_state(lambda s: s["overlay"] == "menu")
    mode = daemon.call(cmd="status")["mode"]
    check("the menu opens from mouse mode", st["overlay"] == "menu" and mode == "ui", f"{st['overlay']} {mode}")
    step(pad, e.BTN_EAST)
    status = daemon.wait_status(lambda s: s["mode"] == "mouse")
    check("closing it returns to mouse mode", status["mode"] == "mouse", status["mode"])
    long_press(pad, e.BTN_MODE)
    wait_state(lambda s: s["overlay"] == "menu")
    step(pad, *["down"] * 7, e.BTN_SOUTH)                            # Mouse mode
    status = daemon.wait_status(lambda s: s["mode"] == "app")
    check("menu: mouse mode off", status["mode"] == "app" and not state()["mouse"], status["mode"])

    # --- a broken bindings file shows up in the menu state ---
    conf = f"{RUNTIME}/../../../home/tv/.config/tvbox/bindings.toml"
    subprocess.run(["sudo", "-u", "tv", "sh", "-c", f'echo "[global" > {conf}'])
    st = wait_state(lambda s: s["config_errors"], 5)
    check("bindings errors reach the UI", bool(st["config_errors"]), str(st["config_errors"]))
    subprocess.run(["rm", "-f", conf])
    wait_state(lambda s: not s["config_errors"], 5)

    # --- failures must not leave the controller stuck in menu mode ---
    long_press(pad, e.BTN_MODE)
    wait_state(lambda s: s["overlay"] == "menu")
    tv("systemctl", "--user", "kill", "-s", "KILL", "tvbox-shell")
    status = daemon.wait_status(lambda s: s["mode"] == "app")
    check("shell dies with the menu open: input returns to the app", status["mode"] == "app", status["mode"])
    st = wait_state(ui_ready, 20)
    long_press(pad, e.BTN_MODE)
    st = wait_state(lambda s: s["overlay"] == "menu")
    check("shell is restarted and the menu opens again", st["overlay"] == "menu", str(st.get("overlay")))

    tv("systemctl", "--user", "kill", "-s", "KILL", "tvbox-hub")
    status = daemon.wait_status(lambda s: s["mode"] == "app", 15)
    check("hub dies with the menu open: input returns to the app", status["mode"] == "app", status["mode"])
    st = wait_state(ui_ready, 20)
    long_press(pad, e.BTN_MODE)
    st = wait_state(lambda s: s.get("overlay") == "menu")
    check("hub is restarted and the menu opens again", st.get("overlay") == "menu", str(st.get("overlay")))
    step(pad, e.BTN_EAST)

    # --- restart session ---
    old = sway_pid()
    long_press(pad, e.BTN_MODE)
    wait_state(lambda s: s["overlay"] == "menu")
    step(pad, "up", "up", e.BTN_SOUTH)            # Restart session (wraps from the top)
    time.sleep(0.3)
    step(pad, "down", e.BTN_SOUTH)                # confirm
    new = old
    for _ in range(60):
        time.sleep(0.5)
        new = sway_pid()
        if new and new != old:
            break
    check("menu: restart session restarts sway (no login prompt)", bool(new) and new != old, f"{old} -> {new}")
    st = wait_state(lambda s: ui_ready(s) and s.get("overlay") is None, 40)
    check("shell reconnects after the session restart", ui_ready(st), str(st)[:300])
    status = daemon.wait_status(lambda s: s["mode"] == "app", 10)
    long_press(pad, e.BTN_MODE)
    st = wait_state(lambda s: s.get("overlay") == "menu")
    check("and the menu works in the new session", st.get("overlay") == "menu", str(st.get("overlay")))
    step(pad, e.BTN_EAST)
    wait_state(lambda s: s.get("overlay") is None, 5)
    pad.close()


if __name__ == "__main__":
    main()
    print(f"{'FAILED: ' + ', '.join(failed) if failed else 'all menu checks passed'}")
    sys.exit(1 if failed else 0)
