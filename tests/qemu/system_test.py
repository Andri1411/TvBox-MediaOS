#!/usr/bin/python
"""Runs inside the test VM as root (see tests/qemu/session.sh): the watchdog
(a frozen and a crashed browser tab get their app restarted), the update
service's read-only side, and the boot health check. The update flow itself,
with reboots, is tests/qemu/update.sh.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from input_test import check, failed, tv
from launcher_test import WEB_PORT, WWW, cdp, unit_pid, wait_for
from menu_test import HUB, SERVICES, api, service, ui_ready, wait_state

SERVICE = f'''
[[service]]
id = "frozen"
name = "Watchdog test"
kind = "browser"
url = "http://127.0.0.1:{WEB_PORT}/index.html"
'''
UPDATER = "/run/tvbox/updater.sock"


def renderers(service_id):
    """PIDs of the Chromium renderer processes of a service."""
    pids = []
    cgroup = Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice") / \
        f"app-tvbox\\x2dapp.slice/tvbox-app@{service_id}.service/cgroup.procs"
    if not cgroup.exists():
        cgroup = next(Path("/sys/fs/cgroup").glob(f"**/tvbox-app@{service_id}.service/cgroup.procs"), cgroup)
    for pid in cgroup.read_text().split() if cgroup.exists() else []:
        try:
            if b"--type=renderer" in Path(f"/proc/{pid}/cmdline").read_bytes():
                pids.append(pid)
        except OSError:
            continue
    return pids


def health_restarts():
    import urllib.request
    with urllib.request.urlopen(f"{HUB}/api/health", timeout=20) as r:
        return [e for e in json.load(r)["restarts"] if e["message"].startswith("watchdog")]


def updater(request):
    out = subprocess.run(["sudo", "-u", "tv", "python", "-c", f'''
import json, socket
s = socket.socket(socket.AF_UNIX); s.connect("{UPDATER}")
s.sendall(json.dumps({json.dumps(request)}).encode() + b"\\n")
for line in s.makefile():
    msg = json.loads(line)
    if msg.get("event") != "log": print(json.dumps(msg)); break
'''], capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


def main():
    st = wait_state(ui_ready, 30)
    check("shell and inputd are connected to the hub", ui_ready(st), str(st)[:200])

    # --- update service (read-only side) ---
    check("update service socket belongs to group tv",
          subprocess.run(["stat", "-c", "%G %a", UPDATER], capture_output=True, text=True).stdout.strip() == "tv 660")
    snaps = updater({"cmd": "snapshots"})
    check("the tv user can list snapshots", snaps.get("event") == "done" and len(snaps.get("snapshots", [])) >= 1,
          str(snaps)[:200])
    bad = updater({"cmd": "rollback", "number": 99999})
    check("rollback to a snapshot that doesn't exist is refused", bad.get("event") == "error", str(bad))
    other = subprocess.run(["sudo", "-u", "nobody", "python", "-c",
                            f"import socket; socket.socket(socket.AF_UNIX).connect('{UPDATER}')"],
                           capture_output=True, text=True)
    check("other users can't reach the update service", other.returncode != 0, other.stderr[-100:])
    st = wait_state(lambda s: s.get("update", {}).get("snapshots"), 10)
    check("the hub knows the snapshots", bool(st.get("update", {}).get("snapshots")), str(st.get("update"))[:200])
    timer = subprocess.run(["systemctl", "is-enabled", "tvbox-boot-ok.timer"], capture_output=True, text=True)
    check("boot health check is enabled", timer.stdout.strip() == "enabled", timer.stdout)
    check("boot fallback is in GRUB's menu",
          "tvbox boot fallback" in Path("/boot/grub/grub.cfg").read_text()
          and "--id tvbox-snapshot" in Path("/boot/grub/grub.cfg").read_text())

    # --- watchdog ---
    WWW.mkdir(exist_ok=True)
    (WWW / "index.html").write_text("<!doctype html><title>watchdog test</title><body style='background:#234'>alive")
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(WEB_PORT), "--bind", "127.0.0.1"],
                              cwd=WWW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        Path(SERVICES).write_text(SERVICE)
        wait_state(lambda s: service(s, "frozen"), 5)
        before = len(health_restarts())
        api(cmd="launch", id="frozen")
        alive = wait_for(lambda: cdp("frozen", "document.title") == "watchdog test", 60)
        check("watchdog test page is up", bool(alive))
        time.sleep(50)                                 # past the start-up grace time
        main_pid = unit_pid("frozen")
        stopped = renderers("frozen")
        for pid in stopped:
            subprocess.run(["kill", "-STOP", pid])
        restarted = wait_for(lambda: unit_pid("frozen") not in ("", "0", main_pid), 60)
        check("a frozen page gets its app restarted", bool(restarted) and bool(stopped),
              f"renderers {stopped}, pid {main_pid} -> {unit_pid('frozen')}")
        restarts = health_restarts()
        check("the restart is on the health page", len(restarts) == before + 1 and "frozen" in restarts[0]["unit"],
              str(restarts[:2]))
        alive = wait_for(lambda: cdp("frozen", "document.title") == "watchdog test", 60)
        check("and the page is back", bool(alive))

        time.sleep(50)
        main_pid = unit_pid("frozen")
        crashed = renderers("frozen")
        for pid in crashed:
            subprocess.run(["kill", "-KILL", pid])     # (Chromium catches SIGSEGV)
        restarted = wait_for(lambda: unit_pid("frozen") not in ("", "0", main_pid), 60)
        check("a crashed tab gets its app restarted", bool(restarted) and bool(crashed),
              f"renderers {crashed}, pid {main_pid} -> {unit_pid('frozen')}")
    finally:
        server.terminate()
        tv("systemctl", "--user", "stop", "tvbox-app@frozen")
        Path(SERVICES).unlink(missing_ok=True)
    api(cmd="home")


if __name__ == "__main__":
    main()
    print(f"{'FAILED: ' + ', '.join(failed) if failed else 'all system checks passed'}")
    sys.exit(1 if failed else 0)
