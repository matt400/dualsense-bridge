# Security

**English** | [Polski](BEZPIECZENSTWO.pl.md)

The receiver runs **as administrator** on the PC, the sender runs **as root**, and they talk over UDP on the local
network. Without a key anyone on that network can send them packets. At home on your own network that may be fine;
on a shared network (flatmates, guests' Wi-Fi, untrusted smart devices) set a key.

## What a key protects

Without `key`, anyone on the local network can:
- move the mouse and click on the PC (touchpad as a mouse) and trigger master pad actions (sleep, shutdown),
- send commands to the sender: update and restart, pair Bluetooth devices, turn pads off.

With the same `key` on both sides every packet is signed (HMAC-SHA256). Both programs then switch to **protocol v3**
by themselves: each packet also carries a freshness token, so a recorded packet sent again later is rejected (replay
protection). The switch is remembered in `auth.json` next to each program; changing the key starts it over.

## Setting a key

1. Generate one: Settings → Security → **Copy a new key**, or
   `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
2. Receiver: `key = "..."` in `receiver.toml` (or the `PADKEY` environment variable).
3. Sender: `key = "..."` in `sender.toml`, or `sudo ./install-service.sh <PC IP> --key "..."` - the installer puts the
   key into `/etc/dualsense-sender.env` (mode 0600), not into the service command line, which every user can see in
   `ps`.
4. Restart both. The window warns about a mismatched key in the event log.

After going back to a version without protocol v3 on one side, delete `auth.json` on the other side.

## Other settings

| Setting | Where | Default | Why |
|---|---|---|---|
| `allow` | receiver | "" | accept packets only from this IP (the sender) |
| `dsu_bind` | receiver | `127.0.0.1` | the DSU server (gyro for emulators) only for this PC; `0.0.0.0` = an emulator on another device, but then anyone on the network sees the pads' motion and buttons |
| `dsu_max_clients` | receiver | 4 | at most this many DSU subscribers (each gets a stream of up to `dsu_hz` packets/s) |
| `hm_min_age_days` | receiver | 7 | unused while HIDMaestro is pinned (the default: a fixed version and link in `PINNED_TAG` of `rx_hm_update.py`, the SHA-256 of every file taken from the zip is checked, a newer release never installs on its own, and moving the pin is a reviewed commit). Without a pin a new release is installed only this many days after it is published (it has no signature, and it runs in an administrator process); the first install doesn't wait; `0` = immediately; **Settings → Pads → Install now** skips the wait for the release that is waiting, once |

## Files and permissions

- **Receiver folder.** The receiver (and the autostart task with highest privileges) runs code from its folder as
  administrator. If the folder belongs to your regular account, any program you run can change that code. Settings →
  Security → **Secure folder** gives the folder to Administrators (read-only for regular users); the receiver still
  saves settings and updates itself. When the receiver runs from a git checkout this covers the whole repository
  including `.git`: the updater runs git as administrator there, so a `.git` you can write to (hooks, config) would
  run code as administrator at the next update check. Python installed "for this user only" has the same problem - install it for all
  users.
- **Sender folder.** The service runs as root, the code is in your user's folder. `sudo ./install-service.sh --own-root
  ...` gives the repository to root (the Update button keeps working; run `setup-update-key.sh` with `sudo`/`doas`
  afterwards).
- **systemd.** The service runs with `NoNewPrivileges`, a read-only file system outside the repository
  (`ProtectSystem=strict`), read-only `/home`, private `/tmp`. If something stops working on your system:
  `sudo systemctl edit dualsense-sender` and reset the offending option.

## Sender settings from the window

Changing the sender's settings (Settings → Sender) is accepted only in protocol v3 with a valid HMAC: a sender without a key, or a
packet in v2, answers with a refusal and changes nothing. Without a key the other commands stay open to the network as before,
but this one is closed. Every value is range-checked by the sender; host, port, key and file paths cannot be changed this way.
`remote_cfg_file = ""` in `sender.toml` switches the feature off.

## Bluetooth from the window

Pairing from the window works only for game controllers; only pads get the "trusted" flag (a trusted device can
connect by itself). After pairing the adapter is no longer pairable. A device can still pretend to be a pad - pair
only while you are watching the list.

## Reporting a problem

Open an issue on GitHub. For something that shouldn't be public, use GitHub's private vulnerability reporting
(Security tab → Report a vulnerability).
