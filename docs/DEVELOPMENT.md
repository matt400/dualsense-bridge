# Development

**English** | [Polski](ROZWOJ.pl.md)

## Structure

```
receiver/   Windows: receiver_controller.py (entry), rx_*.py (modules), rx_qt*.py (window)
sender/     Linux:   sender_controller.py (entry), sender_*.py (modules)
tests/      tests on Linux with Windows and evdev device stubs
```

`proto.py` (packet format) must be identical in `receiver/` and `sender/`. The tests check this.

## Screenshots

```
pip install PySide6-Essentials
python3 tools/screenshots.py          # -> docs/img/*.png (or: python3 tools/screenshots.py OUT_DIR)
```

Renders the receiver window offscreen (Qt, no display, no Windows) with the test stubs and mock data: two pads,
battery, latency, a remapped profile, the Test view, the Bluetooth drawer and two settings tabs. English UI. Run it
after UI changes and commit the updated PNGs.

## Tests

```
pip install evdev ruff mypy PySide6-Essentials
./tests/run_tests.sh
```

`ruff` and `mypy` (rules in `pyproject.toml`) check all code before the tests; `mypy` runs once per package (the receiver
as on Windows, the sender as on Linux) because the modules import each other by flat name. GitHub Actions runs the same on
every push and PR (`.github/workflows/tests.yml`).

The tests cover:
- logic on both sides: touchpad, gestures, limiter, buffer overflow, shortcuts, profiles, gyro, stick shaping;
- end-to-end communication over UDP;
- starting real processes and a clean shutdown on restart from settings (UDP ports, virtual DS4, instance lock);
- console mode with stubbed display, audio and notification commands (config, double click, auto-OFF, errors);
- master pad (permissions, PS + button actions, takeover), console mode auto-ON, R2 with hysteresis,
  touchpad click stabilisation, `slots.json`;
- the receiver window (Qt offscreen, no display);
- settings backups, latency measurement (state2 / pong2, PC clock, Bluetooth) and trigger resistance in the HID report
  (`test_features.py`, `test_features_tx.py`);
- trigger effects: click, vibration, game rumble, trig2 / trig depending on sender version, inheritance in game
  profiles, master only (`test_trigfx.py`, `test_trigfx_tx.py`); trigger effects from games (HIDMaestro, trig3):
  reading from the output report, the profile wins per trigger, release after the program closes (`test_gametrig.py`,
  `test_gametrig_tx.py`);
- regressions from bug reviews (`test_review.py`, `test_review_tx.py`, and the topic-named tests below) - among others starting /
  stopping a Bluetooth scan at the same time, a finger resting on the touchpad when connecting, a touchpad click
  released in the Test view, PS shortcuts in TV mode, registering game rumble in ViGEm (callback signature from
  vgamepad, lightbar colour as a struct), receiver without ViGEmBus, COM audio objects released before
  CoUninitialize; shortcuts, master actions and the console mode button at the edge of the Test view computed from
  held inputs, sender Bluetooth without busctl / permissions, autobias after calibration from the window
  (`test_input_state_boundaries.py`, `test_bt_window_errors_tx.py`); a new DSU client gets the state immediately, atomic settings file writes
  with fsync - `proto.write_file` / `write_json` (`test_atomic_writes.py`, `test_atomic_writes_tx.py`); HIDMaestro downloaded with
  a single Range request per file (a 200 response = error), starting as administrator without a UAC prompt for a
  second instance and without a loop for a read-only folder, the administrator receiver's lock seen from an
  unelevated process, autostart tasks with custom arguments (`test_hm_download_elevation.py`);
  without a master, a restart with console mode off doesn't turn it on via the pad identifier (`test_console.py`),
  the window returns to the centre after leaving a game that changed the resolution (`test_settings_gui.py`),
  Bluetooth commands as the same packet repeated (`test_bt.py`);
  virtual DualSense battery on a 0-10 scale (a full pad without a cable is 100 %, not 85 %) (`test_vpad.py`),
  Bluetooth controller selection robust to an rfkill error halfway through - the selected one unblocked before
  blocking the rest, a failed change retried (`test_btadapter_tx.py`);
  with several Bluetooth controllers (built-in blocked + dongle) a scan from the window on the selected controller
  (`select` in bluetoothctl, timeout on our side) and turning a pad off over D-Bus on the controller it is connected
  to (`test_bt_multi_adapter_tx.py`);
  Bluetooth controller selection and link policy without SNIFF also without the receiver (PC off, Wake-on-LAN), SNIFF
  fixed after every controller power-on - `hci_dev_info` layout as in the kernel (`test_bt_link_policy_tx.py`); window
  raised only on a new pad connection, not after a brief signal drop (`test_settings_gui.py`); a HIDMaestro update
  while running doesn't delete the folder of the version loaded in the process (DLL dependencies load lazily) - it is
  removed only after a restart (`test_hm_update.py`);
- button → game, event log (also sender events end-to-end), battery-based lightbar, settings for the sender
  (`test_extras.py`, `test_extras_gui.py`) and Wake-on-LAN on the sender side - combo, ARP, broadcast, magic
  packet, `wol.json` (`test_wol_tx.py`);
- Test view: drift measurement and suggested deadzone, drift correction in all pad profiles, blocking output to the
  game (DS4, DSU, mouse, shortcuts), gyro calibration from the window and saving it on the sender, sticks without the
  sender's deadzone during measurement, disabled shortcuts and the Options + Create exit (`test_padtest.py`,
  `test_padtest_gui.py`, `test_calib_tx.py`);
- sender Bluetooth from the window: protocol, BlueZ via a substituted `busctl`, scanning on a real process with a
  timeout, adapter / permission / program errors, list in rounds, everything over UDP (`test_bt.py`) and the panel in
  the window with pad navigation (`test_bt_gui.py`);
- the pad shortcuts panel (`test_keys_gui.py`) and autostart via Task Scheduler with migration of the Run entry
  (`test_autostart.py`);
- performance and reliability: SCHED_FIFO on musl via syscall (`test_rt_tx.py`), pad detection via inotify on
  `/dev/input` and bye / command / trigger repeats as the same packet spread over time (`test_devwatch_tx.py`,
  `test_rx2.py`, `test_e2e.py`), reading only the needed bytes of the HIDMaestro output report (`test_vpad.py`),
  kernel receive timeout (SO_RCVTIMEO) instead of select() before every packet (`test_rx2.py`);
- virtual pads: HIDMaestro / ViGEmBus backend and switching between them at runtime, placeholder pad for fixed
  player order, DualSense report, feedback from the DualSense output report (`test_vpad.py`, the Pads tab in
  `test_settings_gui.py`), downloading HIDMaestro via HTTP Range, versions, zip / network errors and running as
  administrator (`test_hm_update.py`);
- security: protocol v3 with a key - switching over by itself (trial ping), freshness token, exact replays, signed v2
  after the switch, remembered in `auth.json`, the sender's PC clock after a receiver restart, a replayed ping can't
  move the sender clock back, end to end over UDP including a recorded packet sent again (`test_security_tx.py`,
  `test_security.py`, `test_e2e_key.py`); DSU only on 127.0.0.1 with a client limit, a new HIDMaestro release waits,
  the Security list, plain text in window labels (`test_security.py`); pairing from the window only for pads, the
  adapter not left pairable, trust only for pads (`test_bt.py`); the key in `/etc/dualsense-sender.env` instead of
  `ExecStart`, systemd restrictions (`test_service_tx.py`).

Behaviour with a real pad, ViGEm, HIDMaestro (pythonnet, driver), `DisplaySwitch.exe`, audio switching (pycaw) and
Windows notifications has to be checked manually.
