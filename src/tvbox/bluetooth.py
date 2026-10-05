"""Bluetooth pairing for controllers, headphones and speakers, through BlueZ
on the system D-Bus (dbus-fast).

The hub registers itself as BlueZ's pairing agent with "NoInputNoOutput":
controllers and audio devices pair with "just works", and there is nobody
at a keyboard to type a PIN anyway. Bluetooth audio then shows up as an
audio output (PipeWire) like any other.

The agent accepts nothing unless the user started pairing on the TV or the
phone in the last few minutes, and the adapter is only pairable during that
window: otherwise any device in range could pair itself, e.g. as a keyboard
typing into the box.
"""
# No "from __future__ import annotations" here: dbus-fast reads the D-Bus
# signatures from the method annotations ("o", "s", ...) at runtime.
import asyncio

from .util import setup_logging

log = setup_logging("hub")

try:
    from dbus_fast import BusType, DBusError, Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import ServiceInterface, method
except ImportError:                         # dev hosts without dbus-fast
    MessageBus = None

BLUEZ = "org.bluez"
AGENT_PATH = "/org/tvbox/agent"
SCAN_S = 25
PAIRING_WINDOW_S = 180         # how long after the user starts pairing requests are accepted
KINDS = {"input-gaming": "controller", "audio-headphones": "headphones", "audio-headset": "headphones",
         "audio-card": "speaker", "input-keyboard": "keyboard", "input-mouse": "mouse",
         "phone": "phone", "computer": "computer"}


def describe(path: str, props: dict) -> dict:
    """A device for the UI from its org.bluez.Device1 properties."""
    def value(key, default=None):
        item = props.get(key)
        return item.value if item is not None and hasattr(item, "value") else (item if item is not None else default)
    name = value("Alias") or value("Name") or value("Address", "?")
    return {"path": path, "address": value("Address", ""), "name": name,
            "kind": KINDS.get(value("Icon", ""), "device"), "paired": bool(value("Paired", False)),
            "connected": bool(value("Connected", False)), "trusted": bool(value("Trusted", False)),
            "rssi": value("RSSI")}


if MessageBus:
    class Agent(ServiceInterface):
        """org.bluez.Agent1 (NoInputNoOutput) that accepts requests only while
        the user is pairing (`allowed()`), and rejects them otherwise."""

        def __init__(self, allowed):
            super().__init__("org.bluez.Agent1")
            self.allowed = allowed

        def _check(self, what: str):
            if not self.allowed():
                log.warning("bluetooth: rejected %s (not pairing now)", what)
                raise DBusError("org.bluez.Error.Rejected", "pairing is not open on the TV")

        @method()
        def Release(self):  # noqa: N802 (D-Bus names)
            pass

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: N802,F821
            self._check("RequestPinCode")
            return "0000"

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: N802,F821
            pass

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: N802,F821
            self._check("RequestPasskey")
            return 0

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: N802,F821
            pass

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: N802,F821
            self._check("RequestConfirmation")

        @method()
        def RequestAuthorization(self, device: "o"):  # noqa: N802,F821
            self._check("RequestAuthorization")

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: N802,F821
            self._check("AuthorizeService")

        @method()
        def Cancel(self):  # noqa: N802
            pass


class Bluetooth:
    def __init__(self, hub):
        self.hub = hub
        self.bus = None
        self.adapter: str | None = None
        self.devices: list[dict] = []
        self.scanning = False
        self.busy: str | None = None          # path of a device being paired/connected
        self.message = ""
        self.pairing_until = 0.0              # monotonic time until which pairing is open

    def pairing_open(self) -> bool:
        return asyncio.get_running_loop().time() < self.pairing_until

    async def open_pairing(self) -> None:
        """The user is pairing: accept requests (and be pairable) for a while."""
        self.pairing_until = asyncio.get_running_loop().time() + PAIRING_WINDOW_S
        await self._set_pairable(True)
        self.hub.spawn(self._close_pairing_later())

    async def _close_pairing_later(self) -> None:
        await asyncio.sleep(PAIRING_WINDOW_S + 1)
        if not self.pairing_open():
            await self._set_pairable(False)

    async def _set_pairable(self, on: bool) -> None:
        if not self.adapter:
            return
        try:
            props = await self._interface(self.adapter, "org.freedesktop.DBus.Properties")
            await props.call_set("org.bluez.Adapter1", "Pairable", Variant("b", on))
        except DBusError as err:
            log.warning("bluetooth: pairable=%s: %s", on, err.text)

    def state(self) -> dict:
        return {"available": self.adapter is not None, "devices": self.devices, "scanning": self.scanning,
                "busy": self.busy, "message": self.message}

    async def start(self) -> None:
        """Connect to BlueZ, power the adapter, become the pairing agent."""
        if MessageBus is None:
            return
        try:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self.bus.export(AGENT_PATH, Agent(self.pairing_open))
            manager = await self._interface("/org/bluez", "org.bluez.AgentManager1")
            await manager.call_register_agent(AGENT_PATH, "NoInputNoOutput")
            await manager.call_request_default_agent(AGENT_PATH)
        except (DBusError, OSError) as err:
            log.info("bluetooth not available: %s", err)
            return
        await self.refresh()
        if self.adapter:
            adapter = await self._interface(self.adapter, "org.freedesktop.DBus.Properties")
            await adapter.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
            await adapter.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", False))
            await self._set_pairable(False)

    async def _interface(self, path: str, name: str):
        introspection = await self.bus.introspect(BLUEZ, path)
        return self.bus.get_proxy_object(BLUEZ, path, introspection).get_interface(name)

    async def refresh(self) -> None:
        if not self.bus:
            return
        try:
            manager = await self._interface("/", "org.freedesktop.DBus.ObjectManager")
            objects = await manager.call_get_managed_objects()
        except (DBusError, OSError) as err:
            log.warning("bluetooth: %s", err)
            self.adapter, self.devices = None, []
            return
        adapters = sorted(p for p, ifaces in objects.items() if "org.bluez.Adapter1" in ifaces)
        self.adapter = adapters[0] if adapters else None
        devices = [describe(p, ifaces["org.bluez.Device1"]) for p, ifaces in objects.items()
                   if "org.bluez.Device1" in ifaces]
        # Known devices first, then what the scan found, strongest first.
        self.devices = sorted(devices, key=lambda d: (not d["paired"], -(d["rssi"] or -999), d["name"]))
        self.hub.push_state()

    def device(self, path: str) -> dict:
        found = next((d for d in self.devices if d["path"] == path), None)
        if not found:
            raise ValueError("unknown Bluetooth device")
        return found

    async def scan(self) -> None:
        if not self.adapter or self.scanning:
            return
        self.scanning, self.message = True, ""
        await self.open_pairing()
        self.hub.push_state()
        adapter = await self._interface(self.adapter, "org.bluez.Adapter1")
        try:
            await adapter.call_start_discovery()
            for _ in range(SCAN_S // 2):
                await asyncio.sleep(2)
                await self.refresh()
        except DBusError as err:
            self.message = f"Search failed: {err.text}"
        finally:
            try:
                await adapter.call_stop_discovery()
            except DBusError:
                pass
            self.scanning = False
            await self.refresh()

    async def action(self, path: str, what: str) -> None:
        """pair (pair, trust, connect) | connect | disconnect | remove."""
        device = self.device(path)
        if what in ("pair", "connect"):
            await self.open_pairing()
        self.busy, self.message = path, ""
        self.hub.push_state()
        try:
            if what == "remove":
                adapter = await self._interface(self.adapter, "org.bluez.Adapter1")
                await adapter.call_remove_device(path)
                self.message = f"Removed {device['name']}"
            else:
                iface = await self._interface(path, "org.bluez.Device1")
                if what == "pair":
                    if not device["paired"]:
                        await iface.call_pair()
                    props = await self._interface(path, "org.freedesktop.DBus.Properties")
                    await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
                    await iface.call_connect()
                    self.message = f"Connected to {device['name']}"
                elif what == "connect":
                    await iface.call_connect()
                    self.message = f"Connected to {device['name']}"
                elif what == "disconnect":
                    await iface.call_disconnect()
                    self.message = f"Disconnected {device['name']}"
                else:
                    raise ValueError(f"unknown action {what!r}")
        except DBusError as err:
            self.message = f"{device['name']}: {err.text or err.type}"
        finally:
            self.busy = None
            self.hub.osd(kind="message", text=self.message)
            await self.refresh()
