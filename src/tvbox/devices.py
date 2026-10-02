"""Device profiles: classify input devices and translate their raw evdev
events into logical buttons (first layer of the two-layer mapping).

No device I/O here, so everything is testable without hardware.
"""
from __future__ import annotations

from evdev import ecodes as e

from .bindings import DeviceRule

VIRTUAL_NAME = "tvbox virtual input"

# xpad (USB) and xpadneo (Bluetooth) report the same codes. BTN_X/BTN_Y follow
# the labels on an Xbox pad (the kernel's BTN_NORTH/BTN_WEST aliases do not).
GAMEPAD_KEYS = {
    e.BTN_SOUTH: "ok", e.BTN_EAST: "back", e.BTN_X: "x", e.BTN_Y: "y",
    e.BTN_TL: "lb", e.BTN_TR: "rb", e.BTN_SELECT: "view", e.BTN_START: "start",
    e.BTN_MODE: "home", e.BTN_THUMBL: "ls", e.BTN_THUMBR: "rs",
    e.BTN_DPAD_UP: "up", e.BTN_DPAD_DOWN: "down",
    e.BTN_DPAD_LEFT: "left", e.BTN_DPAD_RIGHT: "right",
    # triggers on pads that report them as buttons
    e.BTN_TL2: "lt", e.BTN_TR2: "rt",
}

REMOTE_KEYS = {
    e.KEY_UP: "up", e.KEY_DOWN: "down", e.KEY_LEFT: "left", e.KEY_RIGHT: "right",
    e.KEY_ENTER: "ok", e.KEY_KPENTER: "ok", e.KEY_OK: "ok", e.KEY_SELECT: "ok",
    e.KEY_ESC: "back", e.KEY_BACK: "back", e.KEY_BACKSPACE: "back", e.KEY_EXIT: "back",
    e.KEY_HOMEPAGE: "home", e.KEY_HOME: "home",
    e.KEY_MENU: "menu", e.KEY_COMPOSE: "menu", e.KEY_CONTEXT_MENU: "menu",
    e.KEY_PLAYPAUSE: "play_pause", e.KEY_PLAY: "play_pause", e.KEY_PAUSE: "play_pause",
    e.KEY_PLAYCD: "play_pause", e.KEY_PAUSECD: "play_pause",
    e.KEY_VOLUMEUP: "vol_up", e.KEY_VOLUMEDOWN: "vol_down", e.KEY_MUTE: "mute",
    e.KEY_NEXTSONG: "next", e.KEY_PREVIOUSSONG: "prev",
    e.KEY_FASTFORWARD: "ff", e.KEY_REWIND: "rew",
}

_ARROWS = {e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT}
_SELECT = {e.KEY_ENTER, e.KEY_OK, e.KEY_SELECT}
_LETTERS = {e.KEY_Q, e.KEY_A, e.KEY_Z, e.KEY_M, e.KEY_P}

STICK_ON, STICK_OFF, STICK_STICKY = 0.6, 0.4, 0.15
TRIGGER_ON, TRIGGER_OFF = 0.5, 0.3


def classify(name: str, vendor: int, product: int, keys: set[int], has_abs: bool,
             rules: tuple[DeviceRule, ...] = ()) -> tuple[str, bool, dict[int, str]] | None:
    """(profile, grab, extra key map) for a device, or None to leave it alone.

    Config rules win. Otherwise: gamepads and remote-like devices (arrows + OK
    but no alphabet) are handled and grabbed; real keyboards, mice, power
    buttons and everything else are not touched.
    """
    if name == VIRTUAL_NAME:
        return None
    for rule in rules:
        if rule.matches(name, vendor, product):
            if rule.profile == "ignore":
                return None
            extra = {e.ecodes[k]: button for k, button in rule.map.items()}
            return rule.profile, rule.grab, extra
    if e.BTN_SOUTH in keys and has_abs:
        return "gamepad", True, {}
    if _ARROWS <= keys and keys & _SELECT and not _LETTERS <= keys:
        return "remote", True, {}
    return None


class Remote:
    """Keyboard-like device: keys and consumer-control keys -> buttons."""

    def __init__(self, extra: dict[int, str] | None = None):
        self.keymap = REMOTE_KEYS | (extra or {})

    def feed(self, etype: int, code: int, value: int) -> list[tuple[str, bool]]:
        if etype == e.EV_KEY and value in (0, 1) and code in self.keymap:
            return [(self.keymap[code], bool(value))]
        return []


class Gamepad:
    """Xbox-style pad: buttons, d-pad hat, triggers and sticks -> buttons.

    Sticks become four-way `ls_*` / `rs_*` buttons with hysteresis; their
    analog position stays available in `axes` for mouse mode.
    """

    def __init__(self, absinfo: dict[int, tuple[int, int]], extra: dict[int, str] | None = None):
        self.keymap = GAMEPAD_KEYS | (extra or {})
        self.absinfo = absinfo
        self.axes = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}
        self._stick_dir: dict[str, str | None] = {"ls": None, "rs": None}
        self._held: set[str] = set()      # trigger buttons currently down
        self._hat = {e.ABS_HAT0X: 0, e.ABS_HAT0Y: 0}
        # Without xpadneo, the kernel's generic HID driver puts a Bluetooth
        # Xbox pad's triggers on BRAKE/GAS and the right stick on Z/RZ.
        if e.ABS_GAS in absinfo and e.ABS_BRAKE in absinfo:
            self._triggers = {e.ABS_BRAKE: "lt", e.ABS_GAS: "rt"}
            self._sticks = {e.ABS_X: "lx", e.ABS_Y: "ly", e.ABS_Z: "rx", e.ABS_RZ: "ry"}
        else:
            self._triggers = {e.ABS_Z: "lt", e.ABS_RZ: "rt"}
            self._sticks = {e.ABS_X: "lx", e.ABS_Y: "ly", e.ABS_RX: "rx", e.ABS_RY: "ry"}

    def _unit(self, code: int, value: int) -> float:
        """Axis position as 0..1."""
        lo, hi = self.absinfo.get(code, (0, 255))
        return min(1.0, max(0.0, (value - lo) / (hi - lo))) if hi > lo else 0.0

    def feed(self, etype: int, code: int, value: int) -> list[tuple[str, bool]]:
        if etype == e.EV_KEY:
            if value in (0, 1) and code in self.keymap:
                return [(self.keymap[code], bool(value))]
            return []
        if etype != e.EV_ABS:
            return []
        if code in self._hat:
            return self._feed_hat(code, value)
        if code in self._triggers:
            button, pos = self._triggers[code], self._unit(code, value)
            if button not in self._held and pos >= TRIGGER_ON:
                self._held.add(button)
                return [(button, True)]
            if button in self._held and pos <= TRIGGER_OFF:
                self._held.discard(button)
                return [(button, False)]
            return []
        if code in self._sticks:
            axis = self._sticks[code]
            self.axes[axis] = self._unit(code, value) * 2 - 1
            return self._feed_stick("ls" if axis[0] == "l" else "rs")
        return []

    def _feed_hat(self, code: int, value: int) -> list[tuple[str, bool]]:
        names = ("left", "right") if code == e.ABS_HAT0X else ("up", "down")
        old, new = self._hat[code], (value > 0) - (value < 0)
        self._hat[code] = new
        out = []
        if old != new:
            if old:
                out.append((names[old > 0], False))
            if new:
                out.append((names[new > 0], True))
        return out

    def _feed_stick(self, stick: str) -> list[tuple[str, bool]]:
        x, y = self.axes[stick[0] + "x"], self.axes[stick[0] + "y"]
        old = self._stick_dir[stick]
        new = None
        if max(abs(x), abs(y)) >= (STICK_OFF if old else STICK_ON):
            if abs(x) > abs(y):
                new = "right" if x > 0 else "left"
            else:
                new = "down" if y > 0 else "up"
            # Near the diagonal, keep the current direction instead of flapping.
            if old and new != old and abs(abs(x) - abs(y)) < STICK_STICKY:
                new = old
        if new == old:
            return []
        self._stick_dir[stick] = new
        out = []
        if old:
            out.append((f"{stick}_{old}", False))
        if new:
            out.append((f"{stick}_{new}", True))
        return out

    def release_all(self) -> list[tuple[str, bool]]:
        """Button-up events for everything the analog inputs hold down."""
        out = [(b, False) for b in sorted(self._held)]
        out += [(f"{s}_{d}", False) for s, d in self._stick_dir.items() if d]
        for code, value in self._hat.items():
            if value:
                names = ("left", "right") if code == e.ABS_HAT0X else ("up", "down")
                out.append((names[value > 0], False))
        self._held.clear()
        self._stick_dir = {"ls": None, "rs": None}
        self._hat = dict.fromkeys(self._hat, 0)
        return out
