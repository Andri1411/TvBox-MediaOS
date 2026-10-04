# tvbox user guide

Everything here works with the Xbox controller alone. A phone works too (see
"Phone remote"); a keyboard is never needed.

## The buttons

| Button | What it does |
|---|---|
| D-pad or left stick | Move around |
| **A** | Select / OK |
| **B** | Back |
| **Xbox button**, short press | Home screen |
| **Xbox button**, hold (half a second) | System menu, from anywhere |
| **Start** | Play / pause |
| **LB / RB** | Rewind / fast-forward |
| **LT / RT** | Volume down / up (hold to keep changing) |
| **Y** | On-screen keyboard |
| **View** (the small button left of the Xbox button) | Switch between apps |

Holding a direction keeps moving, the same as holding a key.

## Home screen

The tiles are your apps: YouTube, Netflix, Disney+, Floatplane, Jellyfin.
Move to one and press **A**. An app you leave keeps running in the
background, so going back to it is instant, and the video you were watching
is paused when you leave. "running" on a tile means it is still open.

The last tile, **Settings**, has everything else (below).

## The system menu (hold the Xbox button)

It opens on top of whatever is on screen:

- **Home**
- **Switch app**: everything that is open, and the rest
- **Volume**: left / right on this line changes it
- **Mute**
- **Audio output**: TV speakers, a soundbar, Bluetooth headphones
- **Restart app**: when an app misbehaves
- **Mouse mode**: see below
- **Settings**
- **Restart session**: closes all apps and starts the screen again
- **Reboot**

**B** or the Xbox button (held) closes it.

## Signing in to the services

- **YouTube**: choose "Sign in with your phone" and follow the code on screen
  with your phone. Premium is recognised.
- **Floatplane**: shows a QR code and a code for floatplane.com/link; use your
  phone.
- **Netflix, Disney+**: these are the normal websites. Move with the D-pad (a
  white frame shows where you are). When you reach the e-mail or password
  field, the on-screen keyboard opens by itself.
- **Jellyfin**: the first time, it asks for your server's address. Press
  **Y** for the keyboard, type the address (for example
  `192.168.1.10:8096`), then sign in.

Cookie banners on Netflix and Disney+: the first press of the D-pad lands on
their first button; move to "Reject" or "Accept" and press **A**.

## On-screen keyboard (Y)

Move over the keys with the D-pad and press **A** to type. Then:

- **X** deletes the last letter
- **Start** presses Enter (search, sign in) and closes the keyboard
- **B** closes the keyboard
- **⇧** makes the next letter a capital
- **#+=** shows symbols, **áð** shows accented letters (á é í ó ú ý þ æ ö ð
  and more)

The line at the top shows what you have typed, in case the keyboard covers
the field.

## Mouse mode

For the rare page the D-pad can't reach: System menu → **Mouse mode**. Then
the left stick moves the pointer, **A** clicks (hold it to drag), **X**
right-clicks and the right stick scrolls. Turn it off the same way.

## Settings

- **Audio output** and **Volume**
- **Display scale**: "Automatic" is right for most TVs. If the picture or the
  text looks too small or too large, try another size. If the edges of the
  picture are cut off, switch the TV to "Just scan" / "Screen fit" (in the
  TV's own picture settings).
- **Pair a phone**: see "Phone remote"
- **Wi-Fi**: choose the network, type the password with the keyboard, press
  **Start**. A cable works without any setting.
- **Bluetooth**: put the controller or headphones in pairing mode (Xbox
  controller: hold the small pairing button on top until the Xbox logo
  flashes fast), choose **Search for devices**, then the device. Headphones
  then appear under **Audio output**.
- **Updates**: see below
- **Snapshots**: backups of the system, see below
- **Restart session**, **Reboot**, **About** (version, the box's name and
  address)

## Updates

Updates are never installed by themselves. Settings → **Updates** →
**Check for updates** shows what would change. **Install** installs it; a
backup (snapshot) is taken first. When it says a restart is needed, choose
**Reboot now**.

### If the box doesn't start properly after an update

You don't need to do anything. If the box fails to start properly twice in a
row, it starts the backup from before the update by itself, and the TV shows
**Started from a backup** with three choices:

- **Keep this backup**: the update is undone for good.
- **Try the updated system again**: restarts with the update.
- **Decide later**: keep using the backup for now. The next restart tries
  the updated system again (and falls back again if it still fails).

You can also go back on your own: Settings → **Snapshots**, choose the
snapshot ("before the last update" is usually the one), then **Start it
once** (just for now) or **Roll back to it** (for good).

## Phone remote

Settings → **Pair a phone** shows a QR code. Scan it with the phone's camera
and open the link. The phone is now a remote, as long as it is on the same
network as the box (it opens at `http://tv.local:8080`). It has:

- D-pad and buttons (hold Home for the system menu)
- a text field that types into the TV (much faster than the on-screen
  keyboard)
- a touchpad (drag to move the pointer, tap to click, long tap for a right
  click, two fingers to scroll)
- apps, settings, updates and snapshots
- **Health**: temperature, free space, which parts are running, recent
  restarts
- **Bindings**: change what the controller buttons do

Paired phones are listed under Settings → Pair a phone, where they can be
removed.

## When something goes wrong

| Problem | Try |
|---|---|
| An app shows a blank or frozen screen | YouTube, Netflix, Disney+ and Floatplane are restarted by themselves within about half a minute. Any app: System menu → **Restart app**. |
| The controller does nothing | Press the Xbox button to wake it. If it was paired by Bluetooth, re-pair it (Settings → Bluetooth), or plug it in with a USB cable. |
| No sound | System menu → **Audio output**, choose the TV or soundbar. Check **Mute**. |
| The picture is black after switching the TV back on | Wait a few seconds; if it stays black, hold the Xbox button and choose **Restart session**. |
| Everything is stuck | Hold the Xbox button → **Reboot**. If the menu doesn't come up, unplug the box for ten seconds. |
| A service changed its website and navigation broke | Use **Mouse mode** until an update fixes it. |

## For whoever maintains the box

- SSH: `ssh root@tv.local` with the key given at installation.
- Logs: `journalctl -b` (system), `journalctl --user -M tv@` is not available
  as root; use `sudo -u tv XDG_RUNTIME_DIR=/run/user/1000 journalctl --user`.
- Status of the input layer: `sudo -u tv XDG_RUNTIME_DIR=/run/user/1000 tvbox-ctl status`.
- Configuration overrides (all optional, applied on save):
  `~tv/.config/tvbox/bindings.toml` (buttons), `~tv/.config/tvbox/services.toml`
  (tiles), `/etc/tvbox/` (the same, for the whole box). The defaults with
  comments are in `/usr/share/tvbox/`.
