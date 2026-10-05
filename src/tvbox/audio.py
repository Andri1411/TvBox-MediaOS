"""Audio output control through WirePlumber's wpctl and pw-dump."""
from __future__ import annotations

import asyncio
import json
import os
import re

SINK = "@DEFAULT_AUDIO_SINK@"


async def run(*argv: str, timeout: float = 5, env: dict | None = None,
              stderr: bool = False) -> tuple[int, str]:
    """Run a command; (exit code, stdout, plus stderr if asked). Missing
    binary or timeout = code -1. `env` adds to the environment."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if stderr else asyncio.subprocess.DEVNULL,
            env={**os.environ, **env} if env else None)
    except OSError:
        return -1, ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, ""
    return proc.returncode or 0, out.decode(errors="replace")


def parse_volume(text: str) -> tuple[int, bool] | None:
    """`Volume: 0.45 [MUTED]` -> (45, True)."""
    match = re.search(r"Volume:\s*([0-9.]+)", text)
    if not match:
        return None
    return round(float(match.group(1)) * 100), "[MUTED]" in text


def _props(obj: dict) -> dict:
    return (obj.get("info") or {}).get("props") or {}


def _params(obj: dict, name: str) -> list[dict]:
    return ((obj.get("info") or {}).get("params") or {}).get(name) or []


def _route_info(route: dict) -> dict:
    """A route's `info` is [count, key, value, key, value, ...]."""
    items = route.get("info") or []
    return dict(zip(items[1::2], items[2::2])) if items else {}


def _default_sink(dump: list[dict]) -> str | None:
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Metadata" and \
                obj.get("props", {}).get("metadata.name") == "default":
            for entry in obj.get("metadata", []):
                if entry.get("key") == "default.audio.sink":
                    value = entry.get("value")
                    return value.get("name") if isinstance(value, dict) else None
    return None


def card_outputs(device: dict) -> list[dict]:
    """The outputs of one sound card (an ALSA device in `pw-dump`): one per
    connector that may have something plugged in (HDMI to the TV, the analog
    jack), each with the card profile that plays through it.

    A card plays through one profile at a time, so the analog jack and HDMI
    are not both sinks at once; switching between them means switching the
    card's profile. Stereo profiles are preferred over surround ones (TVs
    take stereo PCM; the TV or soundbar does its own upmixing)."""
    profiles = {p.get("index"): p for p in _params(device, "EnumProfile")}
    active = next((p.get("index") for p in _params(device, "Profile")), None)
    outputs = {}
    for route in _params(device, "EnumRoute"):
        if route.get("direction") != "Output" or route.get("available") == "no":
            continue
        candidates = [profiles[i] for i in route.get("profiles") or []
                      if i in profiles and profiles[i].get("available") != "no"
                      and "surround" not in profiles[i].get("name", "")]
        if not candidates:
            continue
        best = max(candidates, key=lambda p: p.get("priority", 0))
        info = _route_info(route)
        hdmi = info.get("port.type") == "hdmi" or route.get("name", "").startswith("hdmi")
        product = info.get("device.product.name")
        if hdmi:
            name = f"{product} (HDMI)" if product else route.get("description", "HDMI")
        else:
            name = f"{route.get('description', 'Analog')} (analog)"
        # Routes sharing a profile (analog speakers and headphones) are one
        # output: the card switches between them by itself.
        key = best["name"].split("+")[0]
        if key in outputs:
            continue
        outputs[key] = {"device": device["id"], "profile": best["index"], "profile_name": best["name"],
                        "name": name, "hdmi": hdmi,
                        "active": profiles.get(active, {}).get("name", "").split("+")[0] == key}
    return list(outputs.values())


def parse_outputs(dump: list[dict]) -> list[dict]:
    """Audio outputs from `pw-dump` as [{id, name, default}], sorted by name.

    Sound cards contribute one entry per connector ("card:<device>:<profile>",
    which switches the card's profile); other sinks (Bluetooth) one entry each
    ("sink:<id>")."""
    default = _default_sink(dump)
    cards = {obj["id"]: obj for obj in dump if obj.get("type") == "PipeWire:Interface:Device"
             and _props(obj).get("device.api") == "alsa" and _params(obj, "EnumProfile")}
    default_device = None
    result = []
    for obj in dump:
        props = _props(obj)
        if obj.get("type") != "PipeWire:Interface:Node" or props.get("media.class") != "Audio/Sink":
            continue
        is_default = props.get("node.name") == default
        if props.get("device.id") in cards:
            if is_default:
                default_device = props.get("device.id")
            continue
        name = props.get("node.description") or props.get("node.nick") or props.get("node.name", "?")
        result.append({"id": f"sink:{obj['id']}", "name": name, "default": is_default})
    for device_id, device in cards.items():
        for out in card_outputs(device):
            result.append({"id": f"card:{device_id}:{out['profile']}", "name": out["name"],
                           "default": out["active"] and default_device == device_id, "hdmi": out["hdmi"]})
    return sorted(result, key=lambda s: s["name"].lower())


def hdmi_switch(dump: list[dict]) -> tuple[int, int] | None:
    """(device, profile) when a sound card plays through something other
    than HDMI while a TV is connected to its HDMI: what the box should
    switch to by default. None when nothing should change."""
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Device" or _props(obj).get("device.api") != "alsa":
            continue
        outputs = card_outputs(obj)
        hdmi = [o for o in outputs if o["hdmi"]]
        if hdmi and not any(o["active"] for o in hdmi):
            return obj["id"], hdmi[0]["profile"]
    return None


async def get_volume() -> tuple[int, bool] | None:
    code, out = await run("wpctl", "get-volume", SINK)
    return parse_volume(out) if code == 0 else None


async def change_volume(delta: int) -> None:
    # -l 1.0: never amplify above 100 %.
    await run("wpctl", "set-volume", "-l", "1.0", SINK, f"{abs(delta)}%{'+' if delta > 0 else '-'}")


async def set_volume(percent: int) -> None:
    await run("wpctl", "set-volume", SINK, f"{percent / 100:.2f}")


async def toggle_mute() -> None:
    await run("wpctl", "set-mute", SINK, "toggle")


async def dump() -> list[dict]:
    code, out = await run("pw-dump")
    if code != 0:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


async def list_outputs(data: list[dict] | None = None) -> list[dict]:
    try:
        return parse_outputs(await dump() if data is None else data)
    except (KeyError, TypeError, AttributeError):
        return []


async def set_profile(device: int, profile: int) -> bool:
    return (await run("wpctl", "set-profile", str(device), str(profile)))[0] == 0


async def select_output(output_id: str) -> bool:
    """Make an entry from list_outputs() the default output."""
    kind, _, rest = str(output_id).partition(":")
    if kind == "sink":
        return (await run("wpctl", "set-default", str(int(rest))))[0] == 0
    if kind != "card":
        raise ValueError(f"unknown audio output {output_id!r}")
    device, profile = (int(x) for x in rest.split(":"))
    if not await set_profile(device, profile):
        return False
    # The card's sink for the new profile appears a moment later.
    for _ in range(20):
        for obj in await dump():
            props = _props(obj)
            if obj.get("type") == "PipeWire:Interface:Node" and props.get("media.class") == "Audio/Sink" \
                    and props.get("device.id") == device:
                return (await run("wpctl", "set-default", str(obj["id"])))[0] == 0
        await asyncio.sleep(0.25)
    return False
