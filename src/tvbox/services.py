"""services.toml: the services (apps) on the home screen.

Same override chain as the bindings: package defaults, /etc, user. Later
files change single keys of a service with the same id, or add services.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import NAME
from .bindings import APP_ID, ConfigError

KINDS = ("browser", "native")
RESERVED_IDS = ("home",)            # the home screen's workspace
_KEYS = {"id", "name", "kind", "url", "user_agent", "flags", "exec", "color", "pause", "nav", "enabled"}


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    kind: str
    url: str = ""
    user_agent: str = ""            # resolved to the full string
    flags: tuple[str, ...] = ()
    exec: tuple[str, ...] = ()
    color: str = "#3a4658"
    pause: bool = True
    nav: bool = False               # load the d-pad navigation extension (desktop sites)


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def parse(documents: list[tuple[str, dict]]) -> list[Service]:
    """Merge decoded TOML documents [(label, data), ...], lowest priority
    first. Raises ConfigError listing every problem."""
    errors: list[str] = []
    agents: dict[str, str] = {}
    raw: dict[str, dict] = {}           # id -> merged keys, in first-seen order
    origin: dict[str, str] = {}
    order: list[str] = []
    for label, doc in documents:
        unknown = set(doc) - {"order", "user_agents", "service"}
        if unknown:
            errors.append(f"{label}: unknown key(s) {', '.join(sorted(unknown))}")
        table = doc.get("user_agents", {})
        if isinstance(table, dict) and all(isinstance(v, str) for v in table.values()):
            agents.update(table)
        else:
            errors.append(f"{label}: [user_agents] must map names to strings")
        if "order" in doc:
            if _strings(doc["order"]):
                order = doc["order"]
            else:
                errors.append(f"{label}: order must be a list of service ids")
        entries = doc.get("service", [])
        if not isinstance(entries, list):
            errors.append(f"{label}: [[service]] must be an array of tables")
            continue
        for i, entry in enumerate(entries):
            where = f"{label}: [[service]] #{i + 1}"
            sid = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(sid, str) or not APP_ID.match(sid) or sid in RESERVED_IDS:
                errors.append(f"{where}: needs an id of lowercase letters, digits, - and _ "
                              f"(not {', '.join(RESERVED_IDS)})")
                continue
            unknown = set(entry) - _KEYS
            if unknown:
                errors.append(f"{where} ({sid}): unknown key(s) {', '.join(sorted(unknown))}")
            raw.setdefault(sid, {}).update(entry)
            origin[sid] = f"{label}: service {sid}"

    services: dict[str, Service] = {}
    for sid, entry in raw.items():
        where, bad = origin[sid], []
        if entry.get("enabled", True) is False:
            continue
        kind = entry.get("kind")
        if kind not in KINDS:
            bad.append(f"kind must be one of {', '.join(KINDS)}")
        if not isinstance(entry.get("name", sid), str) or not entry.get("name", sid):
            bad.append("name must be a string")
        color = entry.get("color", Service.color)
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            bad.append('color must look like "#1a2b3c"')
        if not all(isinstance(entry.get(k, True), bool) for k in ("pause", "nav", "enabled")):
            bad.append("pause, nav and enabled must be true or false")
        agent = entry.get("user_agent", "")
        if not isinstance(agent, str):
            bad.append("user_agent must be a string")
            agent = ""
        elif agent and "/" not in agent:
            if agent in agents:
                agent = agents[agent]
            else:
                bad.append(f"user_agent {agent!r} is not defined in [user_agents]")
        flags = entry.get("flags", [])
        if flags and not (_strings(flags) and all(f.startswith("--") for f in flags)):
            bad.append("flags must be a list of strings starting with --")
            flags = []
        url, command = entry.get("url", ""), entry.get("exec", [])
        if kind == "browser" and not (isinstance(url, str) and re.match(r"https?://\S+$", url)):
            bad.append("a browser service needs url = \"http(s)://...\"")
        if kind == "native" and not (command and _strings(command)):
            bad.append("a native service needs exec = [\"program\", \"arg\", ...]")
        if bad:
            errors += [f"{where}: {b}" for b in bad]
            continue
        services[sid] = Service(sid, entry.get("name", sid), kind, url if kind == "browser" else "",
                                agent, tuple(flags), tuple(command) if kind == "native" else (),
                                color, entry.get("pause", True),
                                kind == "browser" and entry.get("nav", False))
    if errors:
        raise ConfigError(errors)
    ordered = [services[s] for s in dict.fromkeys(order) if s in services]
    return ordered + [s for s in services.values() if s not in ordered]


def default_paths() -> list[Path]:
    data = Path(os.environ.get("TVBOX_DATA_DIR", f"/usr/share/{NAME}"))
    user = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / NAME
    return [data / "services.toml", Path(f"/etc/{NAME}/services.toml"), user / "services.toml"]


def load(paths: list[Path] | None = None) -> list[Service]:
    documents, errors = [], []
    for path in paths or default_paths():
        try:
            with open(path, "rb") as f:
                documents.append((str(path), tomllib.load(f)))
        except FileNotFoundError:
            continue
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as err:
            errors.append(f"{path}: {err}")
    if errors:
        raise ConfigError(errors)
    return parse(documents)


def load_best(paths: list[Path] | None = None) -> tuple[list[Service], list[str]]:
    """Usable services and the problems found: a broken override must not
    empty the home screen, so fall back to the files below it."""
    paths = paths or default_paths()
    errors: list[str] = []
    for count in range(len(paths), 0, -1):
        try:
            return load(paths[:count]), errors
        except ConfigError as err:
            errors = errors or err.errors
    return [], errors
