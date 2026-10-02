import tomllib

import pytest
from conftest import DEFAULTS
from evdev import ecodes as e

from tvbox import bindings
from tvbox.bindings import ConfigError, parse, parse_action
from tvbox.keys import parse_combo

BASE = ("defaults", tomllib.loads(DEFAULTS.read_text()))


def override(text):
    return parse([BASE, ("user", tomllib.loads(text))])


def errors(text):
    with pytest.raises(ConfigError) as info:
        override(text)
    return info.value.errors


def test_defaults_match_the_brief(defaults):
    g = defaults.global_
    assert str(g["ok"].press) == "key:Return"
    assert str(g["back"].press) == "key:Escape"
    assert (str(g["home"].press), str(g["home"].long)) == ("ui:home", "ui:system_menu")
    assert str(g["start"].press) == "key:space"
    assert str(g["y"].press) == "ui:keyboard"
    assert g["up"].repeat and g["lt"].repeat and g["rt"].repeat
    assert g["lt"].press.kind == g["rt"].press.kind == "volume"
    assert defaults.timing.long_press_ms == 500


def test_key_combos():
    assert parse_combo("Return") == (e.KEY_ENTER,)
    assert parse_combo("ctrl+shift+t") == (e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_T)
    assert parse_combo("XF86AudioPlay") == (e.KEY_PLAYPAUSE,)
    assert parse_combo("KEY_F13") == (e.KEY_F13,)
    for bad in ("", "ctrl+", "hyper+a", "NoSuchKey", "BTN_LEFT"):
        with pytest.raises(ValueError):
            parse_combo(bad)


@pytest.mark.parametrize("text", [
    "key:Return", "ui:home", "volume:+5", "volume:-10", "volume:mute",
    "audio:next_output", "mouse:toggle", "app:restart", "app:launch:youtube",
])
def test_valid_actions(text):
    assert str(parse_action(text)) == text


@pytest.mark.parametrize("text", [
    "Return", "key:", "ui:nope", "volume:5", "volume:+abc", "audio:hdmi",
    "mouse:on", "app:launch:", "app:launch:Bad Id", "shell:rm -rf /", 5,
])
def test_invalid_actions(text):
    with pytest.raises(ValueError):
        parse_action(text)


def test_none_unbinds():
    cfg = override('[global]\ny = "none"')
    assert cfg.lookup("y").actions() == []


def test_override_replaces_only_listed_buttons(defaults):
    cfg = override('[timing]\nrepeat_hz = 20\n[global]\nstart = "key:k"')
    assert str(cfg.global_["start"].press) == "key:k"
    assert cfg.global_["ok"] == defaults.global_["ok"]
    assert cfg.timing.repeat_hz == 20
    assert cfg.timing.long_press_ms == 500


def test_per_app_lookup():
    cfg = override('[app.youtube]\nstart = "key:k"')
    assert str(cfg.lookup("start", "youtube").press) == "key:k"
    assert str(cfg.lookup("start", "netflix").press) == "key:space"
    assert str(cfg.lookup("ok", "youtube").press) == "key:Return"
    assert cfg.lookup("rs_up", "youtube") is None


def test_left_stick_falls_back_to_dpad():
    cfg = override('[global]\nls_left = "key:a"\n[app.youtube]\nup = "key:w"')
    assert str(cfg.lookup("ls_up").press) == "key:Up"
    assert str(cfg.lookup("ls_up", "youtube").press) == "key:w"
    assert str(cfg.lookup("ls_left", "youtube").press) == "key:a"
    assert cfg.lookup("rs_left") is None


def test_all_problems_are_reported_at_once():
    errs = errors("""
[timing]
long_press_ms = 10
[global]
fire = "key:x"
ok = "key:NoSuchKey"
x = { press = "key:a", long = "key:b", repeat = true }
lb = { hold = "key:a" }
[app."You Tube"]
ok = "key:a"
[typo]
""")
    text = "\n".join(errs)
    for needle in ("long_press_ms", "[global].fire: unknown button", "NoSuchKey",
                   "repeat and long", "unknown key(s) hold", "app ids", "typo"):
        assert needle in text
    assert all(err.startswith("user: ") for err in errs)


def test_system_menu_must_stay_reachable():
    assert "must stay reachable" in errors('[global]\nhome = "ui:home"\nmenu = "none"')[0]
    # one of the two default menu buttons may go
    override('[global]\nmenu = "none"')
    assert "cannot be rebound per app" in errors('[app.netflix]\nhome = "key:a"')[0]


def test_device_rules_user_first():
    base = ("etc", tomllib.loads('[[device]]\nname = "Foo*"\nprofile = "ignore"'))
    user = ("user", tomllib.loads(
        '[[device]]\nid = "303A:1001"\nmap = { KEY_F1 = "menu" }\n'))
    cfg = parse([BASE, base, user])
    assert [r.profile for r in cfg.devices] == ["remote", "ignore"]
    assert cfg.devices[0].matches("anything", 0x303A, 0x1001)
    assert not cfg.devices[0].matches("anything", 0x303A, 0x1002)
    assert cfg.devices[1].matches("foo remote", 1, 2)


def test_bad_device_rules():
    errs = "\n".join(errors("""
[[device]]
profile = "remote"
[[device]]
name = "x"
id = "zz"
profile = "joystick"
map = { F1 = "menu", KEY_F2 = "nope" }
"""))
    for needle in ("needs name and/or id", "vendor:product", "profile: must be one of",
                   "map.F1", "unknown button 'nope'"):
        assert needle in errs


def test_load_and_validate_text(tmp_path):
    user = tmp_path / "bindings.toml"
    user.write_text('[global]\nstart = "key:k"\n')
    assert str(bindings.load([DEFAULTS, tmp_path / "missing.toml", user]).global_["start"].press) == "key:k"
    user.write_text("[global\n")
    with pytest.raises(ConfigError) as info:
        bindings.load([DEFAULTS, user])
    assert str(user) in info.value.errors[0]
    assert bindings.validate_text('[global]\nx = "key:x"', "edit", [DEFAULTS]) == []
    assert bindings.validate_text("[global", "edit", [DEFAULTS])[0].startswith("edit: ")
    assert "unknown button" in bindings.validate_text('[global]\nq = "none"', "edit", [DEFAULTS])[0]
