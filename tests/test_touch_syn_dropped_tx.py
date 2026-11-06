"""Sender: a tap right after SYN_DROPPED still clicks.
    python3 tests/test_touch_syn_dropped_tx.py sender"""
import asyncio
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from evdev import ecodes as ec
from fakedev import SYN, Ev, frame
from fakedev import Dev as TouchDev

import sender_ctrl
import sender_net as net
import sender_streams as st
from proto import T_MOUSE

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- a tap right after SYN_DROPPED still clicks ----------
def taps(evs):
    out.clear()
    async def go():
        c = sender_ctrl.Ctrl(0, "aa", None, None, TouchDev(evs)); await st.stream_touch(c); await asyncio.sleep(0.03)
    asyncio.run(go())
    return [v for t, v in out if t == T_MOUSE and v[2] & 1]
tap = (frame(0.02, (ec.ABS_MT_SLOT, 0), (ec.ABS_MT_TRACKING_ID, 1), (ec.ABS_MT_POSITION_X, 500), (ec.ABS_MT_POSITION_Y, 400))
       + frame(0.05, (ec.ABS_MT_TRACKING_ID, -1)))
drop = [(0, Ev(ec.EV_SYN, ec.SYN_DROPPED, 0)), (0, SYN)]
check(bool(taps(tap)), "touch: a plain tap clicks")
check(bool(taps(drop + tap)), "touch: a tap after SYN_DROPPED clicks too")

sys.exit(1 if fails else 0)
