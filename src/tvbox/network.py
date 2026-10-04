"""Wi-Fi settings through NetworkManager's nmcli."""
from __future__ import annotations

import re

from .audio import run


def split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t` output: fields are separated by ':',
    and ':' or '\\' inside a field is escaped with '\\'."""
    fields, current, escaped = [], [], False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def parse_devices(text: str) -> dict:
    status = {"wifi_device": None, "ssid": None, "ethernet": False}
    for line in text.splitlines():
        device, kind, state, connection = (split_terse(line) + ["", "", "", ""])[:4]
        if kind == "wifi" and status["wifi_device"] is None:
            status["wifi_device"] = device
            if state == "connected":
                status["ssid"] = connection
        elif kind == "ethernet" and state == "connected":
            status["ethernet"] = True
    return status


def parse_scan(text: str) -> list[dict]:
    """Networks from `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`,
    one entry per name (the strongest), strongest first."""
    best: dict[str, dict] = {}
    for line in text.splitlines():
        in_use, ssid, signal, security = (split_terse(line) + ["", "", "", ""])[:4]
        if not ssid:
            continue                                # hidden network
        network = {"ssid": ssid, "signal": int(signal) if signal.isdigit() else 0,
                   "secure": security not in ("", "--"), "connected": in_use == "*"}
        if ssid not in best or network["signal"] > best[ssid]["signal"] or network["connected"]:
            network["connected"] = network["connected"] or best.get(ssid, {}).get("connected", False)
            best[ssid] = network
    return sorted(best.values(), key=lambda n: (not n["connected"], -n["signal"]))


def parse_known(text: str) -> list[str]:
    return [fields[0] for fields in map(split_terse, text.splitlines())
            if len(fields) >= 2 and fields[1] == "802-11-wireless"]


async def status() -> dict:
    code, out = await run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")
    result = parse_devices(out) if code == 0 else {"wifi_device": None, "ssid": None, "ethernet": False}
    code, out = await run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show")
    result["known"] = parse_known(out) if code == 0 else []
    return result


async def scan() -> list[dict]:
    code, out = await run("nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                          "--rescan", "yes", timeout=30)
    return parse_scan(out) if code == 0 else []


async def connect(ssid: str, password: str | None) -> str | None:
    """Connect (and remember) a network; an error message, or None."""
    args = ["nmcli", "--wait", "40", "device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    code, out = await run(*args, timeout=50, stderr=True)
    if code == 0:
        return None
    message = out.strip().splitlines()[-1] if out.strip() else "could not connect"
    if re.search(r"secrets were required|802-1x|password", message, re.I):
        return "Wrong password?"
    return message.removeprefix("Error: ")


async def forget(ssid: str) -> bool:
    return (await run("nmcli", "connection", "delete", "id", ssid))[0] == 0
