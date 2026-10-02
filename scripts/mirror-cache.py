#!/usr/bin/env python3
"""Caching HTTP mirror of Arch Linux for QEMU tests (stdlib only).

    mirror-cache.py [--port 8801] [--upstream https://geo.mirror.pkgbuild.com] [--cache build/mirror-cache]

A guest using QEMU user networking reaches it at http://10.0.2.2:<port>/$repo/os/$arch.
Packages and signatures are cached on disk, so repeated test installs don't
download the same ~1 GB again; databases are always fetched fresh. Upstream
requests honour HTTPS_PROXY and the system CA store, so this also works on
networks where the guest itself cannot reach the internet directly.
"""
import argparse
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CACHEABLE = (".pkg.tar.zst", ".pkg.tar.xz", ".pkg.tar.zst.sig", ".pkg.tar.xz.sig")


class Handler(BaseHTTPRequestHandler):
    upstream = ""
    cache = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("mirror-cache: " + fmt % args + "\n")

    def do_HEAD(self):
        self.handle_get(head=True)

    def do_GET(self):
        self.handle_get(head=False)

    def handle_get(self, head):
        path = self.path.split("?", 1)[0]
        if ".." in path or not path.startswith("/"):
            self.send_error(400)
            return
        cached = os.path.join(self.cache, path.lstrip("/"))
        if path.endswith(CACHEABLE) and os.path.isfile(cached):
            self.send_file(cached, head)
            return
        try:
            with urllib.request.urlopen(self.upstream + path, timeout=60) as resp:
                if path.endswith(CACHEABLE):
                    os.makedirs(os.path.dirname(cached), exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cached))
                    with os.fdopen(fd, "wb") as out:
                        shutil.copyfileobj(resp, out)
                    os.replace(tmp, cached)
                    self.send_file(cached, head)
                else:
                    body = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_error(e.code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.log_message("upstream error for %s: %s", path, e)
            self.send_error(502)

    def send_file(self, file, head):
        self.send_response(200)
        self.send_header("Content-Length", str(os.path.getsize(file)))
        self.end_headers()
        if not head:
            with open(file, "rb") as f:
                shutil.copyfileobj(f, self.wfile)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--upstream", default="https://geo.mirror.pkgbuild.com")
    ap.add_argument("--cache", default="build/mirror-cache")
    a = ap.parse_args()
    Handler.upstream = a.upstream.rstrip("/")
    Handler.cache = os.path.abspath(a.cache)
    os.makedirs(Handler.cache, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
