"""Bindings config: logical button -> action, global and per app.

Files are merged in order (package defaults, /etc, user); a later file
replaces whole bindings of the same button, single [timing] keys, and puts its
[[device]] rules first. The same code validates files for inputd and for the
phone's bindings editor.
"""
from __future__ import annotations

import fnmatch
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import NAME
from .keys import parse_combo

# Buttons every device can produce, gamepad extras, and stick directions.
# ls_* fall back to the binding of up/down/left/right when not bound themselves.
BUTTONS = (
    "up", "down", "left", "right", "ok", "back", "home", "menu",
    "play_pause", "vol_up", "vol_down", "mute", "next", "prev", "ff", "rew",
    "x", "y", "lb", "rb", "lt", "rt", "view", "start", "ls", "rs",
    "ls_up", "ls_down", "ls_left", "ls_right",
    "rs_up", "rs_down", "rs_left", "rs_right",
)
PROFILES = ("gamepad", "remote", "ignore")
UI_ACTIONS = ("home", "system_menu", "keyboard", "app_switcher", "back")
MENU_ACTION = "ui:system_menu"
APP_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Action:
    kind: str            # key | ui | volume | audio | mouse | app
    arg: str = ""
    keys: tuple[int, ...] = ()   # evdev codes, for kind == "key"

    def __str__(self) -> str:
        return f"{self.kind}:{self.arg}"


@dataclass(frozen=True)
class Binding:
    press: Action | None = None
    long: Action | None = None
    repeat: bool = False

    def actions(self) -> list[Action]:
        return [a for a in (self.press, self.long) if a]


@dataclass(frozen=True)
class Timing:
    long_press_ms: int = 500
    repeat_delay_ms: int = 350
    repeat_hz: float = 12


@dataclass(frozen=True)
class Mouse:
    speed: float = 900            # pointer px/s at full stick deflection (doubles while held)
    scroll_speed: float = 18      # wheel notches/s at full deflection


@dataclass(frozen=True)
class DeviceRule:
    """Overrides automatic device classification (first matching rule wins)."""
    name: str = "*"              # glob on the device name
    id: str = ""                 # "vendor:product" in hex, empty = any
    profile: str = "remote"
    grab: bool = True
    map: dict[str, str] = field(default_factory=dict)   # evdev key name -> button

    def matches(self, name: str, vendor: int, product: int) -> bool:
        if self.id and self.id.lower() != f"{vendor:04x}:{product:04x}":
            return False
        return fnmatch.fnmatchcase(name.lower(), self.name.lower())


@dataclass(frozen=True)
class Config:
    timing: Timing = Timing()
    mouse: Mouse = Mouse()
    global_: dict[str, Binding] = field(default_factory=dict)
    apps: dict[str, dict[str, Binding]] = field(default_factory=dict)
    devices: tuple[DeviceRule, ...] = ()

    def lookup(self, button: str, app: str | None = None) -> Binding | None:
        """Binding for a button: app override, then global, then the d-pad
        binding for an unbound left stick direction."""
        for name in (button, button[3:] if button.startswith("ls_") else None):
            if name is None:
                continue
            for table in (self.apps.get(app or "", {}), self.global_):
                if name in table:
                    return table[name]
        return None


def parse_action(text: str) -> Action | None:
    """Parse one action string. `none` gives None. Raises ValueError."""
    if not isinstance(text, str):
        raise ValueError(f"action must be a string, got {type(text).__name__}")
    if text == "none":
        return None
    kind, sep, arg = text.partition(":")
    if not sep or not arg:
        raise ValueError(f"{text!r} is not an action (expected e.g. \"key:Return\" or \"none\")")
    if kind == "key":
        return Action("key", arg, parse_combo(arg))
    if kind == "ui":
        if arg not in UI_ACTIONS:
            raise ValueError(f"unknown ui action {arg!r} (one of: {', '.join(UI_ACTIONS)})")
    elif kind == "volume":
        if arg != "mute" and not re.fullmatch(r"[+-]\d{1,3}", arg):
            raise ValueError(f"volume wants +N, -N or mute, got {arg!r}")
    elif kind == "audio":
        if arg != "next_output":
            raise ValueError(f"unknown audio action {arg!r} (only next_output)")
    elif kind == "mouse":
        if arg != "toggle":
            raise ValueError(f"unknown mouse action {arg!r} (only toggle)")
    elif kind == "app":
        launch = arg.removeprefix("launch:")
        if arg != "restart" and not (arg.startswith("launch:") and APP_ID.match(launch)):
            raise ValueError(f"app wants restart or launch:<id>, got {arg!r}")
    else:
        raise ValueError(f"unknown action type {kind!r} in {text!r}")
    return Action(kind, arg)


def _parse_binding(value) -> Binding:
    if isinstance(value, str):
        return Binding(press=parse_action(value))
    if not isinstance(value, dict):
        raise ValueError("binding must be an action string or a table {press, long, repeat}")
    unknown = set(value) - {"press", "long", "repeat"}
    if unknown:
        raise ValueError(f"unknown key(s) {', '.join(sorted(unknown))} (allowed: press, long, repeat)")
    repeat = value.get("repeat", False)
    if not isinstance(repeat, bool):
        raise ValueError("repeat must be true or false")
    b = Binding(press=parse_action(value.get("press", "none")),
                long=parse_action(value.get("long", "none")), repeat=repeat)
    if b.repeat and b.long:
        raise ValueError("repeat and long cannot be combined on one button")
    if b.repeat and not b.press:
        raise ValueError("repeat needs a press action")
    return b


def _parse_table(table, where: str, errors: list[str]) -> dict[str, Binding]:
    out = {}
    if not isinstance(table, dict):
        errors.append(f"{where}: must be a table")
        return out
    for button, value in table.items():
        if button not in BUTTONS:
            errors.append(f"{where}.{button}: unknown button (known: {', '.join(BUTTONS)})")
            continue
        try:
            out[button] = _parse_binding(value)
        except ValueError as err:
            errors.append(f"{where}.{button}: {err}")
    return out


def _parse_device(raw, where: str, errors: list[str]) -> DeviceRule | None:
    from evdev import ecodes
    if not isinstance(raw, dict):
        errors.append(f"{where}: must be a table")
        return None
    ok = True
    unknown = set(raw) - {"name", "id", "profile", "grab", "map"}
    if unknown:
        errors.append(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        ok = False
    if "name" not in raw and "id" not in raw:
        errors.append(f"{where}: needs name and/or id to match a device")
        ok = False
    dev_id = raw.get("id", "")
    if dev_id and not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", str(dev_id)):
        errors.append(f"{where}.id: expected vendor:product in hex, e.g. \"045e:0b13\"")
        ok = False
    profile = raw.get("profile", "remote")
    if profile not in PROFILES:
        errors.append(f"{where}.profile: must be one of {', '.join(PROFILES)}")
        ok = False
    keymap = raw.get("map", {})
    if not isinstance(keymap, dict):
        errors.append(f"{where}.map: must be a table")
        keymap, ok = {}, False
    for key, button in keymap.items():
        if not (key.startswith(("KEY_", "BTN_")) and isinstance(ecodes.ecodes.get(key), int)):
            errors.append(f"{where}.map.{key}: not an evdev key name (e.g. KEY_F1)")
            ok = False
        if button not in BUTTONS:
            errors.append(f"{where}.map.{key}: unknown button {button!r}")
            ok = False
    if not isinstance(raw.get("grab", True), bool) or not isinstance(raw.get("name", "*"), str):
        errors.append(f"{where}: name must be a string and grab true or false")
        ok = False
    if not ok:
        return None
    return DeviceRule(raw.get("name", "*"), str(dev_id), profile, raw.get("grab", True), dict(keymap))


# Numeric settings: section -> key -> (lowest, highest)
_NUMBERS = {
    "timing": {"long_press_ms": (150, 5000), "repeat_delay_ms": (50, 5000), "repeat_hz": (1, 60)},
    "mouse": {"speed": (100, 5000), "scroll_speed": (1, 100)},
}


def parse(documents: list[tuple[str, dict]]) -> Config:
    """Merge already-decoded TOML documents [(label, data), ...], lowest
    priority first. Raises ConfigError listing every problem found."""
    errors: list[str] = []
    numbers: dict[str, dict] = {section: {} for section in _NUMBERS}
    global_: dict[str, Binding] = {}
    apps: dict[str, dict[str, Binding]] = {}
    devices: list[DeviceRule] = []
    for label, doc in documents:
        unknown = set(doc) - {"timing", "mouse", "global", "app", "device"}
        if unknown:
            errors.append(f"{label}: unknown section(s) {', '.join(sorted(unknown))}")
        for section, limits in _NUMBERS.items():
            table = doc.get(section, {})
            if not isinstance(table, dict):
                errors.append(f"{label}: [{section}]: must be a table")
                continue
            for key, value in table.items():
                lo, hi = limits.get(key, (None, None))
                if lo is None:
                    errors.append(f"{label}: [{section}].{key}: unknown setting")
                elif isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                    errors.append(f"{label}: [{section}].{key}: must be a number from {lo} to {hi}")
                else:
                    numbers[section][key] = value
        errs: list[str] = []
        global_.update(_parse_table(doc.get("global", {}), "[global]", errs))
        app_tables = doc.get("app", {})
        if not isinstance(app_tables, dict):
            errs.append("[app]: must be a table of [app.<id>] sections")
            app_tables = {}
        for app, table in app_tables.items():
            if not APP_ID.match(app):
                errs.append(f"[app.{app}]: app ids are lowercase letters, digits, - and _")
                continue
            apps.setdefault(app, {}).update(_parse_table(table, f"[app.{app}]", errs))
        rules = doc.get("device", [])
        if not isinstance(rules, list):
            errs.append("[[device]]: must be an array of tables")
            rules = []
        parsed = [_parse_device(r, f"[[device]] #{i + 1}", errs) for i, r in enumerate(rules)]
        devices[:0] = [r for r in parsed if r]
        errors += [f"{label}: {e}" for e in errs]

    # The system menu is the way out of everything, so it must stay reachable:
    # at least one global button opens it, and apps can't rebind that button.
    menu_buttons = {b for b, bind in global_.items()
                    if any(str(a) == MENU_ACTION for a in bind.actions())}
    if not menu_buttons:
        errors.append(f"[global]: no button is bound to {MENU_ACTION}; "
                      "the system menu must stay reachable")
    for app, table in apps.items():
        for button in sorted(menu_buttons & set(table)):
            errors.append(f"[app.{app}].{button}: this button opens the system menu "
                          "in [global] and cannot be rebound per app")
    if errors:
        raise ConfigError(errors)
    return Config(Timing(**numbers["timing"]), Mouse(**numbers["mouse"]), global_, apps, tuple(devices))


def default_paths() -> list[Path]:
    data = Path(os.environ.get("TVBOX_DATA_DIR", f"/usr/share/{NAME}"))
    user = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / NAME
    return [data / "bindings.toml", Path(f"/etc/{NAME}/bindings.toml"), user / "bindings.toml"]


def load(paths: list[Path] | None = None) -> Config:
    """Load and merge the bindings files that exist. Raises ConfigError."""
    documents, errors = [], []
    for path in paths or default_paths():
        try:
            with open(path, "rb") as f:
                documents.append((str(path), tomllib.load(f)))
        except FileNotFoundError:
            continue
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as err:
            errors.append(f"{path}: {err}")
    if errors:
        raise ConfigError(errors)
    return parse(documents)


def validate_text(text: str, label: str, base: list[Path]) -> list[str]:
    """Problems with `text` as an override on top of the files in `base`
    (for the bindings editor). Empty list = valid."""
    try:
        documents = [(str(p), tomllib.loads(p.read_text())) for p in base if p.exists()]
        parse(documents + [(label, tomllib.loads(text))])
    except tomllib.TOMLDecodeError as err:
        return [f"{label}: {err}"]
    except ConfigError as err:
        return err.errors
    return []
