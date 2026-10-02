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
