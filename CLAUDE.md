# Notes for coding agents

TvBox MediaOS: an Arch Linux–based media PC system (installer ISO, `tvbox-*`
packages, a TV UI driven by an Xbox controller or a phone). All phases of
the original brief are done; work now is fixes and features found by using
the real box.

Read first, as needed:
- `README.md`: what it is, building, testing, licenses.
- `docs/ARCHITECTURE.md`: components, repository layout, build/test flow.
- `docs/DECISIONS.md`: why things are the way they are, what was tried and
  didn't work, and per-change notes (newest at the end). Check it before
  changing a behaviour; it often records why the obvious approach failed.
- `docs/USER_GUIDE.md`: what the user sees; keep it in step with UI changes.
- `media-distro-prompt.md`: the original brief.

## Hard rules

- Never run anything that touches real disks on the development machine.
  Install and boot logic is tested only in QEMU (UEFI/OVMF) on disk images
  under `build/`. Ask before any destructive or irreversible action.
- Do not extract or use CDM (Widevine) keys or decrypt streams.
- No trademarked logos or other third-party assets in the repository (app
  icons are fetched by each box, see `src/tvbox/icons.py`). No ISO in public
  CI: publishing it would mean publishing the sources of every Arch package
  in it (DECISIONS: Licenses).

## How work is done

- One branch and one PR per change, off `main`. When the work is finished
  and tested, push and open the PR without asking. The user merges.
- Add a section to the end of `docs/DECISIONS.md` for every change of
  behaviour: what, why, what was tried, what is unverified.
- Update `README.md` / `docs/USER_GUIDE.md` when the user-visible behaviour
  changes.
- Before a PR: `make lint` (shellcheck, Python checks, unit tests), and for
  anything touching the session, input, hub or UI, the VM tests
  (`make qemu-session`, see below). Add tests next to the existing ones in
  `tests/unit/` and `tests/qemu/*_test.py`.
- Code style: match the surrounding code. Python is plain asyncio + aiohttp,
  no framework; the web UI is plain JS/CSS with no build step. Comments
  explain why, briefly.

## Development machine notes

- Docker builds need the docker group in the current shell: `sg docker -c
  "make repo"` (or `make packages`, `make iso`).
- No global git identity is configured; pass it per commit:
  `git -c user.name="Andri Benedikt Egillson Langdal" -c
  user.email="70175486+Andri1411@users.noreply.github.com" commit ...`
- Unit tests need a venv with `pytest aiohttp evdev-binary dbus-fast qrcode`
  (system Python lacks evdev headers): `PYTHON=<venv>/bin/python make lint`.
- A second, separate test VM can run beside the default one by giving it its
  own build dir and ports, e.g. `BUILD_DIR=build/sec QEMU_SSH_PORT=2225
  QEMU_HUB_PORT=8085 MIRROR_PORT=8805` (then `build/sec/repo` can be a
  symlink to `../repo`). With a custom `BUILD_DIR`, the ISO is in
  `$BUILD_DIR/iso`.

## Testing loop

```sh
make lint
sg docker -c "make repo"                     # packages → build/repo
make qemu-install                            # once: fresh install from a new ISO
PUSH=1 make qemu-session                     # install build/repo in the VM, run all session checks
tests/qemu/vm.sh up | push | ssh | tv <cmd> | shot <file.png> | down
```

Look at screenshots (`vm.sh shot`) after UI changes; the checks don't judge
layout or contrast. What QEMU can't test (video decoding, HDMI audio,
Bluetooth, Widevine quality) is listed in ARCHITECTURE §4.

## The real box

- Reachable as `ssh root@tv.local` when the user has it on; ask before
  changing anything persistent there, and clean up test files afterwards.
- Hardware: Alder Lake, Intel Iris Xe, 4K TV over HDMI, Xbox Wireless
  Controller over Bluetooth (xpadneo), a keyboard remote.
- Updates reach it only through CI: after a merge to `main`, CI builds,
  signs and publishes the repository to GitHub Pages
  (https://andri1411.github.io/TvBox-MediaOS/) in about 15 minutes; then the
  user installs it from Settings → Updates. "Everything is up to date" right
  after a merge usually means CI hasn't finished.
- Things learned there (details in DECISIONS): YouTube serves 4K only to the
  PS4 user agent; the Xbox button's press and release arrive together over
  Bluetooth, so "hold" is impossible and the system menu is a double tap;
  Jellyfin desktop needs its Display mode set to TV for D-pad navigation.

## Open items

- Prime Video's D-pad site rule (`src/extensions/tvnav/sites/prime.js`) is
  untested signed in.
- Jellyfin's TV layout is set by hand on the box; making it automatic on
  fresh installs is still open.
