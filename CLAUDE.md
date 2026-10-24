# DualSense bridge – instructions for Claude

DualSense pad → Bluetooth → **sender** (Linux, evdev) → UDP 5005 → **receiver** (Windows) → virtual DualSense
(HIDMaestro) or DS4 (ViGEmBus) + DSU server (127.0.0.1:26760). Feedback (rumble, lightbar, triggers) flows back.

## Stack
- Python 3.11+ (CI: 3.12), no framework, no build step.
- Sender: `evdev`, asyncio, BlueZ (`busctl` / `bluetoothctl`), systemd; also works on musl (postmarketOS, `doas`).
- Receiver: PySide6 (window + tray), `pythonnet` + HIDMaestro (.NET 10), `vgamepad` (ViGEm), `pycaw`, `winotify`, ctypes WinAPI.

## Layout
```
sender/    sender_controller.py (entry point) + sender_*.py; install-service.sh, service/
receiver/  receiver_controller.py (entry point) + rx_*.py (logic) + rx_qt*.py (window)
tests/     Linux tests with Windows/evdev stubs (rxenv.py); fakebin/ = fake bluetoothctl; run_tests.sh = the CI entry
docs/      EN by default, PL as *.pl.md (user docs, not needed for code work)
tools/     screenshots.py (offscreen window screenshots -> docs/img)
update.bat, .github/workflows/tests.yml
```
Modules import each other by flat name (no packages): run things from inside `receiver/` or `sender/`.

## Module map
Every module has a docstring with its role; find the file here before opening anything.

**Sender** (`sender/`)
- Core: `sender_controller` (main, `forever()`), `sender_config` (DEFAULTS + CLI), `sender_supervisor` (pad discovery, slots, pad lifecycle),
  `sender_ctrl` (`Ctrl`: one pad, packets), `sender_streams` (evdev read loops, heartbeat), `sender_net` (UDP, feedback, pongs), `sender_state`.
- Feedback and features: `sender_rumble` (evdev FF / hidraw), `sender_calib` (gyro calibration per MAC), `sender_wol` (Wake-on-LAN from the pad),
  `sender_control` (PC commands, idle power-off, info), `sender_events` (event packets), `sender_batt`, `sender_update`.
- System: `sender_bt` + `sender_btadapter` (BlueZ driven from the receiver window, several adapters), `sender_rt` (low-latency mode, needs root),
  `sender_migrate` (sender.toml migration), `sender_ioctl`.

**Receiver** (`receiver/`)
- Core: `receiver_controller` (main), `rx_config` (DEFAULTS + CLI), `rx_state` (`S.xxx`, `run_forever`), `rx_net` (packet receive, events, silence),
  `rx_winapi` (timer, SendInput, single instance, UDP sockets), `rx_settings` (settings.json, changed without restart).
- Virtual pads: `rx_ds4` (pads on demand, backend choice), `rx_hm` (HIDMaestro), `rx_hm_update` (downloads HIDMaestro), `rx_report` (DS4 / DualSense
  reports), `rx_feedback` (game rumble, lightbar, triggers back to the pad), `rx_lightbar`, `rx_dsu` (Cemuhook server).
- Input processing: `rx_buttons` (tables, remap, remap.json), `rx_profiles` + `rx_editor` (profile model, no Qt), `rx_shape` (sticks / triggers),
  `rx_gyro` (gyro mouse), `rx_gstick` + `rx_gstick_io` (tilt → stick), `rx_mouse` (touchpad mouse), `rx_padcal` + `rx_drift` (drift deadzone),
  `rx_hotkeys` + `rx_shortcuts` (PS-modifier shortcuts), `rx_master` (master pad), `rx_swap` (temporary P1 <-> P2 swap), `rx_testmode` (Test view, emergency exit).
- Windows integration: `rx_console` (console mode monitor ↔ TV), `rx_display`, `rx_audio` (pycaw), `rx_games` (foreground program → profile, stop rumble),
  `rx_gamebar`, `rx_autostart` (Task Scheduler).
- Support: `rx_i18n` (PL texts in code + EN dict), `rx_status`, `rx_events` (log model), `rx_latency`, `rx_security`, `rx_backup`, `rx_update` (git pull),
  `rx_bt` (sender Bluetooth model), `rx_icon`.
- Window (PySide6): `rx_qt` (main window), `rx_qt_pad` (pad drawing), `rx_qt_panels` (right column), `rx_qt_console` (settings window), `rx_qt_test`,
  `rx_qt_bt`, `rx_qt_tv` (pad control in TV mode), `rx_qt_drawer`, `rx_qt_keys`, `rx_qt_log`, `rx_qt_gstick`, `rx_qt_screens` (screen / fullscreen placement),
  `rx_qt_style`, `rx_qt_icons`, `rx_qt_widgets`.

**Large files** – grep or read by range, never whole: `rx_i18n` (~1000, translation dicts), `proto` (~960, two identical copies), `rx_qt`, `rx_qt_console`,
`sender_rumble`, `sender_bt`, `rx_net`.

**Runtime files** (gitignored, never commit, no need to read): `*.toml` except `*.example.toml`, `remap.json`, `settings.json`, `console.json`, `slots.json`,
`wol.json`, `gyro_cal.json`, `bt.json`, `auth.json` (HMAC key), `padcal.json`, `receiver_ui.ini`, `receiver/backups/`, `receiver/hidmaestro/`.

## Hard rules
- `receiver/proto.py` and `sender/proto.py` MUST be byte-for-byte identical (a test checks this). Changing one means editing both.
- The protocol is backward compatible: new fields = a longer variant of the same packet type, sent only to a peer
  known to understand it (version / CAP_* / pong2 / scfg). An older peer drops an unknown length.
  Never change the formats of existing packets.
- Hot path (evdev read, packing, UDP receive, HID report building) – no allocations or copies, latency is what matters.
- Settings files are written only via `proto.write_file` / `proto.write_json` (atomic, fsync).
- Window texts go through `rx_i18n` (PL + EN); never hardcode UI strings.
- Windows code (ctypes, pythonnet, COM) must import on Linux using the stubs from `tests/rxenv.py`.

## Code style
- Files: `sender_<area>.py`, `rx_<area>.py`, window `rx_qt_<area>.py`; snake_case.
- Comments and docstrings are in English and explain how the code works (not just what, but how and why); a module docstring describes the module's role (and, in entry-point files, lists the modules). Texts shown in the window stay as `_("...")` keys (in Polish, translated in `rx_i18n`).
- Receiver: shared state lives in `rx_state` (`S.xxx`, `S.lock`); long-running thread loops go through `rx_state` (run_forever).
- Sender: asyncio; loops are wrapped in `forever()` (restart after an exception, never kill the process).
- Configuration: `DEFAULTS` in `sender_config` / `rx_config`, TOML/JSON file, CLI flags override the file.
- Lint and types: `ruff` and `mypy` per `pyproject.toml` (line length 140, py311). New functions get type annotations; a
  return type is required for new code (`-> None` included). Do not annotate the ViGEm notification callback in
  `rx_feedback.make_cb`: vgamepad compares its signature, annotations included.
- Docstrings follow the Google convention (ruff `D`); complexity is capped (C901 max 12, PLR0912 / PLR0915): split a long function instead of adding `noqa`.
- Per-pad state lists in `rx_state` are `None` until `init()`; they are declared with their real types on purpose.

## Tests
```
pip install evdev ruff mypy PySide6-Essentials
./tests/run_tests.sh          # from the repo root; ruff + mypy + all tests, GUI in Qt offscreen (output text is Polish)
python3 tests/test_x.py receiver|sender                          # one test (e2e / bt take both: ... receiver sender)
ruff check receiver sender tests                                 # lint only
(cd receiver && python3 -m mypy --config-file ../pyproject.toml --platform win32 .)   # types; sender: same without --platform
```
- Tests are plain scripts, no pytest. `*_tx.py` = sender, the rest = receiver, `test_e2e*` / `test_bt` = both.
- Add a new test file to the matching list in `tests/run_tests.sh`, otherwise it won't run in CI.
- Regressions from bug reviews go into topic-named tests (e.g. `test_atomic_writes.py`, `test_bt_scan_lifecycle_tx.py`), not numbered ones.
- Manual only (can't be done in CI): a real pad, ViGEm, HIDMaestro, DisplaySwitch, pycaw, notifications.

## Running
- Receiver: `cd receiver && pip install -r requirements.txt && python receiver_controller.py` (elevates itself to admin; `--no-elevate`).
- Sender: `cd sender && cp sender.example.toml sender.toml` (set `host`), `sudo ./install-service.sh`;
  log: `journalctl -u dualsense-sender -f`.
- After UI changes: run `python3 tools/screenshots.py` and commit the PNGs in `docs/img`.

## Git
- Branches: `fix/…`, `feat/…`, `docs/…`, `security/…`; changes go through a PR merged with a merge commit into `main`.
- Commits and PR descriptions are in English: a short summary with an area prefix (`Receiver: …`, `Sender BT: …`, `docs: …`).
- Documentation: a change in EN (`docs/*.md`) needs a matching change in PL (`docs/*.pl.md`) and vice versa.
