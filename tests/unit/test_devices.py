from evdev import ecodes as e

from tvbox.bindings import DeviceRule
from tvbox.devices import VIRTUAL_NAME, Gamepad, Remote, classify

XPAD_KEYS = {e.BTN_SOUTH, e.BTN_EAST, e.BTN_X, e.BTN_Y, e.BTN_TL, e.BTN_TR,
             e.BTN_SELECT, e.BTN_START, e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR}
KEYBOARD_KEYS = {getattr(e, f"KEY_{c}") for c in "QWERTYUIOPASDFGHJKLZXCVBNM"} | {
    e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT, e.KEY_ENTER, e.KEY_ESC}
REMOTE_KEYS = {e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT, e.KEY_OK, e.KEY_BACK,
               e.KEY_PLAYPAUSE, e.KEY_VOLUMEUP, e.KEY_VOLUMEDOWN}
# xpad: sticks -32768..32767, triggers 0..1023
XPAD_ABS = {e.ABS_X: (-32768, 32767), e.ABS_Y: (-32768, 32767),
            e.ABS_RX: (-32768, 32767), e.ABS_RY: (-32768, 32767),
            e.ABS_Z: (0, 1023), e.ABS_RZ: (0, 1023),
            e.ABS_HAT0X: (-1, 1), e.ABS_HAT0Y: (-1, 1)}


def test_classify():
    assert classify("Microsoft X-Box One pad", 0x045E, 0x02EA, XPAD_KEYS, True) == ("gamepad", True, {})
    assert classify("Some Remote", 1, 2, REMOTE_KEYS, False) == ("remote", True, {})
    # real keyboards, mice and power buttons are left alone
    assert classify("AT Translated Set 2 keyboard", 1, 1, KEYBOARD_KEYS, False) is None
    assert classify("Power Button", 0, 1, {e.KEY_POWER}, False) is None
    assert classify("Mouse", 1, 2, {e.BTN_LEFT, e.BTN_RIGHT}, False) is None
    # never read our own output device back in
    assert classify(VIRTUAL_NAME, 0, 0, KEYBOARD_KEYS, False) is None


def test_classify_rules_override_detection():
    rules = (DeviceRule(name="ESP32*", profile="remote", grab=True, map={"KEY_F1": "menu"}),
             DeviceRule(id="045e:02ea", profile="ignore"))
    assert classify("esp32 remote", 9, 9, KEYBOARD_KEYS, False, rules) == (
        "remote", True, {e.KEY_F1: "menu"})
    assert classify("Microsoft X-Box One pad", 0x045E, 0x02EA, XPAD_KEYS, True, rules) is None


def test_remote_keys():
    remote = Remote({e.KEY_F1: "menu"})
    assert remote.feed(e.EV_KEY, e.KEY_OK, 1) == [("ok", True)]
    assert remote.feed(e.EV_KEY, e.KEY_OK, 2) == []          # kernel autorepeat
    assert remote.feed(e.EV_KEY, e.KEY_OK, 0) == [("ok", False)]
    assert remote.feed(e.EV_KEY, e.KEY_PLAYPAUSE, 1) == [("play_pause", True)]
    assert remote.feed(e.EV_KEY, e.KEY_F1, 1) == [("menu", True)]
    assert remote.feed(e.EV_KEY, e.KEY_Q, 1) == []


def test_gamepad_buttons_follow_xbox_labels():
    pad = Gamepad(XPAD_ABS)
    expect = {e.BTN_SOUTH: "ok", e.BTN_EAST: "back", e.BTN_X: "x", e.BTN_Y: "y",
              e.BTN_MODE: "home", e.BTN_START: "start", e.BTN_SELECT: "view",
              e.BTN_TL: "lb", e.BTN_TR: "rb"}
    for code, button in expect.items():
        assert pad.feed(e.EV_KEY, code, 1) == [(button, True)]
        assert pad.feed(e.EV_KEY, code, 0) == [(button, False)]


def test_dpad_hat():
    pad = Gamepad(XPAD_ABS)
    assert pad.feed(e.EV_ABS, e.ABS_HAT0X, -1) == [("left", True)]
    assert pad.feed(e.EV_ABS, e.ABS_HAT0X, 1) == [("left", False), ("right", True)]
    assert pad.feed(e.EV_ABS, e.ABS_HAT0X, 0) == [("right", False)]
    assert pad.feed(e.EV_ABS, e.ABS_HAT0Y, -1) == [("up", True)]
    assert pad.feed(e.EV_ABS, e.ABS_HAT0Y, 0) == [("up", False)]
    assert pad.feed(e.EV_ABS, e.ABS_HAT0Y, 1) == [("down", True)]


def test_triggers_have_hysteresis():
    pad = Gamepad(XPAD_ABS)
    assert pad.feed(e.EV_ABS, e.ABS_Z, 300) == []
    assert pad.feed(e.EV_ABS, e.ABS_Z, 600) == [("lt", True)]
    assert pad.feed(e.EV_ABS, e.ABS_Z, 1023) == []
    assert pad.feed(e.EV_ABS, e.ABS_Z, 450) == []            # between off and on
    assert pad.feed(e.EV_ABS, e.ABS_Z, 200) == [("lt", False)]
    assert pad.feed(e.EV_ABS, e.ABS_RZ, 1023) == [("rt", True)]


def test_left_stick_is_four_way():
    pad = Gamepad(XPAD_ABS)
    assert pad.feed(e.EV_ABS, e.ABS_X, 10000) == []           # 0.3: inside threshold
    assert pad.feed(e.EV_ABS, e.ABS_X, 30000) == [("ls_right", True)]
    assert pad.feed(e.EV_ABS, e.ABS_X, 16000) == []           # 0.49: still held
    assert pad.feed(e.EV_ABS, e.ABS_Y, 14000) == []           # near diagonal: keep direction
    assert pad.feed(e.EV_ABS, e.ABS_Y, 32767) == [("ls_right", False), ("ls_down", True)]
    assert pad.feed(e.EV_ABS, e.ABS_X, 0) == []
    assert pad.feed(e.EV_ABS, e.ABS_Y, 0) == [("ls_down", False)]
    assert pad.feed(e.EV_ABS, e.ABS_Y, -32768) == [("ls_up", True)]
    assert abs(pad.axes["ly"] + 1) < 1e-6


def test_right_stick_and_release_all():
    pad = Gamepad(XPAD_ABS)
    assert pad.feed(e.EV_ABS, e.ABS_RX, -32768) == [("rs_left", True)]
    pad.feed(e.EV_ABS, e.ABS_RZ, 1023)
    pad.feed(e.EV_ABS, e.ABS_HAT0Y, 1)
    assert sorted(pad.release_all()) == [("down", False), ("rs_left", False), ("rt", False)]
    assert pad.release_all() == []


def test_bluetooth_pad_without_xpadneo():
    # hid-generic layout: triggers on BRAKE/GAS, right stick on Z/RZ
    absinfo = {e.ABS_X: (0, 65535), e.ABS_Y: (0, 65535), e.ABS_Z: (0, 65535),
               e.ABS_RZ: (0, 65535), e.ABS_BRAKE: (0, 1023), e.ABS_GAS: (0, 1023)}
    pad = Gamepad(absinfo)
    assert pad.feed(e.EV_ABS, e.ABS_GAS, 1023) == [("rt", True)]
    assert pad.feed(e.EV_ABS, e.ABS_BRAKE, 1023) == [("lt", True)]
    assert pad.feed(e.EV_ABS, e.ABS_Z, 65535) == [("rs_right", True)]
    assert pad.feed(e.EV_ABS, e.ABS_X, 0) == [("ls_left", True)]
