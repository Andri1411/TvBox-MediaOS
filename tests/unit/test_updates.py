import asyncio

from tvbox import updates


class FakeHub:
    def push_state(self):
        pass


def fake_updater(monkeypatch, answers):
    async def call(request, on_log=None, timeout=0):
        return answers[request["cmd"]]
    monkeypatch.setattr(updates, "call", call)


def test_restart_needed_survives_a_new_check_and_a_hub_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    fake_updater(monkeypatch, {
        "apply": {"reboot_for": ["linux-lts"], "changed": ["linux-lts"]},
        "check": {"updates": [], "reboot_for": []},
        "snapshots": {"snapshots": []},
    })
    u = updates.Updates(FakeHub())
    assert u.state()["restart_for"] == []
    asyncio.run(u.apply())
    asyncio.run(u.check())
    assert u.status == "none"
    assert u.state()["restart_for"] == ["linux-lts"]
    assert updates.Updates(FakeHub()).state()["restart_for"] == ["linux-lts"]


def test_no_restart_needed_after_an_update_without_one(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    fake_updater(monkeypatch, {"apply": {"reboot_for": []}, "snapshots": {"snapshots": []}})
    u = updates.Updates(FakeHub())
    asyncio.run(u.apply())
    assert u.state()["restart_for"] == []
    (tmp_path / "tvbox" / "restart-needed.json").write_text("not json")
    assert updates.Updates(FakeHub()).state()["restart_for"] == []
