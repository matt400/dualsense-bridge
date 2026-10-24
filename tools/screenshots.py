"""Screenshots of the receiver window for the README / docs - no Windows and no pads (the stubs from tests/rxenv.py, Qt offscreen).

    python3 tools/screenshots.py            # -> docs/img/*.png
    python3 tools/screenshots.py OUT_DIR

Requirements as for the GUI tests: python3 >= 3.11, pip install PySide6-Essentials.
The data is fake: two pads, a realistic battery, ping and latencies, the sender online.
"""
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIANGLE_FROM = 140               # frame from which the triangle button is held in the animation
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "img").resolve()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PATH"] = f"{ROOT / 'tests' / 'fakebin'}{os.pathsep}{os.environ['PATH']}"   # bez prawdziwego bluetoothctl
sys.path.insert(0, str(ROOT / "tests"))
sys.argv[1:] = [str(ROOT / "receiver")]                          # rxenv bierze katalog receivera z argv[1]

from rxenv import BIT, C, N, S, TMP, F, feed, state, store      # noqa: E402

import rx_i18n                                                    # noqa: E402
rx_i18n.set_lang("en")
C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")

import rx_audio, rx_bt as RB, rx_console, rx_drift as D, rx_latency, rx_master, rx_qt   # noqa: E402,E401
from proto import (BTD_CONNECTED, BTD_PAD, BTD_PAIRED, BTD_TRUSTED, BTS_OK, CAP_CALIB,   # noqa: E402
                   RUMBLE_HIDRAW, T_BTDEV, T_BTSTATE, T_IDENT, T_TOUCH, mac_bytes)
from rx_console import Console, ConsoleConfig                     # noqa: E402
from PySide6.QtTest import QTest                                  # noqa: E402
from PySide6.QtWidgets import QWidget                               # noqa: E402

BTF_LIST = 4                                                      # sender_bt.BTF_LIST: a device list round
PADS = "a0:5a:5c:8e:12:41", "a0:5a:5c:3b:77:c2"
SIZE = 1180, 760
pump = lambda ms=60: QTest.qWait(ms)                              # noqa: E731

rx_audio.list_outputs = lambda: [("1", "Speakers"), ("2", "TV (HDMI)")]
rx_console.ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None


def save(widget: QWidget, name: str) -> None:
    """Grab the widget and write it to OUT/<name>.png."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    widget.grab().save(str(path), "PNG", 0)                        # 0 = najmocniejsza kompresja (bezstratnie)
    print(f"saved {path} ({path.stat().st_size // 1024} KB)")


def link(i: int, batt: int, ping: int) -> None:
    """A 'connected' pad: the sender's state, battery, ping, packets/s and fresh pad -> game latency samples."""
    state(i, batt=batt)
    now = time.monotonic()
    S.alive[i] = True
    S.batt_info[i] = (batt, 0)
    S.info[i] = (ping, 250, 250, now)
    S.pps[i] = 250.0
    S.sender_info[i] = (9, RUMBLE_HIDRAW | CAP_CALIB, 0)
    t = time.perf_counter_ns()
    # lat = (arrival on the PC ns, sender µs, Bluetooth µs, network µs); PC = done - arrival. A little spread for p95 / max.
    for k in range(60):
        j = k * 37 % 11
        rx_latency.add(i, (t - 160_000 - j * 4_000, 300 + j * 25, 350 + j * 60, 230 + j * 9), t)
    for k in range(25):
        j = k * 53 % 7
        rx_latency.add_button(i, (t - 170_000, 320 + j * 30, 420 + j * 70, 240 + j * 10), t)


def online() -> None:
    """Mark the sender as online (fresh packet, no loss), a phone sender with a battery that is charging."""
    S.last_pkt, S.link_loss, S.peer = time.monotonic(), 0.0, ("192.168.1.50", 5005)
    S.sender_batt = (78, True)


# ---------- dwa pady, pad 1 master, profil z remapem i efektem spustu ----------
for i, mac in enumerate(PADS):
    feed(T_IDENT, i, mac.encode())
rx_master.set_master(0)
base = S.profile_key(0)
store.save(base, {"buttons": {"cross": "circle", "circle": "cross"},
                  "triggers": {"r2": {"mode": "click", "pos": 5, "force": 6}}})
S.reload_profile(0)
online()
link(0, 78, 1.1)
link(1, 41, 1.3)

win = rx_qt.create_window(os.path.join(TMP, "ui.ini"))
win.resize(*SIZE)
win.show()
pump(200)


def tick() -> None:
    """Refresh the fake link of both pads."""
    online()
    for i, b in ((0, 78), (1, 41)):
        S.alive[i] = True
        S.batt_info[i] = (b, 0)
    win.slow_tick()
    win.refresh_all()
    pump()


# ---------- 1. the main window: pads, battery, latency, the right panel's remap ----------
tick()
win.on_part("cross")
tick()
save(win, "main")

# ---------- 2. remap: spust R2 z efektem adaptacyjnym ----------
win.on_part("r2")
tick()
save(win, "remap_trigger")

# ---------- 3. widok Test: wykresy ruchu, touchpad, dryf ----------
win.seg.buttons["test"].click()
pump()
panel = win.test_panel
panel.start_drift()                                               # dryf: pomiar w spoczynku -> propozycja strefy
t0 = panel.run.t0
panel._drift_tick(t0 + D.SETTLE_S)
for lx, ry in ((130, 128), (127, 128), (131, 129), (129, 128)):
    state(0, sticks=(lx, 128, 128, ry), batt=78)
panel._drift_tick(t0 + D.SETTLE_S + D.MEASURE_S)
panel.refresh()
for k in range(160):                                              # then a few seconds of live pad movement
    a = k / 9
    gyro = (round(2600 * math.sin(a)), round(-1700 * math.sin(a * 0.7 + 1)), round(900 * math.cos(a * 1.3)))
    state(0, mask=BIT[304] if k > TRIANGLE_FROM else 0, lt=min(255, k * 2), rt=round(127 + 127 * math.sin(a / 2)),
          sticks=(128 + round(60 * math.sin(a)), 128 + round(40 * math.cos(a)), 130, 127), gyro=gyro, batt=78)
    win.live_tick()
feed(T_TOUCH, 0, 1, 1, 1180, 520, 0, 0, 0, 0)
win.live_tick()
tick()
save(win, "test_view")
win.seg.buttons["out"].click()
pump()

# ---------- 4. Bluetooth nadajnika: wysuwany panel ----------
devs = [(PADS[0], "DualSense Wireless Controller", -52, BTD_PAIRED | BTD_CONNECTED | BTD_TRUSTED | BTD_PAD),
        (PADS[1], "DualSense Wireless Controller", -61, BTD_PAIRED | BTD_CONNECTED | BTD_TRUSTED | BTD_PAD),
        ("e4:17:d8:90:3a:15", "DualSense Wireless Controller", -74, BTD_PAD),
        ("38:18:4c:2f:61:07", "WH-1000XM4", -88, BTD_PAIRED)]
S.last_pkt = time.monotonic()
RB.on_packet(T_BTSTATE, N.codec.pack(T_BTSTATE, 255, BTS_OK, BTF_LIST, 0, 2, len(devs), 1, 0, 0, bytes(6)))
for k, (m, name, rssi, f) in enumerate(devs):
    RB.on_packet(T_BTDEV, N.codec.pack(T_BTDEV, 255, 1, k, len(devs), mac_bytes(m), rssi, f, name.encode()))
win.bt_btn.click()
pump(400)
win.bt_drawer.refresh()
tick()
save(win, "bt_drawer")
win.bt_drawer.close_drawer()
pump(400)

# ---------- 5. ustawienia: tryb konsoli i master ----------
win.open_settings()
pump(150)
dlg = win.settings_dlg
for tab, name in ((0, "settings_console"), (2, "settings_master")):
    dlg.tabs.setCurrentIndex(tab)
    pump()
    save(dlg, name)
