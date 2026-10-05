import asyncio
import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from tvbox import audio
from tvbox.apps import sway_id as sway_safe
from tvbox.hub import Hub


def test_parse_volume():
    assert audio.parse_volume("Volume: 0.45\n") == (45, False)
    assert audio.parse_volume("Volume: 1.00 [MUTED]\n") == (100, True)
    assert audio.parse_volume("Volume: 0.07") == (7, False)
    assert audio.parse_volume("") is None


def box_dump(active_profile=None, bluetooth=False):
    """`pw-dump` of the real box (Alder Lake, ALC257 + HDMI to a TCL TV),
    trimmed; optionally with another active card profile and a Bluetooth sink."""
    dump = json.loads((Path(__file__).parent / "data/pwdump-alc257-hdmi.json").read_text())
    if active_profile:
        card = next(o for o in dump if o["id"] == 42)
        card["info"]["params"]["Profile"] = [{"index": active_profile}]
    if bluetooth:
        dump.append({"id": 70, "type": "PipeWire:Interface:Node", "info": {"props": {
            "media.class": "Audio/Sink", "node.name": "bluez_output.aa", "node.description": "Headphones"}}})
    return dump


def test_outputs_are_the_card_connectors_and_other_sinks():
    assert audio.parse_outputs(box_dump(bluetooth=True)) == [
        {"id": "sink:70", "name": "Headphones", "default": False},
        {"id": "card:42:1", "name": "Speakers (analog)", "default": False, "hdmi": False},
        {"id": "card:42:3", "name": "TCL SMART TV (HDMI)", "default": True, "hdmi": True},
    ]
    assert audio.parse_outputs([]) == []


def test_analog_with_a_tv_on_hdmi_switches_to_hdmi():
    analog = box_dump(active_profile=1)          # output:analog-stereo+input:analog-stereo
    assert [o["name"] for o in audio.parse_outputs(analog) if o["default"]] == ["Speakers (analog)"]
    assert audio.hdmi_switch(analog) == (42, 3)  # output:hdmi-stereo+input:analog-stereo
    assert audio.hdmi_switch(box_dump()) is None  # already on HDMI


def test_no_switch_without_a_tv():
    dump = box_dump(active_profile=1)
    card = next(o for o in dump if o["id"] == 42)
    for route in card["info"]["params"]["EnumRoute"]:
        if route["name"].startswith("hdmi"):
            route["available"] = "no"
    assert audio.hdmi_switch(dump) is None
    assert [o["name"] for o in audio.parse_outputs(dump)] == ["Speakers (analog)"]


def test_a_card_with_one_profile():
    """QEMU's sound card: a single analog line out, one profile."""
    card = {"id": 42, "type": "PipeWire:Interface:Device", "info": {
        "props": {"device.api": "alsa"},
        "params": {"EnumProfile": [{"index": 0, "name": "off", "available": "yes", "priority": 0},
                                   {"index": 1, "name": "output:analog-stereo", "available": "yes", "priority": 6500},
                                   {"index": 2, "name": "pro-audio", "available": "unknown", "priority": 1}],
                   "Profile": [{"index": 1}],
                   "EnumRoute": [{"index": 0, "direction": "Output", "name": "analog-output-lineout",
                                  "description": "Line Out", "available": "unknown", "profiles": [1],
                                  "info": [1, "port.type", "line"]}]}}}
    sink = {"id": 49, "type": "PipeWire:Interface:Node", "info": {"props": {
        "media.class": "Audio/Sink", "node.name": "alsa_output.analog-stereo", "device.id": 42}}}
    meta = {"id": 1, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
            "metadata": [{"key": "default.audio.sink", "value": {"name": "alsa_output.analog-stereo"}}]}
    assert audio.parse_outputs([meta, card, sink]) == [
        {"id": "card:42:1", "name": "Line Out (analog)", "default": True, "hdmi": False}]
    assert audio.hdmi_switch([meta, card, sink]) is None


def test_workspace_names_cannot_inject_sway_commands():
    assert sway_safe("youtube") and sway_safe("disney-plus_2")
    for bad in ("", "a b", "x; exec foot", "x,exit", 'a"b'):
        assert not sway_safe(bad)


def test_requests_from_web_pages_are_refused(tmp_path, monkeypatch):
    """A page in one of the box's browsers must not be able to drive the hub."""
    (tmp_path / "web").mkdir()
    monkeypatch.setenv("TVBOX_DATA_DIR", str(tmp_path))

    async def scenario():
        server = TestServer(Hub().app_factory(port=8080))
        async with TestClient(server) as client:
            async def get(headers):
                async with client.get("/api/state", headers=headers) as r:
                    return r.status
            own = {"Host": "127.0.0.1:8080"}
            assert await get(own) == 200
            assert await get({"Host": "localhost:8080"}) == 200
            assert await get(own | {"Origin": "http://127.0.0.1:8080"}) == 200
            assert await get(own | {"Origin": "https://www.netflix.com"}) == 403
            assert await get(own | {"Origin": "null"}) == 403
            assert await get({"Host": "evil.example:8080"}) == 403          # DNS rebinding
            async with client.post("/api/cmd", headers=own | {"Origin": "https://ads.example"},
                                   data='{"cmd": "reboot"}') as r:
                assert r.status == 403
            async with client.post("/api/cmd", headers=own, data='{"cmd": "nope"}') as r:
                assert r.status == 400
            # our navigation extension may report text focus, and nothing else
            ext = own | {"Origin": "chrome-extension://ecgejpihnmlnjmgnejffnelbhbiehbpm"}
            async with client.post("/api/cmd", headers=ext, data='{"cmd": "text_focus", "focused": false}') as r:
                assert r.status == 200
            async with client.post("/api/cmd", headers=ext, data='{"cmd": "reboot"}') as r:
                assert r.status == 400
            async with client.get("/api/state", headers=ext) as r:
                assert r.status == 403
            other = own | {"Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
            async with client.post("/api/cmd", headers=other, data='{"cmd": "text_focus"}') as r:
                assert r.status == 403
            async with client.get("/ws", headers=own | {
                    "Origin": "https://www.netflix.com", "Upgrade": "websocket",
                    "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
                    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}) as r:
                assert r.status == 403
    asyncio.run(scenario())


def test_nmcli_parsing():
    from tvbox import network
    assert network.split_terse(r"*:Café\: 5G:72:WPA2") == ["*", "Café: 5G", "72", "WPA2"]
    assert network.split_terse(r"a\\b:c") == ["a\\b", "c"]
    devices = "wlan0:wifi:connected:Home\nenp1s0:ethernet:unavailable:\nlo:loopback:connected (externally):lo\n"
    assert network.parse_devices(devices) == {"wifi_device": "wlan0", "ssid": "Home", "ethernet": False}
    scan = " :Home:40:WPA2\n*:Home:70:WPA2\n :Cafe:90:\n :Neighbour:55:WPA1 WPA2\n ::30:WPA2\n"
    assert network.parse_scan(scan) == [
        {"ssid": "Home", "signal": 70, "secure": True, "connected": True},
        {"ssid": "Cafe", "signal": 90, "secure": False, "connected": False},
        {"ssid": "Neighbour", "signal": 55, "secure": True, "connected": False},
    ]
    assert network.parse_known("Home:802-11-wireless\nWired 1:802-3-ethernet\n") == ["Home"]
