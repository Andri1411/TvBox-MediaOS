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
