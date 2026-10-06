# TvBox MediaOS

A small Arch Linux–based system for a media PC behind the TV, used entirely
from the couch with an Xbox controller or a phone. It boots straight into a
TV home screen with YouTube (TV interface), Netflix, Disney+, Floatplane and
Jellyfin, keeps itself recoverable with snapshots, and updates over the air
from this repository, but only when you ask.

| | |
|---|---|
| **Using it** (controller, phone, settings, updates, troubleshooting) | [docs/USER_GUIDE.md](docs/USER_GUIDE.md) |
| How it is built and why | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DECISIONS.md](docs/DECISIONS.md) |
| The original brief | [media-distro-prompt.md](media-distro-prompt.md) |

**Status:** complete and tested in virtual machines; not yet tested on the
real hardware. What only the hardware can show is listed at the end of
[docs/DECISIONS.md](docs/DECISIONS.md) (Phase 6 status).

## What you need

- The PC: x86-64 with UEFI (built for a 12th-gen Intel laptop board with Intel
  graphics). **Its internal disk is erased by the installation.**
- A USB stick of 2 GB or more.
- For the installation only: a USB keyboard, and a network cable or Wi-Fi.
- An Xbox controller (USB cable, or Bluetooth after setup).
- Optional, for SSH maintenance: a public SSH key, either uploaded to your
  GitHub account (github.com/settings/keys) or at hand to type in. **It can only be
  added during installation.**

## 1. Get the installer ISO

Either download it from GitHub (Actions → *ci* → *Run workflow* with
"Also build the installer ISO" ticked; the ISO is attached to the run as an
artifact), or build it yourself on Linux (needs docker, see "Building"):

```sh
git clone https://github.com/Andri1411/TvBox-MediaOS && cd TvBox-MediaOS
make iso            # → build/iso/tvbox-mediaos-<date>-x86_64.iso
```

## 2. Write it to a USB stick

This erases the stick. Find its device name first (`lsblk` before and after
plugging it in, e.g. `/dev/sdb`), then:

```sh
sudo dd if=build/iso/tvbox-mediaos-<date>-x86_64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

GNOME Disks ("Restore Disk Image") or balenaEtcher work too.

## 3. Install

1. In the PC's firmware settings (often F2 or Del at power-on): **turn off
   Secure Boot** (the installer and the boot loader are not signed for it)
   and choose the USB stick to boot from.
2. The installer starts by itself. With the keyboard:
   - **Disk:** choose the internal disk. Everything on it is erased.
   - **Network:** nothing to do with a cable; otherwise pick your Wi-Fi and
     type its password.
   - **SSH (optional):** your GitHub username (its public keys are
     imported), or paste a public key, or leave it empty for no SSH.
   - **Confirm** (the default answer is No, on purpose).
3. It installs for 5 to 15 minutes, depending on the network.
   Then remove the stick and press Enter. The box restarts into the home
   screen.

The box is now called **tv** on your network (`tv.local`). The keyboard is no
longer needed.

## 4. First steps (with the controller)

- **Sign in** to the services: YouTube and Floatplane show a code to confirm
  on your phone; Netflix and Disney+ take e-mail and password (the on-screen
  keyboard opens by itself); Jellyfin asks for your server's address once.
  Details: [USER_GUIDE.md](docs/USER_GUIDE.md#signing-in-to-the-services).
- **Pair your phone** as a remote: Settings → Pair a phone, scan the QR code.
  The phone remote has a text field, which is the easiest way to type passwords.
- **Bluetooth controller or headphones:** Settings → Bluetooth.
- **Audio output** (TV, soundbar, headphones): Xbox button twice → Audio
  output.

The buttons, briefly: D-pad = move, **A** = OK, **B** = back, **Xbox** =
home, **Xbox twice** = system menu, **Start** = play/pause, **LT/RT** =
volume, **Y** = keyboard. Everything else is in the
[user guide](docs/USER_GUIDE.md).

## Updates

Every change merged into `main` here is built, signed and published by CI to
[andri1411.github.io/TvBox-MediaOS](https://andri1411.github.io/TvBox-MediaOS/), together
with Arch Linux's own updates from the Arch mirrors. The box looks for
updates once a day and shows a badge on the Settings tile; nothing is
installed until you choose **Settings → Updates → Install**. A snapshot is
taken before every update; if the box doesn't come up properly afterwards,
it starts the snapshot by itself and asks what to do. You never need the
USB stick again, except to change the disk layout.

## Changing things

Configuration overrides are plain TOML files, applied when saved (no
restart):

- `~tv/.config/tvbox/bindings.toml`: what the controller buttons do, globally
  or per app (also editable from the phone, with validation).
- `~tv/.config/tvbox/services.toml`: tiles on the home screen: add, remove,
  reorder, change URLs or user agents.
- `/etc/tvbox/` holds the same files for the whole box; the commented
  defaults are in `/usr/share/tvbox/`.

Maintenance over SSH: `ssh root@tv.local` (with the key given at
installation). `tvbox-ctl status` shows the input layer; logs are in the
journal.

## Building and testing (for development)

Requirements: Linux with `make`, docker or podman (or an Arch host), and for
testing `qemu-system-x86_64` with OVMF (`edk2-ovmf` on Arch, `ovmf` on
Debian/Ubuntu) and KVM. Docker must be usable by your user
(`sudo usermod -aG docker $USER`, then log in again).

```sh
make help          # all targets
make packages      # build pkgs/* (and pinned AUR packages) into build/pkgs
make repo          # the pacman repository in build/repo
make iso           # the installer ISO in build/iso
make lint          # shellcheck, Python checks, unit tests
make qemu-iso      # boot the ISO in QEMU (UEFI, NVMe disk image)
make qemu-disk     # boot the installed disk image
make qemu-install  # unattended install from the ISO in QEMU + checks
make qemu-session  # input, menu, launcher, navigation, keyboard, watchdog,
                   # Wi-Fi and phone checks on that install (fake Xbox pad)
make qemu-update   # update, automatic boot fallback and rollback
                   # (first: make repo VERSION=<higher than installed>)
```

- `make lint` runs the unit tests when `python3` has `pytest`, `evdev` and
  `aiohttp`; otherwise `PYTHON=~/venv/bin/python make lint` (in a venv
  without Python headers: `pip install pytest aiohttp evdev-binary
  dbus-fast qrcode`).
- `tests/qemu/vm.sh up | push | reboot | ssh | tv | shot | down` drives the
  installed test VM; `push` installs the packages from `build/repo` into it.
- Settings live in `config.mk` and can be overridden per call, e.g.
  `make qemu-iso QEMU_MEM=8G QEMU_DISPLAY=gtk`.
- Repository layout: `pkgs/` (one directory per package), `src/tvbox/` (input
  daemon, hub, shell, updater), `src/web/` (TV and phone UI),
  `src/extensions/` (navigation for desktop sites), `iso/` (installer
  image), `tests/` (unit and QEMU tests), `scripts/` (build tooling).

Nothing here ever writes to a real disk on the development machine; installs
are only tested on QEMU disk images under `build/`.
