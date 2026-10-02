"""Key names used in `key:` actions -> evdev key codes.

Names follow xkb keysym spelling where one exists (`Return`, `Page_Up`,
`XF86AudioPlay`), because that is what sway and most documentation use. Any
evdev name (`KEY_F13`) is accepted as an escape hatch. Codes are positions on
a US keyboard; sway's layout turns them into characters.
"""
from evdev import ecodes as e

MODIFIERS = {
    "ctrl": e.KEY_LEFTCTRL, "control": e.KEY_LEFTCTRL,
    "shift": e.KEY_LEFTSHIFT,
    "alt": e.KEY_LEFTALT,
    "super": e.KEY_LEFTMETA, "logo": e.KEY_LEFTMETA, "meta": e.KEY_LEFTMETA,
}

_NAMED = {
    "return": e.KEY_ENTER, "enter": e.KEY_ENTER,
    "escape": e.KEY_ESC, "esc": e.KEY_ESC,
    "space": e.KEY_SPACE, "tab": e.KEY_TAB,
    "backspace": e.KEY_BACKSPACE, "delete": e.KEY_DELETE, "insert": e.KEY_INSERT,
    "home": e.KEY_HOME, "end": e.KEY_END,
    "page_up": e.KEY_PAGEUP, "prior": e.KEY_PAGEUP,
    "page_down": e.KEY_PAGEDOWN, "next": e.KEY_PAGEDOWN,
    "up": e.KEY_UP, "down": e.KEY_DOWN, "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
    "minus": e.KEY_MINUS, "equal": e.KEY_EQUAL, "comma": e.KEY_COMMA,
    "period": e.KEY_DOT, "slash": e.KEY_SLASH, "semicolon": e.KEY_SEMICOLON,
    "apostrophe": e.KEY_APOSTROPHE, "grave": e.KEY_GRAVE,
    "bracketleft": e.KEY_LEFTBRACE, "bracketright": e.KEY_RIGHTBRACE,
    "backslash": e.KEY_BACKSLASH, "menu": e.KEY_COMPOSE,
    "xf86audioplay": e.KEY_PLAYPAUSE, "xf86audiopause": e.KEY_PAUSECD,
    "xf86audiostop": e.KEY_STOPCD,
    "xf86audionext": e.KEY_NEXTSONG, "xf86audioprev": e.KEY_PREVIOUSSONG,
    "xf86audiorewind": e.KEY_REWIND, "xf86audioforward": e.KEY_FASTFORWARD,
    "xf86audioraisevolume": e.KEY_VOLUMEUP, "xf86audiolowervolume": e.KEY_VOLUMEDOWN,
    "xf86audiomute": e.KEY_MUTE,
    "xf86back": e.KEY_BACK, "xf86forward": e.KEY_FORWARD,
    "xf86homepage": e.KEY_HOMEPAGE, "xf86reload": e.KEY_REFRESH,
    "xf86search": e.KEY_SEARCH,
}
_NAMED.update({c: getattr(e, f"KEY_{c.upper()}") for c in "abcdefghijklmnopqrstuvwxyz0123456789"})
_NAMED.update({f"f{n}": getattr(e, f"KEY_F{n}") for n in range(1, 25)})

# Everything the virtual keyboard may ever send: it has to be declared when
# the uinput device is created.
ALL_KEYS = sorted({c for n, c in e.ecodes.items()
                   if n.startswith("KEY_") and n not in ("KEY_RESERVED", "KEY_MAX", "KEY_CNT")})


def key_code(name: str) -> int:
    """Code for one key name. Raises ValueError for unknown names."""
    code = _NAMED.get(name.lower())
    if code is None and name.upper().startswith("KEY_"):
        code = e.ecodes.get(name.upper())
    if not isinstance(code, int):
        raise ValueError(f"unknown key {name!r}")
    return code


def parse_combo(combo: str) -> tuple[int, ...]:
    """`ctrl+shift+t` -> (KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_T).

    Modifiers come first and are held while the last key is tapped.
    """
    parts = [p.strip() for p in combo.split("+")]
    if not all(parts):
        raise ValueError(f"malformed key combo {combo!r}")
    codes = []
    for mod in parts[:-1]:
        if mod.lower() not in MODIFIERS:
            raise ValueError(f"unknown modifier {mod!r} in {combo!r}")
        codes.append(MODIFIERS[mod.lower()])
    last = parts[-1]
    codes.append(MODIFIERS[last.lower()] if last.lower() in MODIFIERS else key_code(last))
    return tuple(codes)
