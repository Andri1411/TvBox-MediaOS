"""tvbox-shell: draws the hub's web UI on the TV. One process, two surfaces:

- the home screen: a normal fullscreen window on the "home" workspace, with
  keyboard focus (the controller's keys arrive as key events);
- the overlay (system menu, OSD): a layer-shell surface above every app,
  fullscreen ones included. It never takes keyboard focus and lets pointer
  input through; navigation reaches the page from the hub. It is only mapped
  while the page has something to show, so the compositor does not blend an
  empty surface over the video.
"""
from __future__ import annotations

import os
import sys
from ctypes import CDLL

# gtk4-layer-shell has to be loaded before libwayland-client, i.e. before GTK.
CDLL("libgtk4-layer-shell.so")

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell, WebKit  # noqa: E402

from .hub import PORT  # noqa: E402
from .util import sd_notify, setup_logging, watchdog_interval  # noqa: E402

log = setup_logging("shell")
BASE_URL = os.environ.get("TVBOX_HUB_URL", f"http://127.0.0.1:{PORT}")
APP_ID = "org.tvbox.Shell"          # the sway config assigns this to workspace "home"
RETRY_MS = 1000


class Page:
    """A WebView on one of the hub's pages that reloads itself when the hub
    was not up yet or its web process died."""

    def __init__(self, path: str, manager: WebKit.UserContentManager | None = None):
        self.url = BASE_URL + path
        self.view = WebKit.WebView(user_content_manager=manager) if manager else WebKit.WebView()
        # JavaScript errors and console output end up in the journal.
        self.view.get_settings().set_enable_write_console_messages_to_stdout(True)
        self.view.connect("load-failed", self.on_load_failed)
        self.view.connect("web-process-terminated", self.on_crash)
        self.view.load_uri(self.url)

    def on_load_failed(self, _view, _event, uri, error) -> bool:
        log.warning("cannot load %s (%s), retrying", uri, error.message)
        GLib.timeout_add(RETRY_MS, self.reload)
        return True

    def on_crash(self, _view, reason) -> None:
        log.error("web process of %s terminated (%s), reloading", self.url, reason.value_nick)
        GLib.timeout_add(RETRY_MS, self.reload)

    def reload(self) -> bool:
        self.view.load_uri(self.url)
        return GLib.SOURCE_REMOVE


class Home:
    def __init__(self, app: Gtk.Application):
        self.window = Gtk.ApplicationWindow(application=app, title="tvbox home")
        self.page = Page("/home")
        self.page.view.set_background_color(Gdk.RGBA(0.063, 0.078, 0.094, 1))
        self.window.set_child(self.page.view)
        self.window.fullscreen()
        self.window.present()
        self.page.view.grab_focus()


class Overlay:
    def __init__(self, app: Gtk.Application):
        self.window = Gtk.ApplicationWindow(application=app, title="tvbox overlay")
        self.window.add_css_class("tvbox-overlay")
        css = Gtk.CssProvider()
        css.load_from_string("window.tvbox-overlay { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, "tvbox-overlay")
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.OVERLAY)
        for edge in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM,
                     Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT):
            Gtk4LayerShell.set_anchor(self.window, edge, True)
        Gtk4LayerShell.set_exclusive_zone(self.window, -1)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)

        manager = WebKit.UserContentManager()
        manager.register_script_message_handler("tvbox", None)
        manager.connect("script-message-received::tvbox", self.on_message)
        self.page = Page("/overlay", manager)
        self.page.view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        self.page.view.connect("load-failed", lambda *_: self.window.set_visible(False))
        self.page.view.connect("web-process-terminated", lambda *_: self.window.set_visible(False))
        self.window.set_child(self.page.view)
        self.window.connect("map", self.on_map)
        # Realize without showing: the page loads and keeps its WebSocket
        # while the window is unmapped.
        self.window.realize()

    def on_map(self, _window) -> None:
        # Empty input region: clicks in mouse mode go to the app underneath.
        self.window.get_surface().set_input_region(cairo.Region())

    def on_message(self, _manager, value) -> None:
        visible = value.is_object() and value.object_get_property("visible").to_boolean()
        if visible != self.window.get_visible():
            self.window.set_visible(visible)


def main() -> int:
    app = Gtk.Application(application_id=APP_ID)
    keep = []

    def activate(application):
        if not keep:
            keep.extend([Home(application), Overlay(application)])
            sd_notify("READY=1")
            interval = watchdog_interval()
            if interval:
                GLib.timeout_add(int(interval * 1000), lambda: sd_notify("WATCHDOG=1") or True)

    app.connect("activate", activate)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
