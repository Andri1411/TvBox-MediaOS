import asyncio
import struct
import zlib

from aiohttp.test_utils import TestClient, TestServer

from tvbox import icons
from tvbox.hub import Hub


def png(width):
    """A minimal PNG header of the given width (enough for image_size)."""
    ihdr = struct.pack(">IIBBBBB", width, width, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))


def test_page_links_and_manifest():
    html = """<html><head>
      <link rel="icon" href="/favicon.ico">
      <link rel="apple-touch-icon" sizes="180x180" href="https://cdn.example/touch.png">
      <link rel="apple-touch-icon-precomposed" sizes="152x152" href="m152.png">
      <link rel="manifest" href="/app.webmanifest">
      <link rel="stylesheet" href="x.css">
    </head></html>"""
    found, manifests = icons.page_candidates(html, "https://www.example.com/tv/")
    assert found == [("https://www.example.com/favicon.ico", 0), ("https://cdn.example/touch.png", 180),
                     ("https://www.example.com/tv/m152.png", 152)]
    assert manifests == ["https://www.example.com/app.webmanifest"]
    manifest = """{"icons": [{"src": "/i/192.png", "sizes": "192x192"},
                             {"src": "/i/mono.png", "sizes": "512x512", "purpose": "monochrome"},
                             {"src": "logo.svg", "sizes": "any"}]}"""
    assert icons.manifest_candidates(manifest, "https://www.example.com/app.webmanifest") == [
        ("https://www.example.com/i/192.png", 192), ("https://www.example.com/logo.svg", 1024)]
    assert icons.manifest_candidates("not json", "https://x") == []


def test_best_is_the_largest_png_and_never_a_web_svg():
    ico = b"\x00\x00\x01\x00\x01\x00" + bytes([32]) + b"\x00" * 15
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert icons.best([("a", png(64)), ("b", png(192)), ("c", ico), ("d", svg)]) == (".png", png(192))
    assert icons.best([("c", ico), ("d", svg)]) == (".ico", ico)          # only when nothing else
    assert icons.best([("d", svg), ("e", b"<html>not found</html>")]) is None
    assert icons.image_size(svg) == (".svg", 1024)


def test_native_icon_from_desktop_file_and_theme(tmp_path):
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "org.example.Player.desktop").write_text(
        "[Desktop Entry]\nName=Player\nExec=/usr/bin/player --tv %U\nIcon=org.example.Player\n")
    (apps / "other.desktop").write_text("[Desktop Entry]\nExec=other\nIcon=other\n")
    assert icons.desktop_icon("player", [apps]) == "org.example.Player"
    assert icons.desktop_icon("missing", [apps]) is None

    theme = tmp_path / "hicolor"
    for size in ("48x48", "256x256"):
        (theme / size / "apps").mkdir(parents=True)
        (theme / size / "apps" / "org.example.Player.png").write_bytes(png(int(size.split("x")[0])))
    assert icons.theme_icon("org.example.Player", [theme]) == theme / "256x256/apps/org.example.Player.png"
    (theme / "scalable" / "apps").mkdir(parents=True)
    (theme / "scalable/apps/org.example.Player.svg").write_text("<svg/>")
    assert icons.theme_icon("org.example.Player", [theme]).suffix == ".svg"   # scalable wins
    assert icons.theme_icon("nothing", [theme]) is None


def test_cache_and_stale(tmp_path):
    store = icons.Icons(tmp_path)
    assert store.path("youtube") is None and store.url("youtube") == "" and store.stale("youtube")
    store.save("youtube", ".png", png(192))
    assert store.path("youtube") == tmp_path / "youtube.png" and store.url("youtube").startswith("/icons/youtube?v=")
    assert not store.stale("youtube")
    store.save("youtube", ".ico", b"\x00\x00\x01\x00")                     # replaces, never two
    assert sorted(p.name for p in tmp_path.iterdir()) == ["youtube.ico"]


def test_icon_route_serves_pictures_only(tmp_path, monkeypatch):
    (tmp_path / "web").mkdir()
    monkeypatch.setenv("TVBOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    async def scenario():
        hub = Hub()
        hub.icons = icons.Icons(tmp_path / "cache/icons")
        hub.icons.save("youtube", ".png", png(192))
        server = TestServer(hub.app_factory(port=8080))
        async with TestClient(server) as client:
            own = {"Host": "127.0.0.1:8080"}
            async with client.get("/icons/youtube?v=1", headers=own) as r:
                assert r.status == 200 and r.content_type == "image/png"
                assert "sandbox" in r.headers["Content-Security-Policy"]
                assert r.headers["X-Content-Type-Options"] == "nosniff"
                assert await r.read() == png(192)
            for bad in ("netflix", "..%2Fdevices", "YouTube"):
                async with client.get(f"/icons/{bad}", headers=own) as r:
                    assert r.status == 404, bad
            # Under the same access rules as the rest (here: DNS rebinding from loopback).
            async with client.get("/icons/youtube", headers={"Host": "evil.example:8080"}) as r:
                assert r.status == 403
    asyncio.run(scenario())


def test_read_all_reads_every_chunk():
    class Content:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, n):
            return self.chunks.pop(0)[:n] if self.chunks else b""

    class Response:
        def __init__(self, chunks):
            self.content = Content(chunks)

    assert asyncio.run(icons.read_all(Response([b"ab", b"cd", b"e"]))) == b"abcde"
    big = Response([b"x" * icons.MAX_BYTES, b"more"])
    assert len(asyncio.run(icons.read_all(big))) == icons.MAX_BYTES
