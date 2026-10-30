"""Sender: idle power-off - a held stick is use, a USB pad is never powered off.
    python3 tests/test_idle_power_off_tx.py sender"""
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])

import sender_control as ctl
import sender_ctrl
import sender_net as net
import sender_supervisor as sup
from sender_config import C

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- 6. idle power-off: held stick is use, a USB pad is never powered off ----------
class PadDev:
    def __init__(s, bus): s.info = type("I", (), {"bustype": bus})()
def tick(c, now):
    spawned = []
    sup.state.active.clear(); sup.state.active[c.key] = c
    real = ctl.state.spawn
    ctl.state.spawn = lambda coro: (spawned.append(1), coro.close())
    try:
        ctl._info_tick({}, now)
    finally:
        ctl.state.spawn = real; sup.state.active.clear()
    return bool(spawned)
C.IDLE_OFF_S = 60.0
bt = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:03", PadDev(5), None, None)
usb = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:04", PadDev(3), None, None)
check(bt.is_bluetooth() and not usb.is_bluetooth(), "is_bluetooth: bus type decides")
bt.last_input = 0.0; usb.last_input = 0.0
check(tick(bt, 100.0), "idle: a Bluetooth pad without input is powered off")
check(not tick(usb, 100.0), "idle: a USB pad is left alone")
held = sender_ctrl.Ctrl(2, "aa:bb:cc:dd:ee:05", PadDev(5), None, None)
held.last_input = 0.0; held.mask = 1
check(not tick(held, 100.0) and held.last_input == 100.0, "idle: a button held without events counts as use")

sys.exit(1 if fails else 0)
