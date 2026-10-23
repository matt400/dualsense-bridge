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
tests/     Linux tests with Windows/evdev stubs; fakebin/ = fake bluetoothctl
docs/      EN by default, PL as *.pl.md
tools/     screenshots.py (offscreen window screenshots -> docs/img)
```

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
- Lint: `ruff` per `pyproject.toml` (line length 140, py311).

## Tests
```
pip install evdev ruff PySide6-Essentials
./tests/run_tests.sh          # from the repo root; ruff + all tests, GUI in Qt offscreen
```
- Tests are plain scripts (`python3 tests/test_x.py receiver|sender`), no pytest.
  `*_tx.py` = sender, the rest = receiver, `test_e2e*` / `test_bt` = both.
- Add a new test file to the matching list in `tests/run_tests.sh`, otherwise it won't run in CI.
- Regressions from bug reviews: `tests/test_hunt<N>.py` / `test_hunt<N>_tx.py`.
- Manual only (can't be done in CI): a real pad, ViGEm, HIDMaestro, DisplaySwitch, pycaw, notifications.

## Running
- Receiver: `cd receiver && pip install -r requirements.txt && python receiver_controller.py` (elevates itself to admin; `--no-elevate`).
- Sender: `cd sender && cp sender.example.toml sender.toml` (set `host`), `sudo ./install-service.sh`;
  log: `journalctl -u dualsense-sender -f`.
- After UI changes: run `python3 tools/screenshots.py` and commit the PNGs in `docs/img`.

## Git
- Branches: `fix/…`, `feat/…`, `docs/…`, `security/…`; changes go through a PR merged with a merge commit into `main`.
- Commits and PR descriptions are in English: a short summary with an area prefix (`Receiver: …`, `Sender BT: …`, `Bug hunt N: …`, `docs: …`).
- Documentation: a change in EN (`docs/*.md`) needs a matching change in PL (`docs/*.pl.md`) and vice versa.
