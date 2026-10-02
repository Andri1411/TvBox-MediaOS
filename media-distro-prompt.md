# Project: Arch-based media PC distro ("tvbox" – working name)

You are building a small Arch Linux–based distribution for a single dedicated media PC behind a TV. It must be fully usable from the couch with no keyboard or mouse. Work in phases, commit after each phase, and keep a running `docs/DECISIONS.md` explaining non-obvious choices (especially anything you tried that didn't work).

## Target hardware
- Bare laptop mainboard (Intel Core i7 12th gen, Intel iGPU only, no dGPU, no screen, no keyboard, no battery assumptions). Runs headless-of-peripherals behind a TV, connected via HDMI.
- Always on. It should almost never need to be powered off or rebooted. The user controls TV power themselves, so no CEC is needed.
- Input at launch: an Xbox controller (handle both USB and Bluetooth; use `xpadneo` for Bluetooth if needed). Later: a phone web remote (in scope now) and a DIY BLE HID remote (ESP32-C6, acting as keyboard + consumer-control keys; out of scope except that the input layer must accept generic keyboard/media-key devices).
- Audio: HDMI and Bluetooth headphones/speakers, switchable from the remote.

## Deliverable
1. An **archiso profile** that builds a bootable ISO which installs the system onto the target disk with minimal interaction (disk selection + Wi-Fi + confirm).
2. A **custom pacman repository** (built in CI or locally via a script) containing the project's own packages, so the installed system updates its own components with `pacman` like any other package. Avoid one-shot install scripts that can't be updated later.
3. Source for every component in this repo, with a `Makefile` or `justfile` to build packages, build the repo, build the ISO, and boot it in QEMU.

**Never run anything that touches real disks on the development machine.** Test all install and boot logic in QEMU (UEFI via OVMF). Ask before any destructive or irreversible action.

## Base system
- Arch Linux, systemd, UEFI boot, **btrfs** root with subvolumes laid out for snapshots (`@`, `@home`, `@var_log`, `@snapshots`, etc.).
- **Snapshots, low overhead:** `snapper` + `snap-pac` (pre/post snapshot on every pacman transaction) + bootable snapshots in the boot menu (`grub-btrfs` or `limine` equivalent – pick one and justify it). Keep a small retention count. Do not make the root filesystem read-only; the system must stay fully customizable.
- **Updates are never automatic.** The user triggers them from the system menu (on the TV or the phone). Before updating, show a summary of what will change. After updating, offer a reboot if the kernel or core libraries changed. If boot fails, the user should be able to pick the previous snapshot from the boot menu with the controller.
- Auto-login to a dedicated unprivileged user. Graphical session starts automatically.
- Networking: NetworkManager (Wi-Fi + Ethernet), configurable from the on-screen menu and phone.
- Bluetooth: BlueZ, with pairing for controllers and audio devices doable from the on-screen menu.
- Audio: PipeWire + WirePlumber; default to HDMI; switch to a Bluetooth sink from the menu.
- Intel hardware video decoding: `intel-media-driver` (VA-API), verified working in the chosen browser for H.264, VP9 and AV1 on YouTube (check with `chrome://media-internals` or equivalent and document how). Dropped frames at 1080p/4K must be minimal.
- Keep the system awake indefinitely (no suspend, no screen blanking, no DPMS-off that the TV can't recover from). Handle the TV being turned off and on again (HDMI hotplug) without needing a restart.
- Minimize writes and log growth (journald size limits).

## Session
- **Sway** as the compositor, configured as a kiosk: no bars, no visible desktop, no window decorations, every app fullscreen.
- Cursor hidden unless mouse mode is on.
- Correct scaling for a 1080p or 4K TV, with an option for overscan/scale adjustment in settings.

## Home screen (launcher)
- A custom TV-style home screen: large tiles for each service, fully navigable with D-pad / arrow keys, clear focus highlight, readable from 3 m away.
- Services at launch: YouTube, Netflix, Disney+, Floatplane, Jellyfin. Adding/removing/reordering services must be a config change, not a code change.
- Each service runs in its own persistent browser profile/app instance so logins survive restarts. Switching between services and back to home must be fast; keep recently used services alive in the background if memory allows.
- A settings area: Wi-Fi, Bluetooth pairing, audio output, display scale, updates, restart session, reboot, about/version.
- Implementation suggestion (evaluate, don't treat as mandatory): a local web app rendered in a Chromium kiosk window, served by the same local daemon that powers the phone remote, so the UI code is shared.

## Services
- **Browser:** choose a Chromium-based browser with working Widevine on Arch (e.g. Google Chrome, or Chromium + Widevine). Justify the choice in DECISIONS.md. Enable VA-API decoding flags.
- **YouTube:** use the TV interface (`youtube.com/tv`) by spoofing a smart-TV user agent. The user has YouTube Premium, so sign-in must work in this mode. Verify sign-in, playback and voice/text search.
- **Jellyfin:** prefer the native **Jellyfin Media Player / jellyfin-desktop** client (mpv playback, TV layout) over the web UI. Fall back to the web UI in TV mode only if the native client is unusable.
- **Floatplane:** use the TV interface at `floatplane.com/tv`. Check whether it needs a smart-TV user agent (like YouTube) and whether its login flow (possibly a code/QR pairing step) works in the kiosk. Fall back to the desktop site plus the navigation scripts below only if the TV interface is unusable.
- **Netflix, Disney+:** no TV web interface exists. Make the desktop sites usable with the controller:
  - First investigate per-site injected user scripts or a small extension that add D-pad spatial navigation (focus rings, arrow-key movement between tiles, Enter to select, Back to go back). Keep site-specific scripts isolated so a site redesign breaks only that site.
  - Mouse mode (below) is always available as a fallback.
- **Streaming quality:** Linux browsers get Widevine L3, which typically limits Netflix and Disney+ to about 720p. 720p is acceptable, but higher is preferred. Spend real but bounded effort on client-side approaches: browser choice, user-agent / platform spoofing, and extensions or injected scripts that request higher-resolution profiles from the service (e.g. the "Netflix 1080p"-style extensions). Test each approach, measure the actual resolution and bitrate delivered (Netflix's Ctrl+Alt+Shift+D stats overlay, or equivalent), and record what works in DECISIONS.md. Make whatever works a per-service toggle, since these break when services change. Do not extract or use CDM keys or decrypt streams.
- **Ad blocking:** uBlock Origin in all browser profiles where it doesn't conflict with sign-in or playback.

## Input layer (most important part)
A single daemon (Python or Rust; justify) that reads all input devices via evdev and emits actions via uinput / Sway IPC / the launcher.

- **Bindings config file** (TOML) under `/etc/<name>/` with per-user override in `~/.config/<name>/`. Maps physical buttons → actions, with:
  - global bindings and **per-app bindings** (the same button can do different things in YouTube vs Netflix),
  - short press, long press, and hold-repeat,
  - device-agnostic button names so the Xbox controller, the phone remote and a future BLE keyboard-style remote can all share bindings.
  - Reload on change without restarting anything.
- **Default Xbox controller mapping:** D-pad / left stick = arrows; A = Enter; B = Back; Start = play/pause; bumpers = seek; triggers = volume; Y = on-screen keyboard; Xbox/Guide **long press** = system menu; Guide short press = home.
- **System menu** (overlay, always reachable no matter what app is focused): Home, switch app, volume, mute, audio output, restart current app, mouse mode toggle, settings, reboot.
- **Mouse mode:** toggled from the menu or a binding; left stick moves the pointer with acceleration, A = click, right stick = scroll.
- **On-screen keyboard:** controller-friendly (grid navigation), appears on demand or when a text field is focused if that is detectable; types via uinput.
- **Volume/OSD:** small on-screen indicator for volume and app switching.

## Phone remote
- The box serves a local web page (LAN only, no cloud) with: D-pad, back, home, play/pause, volume, menu, a text field that types into the focused app (for searching), mouse touchpad mode, app switcher, and the settings/update screen.
- Pair the phone simply (e.g. a QR code shown on the TV containing a URL with a one-time token). Reject unauthenticated LAN clients.
- Also a page for editing the bindings config with validation.

## Reliability
- **Watchdog:** detect a crashed or frozen browser/app (process exit, unresponsive renderer, black screen if detectable) and restart it, returning to home if needed. Log restarts.
- All long-running pieces are systemd user services with restart policies.
- Health page on the phone remote showing service status, recent restarts, temperature, uptime and free disk.
- Thermal sanity: the bare mainboard may have poor cooling. Expose CPU temperature (°C) and avoid needless background load.

## Working method
1. Phase 0: repo skeleton, build tooling, QEMU test harness. Write `docs/ARCHITECTURE.md` with the planned components and how they communicate, and stop for review.
2. Phase 1: archiso + installer + base system + btrfs/snapper, boots in QEMU to Sway.
3. Phase 2: input daemon + bindings config + controller defaults + system menu.
4. Phase 3: launcher + browser profiles + YouTube TV + Jellyfin client.
5. Phase 4: Netflix/Disney+/Floatplane navigation scripts + mouse mode + on-screen keyboard.
6. Phase 5: phone remote + pairing + bindings editor + health page.
7. Phase 6: watchdog, update flow, Bluetooth audio, polish, docs (`docs/USER_GUIDE.md` written for someone holding only a controller).

At the end of each phase: list what was tested, what couldn't be tested in QEMU (e.g. real VA-API, Bluetooth, real controller) and needs testing on hardware, and any open questions. Ask me rather than guess when a decision is hard to reverse.
