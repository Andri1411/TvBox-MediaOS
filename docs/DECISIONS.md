# Decisions log

Non-obvious choices, newest phase last. Each entry: what, why, alternatives,
and anything that was tried and didn't work. Design rationale that belongs to
the whole system lives in [ARCHITECTURE.md §5](ARCHITECTURE.md#5-key-decisions-and-trade-offs-details-in-decisionsmd);
this file records the concrete decisions as they are made.

## Phase 0 — skeleton, build tooling, QEMU harness

### Make, not just
`make` is preinstalled on every dev machine and CI runner; `just` would be one
more thing to install for no real gain. Targets: `make help`.

### Builds run in an Arch container
`makepkg`, `repo-add` and `mkarchiso` only exist on Arch.
`scripts/run-in-builder.sh` runs each build step in `tools/builder.Dockerfile`
(docker or podman, auto-detected) and runs it natively when the host is Arch
(`CONTAINER=none`). The container is a clean environment per run, which is
what `makechrootpkg` would give us; `makechrootpkg` itself is not used because
it needs systemd-nspawn, which does not run reliably inside docker.
- The entrypoint remaps the `builder` user to the host UID so outputs in
  `build/` are owned by the developer, and `makepkg` (which refuses root)
  works.
- Only `make iso` uses `--privileged` (mkarchiso mounts filesystems).
- `EXTRA_CA_CERT=… make builder` bakes an extra CA into the image for
  networks with TLS-intercepting proxies.

### Package versions = VERSION.r<commit count>
Every commit yields a strictly newer `pkgver`, so the box sees any new build as
an update without anyone bumping versions by hand. PKGBUILDs read it from
`$TVBOX_PKGVER`. CI must check out full history (`fetch-depth: 0`).

### Packages can depend on each other within one build
`build-packages.sh` registers each built package in a throwaway local repo
(`_buildlocal`) that pacman inside the builder can resolve, then builds the
next one. Order: pinned AUR packages (`pkgs/aur.list`) first, then
`pkgs/build-order`.

### Third-party packages are pinned AUR commits, rebuilt into [tvbox]
The box never talks to the AUR. `pkgs/aur.list` pins each AUR package to a git
commit, so an upstream PKGBUILD change is reviewed (by bumping the pin) before
it reaches the box.

### QEMU test machine mirrors the board
q35 + OVMF (UEFI), **NVMe** disk so the installer sees `/dev/nvme0n1` like on
the real mainboard, virtio-gpu (optional `QEMU_GL=1` for virgl), xHCI with USB
keyboard + tablet, optional USB passthrough of a real controller
(`QEMU_USB_HOST=vid:pid`), optional HDA audio. Serial log and a QMP socket per
run allow scripted tests; `scripts/qmp.py` is stdlib-only so it runs anywhere.
No KVM → falls back to TCG automatically (slow but works in CI).

`make qemu-smoke` boots the bare firmware headless, waits for OVMF output on the
serial log, takes a QMP screendump, sends keys and quits. It validates the
harness itself (OVMF detection, UEFI vars copy, disk creation, QMP) on any
host, independent of Arch.

### Tried and didn't work
- **Building packages/ISO from the cloud dev session used for Phase 0:** its
  network policy blocks Arch mirrors (`*.mirror.pkgbuild.com`,
  `mirrors.kernel.org`, `archive.archlinux.org`) and the AUR
  (`aur.archlinux.org`); pacman gets 403 from the egress proxy. The builder
  image therefore could not be built there. Not a design issue: CI (GitHub
  runners) and any normal machine have mirror access. Everything that doesn't
  need mirrors (lint, QEMU harness) was run there.
- **shellcheck SC2054** flags QEMU's comma-separated option values as array
  mistakes; disabled file-wide in `scripts/qemu.sh` only.

### Phase 0 review answers
Name `tvbox`; automatic boot fallback instead of controller-driven GRUB menu;
`linux-lts`; phone remote over plain HTTP with token auth; sshd on (key-only);
32 GB RAM, so all services may stay alive in the background; home
screen/overlay in WebKitGTK; browser chosen by testing in Phase 3.

### Package repository: GitHub Pages, signed in CI
- **Hosting:** every push to `main` builds the repo in CI and, if a signing key
  is configured, deploys it to GitHub Pages (`make pages` stages it as
  `x86_64/` with symlinks replaced by real files, since static hosting serves
  no symlinks). The box's `/etc/pacman.d/tvbox-mirrorlist` points there. Only
  the latest packages are hosted; old versions are not needed because rollback
  uses snapper snapshots, not package downgrades.
- **Signing:** one ed25519 key without passphrase, created once by
  `scripts/gen-signing-key.sh` on the owner's machine. The private half lives
  only in the GitHub secret `TVBOX_SIGNING_KEY` (plus the owner's backup);
  the public half is committed in `pkgs/tvbox-keyring` and installed into
  pacman's keyring. `[tvbox]` uses `SigLevel = Required`, so the box rejects
  any unsigned or foreign package. Unsigned builds (no secret, or from
  branches and PRs) are built and uploaded as CI artifacts but never published.
- **Why not generate the key in the dev session:** the private key would pass
  through the session transcript. The script refuses to write the private key
  inside the repository.
- **Requirement:** GitHub Pages for a private repository needs a paid GitHub
  plan. With a free account the repository must be public, or the Pages site
  must come from a separate public repository.

## Phase 1 — installer ISO, base system, btrfs/snapper, session

### ISO: releng, trimmed, UEFI only
`iso/` started as archiso's `releng` profile with BIOS/syslinux, the speech
and memtest entries, cloud-init, VM guest agents, modem/VPN tooling and most
rescue tools removed. The target is UEFI-only, so systemd-boot is the only
ISO boot mode. Kernel params add `console=ttyS0` so QEMU tests can read the
serial log; on real hardware without a serial port it is harmless.

### The installer is a package (`tvbox-installer`)
It is linted, versioned and built like everything else, and the ISO just
installs it. It autostarts on tty1 (`/root/.zlogin`); other ttys and SSH get
a normal shell for rescue work. A `dialog` TUI asks only for the disk, Wi-Fi
(only when no wired connection works) and an optional GitHub username to
import SSH keys, then a default-*no* confirmation.

### Unattended install only via QEMU fw_cfg
Automated tests answer the installer through a QEMU fw_cfg blob
(`opt/tvbox/autoinstall`). Real hardware has no fw_cfg, so there is no kernel
parameter or file on the stick that could make a real machine wipe its disk
without a confirmation.

### Disk layout
1 GiB ESP at `/efi` (GRUB's EFI binary only) + one btrfs partition with
`@ @home @snapshots @var_log @var_cache_pacman_pkg @var_tmp`, mounted
`noatime,compress=zstd:1`. `/boot` is inside `@`. `subvolid=` is stripped from
fstab so a rolled-back `@` mounts by name. No swap partition: zram.

### What the installer still writes by hand
Only machine-specific or one-time things: fstab, hostname, locale, timezone
(auto-detected from IP, fallback UTC), the `[tvbox]` stanza in
`/etc/pacman.conf`, users, SSH keys, the Wi-Fi profile, the snapper config
(copied from a template in `tvbox-base`), and 3 lines in `/etc/default/grub`.
`/etc/default/grub` is owned by the `grub` package and Arch's GRUB has no
`grub.d` drop-in directory for it, so editing it once at install time is the
least bad option.

### Enabling units from a package without fighting the user
`tvbox-base` lists the units it wants in `/usr/share/tvbox/enabled-units`. Its
install script enables each unit the first time it appears and records it in
`/var/lib/tvbox/enabled-units.seen`. A unit added in a later version is
enabled on upgrade, and a unit the user disabled stays disabled. A blanket
`systemctl preset-all` was rejected because it would also reset units we
don't own.

### GRUB is reinstalled on every grub upgrade
Arch doesn't re-run `grub-install` when the grub package updates, which can
leave an old EFI binary with new modules. A pacman hook runs
`tvbox-grub-update`, which installs the named entry plus the removable path
`EFI/BOOT/BOOTX64.EFI` (bare boards sometimes lose NVRAM entries) and
regenerates `grub.cfg`.

### initramfs uses busybox/udev hooks, not systemd
`grub-btrfs-overlayfs` (makes read-only snapshots bootable with a tmpfs
overlay) is a busybox `run_latehook` hook and does not work with the systemd
initramfs, so the HOOKS line uses `base udev …`. No `fsck` hook (btrfs
doesn't need it).

### Session: greetd + restart loop
greetd auto-logs in `tv` once (`initial_session`). `tvbox-session` restarts
sway if it exits with an error and gives up after 5 crashes within 10 s each,
so a broken compositor falls back to a text login (`default_session`) instead
of a crash loop. greetd's own config file belongs to the greetd package, so
ours is selected with a `greetd.service` drop-in (`--config`).
`WLR_RENDERER_ALLOW_SOFTWARE=1` lets sway start in QEMU without virgl; on
real hardware the Intel GPU is used anyway.

### Who can do what
`tv` has no password, no sudo, and is in `input video audio`. Maintenance is
`ssh root@box`, key-only. If `tv` (which runs the browsers) is compromised,
that doesn't give root.

### No packages built with --syncdeps
Our packages are `arch=any` with no build step, so `makepkg --nodeps` is used
for them. Otherwise building `tvbox-base` would install its whole runtime
dependency tree (kernel, mesa, ...) into the builder. AUR packages still use
`--syncdeps`.

### Tried and didn't work (Phase 1)
- **Separate `intel-ucode.img` initrd in the ISO boot entry:** current archiso
  embeds microcode in the initramfs (mkinitcpio `microcode` hook) and no
  longer ships the file; systemd-boot failed with "Error preparing initrd: Not
  found" and OVMF fell through to the UEFI shell. Removed the line.
- **releng's `systemd-time-wait-sync`:** blocks boot until NTP succeeds, with
  no time limit. On a network that blocks NTP (the QEMU test sandbox, some
  guest Wi-Fi) the installer never starts. Removed from the ISO; the RTC is
  accurate enough for signature checks and timesyncd still runs.
- **Guest downloading directly from Arch mirrors in the test sandbox:** the
  sandbox only allows HTTPS through an intercepting proxy, and the VM
  doesn't trust its certificate, so the installer reported "no network".
  `tests/qemu/install.sh` now starts `scripts/mirror-cache.py`, a small
  caching HTTP mirror on the host (packages cached in `build/mirror-cache`,
  databases always fresh). The installer takes `mirror=` from the
  fw_cfg answers. This is useful anyway: repeat test installs no longer download ~1 GB.
  The installer's online check now probes the first configured mirror instead
  of a hard-coded host.
- **foot as the placeholder screen in QEMU:** under TCG, foot drew its
  background but no glyphs. The cause turned out to be Mesa's llvmpipe (see
  below): with the pixman renderer in VMs, foot renders text normally. The
  placeholder still uses `pango-view` + the sway background, which is simpler
  than a terminal for a static status screen.
- **Leaving the embedded repo's sync db on the installed system:** a local
  (unsigned) dev ISO left `/var/lib/pacman/sync/tvbox.db` behind, and with
  `SigLevel = Required` every pacman operation then failed with "missing
  required signature". The installer now deletes it after pacstrap, so the box
  fetches the signed db from its own mirror.
- **sway output power on right after power off** failed once with "Backend
  commit failed" in QEMU and worked on retry. To keep in mind for HDMI
  hotplug handling (Phase 6): retry output re-enable.
- **`grub-reboot` into a grub-btrfs snapshot entry:** GRUB follows
  `next_entry` into the "snapshots" submenu but stops there and waits for a
  key. grub-btrfs loads its menu with `configfile`, and GRUB doesn't carry the
  default entry or the timeout into configfile menus. Manual selection with a
  keyboard works. **Consequence for the automatic boot fallback (Phase 6):** it
  cannot point at grub-btrfs entries. tvbox will generate its own top-level
  menuentry, with a fixed ID, for the recorded fallback snapshot
  (`/etc/grub.d/` script reading grubenv), and use that for both the
  automatic fallback and "boot into snapshot once" from the TV menu.

### Verified: booting a snapshot (QEMU)
Selected `@snapshots/2/snapshot` by hand in GRUB's "snapshots" menu. It booted
the snapshot's own kernel (`/@snapshots/2/snapshot/boot/vmlinuz-linux-lts`),
`/` was the read-only snapshot under a writable tmpfs overlay
(`grub-btrfs-overlayfs`), and the sway session started. The only failed unit
was `systemd-remount-fs` (it tries to apply fstab's root options to the
overlay). That's harmless, but the Phase 6 health page must not report it
as a fault while booted into a snapshot.
- **Mesa llvmpipe under QEMU TCG:** sway crashed in `libgallium` (a jump to a
  null address in JIT-compiled code). That's also the likely cause of foot's
  missing glyphs. In a VM, `tvbox-session` now selects wlroots' pixman
  renderer (`WLR_RENDERER=pixman`). Testing for a GPU render node didn't
  work: virtio-gpu exposes `/dev/dri/renderD128` even without 3D. The real box
  is never a VM and keeps the GLES renderer.
  `/etc/tvbox/session.conf` can override it (e.g. GLES with `QEMU_GL=1`).
- **pango-view segfaults on an unknown output extension** (`foo.png.tmp`).
  The status screen's temporary file is now `*.new.png`.
- **`systemctl restart greetd` lands on the text login:** greetd runs its
  `initial_session` (auto-login) only once per boot. "Restart session" in
  the TV menu (Phase 2) must therefore restart sway through
  `tvbox-session`'s loop (e.g. `swaymsg exit` with a non-zero code), never by
  restarting greetd. The sway crash above confirmed the loop works: sway
  came back by itself.

### Phase 1 status
**Tested in QEMU** (`make qemu-install`, clean install from the ISO, all 17
checks pass): unattended install onto NVMe; btrfs subvolume layout and mounts;
`/boot` inside `@`; linux-lts boots via GRUB; no failed units; greetd
auto-login → sway as `tv`; status screen renders; user session target up;
snapper config; zram; journald limits; suspend disabled; sshd key-only;
`[tvbox]` repo + key configured; pacman creates pre/post snapshots; snapshots
appear in GRUB. Booting a snapshot by hand from GRUB was verified separately.

**Not testable in QEMU, needs the real box:** the interactive installer UI
including Wi-Fi via iwd; UEFI NVRAM behaviour on the real board (the removable
`BOOTX64.EFI` fallback); Intel GPU with the GLES renderer (QEMU uses pixman);
HDMI output, 4K scaling and hotplug; Bluetooth; PipeWire HDMI audio; thermals.

**Open for later phases:** automatic boot fallback needs its own GRUB entry
(Phase 6); `systemd-remount-fs` "fails" when booted into a snapshot (health
page must not flag it); retry output power-on for HDMI hotplug.

## Phase 2 — input daemon, bindings, controller defaults, system menu

### How a button becomes an action
Two layers, as planned. `devices.py` turns raw evdev events into logical
buttons; `engine.py` turns buttons into actions using `bindings.toml`.
- A binding without `long` fires when the button goes **down** (lowest
  latency). A binding with `long` fires its short action on **release** and
  its long action after `long_press_ms` while still held. `repeat` and `long`
  can't be combined on one button; the parser rejects it.
- `key:` actions are taps (down + up at once), not holds. Hold-repeat is done
  by the daemon (`repeat = true`), so the rate is the same in every app and
  does not depend on each client's own key-repeat settings.
- The left stick produces `ls_up`… buttons, which fall back to the d-pad
  bindings unless bound themselves; the right stick produces `rs_*`, unbound
  by default. Sticks are four-way with hysteresis, so a menu never moves
  diagonally or flaps near the threshold.
- When the focused app or the mode changes under a held button, the button
  is cancelled: its repeat stops and its release fires nothing. Otherwise a
  long press that opens the menu would also "press" something in the menu.

### The system menu button can't be configured away
The parser rejects a config in which no `[global]` button opens
`ui:system_menu`, and rejects `[app.*]` sections that rebind such a button.
That is what "always reachable no matter what app is focused" means in
practice; it also protects the phone's bindings editor (Phase 5) from locking
the user out.

### A broken bindings file never takes the controller away
On reload, a file with errors is rejected as a whole and the previous bindings
stay active. At startup, if the user file is broken, the daemon falls back to
`/etc` + defaults, then defaults alone. The errors are in `tvbox-ctl status`,
the journal, and the system menu shows a notice. `tvbox-ctl check [file]`
validates without touching the running daemon.

### Keyboards are left alone (change from the Phase 0 plan)
ARCHITECTURE said a plugged-in keyboard would be read without grabbing it,
with "only explicitly bound global keys intercepted". evdev can't intercept
single keys: either the device is grabbed and everything is re-emitted, or the
app sees every key too, and a bound key would then act twice. So:
- **gamepads** and **remotes** (devices with arrows + OK but no alphabet) are
  grabbed and go through the bindings;
- **keyboards** are not touched by inputd at all. Their way into the system
  menu is sway: `Ctrl+Alt+M` or the Menu key opens it, `Ctrl+Alt+H` goes home,
  and while the menu is open the hub switches sway into a binding mode where
  arrows/Enter/Escape drive the menu instead of the app;
- a keyboard-like device that should behave as a remote (the future ESP32 BLE
  remote presents itself as a full keyboard) gets a `[[device]]` rule in
  bindings.toml: `profile = "remote"`, `grab = true`, optional extra key map.

### The focused app is the sway workspace name
One workspace per app, named after the service id (Phase 3). inputd subscribes
to sway's workspace events itself, so per-app bindings work without the hub.
Matching on window `app_id`/class was rejected: all Chrome instances share
one unless each is started with its own class, and the workspace is already
unique.

### While the overlay is open, nothing reaches the app
In `ui` mode, d-pad/stick/A/B go to the hub as navigation events. Other
buttons only fire `ui:`, `volume:`, `audio:` and `mouse:` actions; `key:` and
`app:` actions are dropped. If the shell or the hub dies while the menu is
open, the hub (or its restarted successor) puts inputd back into the previous
mode, and the hub refuses to open the menu while no shell is connected, so
the controller can't get stuck steering an invisible menu. Both cases are in
the VM test.

### Mouse mode came early
Basic mouse mode (left stick = pointer with a quadratic curve and a speed
ramp, right stick = scroll, A = click) is in Phase 2 instead of Phase 4,
because the menu item would otherwise do nothing and the virtual device has
to declare its pointer capabilities at creation anyway. The cursor is hidden
with sway's `seat * hide_cursor 100` and shown with `hide_cursor 0` in mouse
mode. Phase 4 still owes: tuning on the real TV, right click, drag.

### One overlay window, mapped only when needed
`tvbox-shell` is a GTK4 layer-shell window (overlay layer, anchored to all
edges, keyboard interactivity none, empty input region) showing the hub's
page in WebKitGTK. The page tells the shell when it has something to show
(menu or OSD) and the window is unmapped otherwise, so sway doesn't blend a
transparent fullscreen surface over the video all day and can scan the video
out directly. The page keeps its WebSocket while unmapped.

### Hub listens on loopback only for now, and loopback is not trusted blindly
`127.0.0.1:8080` until the phone remote brings token authentication
(Phase 5). The QEMU port forward to 8080 therefore answers nothing yet; tests
talk to the hub over SSH.

A web page running in one of the box's own browsers can also send requests to
127.0.0.1 (a cross-site POST, or a WebSocket, which no CORS rule stops). The
hub therefore refuses any request whose `Origin` is not its own or whose
`Host` is not `127.0.0.1`/`localhost` (DNS rebinding). Otherwise an ad on a
streaming site could reboot the box. Phase 5 must keep this check when it
adds the LAN listener.

### Volume
`wpctl` on `@DEFAULT_AUDIO_SINK@`, capped at 100 % (`-l 1.0`). Trigger
repeats arrive faster than `wpctl` runs, so the hub sums pending steps and
applies them in one call. Default step is 2 % at 12 Hz (24 %/s) from the
triggers and 5 % per press in the menu. Output list from `pw-dump`.

### Restart session and reboot
"Restart session" creates `$XDG_RUNTIME_DIR/tvbox/restart-session` and tells
sway to exit; `tvbox-session` restarts sway when the flag exists and treats a
clean exit without it as a logout (see Phase 1: greetd logs in automatically
only once per boot). "Reboot" is plain `systemctl reboot` from the hub; logind
allows it for the `tv` user's active session without a polkit rule (verified
in the VM, also with a root SSH session open).

### Packaging
- `tvbox-core` installs the Python code to `/usr/lib/tvbox`, not
  site-packages: that path contains the Python minor version, and an
  `arch=any` package there would break on every Arch Python bump until
  rebuilt. The launchers in `/usr/bin` add the directory to `sys.path`.
- User units are enabled by shipping the
  `tvbox-session.target.wants/` symlinks in the package; no install script.
- `sway` now runs `dbus-update-activation-environment … && systemctl --user
  start tvbox-session.target` as one command, because separate `exec` lines
  run concurrently and the shell needs `WAYLAND_DISPLAY`.
- `xpadneo-dkms` is pinned in `pkgs/aur.list` and built with `--nocheck`
  (its check step wants kernel headers in the builder; DKMS builds the module
  on the box, verified against linux-lts 6.18 in the VM). Without xpadneo the
  kernel's generic driver reports triggers and right stick on different axes;
  the gamepad profile detects that layout too.

### Tried and didn't work (Phase 2)
- **inotify on `~/.config/tvbox` before it exists:** the watch silently
  failed and saving a new user bindings file did nothing. inputd now creates
  the directory at startup.
- **`journalctl --user -M tv@` as root:** "Connecting to a machine as non-root
  is not supported". `tests/qemu/vm.sh tv <cmd>` runs commands as `tv` with
  its runtime dir instead.
- **Passing a command through `ssh host sh -c '…' -- args`:** ssh joins its
  arguments into one string, so the arguments never reach `"$@"`.
  `vm.sh tv` quotes them with `printf %q`.
- **`pacman -U --needed` for pushing dev builds into the VM:** uncommitted
  changes have the same `pkgver`, so nothing was installed. `vm.sh push`
  always reinstalls.
- **Exact screenshot comparison after closing the menu:** the placeholder
  screen redraws its uptime line. The test compares the share of changed
  pixels instead (`tests/qemu/screendiff.py`).
- **`pip install evdev` on the dev host (Linux Mint):** needs Python headers.
  `evdev-binary` has wheels; see README.
- **WebKitGTK in the VM** logs Mesa/Vulkan errors (no GPU) and falls back to
  software rendering. Harmless there; says nothing about the real box.

### Phase 2 status
**Tested in QEMU.** `make qemu-install` (clean install, 21 checks) and
`make qemu-input` (72 checks) pass, from a freshly built ISO. The input test plugs a fake Xbox
controller into the guest through uinput, with the name, IDs and capabilities
the kernel's `xpad` driver reports, and reads what comes out of the virtual
input device:
default mapping from the brief (A, B, Start, bumpers, d-pad, left stick,
triggers, Y, Xbox short/long); hold-repeat; user override and per-app
bindings; reload on save; rejection of a broken file with the old bindings
kept; ui and mouse modes; hot-unplug/replug; daemon killed and restarted;
system menu opened with the controller, volume/mute/app switch/mouse mode
from the menu, nothing typed into the app meanwhile; shell or hub killed with
the menu open; restart session; keyboard path into the menu; the menu
actually visible on screen and gone afterwards. Reboot from the menu and the
xpadneo DKMS build were checked by hand. Idle CPU of all three daemons is 0 %;
the shell with its WebKit processes uses about 300 MB.

**Not testable in QEMU, needs the real box:** a real Xbox controller over USB
(the fake one follows `xpad`, but trigger ranges and the Guide button differ
between controller generations) and over Bluetooth with xpadneo (pairing is
not in the UI yet: `bluetoothctl` over SSH until the settings screen exists);
stick feel, dead zones and mouse-mode speed on a TV; the overlay over real
video with the GLES renderer (transparency, and whether fullscreen video
still gets direct scanout once the overlay is unmapped); HDMI audio volume
and output switching (the VM has one emulated sound card); whether key taps
of zero length are accepted by every app (fine for sway and terminals).

**Left for later phases, deliberately:** Home and Switch app only switch
sway workspaces and Restart app only acts on a `tvbox-app@<id>` unit, both of
which the launcher provides in Phase 3; Settings is a disabled menu entry;
`ui:keyboard` shows a "later version" notice (Phase 4); the phone sends
buttons through the same `button` command the tests use (Phase 5).

### Phase 2 review answers
- **Back:** B stays Escape globally; browser apps get Alt+Left as a per-app
  binding when the launcher defines them (Phase 3).
- **Unbound buttons** (X, stick clicks, right stick outside mouse mode): left
  unbound until real use shows what is missing. Candidate: X = mouse mode
  toggle.
- **Volume:** the box controls its own output volume (triggers, menu, later
  the phone). This is a requirement, not a convenience: it must keep working
  for every output, including Bluetooth.
