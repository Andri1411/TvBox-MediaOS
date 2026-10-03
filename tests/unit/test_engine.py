import tomllib

from conftest import DEFAULTS

from tvbox.bindings import parse


def config(text):
    return parse([("defaults", tomllib.loads(DEFAULTS.read_text())),
                  ("user", tomllib.loads(text))])


def tap(engine, button):
    engine.button(button, True)
    engine.button(button, False)


def test_plain_press_fires_on_button_down(rig):
    engine, clock, out = rig
    engine.button("ok", True)
    assert out.take() == ["key:Return"]
    clock.advance(5)
    engine.button("ok", False)
    assert out.take() == []


def test_short_press_with_long_binding_fires_on_release(rig):
    engine, clock, out = rig
    engine.button("home", True)
    clock.advance(0.3)
    assert out.take() == []
    engine.button("home", False)
    assert out.take() == ["ui:home"]
    clock.advance(2)
    assert out.take() == []


def test_long_press_fires_while_held_and_not_again(rig):
    engine, clock, out = rig
    engine.button("home", True)
    clock.advance(0.49)
    assert out.take() == []
    clock.advance(0.02)
    assert out.take() == ["ui:system_menu"]
    clock.advance(3)
    engine.button("home", False)
    assert out.take() == []


def test_hold_repeat(rig):
    engine, clock, out = rig
    engine.button("up", True)
    assert out.take() == ["key:Up"]
    clock.advance(0.34)
    assert out.take() == []
    clock.advance(0.02)                 # repeat_delay_ms = 350
    assert out.take() == ["key:Up"]
    clock.advance(1.0)                  # 12 Hz
    assert len(out.take()) == 12
    engine.button("up", False)
    clock.advance(1.0)
    assert out.take() == []


def test_per_app_binding(rig):
    engine, clock, out = rig
    engine.set_config(config('[app.youtube]\nstart = "key:k"'))
    tap(engine, "start")
    engine.set_app("youtube")
    tap(engine, "start")
    engine.set_app("netflix")
    tap(engine, "start")
    assert out.take() == ["key:space", "key:k", "key:space"]


def test_left_stick_acts_as_dpad_and_right_stick_is_unbound(rig):
    engine, clock, out = rig
    tap(engine, "ls_left")
    tap(engine, "rs_left")
    assert out.take() == ["key:Left"]


def test_duplicate_down_is_ignored(rig):
    engine, clock, out = rig
    engine.button("ok", True)
    engine.button("ok", True)
    engine.button("ok", False)
    engine.button("ok", False)
    assert out.take() == ["key:Return"]


def test_app_change_cancels_held_buttons(rig):
    engine, clock, out = rig
    engine.button("up", True)
    engine.button("home", True)
    out.take()
    engine.set_app("youtube")
    clock.advance(2)
    engine.button("home", False)
    engine.button("up", False)
    assert out.take() == []
    tap(engine, "up")                   # and the buttons work again afterwards
    assert out.take() == ["key:Up"]


def test_ui_mode_navigation_goes_to_the_overlay(rig):
    engine, clock, out = rig
    engine.set_mode("ui")
    for button in ("up", "ls_down", "ok", "back"):
        tap(engine, button)
    assert out.take() == ["nav:up", "nav:down", "nav:ok", "nav:back"]
    engine.button("down", True)
    clock.advance(0.36)
    assert out.take() == ["nav:down", "nav:down"]
    engine.button("down", False)
    engine.button("ok", True)
    clock.advance(2)                    # ok does not repeat
    assert out.take() == ["nav:ok"]


def test_ui_mode_types_nothing_into_the_app(rig):
    engine, clock, out = rig
    engine.set_app("youtube")
    engine.set_mode("ui")
    for button in ("lb", "rs_up", "play_pause", "rb"):
        tap(engine, button)
    assert out.take() == []
    tap(engine, "x")                    # the on-screen keyboard uses these two
    tap(engine, "start")
    assert out.take() == ["nav:x", "nav:start"]
    tap(engine, "rt")
    tap(engine, "mute")
    tap(engine, "y")
    assert out.take() == ["volume:+2", "volume:mute", "ui:keyboard"]


def test_menu_button_works_in_every_mode(rig):
    engine, clock, out = rig
    for mode in ("app", "ui", "mouse"):
        engine.set_mode(mode)
        engine.button("home", True)
        clock.advance(0.6)
        engine.button("home", False)
        tap(engine, "home")
        assert out.take() == ["ui:system_menu", "ui:home"], mode


def test_mode_change_while_menu_button_is_held(rig):
    engine, clock, out = rig
    engine.button("home", True)
    clock.advance(0.6)
    engine.set_mode("ui")               # what the hub does when the menu opens
    engine.button("home", False)
    assert out.take() == ["ui:system_menu"]


def test_mouse_mode(rig):
    engine, clock, out = rig
    engine.set_mode("mouse")
    engine.button("ok", True)
    clock.advance(1)
    engine.button("ok", False)
    assert out.take() == ["click:down", "click:up"]
    tap(engine, "x")
    assert out.take() == ["click:down:right", "click:up:right"]
    tap(engine, "ls_up")                # stick moves the pointer instead
    tap(engine, "rs_down")
    assert out.take() == []
    tap(engine, "up")                   # d-pad and other buttons still work
    tap(engine, "back")
    assert out.take() == ["key:Up", "key:Escape"]


def test_leaving_mouse_mode_releases_the_mouse_button(rig):
    engine, clock, out = rig
    engine.set_mode("mouse")
    engine.button("ok", True)
    engine.set_mode("app")
    engine.button("ok", False)
    assert out.take() == ["click:down", "click:up"]


def test_new_config_applies_and_stops_repeats(rig):
    engine, clock, out = rig
    engine.button("up", True)
    out.take()
    engine.set_config(config('[global]\nup = "key:w"'))
    clock.advance(2)
    assert out.take() == []
    engine.button("up", False)
    tap(engine, "up")
    assert out.take() == ["key:w"]
