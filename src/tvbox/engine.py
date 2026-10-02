"""Button state machine: logical button down/up -> actions (second layer).

Handles short press, long press and hold-repeat, per-app bindings and the
three input modes. Pure logic: time comes from a scheduler with asyncio's
`call_later` interface and results go to an output object, so tests drive it
with a fake clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .bindings import Action, Binding, Config

MODES = ("app", "ui", "mouse")
NAV = ("up", "down", "left", "right", "ok", "back")
_DIRECTIONS = NAV[:4]
# With the overlay open nothing may reach the app underneath: only these
# action types still fire from non-navigation buttons.
_UI_MODE_KINDS = ("ui", "volume", "audio", "mouse")


class Scheduler(Protocol):
    def call_later(self, delay: float, callback, *args) -> Any: ...


class Output(Protocol):
    def action(self, action: Action, button: str) -> None: ...
    def nav(self, button: str) -> None: ...            # ui mode navigation
    def click(self, down: bool) -> None: ...           # mouse mode, left button


@dataclass
class _Held:
    binding: Binding | None = None
    nav: str | None = None        # ui mode: send this navigation button instead
    click: bool = False           # mouse mode: this press holds the mouse button
    timer: Any = None
    done: bool = False            # long action fired, or press cancelled


class Engine:
    def __init__(self, config: Config, scheduler: Scheduler, output: Output):
        self.config = config
        self.scheduler = scheduler
        self.output = output
        self.mode = "app"
        self.app: str | None = None
        self._held: dict[str, _Held] = {}

    # -- state changes ------------------------------------------------------
    def set_config(self, config: Config) -> None:
        self.cancel_held()
        self.config = config

    def set_app(self, app: str | None) -> None:
        if app != self.app:
            self.cancel_held()
            self.app = app

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if mode != self.mode:
            self.cancel_held()
            self.mode = mode

    def cancel_held(self) -> None:
        """Stop repeats and pending presses, e.g. when the focused app changes
        under a held button: its release must not fire in the new context."""
        for held in self._held.values():
            if held.timer:
                held.timer.cancel()
                held.timer = None
            if held.click:
                self.output.click(False)
                held.click = False
            held.done = True

    # -- input --------------------------------------------------------------
    def button(self, name: str, down: bool) -> None:
        if down:
            if name not in self._held:      # ignore duplicates (e.g. two devices)
                self._held[name] = held = self._resolve(name)
                self._press(name, held)
        else:
            held = self._held.pop(name, None)
            if held:
                self._release(name, held)

    def _resolve(self, name: str) -> _Held:
        stick = name[:3] in ("ls_", "rs_")
        if self.mode == "mouse":
            if stick:
                return _Held()               # sticks move the pointer / scroll
            if name == "ok":
                return _Held(click=True)
        if self.mode == "ui":
            target = name[3:] if name.startswith("ls_") else name
            if target in NAV:
                return _Held(nav=target)
            binding = self.config.lookup(name)
            if binding:
                allowed = {k: a if a and a.kind in _UI_MODE_KINDS else None
                           for k, a in (("press", binding.press), ("long", binding.long))}
                binding = Binding(allowed["press"], allowed["long"],
                                  binding.repeat and allowed["press"] is not None)
            return _Held(binding=binding)
        return _Held(binding=self.config.lookup(name, self.app))

    def _press(self, name: str, held: _Held) -> None:
        timing = self.config.timing
        if held.click:
            self.output.click(True)
        elif held.nav:
            self.output.nav(held.nav)
            if held.nav in _DIRECTIONS:
                held.timer = self.scheduler.call_later(
                    timing.repeat_delay_ms / 1000, self._repeat, name, held)
        elif held.binding:
            b = held.binding
            if b.long:
                held.timer = self.scheduler.call_later(
                    timing.long_press_ms / 1000, self._long, name, held)
            elif b.press:
                self.output.action(b.press, name)
                if b.repeat:
                    held.timer = self.scheduler.call_later(
                        timing.repeat_delay_ms / 1000, self._repeat, name, held)

    def _release(self, name: str, held: _Held) -> None:
        if held.timer:
            held.timer.cancel()
        if held.click:
            self.output.click(False)
        elif not held.done and held.binding and held.binding.long and held.binding.press:
            # A button with a long action fires its short press on release.
            self.output.action(held.binding.press, name)

    def _long(self, name: str, held: _Held) -> None:
        held.timer = None
        held.done = True
        self.output.action(held.binding.long, name)

    def _repeat(self, name: str, held: _Held) -> None:
        held.timer = self.scheduler.call_later(
            1 / self.config.timing.repeat_hz, self._repeat, name, held)
        if held.nav:
            self.output.nav(held.nav)
        else:
            self.output.action(held.binding.press, name)
