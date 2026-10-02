import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

DEFAULTS = ROOT / "src" / "config" / "bindings.toml"


class FakeTimer:
    def __init__(self, when, callback, args):
        self.when, self.callback, self.args = when, callback, args
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    """asyncio's call_later with a clock that tests move by hand."""

    def __init__(self):
        self.now = 0.0
        self.timers = []

    def call_later(self, delay, callback, *args):
        timer = FakeTimer(self.now + delay, callback, args)
        self.timers.append(timer)
        return timer

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = [t for t in self.timers if not t.cancelled and t.when <= end + 1e-9]
            if not due:
                break
            timer = min(due, key=lambda t: t.when)
            self.timers.remove(timer)
            self.now = timer.when
            timer.callback(*timer.args)
        self.now = end


class Recorder:
    def __init__(self):
        self.events = []

    def action(self, action, button):
        self.events.append(str(action))

    def nav(self, button):
        self.events.append(f"nav:{button}")

    def click(self, down):
        self.events.append(f"click:{'down' if down else 'up'}")

    def take(self):
        events, self.events = self.events, []
        return events


@pytest.fixture
def defaults():
    from tvbox import bindings
    return bindings.load([DEFAULTS])


@pytest.fixture
def rig(defaults):
    from tvbox.engine import Engine
    scheduler, out = FakeScheduler(), Recorder()
    return Engine(defaults, scheduler, out), scheduler, out
