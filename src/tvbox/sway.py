"""Minimal asyncio client for sway's IPC socket (i3 protocol)."""
from __future__ import annotations

import asyncio
import glob
import json
import os
import struct
from typing import AsyncIterator

RUN_COMMAND, GET_WORKSPACES, SUBSCRIBE, GET_OUTPUTS, GET_TREE = 0, 1, 2, 3, 4
_MAGIC = b"i3-ipc"
_HEADER = struct.Struct("<6sII")


def socket_path() -> str | None:
    """The running sway's socket. $SWAYSOCK goes stale in long-running user
    services when sway restarts, so fall back to the newest socket on disk."""
    env = os.environ.get("SWAYSOCK")
    if env and os.path.exists(env):
        return env
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    socks = glob.glob(f"{runtime}/sway-ipc.{os.getuid()}.*.sock")
    return max(socks, key=os.path.getmtime) if socks else None


async def _send(writer: asyncio.StreamWriter, msg_type: int, payload: str) -> None:
    data = payload.encode()
    writer.write(_HEADER.pack(_MAGIC, len(data), msg_type) + data)
    await writer.drain()


async def _recv(reader: asyncio.StreamReader) -> tuple[int, object]:
    magic, length, msg_type = _HEADER.unpack(await reader.readexactly(_HEADER.size))
    if magic != _MAGIC:
        raise ConnectionError("bad reply from sway")
    return msg_type, json.loads(await reader.readexactly(length))


async def _connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    path = socket_path()
    if not path:
        raise ConnectionError("no sway socket")
    return await asyncio.open_unix_connection(path)


async def request(msg_type: int, payload: str = ""):
    """One request/reply on a fresh connection."""
    reader, writer = await _connect()
    try:
        await _send(writer, msg_type, payload)
        return (await _recv(reader))[1]
    finally:
        writer.close()


async def command(cmd: str) -> bool:
    """Run a sway command; True if sway accepted it."""
    replies = await request(RUN_COMMAND, cmd)
    return all(r.get("success") for r in replies)


async def focused_workspace() -> str | None:
    for ws in await request(GET_WORKSPACES):
        if ws.get("focused"):
            return ws.get("name")
    return None


async def events(*names: str) -> AsyncIterator[dict]:
    """Yield events of the given types (e.g. "workspace") until sway goes away."""
    reader, writer = await _connect()
    try:
        await _send(writer, SUBSCRIBE, json.dumps(names))
        _, reply = await _recv(reader)
        if not reply.get("success"):
            raise ConnectionError(f"sway refused subscription to {names}")
        while True:
            msg_type, body = await _recv(reader)
            if msg_type & 0x80000000:
                yield body
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError) as err:
        raise ConnectionError("sway connection lost") from err
    finally:
        writer.close()
