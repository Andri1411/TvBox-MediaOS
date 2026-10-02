"""tvbox-ctl: command line access to the input daemon (debugging, scripts,
sway key bindings)."""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from . import bindings
from .util import input_socket


def _connect() -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(input_socket()))
    except OSError as err:
        raise SystemExit(f"tvbox-ctl: cannot reach tvbox-inputd at {input_socket()}: {err}")
    return sock


def _call(cmd: dict) -> dict:
    """Send one command and return its reply."""
    with _connect() as sock, sock.makefile("rw") as f:
        f.write(json.dumps({"id": 1, **cmd}) + "\n")
        f.flush()
        for line in f:
            msg = json.loads(line)
            if msg.get("reply") == 1:
                return msg
    raise SystemExit("tvbox-ctl: connection closed without a reply")


def _print_status(st: dict) -> None:
    print(f"mode     {st['mode']}")
    print(f"app      {st['app'] or '-'}")
    print("devices  " + ("" if st["devices"] else "none"))
    for dev in st["devices"]:
        grab = "grabbed" if dev["grabbed"] else "shared"
        print(f"  {dev['profile']:8} {dev['id']}  {dev['name']}  ({dev['path']}, {grab})")
    if st["config_errors"]:
        print("bindings REJECTED, previous bindings still active:")
        for err in st["config_errors"]:
            print(f"  {err}")
    else:
        print("bindings ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tvbox-ctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="mode, focused app, devices, bindings state").add_argument(
        "--json", action="store_true")
    p = sub.add_parser("button", help="press a logical button, as a controller would")
    p.add_argument("button", choices=bindings.BUTTONS, metavar="BUTTON")
    p.add_argument("--state", choices=("tap", "down", "up"), default="tap")
    sub.add_parser("action", help='run an action, e.g. "ui:system_menu"').add_argument("action")
    sub.add_parser("key", help='tap a key combo, e.g. "ctrl+l"').add_argument("combo")
    sub.add_parser("mode", help="switch input mode").add_argument("mode", choices=("app", "ui", "mouse"))
    sub.add_parser("reload", help="re-read the bindings files")
    sub.add_parser("watch", help="print daemon events as JSON lines")
    sub.add_parser("check", help="validate bindings (the installed chain, or FILE on top "
                   "of the defaults)").add_argument("file", nargs="?", type=Path)
    args = parser.parse_args(argv)

    if args.command == "check":
        paths = bindings.default_paths()
        try:
            if args.file:
                errors = bindings.validate_text(args.file.read_text(), str(args.file), paths[:1])
            else:
                bindings.load(paths)
                errors = []
        except bindings.ConfigError as err:
            errors = err.errors
        except OSError as err:
            errors = [str(err)]
        for line in errors:
            print(line, file=sys.stderr)
        if not errors:
            print("bindings ok")
        return 1 if errors else 0

    if args.command == "watch":
        with _connect() as sock, sock.makefile("r") as f:
            try:
                for line in f:
                    print(line, end="", flush=True)
            except KeyboardInterrupt:
                pass
        return 0

    cmd = {"cmd": args.command}
    for key in ("button", "state", "action", "combo", "mode"):
        if hasattr(args, key):
            cmd[key] = getattr(args, key)
    reply = _call(cmd)
    if not reply.get("ok"):
        print(f"tvbox-ctl: {reply.get('error', 'failed')}", file=sys.stderr)
        return 1
    if args.command == "status":
        print(json.dumps(reply)) if args.json else _print_status(reply)
    elif args.command == "reload":
        for line in reply.get("errors", []):
            print(line, file=sys.stderr)
        return 1 if reply.get("errors") else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
