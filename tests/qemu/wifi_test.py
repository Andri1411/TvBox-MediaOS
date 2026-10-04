#!/usr/bin/python
"""Runs inside the test VM as root (see tests/qemu/session.sh): Wi-Fi
settings against simulated radios. mac80211_hwsim makes three radios; two of
them become access points with hostapd (one with WPA2, one open) and the
third is the box's own Wi-Fi, driven through the hub as the TV and phone do.
hostapd and dnsmasq (DHCP on the access points) are installed for the test
only.
"""
import subprocess
import sys
import time
from pathlib import Path

from input_test import check, failed
from menu_test import api, ui_ready, wait_state

SECURE, OPEN, PASSWORD = "tvbox-test-wpa", "tvbox-test-open", "correct horse battery"


def sh(command):
    return subprocess.run(command, shell=True, capture_output=True, text=True)


def access_point(iface, ssid, password=None):
    conf = Path(f"/tmp/hostapd-{iface}.conf")
    lines = [f"interface={iface}", "driver=nl80211", f"ssid={ssid}", "hw_mode=g", "channel=6"]
    if password:
        lines += ["wpa=2", f"wpa_passphrase={password}", "wpa_key_mgmt=WPA-PSK", "rsn_pairwise=CCMP"]
    conf.write_text("\n".join(lines) + "\n")
    return subprocess.Popen(["hostapd", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def network():
    return api(cmd="refresh") and wait_state(lambda s: True, 2)["network"]


def main():
    st = wait_state(ui_ready, 30)
    check("shell and inputd are connected to the hub", ui_ready(st), str(st)[:200])
    if sh("command -v hostapd && command -v dnsmasq").returncode:
        sh("sed '/^\\[tvbox\\]/,/^$/d' /etc/pacman.conf > /root/pacman-notvbox.conf; "
           "pacman --config /root/pacman-notvbox.conf -S --noconfirm --needed hostapd dnsmasq")
    sh("modprobe -r mac80211_hwsim; modprobe mac80211_hwsim radios=3")
    time.sleep(3)
    ifaces = sorted(Path("/sys/class/net").glob("wlan*"))
    check("three simulated radios", len(ifaces) == 3, str(ifaces))
    if len(ifaces) != 3:
        return
    box, ap_secure, ap_open = (i.name for i in ifaces)
    for iface in (ap_secure, ap_open):
        sh(f"nmcli device set {iface} managed no")
    time.sleep(2)                       # NetworkManager lets go of them
    aps = []
    for iface, ssid, password in ((ap_secure, SECURE, PASSWORD), (ap_open, OPEN, None)):
        for _attempt in range(3):       # hostapd sometimes can't take a fresh radio at once
            ap = access_point(iface, ssid, password)
            time.sleep(2)
            if ap.poll() is None:
                break
        aps.append(ap)
    # Addresses for the box, like a router would hand out.
    for n, iface in enumerate((ap_secure, ap_open), 1):
        sh(f"ip addr add 10.99.{n}.1/24 dev {iface}")
        aps.append(subprocess.Popen(["dnsmasq", "--keep-in-foreground", "--port=0", f"--interface={iface}",
                                     "--bind-interfaces", f"--dhcp-range=10.99.{n}.10,10.99.{n}.50,1h",
                                     f"--pid-file=/tmp/dnsmasq-{iface}.pid", "--leasefile-ro"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        time.sleep(3)
        net = network()
        check("the hub sees the box's Wi-Fi device", net.get("wifi_device") == box, str(net))

        api(cmd="wifi_scan")
        st = wait_state(lambda s: not s["network"]["scanning"]
                        and {SECURE, OPEN} <= {n["ssid"] for n in s["network"]["networks"]}, 40)
        nets = {n["ssid"]: n for n in st["network"]["networks"]}
        check("scan finds both networks", {SECURE, OPEN} <= nets.keys(), str(list(nets)))
        check("and knows which one needs a password",
              nets.get(SECURE, {}).get("secure") is True and nets.get(OPEN, {}).get("secure") is False, str(nets))

        api(cmd="wifi_connect", ssid=SECURE, password="wrong password")
        st = wait_state(lambda s: s["network"]["connecting"] is None and s["network"]["message"], 60)
        check("a wrong password is reported", st["network"]["ssid"] != SECURE and st["network"]["message"],
              str(st["network"]["message"]))
        sh(f"nmcli connection delete id '{SECURE}'")      # what NetworkManager kept of the failed attempt

        api(cmd="wifi_connect", ssid=SECURE, password=PASSWORD)
        st = wait_state(lambda s: s["network"]["ssid"] == SECURE, 60)
        check("connects with the right password", st["network"]["ssid"] == SECURE, str(st["network"]))
        check("and remembers it", SECURE in st["network"]["known"], str(st["network"]["known"]))
        address = sh(f"ip -4 -o addr show {box}").stdout
        check("gets an address from the access point", "10.99.1." in address, address)

        bad = api(cmd="wifi_connect", ssid=SECURE, password="short")
        check("passwords shorter than 8 characters are refused before trying", bad.get("ok") is False, str(bad))

        api(cmd="wifi_forget", ssid=SECURE)
        st = wait_state(lambda s: SECURE not in s["network"]["known"], 20)
        check("forget removes it", SECURE not in st["network"]["known"] and st["network"]["ssid"] != SECURE,
              str(st["network"]))

        api(cmd="wifi_connect", ssid=OPEN)
        st = wait_state(lambda s: s["network"]["ssid"] == OPEN, 60)
        check("connects to an open network", st["network"]["ssid"] == OPEN, str(st["network"]))
        api(cmd="wifi_forget", ssid=OPEN)
    finally:
        for ap in aps:
            ap.terminate()
        sh("modprobe -r mac80211_hwsim")


if __name__ == "__main__":
    main()
    print(f"{'FAILED: ' + ', '.join(failed) if failed else 'all wifi checks passed'}")
    sys.exit(1 if failed else 0)
