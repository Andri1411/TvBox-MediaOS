"""App icons for the home screen tiles and the menu's app lists.

Service logos are trademarks of their owners, so none are shipped: each box
fetches them itself, like a browser fetching a favicon. A browser service's
icon comes from its own site (the page's icon links and its web app
manifest; the largest PNG wins), a native one's from its .desktop file and
the icon theme. `icon = "<path or https URL>"` in services.toml overrides
both. Icons are cached in ~/.cache/tvbox/icons and fetched again after a
month; without one, a tile is just its colour and name, as before.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import NAME
from .util import setup_logging

log = setup_logging("hub")

MAX_AGE_S = 30 * 86400
RETRY_S = 6 * 3600                  # after a failed fetch (no network yet, ...)
MAX_BYTES = 2 * 1024 * 1024
# A desktop browser: sites hand it their normal pages and icons.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")
TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
ICON_DIRS = [Path("/usr/share/icons/hicolor"), Path("/usr/share/pixmaps")]
DESKTOP_DIRS = [Path("/usr/share/applications"), Path("/usr/local/share/applications")]


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / NAME / "icons"


def cached(service_id: str, directory: Path | None = None) -> Path | None:
    for ext in TYPES:
        path = (directory or cache_dir()) / f"{service_id}{ext}"
        if path.is_file():
            return path
    return None


# -- what a page offers ---------------------------------------------------------
class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[tuple[str, int]] = []      # (href, declared size or 0)
        self.manifests: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k: v or "" for k, v in attrs}
        rel, href = a.get("rel", "").lower().split(), a.get("href", "")
        if not href:
            return
        if "manifest" in rel:
            self.manifests.append(href)
        elif "icon" in rel or any(r.startswith("apple-touch-icon") for r in rel):
            self.icons.append((href, declared_size(a.get("sizes", ""))))


def declared_size(sizes: str) -> int:
    """"180x180" -> 180; "any" (SVG) -> 1024; nothing -> 0."""
    if sizes.strip().lower() == "any":
        return 1024
    found = [int(w) for w, _h in re.findall(r"(\d+)x(\d+)", sizes)]
    return max(found, default=0)


def page_candidates(html: str, base: str) -> tuple[list[tuple[str, int]], list[str]]:
    """Icon links (absolute URL, declared size) and manifest URLs of a page."""
    parser = _Links()
    parser.feed(html)
    return ([(urljoin(base, h), s) for h, s in parser.icons], [urljoin(base, m) for m in parser.manifests])


def manifest_candidates(text: str, base: str) -> list[tuple[str, int]]:
    """Icons of a web app manifest; monochrome ones are left out."""
    try:
        icons = json.loads(text).get("icons", [])
    except (ValueError, AttributeError):
        return []
    return [(urljoin(base, i["src"]), declared_size(i.get("sizes", ""))) for i in icons
            if isinstance(i, dict) and isinstance(i.get("src"), str)
            and "monochrome" not in str(i.get("purpose", ""))]


def image_size(data: bytes) -> tuple[str, int]:
    """(extension, pixel width) of a downloaded icon; ("", 0) if not one."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return ".png", struct.unpack(">I", data[16:20])[0]
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return ".svg", 1024
    if data[:4] == b"\x00\x00\x01\x00" and len(data) >= 22:
        count = struct.unpack("<H", data[4:6])[0]
        widths = [data[6 + 16 * i] or 256 for i in range(count) if 6 + 16 * i < len(data)]
        return ".ico", max(widths, default=0)
    return "", 0


def best(downloads: list[tuple[str, bytes]]) -> tuple[str, bytes] | None:
    """The best of the downloaded (url, data): the largest PNG, an ICO only
    when nothing else came. SVGs from the web are refused: an SVG can carry
    scripts, and these are served from the hub's own origin."""
    scored = []
    for _url, data in downloads:
        ext, width = image_size(data)
        if ext in (".png", ".ico"):
            scored.append(((ext != ".ico", width), ext, data))
    if not scored:
        return None
    _score, ext, data = max(scored, key=lambda s: s[0])
    return ext, data


# -- native apps: .desktop file and icon theme ---------------------------------
def desktop_icon(program: str, desktop_dirs: list[Path] | None = None) -> str | None:
    """Icon= of the .desktop file whose Exec runs `program`."""
    name = Path(program).name
    for directory in desktop_dirs or DESKTOP_DIRS:
        for entry in sorted(directory.glob("*.desktop")) if directory.is_dir() else []:
            try:
                text = entry.read_text(errors="replace")
            except OSError:
                continue
            exe = re.search(r"^Exec=\s*(\S+)", text, re.M)
            icon = re.search(r"^Icon=\s*(.+?)\s*$", text, re.M)
            if exe and icon and Path(exe.group(1).strip('"')).name == name:
                return icon.group(1)
    return None


def theme_icon(icon: str, icon_dirs: list[Path] | None = None) -> Path | None:
    """An icon name (or path) as a file: scalable SVG first, else the largest PNG."""
    if icon.startswith("/"):
        return Path(icon) if Path(icon).is_file() else None
    found: list[tuple[int, Path]] = []
    for base in icon_dirs or ICON_DIRS:
        for path in base.glob(f"**/{icon}.*") if base.is_dir() else []:
            if path.suffix not in (".svg", ".png"):
                continue
            size = 4096 if path.suffix == ".svg" else declared_size(path.parent.parent.name)
            found.append((size, path))
    return max(found, key=lambda f: f[0])[1] if found else None


# -- fetching --------------------------------------------------------------------
async def read_all(response) -> bytes:
    """The whole body, up to MAX_BYTES (content.read(n) stops at what has
    arrived so far)."""
    data = b""
    while len(data) < MAX_BYTES:
        chunk = await response.content.read(MAX_BYTES - len(data))
        if not chunk:
            break
        data += chunk
    return data


class Icons:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or cache_dir()
        self.failed: dict[str, float] = {}          # id -> monotonic time of the last failure

    def path(self, service_id: str) -> Path | None:
        return cached(service_id, self.directory)

    def url(self, service_id: str) -> str:
        """For the pages: the icon's address, changing when the icon does."""
        path = self.path(service_id)
        return f"/icons/{service_id}?v={int(path.stat().st_mtime)}" if path else ""

    def stale(self, service_id: str) -> bool:
        path = self.path(service_id)
        if path and time.time() - path.stat().st_mtime < MAX_AGE_S:
            return False
        failed = self.failed.get(service_id)
        return not failed or time.monotonic() - failed > RETRY_S

    async def refresh(self, services, force: bool = False) -> bool:
        """Fetch the icons that are missing or old. True if any changed."""
        changed = False
        for service in services:
            if not force and not self.stale(service.id):
                continue
            try:
                found = await self.find(service)
            except (OSError, asyncio.TimeoutError, ValueError) as err:
                log.info("icon for %s: %s", service.id, err)
                found = None
            if found:
                self.save(service.id, *found)
                self.failed.pop(service.id, None)
                changed = True
            else:
                self.failed[service.id] = time.monotonic()
        return changed

    def save(self, service_id: str, ext: str, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        for old in TYPES:
            (self.directory / f"{service_id}{old}").unlink(missing_ok=True)
        tmp = self.directory / f".{service_id}{ext}.tmp"
        tmp.write_bytes(data)
        tmp.replace(self.directory / f"{service_id}{ext}")

    async def find(self, service) -> tuple[str, bytes] | None:
        override = getattr(service, "icon", "")
        if override and not override.startswith(("http://", "https://")):
            path = theme_icon(override)
            return (path.suffix, path.read_bytes()) if path else None
        if override:
            return best(await self.download([override]))
        if service.kind == "native":
            icon = desktop_icon(service.exec[0]) if service.exec else None
            path = theme_icon(icon) if icon else None
            return (path.suffix, path.read_bytes()) if path else None
        return await self.find_web(service.url)

    async def find_web(self, url: str) -> tuple[str, bytes] | None:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
            async with session.get(url) as response:
                base = str(response.url)
                html = (await read_all(response)).decode(errors="replace")
            icons, manifests = page_candidates(html, base)
            origin = "{0.scheme}://{0.netloc}".format(urlsplit(base))
            for manifest in dict.fromkeys(manifests + [f"{origin}/manifest.webmanifest",
                                                       f"{origin}/manifest.json"]):
                try:
                    async with session.get(manifest) as response:
                        if response.status == 200:
                            icons += manifest_candidates(await response.text(errors="replace"), str(response.url))
                except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError):
                    pass
            if not icons:
                icons = [(f"{origin}/favicon.ico", 0)]
            # The few largest declared, plus undeclared ones (size unknown).
            icons.sort(key=lambda i: -i[1])
            urls = list(dict.fromkeys([u for u, s in icons if s][:3] + [u for u, s in icons if not s][:3]))
            return best(await self._download(session, urls))

    async def download(self, urls: list[str]) -> list[tuple[str, bytes]]:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                         headers={"User-Agent": USER_AGENT}) as session:
            return await self._download(session, urls)

    @staticmethod
    async def _download(session, urls: list[str]) -> list[tuple[str, bytes]]:
        import aiohttp
        out = []
        for url in urls:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        out.append((url, await read_all(response)))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
        return out
