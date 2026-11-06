"""Sender: losing only the motion / touchpad device leaves the receiver no stale gyro and no finger on the pad.
    python3 tests/test_lost_motion_device_tx.py sender"""
import asyncio, errno, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from evdev import ecodes as ec

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# losing only the motion / touchpad device: the receiver must not stay with the last gyro or a resting finger
import types
import sender_ctrl, sender_net, sender_state, sender_supervisor as sup
from proto import T_STATE, T_TOUCH
sender_config.C.CAL_S, sender_config.C.MOTION_MIN_DT = 0, 0
sender_net.init()
sent = []
sender_net.emit = sender_ctrl.net.emit = lambda t, i, *v: sent.append((t, v))
sup.emit_repeat = lambda t, i, *v, **k: sent.append((t, v))
E = lambda t, c, v: types.SimpleNamespace(type=t, code=c, value=v)


class LostDev:
    """A device that disappears after a few events (OSError as on disconnect)."""
    path = "/dev/input/event99"
    fd = -1
    def __init__(self, events):
        self.events = events
    def absinfo(self, c):
        raise OSError
    async def async_read_loop(self):
        for e in self.events:
            yield e
            await asyncio.sleep(0)
        raise OSError(errno.ENODEV, "No such device")
    def ungrab(self): pass
    def close(self): pass
    def grab(self): pass


async def lost():
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:01", None, None, None)
    sender_state.active[c.key] = c
    sup.attach(c, "motion", LostDev([E(ec.EV_ABS, 4, 50000), E(ec.EV_SYN, ec.SYN_REPORT, 0)] * 3))
    sup.attach(c, "touch", LostDev([E(ec.EV_ABS, ec.ABS_MT_SLOT, 0), E(ec.EV_ABS, ec.ABS_MT_TRACKING_ID, 5),
                                    E(ec.EV_ABS, ec.ABS_MT_POSITION_X, 900), E(ec.EV_ABS, ec.ABS_MT_POSITION_Y, 500),
                                    E(ec.EV_SYN, ec.SYN_REPORT, 0)]))
    await asyncio.sleep(0.2)
    states = [v for t, v in sent if t == T_STATE]
    touches = [v for t, v in sent if t == T_TOUCH]
    check(any(v[15] == 50000 for v in states), "gyro from the motion device reached the receiver")
    check(states[-1][11:17] == (0, 0, 0, 0, 0, 0), f"losing the motion device -> zero gyro sent: {states[-1][11:17]}")
    check(any(v[0] for v in touches), "a finger touch reached the receiver")
    check(touches[-1] == (0, 0, 0, 0, 0, 0, 0, 0), f"losing the touchpad -> no fingers sent: {touches[-1]}")
    sender_state.active.pop(c.key)

asyncio.run(lost())

sys.exit(1 if fails else 0)
