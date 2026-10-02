#!/usr/bin/env python3
"""Minimal QMP client for scripted QEMU tests (stdlib only).

    qmp.py SOCK quit
    qmp.py SOCK screendump out.ppm
    qmp.py SOCK send-key ctrl-alt-f2      # keys joined with '-' are pressed together
    qmp.py SOCK send-key up up ret        # separate args are pressed one after another
    qmp.py SOCK type 'hello world'        # US layout text
    qmp.py SOCK status
"""
import json
import socket
import sys
import time

# Characters typeable without shift -> QEMU qcode, and shifted ones.
_PLAIN = {c: c for c in "abcdefghijklmnopqrstuvwxyz0123456789"}
_PLAIN.update({" ": "spc", "-": "minus", "=": "equal", "[": "bracket_left",
               "]": "bracket_right", ";": "semicolon", "'": "apostrophe",
               ",": "comma", ".": "dot", "/": "slash", "\\": "backslash",
               "`": "grave_accent", "\n": "ret", "\t": "tab"})
_SHIFTED = dict(zip("!@#$%^&*()", "1234567890"))
_SHIFTED.update({"_": "minus", "+": "equal", "{": "bracket_left",
                 "}": "bracket_right", ":": "semicolon", '"': "apostrophe",
                 "<": "comma", ">": "dot", "?": "slash", "|": "backslash",
                 "~": "grave_accent"})


class QMP:
    def __init__(self, path, timeout=10.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.file = self.sock.makefile("rw", encoding="utf-8")
        self._read()  # greeting
        self.cmd("qmp_capabilities")

    def _read(self):
        while True:
            line = self.file.readline()
            if not line:
                raise ConnectionError("QMP socket closed")
            msg = json.loads(line)
            if "event" not in msg:
                return msg

    def cmd(self, name, **arguments):
        req = {"execute": name}
        if arguments:
            req["arguments"] = arguments
        self.file.write(json.dumps(req) + "\n")
        self.file.flush()
        resp = self._read()
        if "error" in resp:
            raise RuntimeError(f"{name}: {resp['error'].get('desc')}")
        return resp.get("return")

    def key(self, combo, hold_ms=80):
        keys = [{"type": "qcode", "data": k} for k in combo.split("-")]
        self.cmd("send-key", keys=keys, **{"hold-time": hold_ms})

    def type(self, text, delay=0.03):
        for ch in text:
            if ch.lower() in _PLAIN and not ch.isupper():
                self.key(_PLAIN[ch])
            elif ch.isupper():
                self.key("shift-" + ch.lower())
            elif ch in _SHIFTED:
                self.key("shift-" + _SHIFTED[ch])
            else:
                raise ValueError(f"cannot type {ch!r}")
            time.sleep(delay)


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    q = QMP(argv[1])
    op, rest = argv[2], argv[3:]
    if op == "quit":
        try:
            q.cmd("quit")
        except ConnectionError:
            pass
    elif op == "screendump":
        q.cmd("screendump", filename=rest[0])
    elif op == "send-key":
        for combo in rest:
            q.key(combo)
            time.sleep(0.05)
    elif op == "type":
        q.type(" ".join(rest))
    elif op == "status":
        print(json.dumps(q.cmd("query-status")))
    else:
        print(f"unknown op {op}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
