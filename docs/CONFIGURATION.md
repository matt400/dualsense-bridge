# Configuration

**English** | [Polski](KONFIGURACJA.pl.md)

All settings with their defaults and descriptions are in `sender/sender.example.toml` and
`receiver/receiver.example.toml`. Copy the file without `.example` and uncomment the lines you need. The receiver
reads `receiver.toml` next to `receiver_controller.py` by itself; the sender needs `--config sender.toml` (the
service installer adds it). Another file: `--config other.toml`. `*.toml` files are in `.gitignore` because they may contain the key.

When defaults change, the sender fixes lines with **old default values** in `sender.toml` by itself at startup
(e.g. `pad_min_dt = 0.004` -> `0.001`); your own settings, comments and file layout stay, a backup goes to
`sender.toml.bak`. Manually: `python3 sender/sender_migrate.py sender/sender.toml`.

Most often changed:

| Setting | Where | Default | What it does |
|---|---|---|---|
| `host` | sender | 192.168.1.4 (example - set your own) | IP of the PC running the receiver |
| `pad_min_dt` | sender | 0.001 | min. interval between state packets (1 ms = 1000 Hz) |
| `idle_off_min` | sender | 15 | turn the pad off after this many minutes of inactivity, 0 = never |
| `low_latency` | sender | true | sender CPU without deep sleep + fastest cores (requires root) |
| `cpu_latency_us` | sender | 50 | `low_latency`: max. CPU wake-up time in µs |
| `rt_priority` | sender | 10 | SCHED_FIFO with this priority, `0` = normal scheduler (requires root; also works on musl - postmarketOS, Alpine) |
| `rumble` | sender | auto | rumble: `auto` (driver and HID report at once), `evdev`, `hidraw`, `off` |
| `key` | both | "" | shared HMAC key: signed packets and replay protection ([SECURITY.md](SECURITY.md)) |
| `dsu_bind` | receiver | 127.0.0.1 | DSU server address; `0.0.0.0` = emulator on another device |
| `hm_min_age_days` | receiver | 7 | a new HIDMaestro release waits this many days after publishing; `0` = immediately |
| `mouse_sens`, `scroll_sens` | receiver | 0.5, 0.6 | touchpad mouse and scroll sensitivity |
| `ps_modifier` | receiver | true | PS as the shortcut key (PS goes to the game on release) |
| `ps_hold_action` | receiver | gamebar | long PS: `gamebar`, `bigpicture`, `none` |
| `game_profiles` | receiver | true | automatic profile based on the active window |

Remap profiles are saved in `receiver/remap.json`, tied to the pad (MAC), not to the slot.

Console mode has its own file `receiver/console.json` (next to the script). A missing file or an invalid value =
defaults (the file is created and fixed, a corrupted one is kept as `console.json.bad`). Easiest to edit in the window
(Settings), but manual editing works too:

| Key | Default | What it does |
|---|---|---|
| `toggle_button` | `r2` | pad button: `r2`, `l2`, `ps`, `share`, `options`, `cross`, `circle`, `triangle`, `square`, `l1`, `r1`, `l3`, `r3`, `dpad_*` |
| `double_click_ms` | 400 | maximum interval between the two presses (100-2000) |
| `audio_tv` | "" | TV audio device name (as listed in Settings; empty = sound unchanged) |
| `audio_pc` | "" | PC audio device name |
| `timeout_s` | 60 | no packets from the pad for this many seconds = automatic OFF (5-3600) |
| `startup_mode` | `extend` | display at receiver start: `extend` or `internal` |
| `auto_on_master` | true | connecting the master pad turns console mode on |
| `allowed_apps` | desktop, Steam, launchers | mode switching only with these programs in the foreground; empty list = no restriction |
| `tv_width`, `tv_height` | 2560, 1440 | TV resolution after turning the mode on (highest refresh rate for it; 0 = unchanged) |
| `tv_scale` | 200 | TV scaling in %: 100, 125, 150, 175, 200, 225, 250, 300... (0 = unchanged) |

Touchpad mouse, master pad, lightbar, Wake-on-LAN, event log settings and virtual pads are in `receiver/settings.json`
(window → Settings, takes effect immediately). Virtual pads (`vpad`): `backend` = `auto` | `hidmaestro` | `vigem`,
`fixed_slots` = fixed player order (placeholder pad in an empty lower slot), `hm_auto` = download and update
HIDMaestro from GitHub (once a day, to `receiver/hidmaestro/vX.Y.Z/`; a new version takes effect after a restart when
an older one is already in use), `hm_dll` = a custom `HIDMaestro.Core.dll` or its folder (then no download). Window preferences (`ui`): `lang` = `pl` | `en`, `pads_mode` = `dynamic` (the left column shows
only connected pads) | `fixed`, `pads_count` = how many pads `fixed` always shows (1-4). The number of slots is `pads` in
`receiver.toml` (4 by default, max 4). The
sender stores the Wake-on-LAN settings from the window in `sender/wol.json`.
The sender stores the fixed pad → slot assignment in `sender/slots.json` (`slots_file = ""` disables it).
Gyro calibration from the window (Test view) is in `sender/gyro_cal.json` (`gyro_cal_file = ""` - not saved),
stick drift correction and the accessibility mode in `receiver/padcal.json` (next to `remap.json`, included in settings backups on update).

## Updating

The **Update** button in the window (or in the tray icon menu) updates **both sides**:
- receiver: `git pull` in the `dualsense-bridge` folder, plus `pip install` when `requirements.txt` changed;
- sender: gets a command, runs `git pull` on the sender (as the folder owner, also from the root service)
  and restarts itself;
- the window waits for the sender's result (up to 60 s), shows both results and restarts the receiver.

While it runs, the button shows a turning ring and the stage as a percent (download, settings backup, libraries, sender).

The version (commit and date) is in the window header next to the name, and the sender's version next to "Sender
online". A bar with a notice appears below the header (the **Update** button is in the side menu) when:
- a newer version is on GitHub - "Update available: 3 changes - title of the latest" (the window checks this in the
  background 20 s after start and every 30 min, without downloading code and without a login prompt; also a one-time
  tray notification);
- the sender has a different version than the receiver - "The sender has a different version (…) than the receiver
  (…)". This requires git clones on both sides (`git clone https://github.com/matt400/dualsense-bridge`, then on the
  sender `install-service.sh` from that folder) and Git for Windows.
Settings and profiles (`console.json`, `settings.json`, `remap.json`, `sender.toml`, `slots.json`) are left untouched,
and after downloading a new version the receiver still makes a backup of them (Settings → Backups) - in case the new
version changes something in them at startup.

A clone from the public repository's https address updates without logging in. A key is only needed for a private
fork or an SSH clone: the sender service runs as root and doesn't have your SSH key, so `git pull` ends with
`Could not read from remote repository`. In that case, once, on the sender, as a regular user (without `sudo` / `doas`):

```
cd dualsense-bridge/sender
./setup-update-key.sh      # creates a read-only key in .git/ and prints what to paste on GitHub
./setup-update-key.sh      # after adding the key: checks access (prints "OK - nadajnik ma dostęp" = the sender has access)
```

The reason a sender update failed is shown in the window on the PC (e.g. `Permission denied (publickey)`).
Without the window: `update.bat` in the main folder (receiver only).
