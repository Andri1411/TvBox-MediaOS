import asyncio

import pytest

from tvbox import bluetooth

pytest.importorskip("dbus_fast")
from dbus_fast import DBusError  # noqa: E402


def test_agent_rejects_everything_unless_pairing_is_open():
    calls = [("RequestPinCode", ("/d",)), ("RequestPasskey", ("/d",)), ("RequestConfirmation", ("/d", 1)),
             ("RequestAuthorization", ("/d",)), ("AuthorizeService", ("/d", "uuid"))]
    closed, open_ = bluetooth.Agent(lambda: False), bluetooth.Agent(lambda: True)
    for name, args in calls:
        with pytest.raises(DBusError) as err:
            getattr(closed, name).__func__(closed, *args)
        assert err.value.type == "org.bluez.Error.Rejected"
        getattr(open_, name).__func__(open_, *args)          # no error: accepted


def test_pairing_window_opens_and_closes():
    class Hub:
        def spawn(self, coro):
            coro.close()

    async def run():
        bt = bluetooth.Bluetooth(Hub())
        assert not bt.pairing_open()
        await bt.open_pairing()                               # no adapter: only the window
        assert bt.pairing_open()
        bt.pairing_until = asyncio.get_running_loop().time() - 1
        assert not bt.pairing_open()
    asyncio.run(run())
