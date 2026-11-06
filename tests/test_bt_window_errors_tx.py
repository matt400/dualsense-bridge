"""Sender: regressions from the ninth review - Bluetooth from the window survives every busctl /
bluetoothctl start error (an operation does not stay "in progress" forever) and the gyro autobias does not spoil a fresh calibration from the window.
    python3 tests/test_bt_window_errors_tx.py sender"""
import asyncio, sys, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
C.GYRO_CAL_FILE = ""
from evdev import ecodes as ec
import sender_bt as SB
import sender_calib as calib
import sender_ctrl
import sender_streams
from proto import (BT_CONNECT, BT_SCAN_START, BTR_BUSY, BTR_DENIED, BTR_FAILED, BTR_RUNNING, BTS_DENIED, BTS_ERROR,
                   BTS_OK)

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

MAC = bytes(range(1, 7))


# ---------- busctl cannot be started (no exec permission, fork out of memory) ----------
def failing(exc):
    async def run(argv, timeout):
        raise exc
    return run

async def bt_ops():
    b = SB.Bluetooth(run=failing(PermissionError(13, "Permission denied")), emit=lambda *a: None)
    await b.refresh()
    check(b.state == BTS_DENIED, "refresh: PermissionError -> state 'no permission' instead of an exception in the loop")
    b.start_op(BT_CONNECT, 0, MAC)
    await b.busy
    check(not b.running, "an operation after a busctl error does not stay 'in progress' (running = True forever before)")
    check(b.last[1] == BTR_DENIED, f"operation result: no permission ({b.last[1]})")
    b.start_op(BT_CONNECT, 0, MAC)
    check(b.last[1] == BTR_RUNNING, "the next operation starts (not BTR_BUSY)")
    await b.busy

    b = SB.Bluetooth(run=failing(OSError(11, "Resource temporarily unavailable")), emit=lambda *a: None)
    await b.refresh()
    check(b.state == BTS_ERROR, "refresh: another OSError -> state 'error', no exception")
    b.start_op(BT_CONNECT, 0, MAC)
    await b.busy
    check(not b.running and b.last[1] == BTR_FAILED, "another OSError: operation ends with 'failed'")
    check(BTR_BUSY != b.last[1], "no false 'another operation in progress'")

    # scan: bluetoothctl cannot be started -> a result for the window instead of an exception in the task
    b = SB.Bluetooth(run=failing(OSError()), emit=lambda *a: None)

    async def ok_refresh():
        b.state, b.adapter = BTS_OK, "/org/bluez/hci0"
    b.refresh = ok_refresh
    orig = SB.asyncio.create_subprocess_exec

    async def no_exec(*a, **k):
        raise PermissionError(13, "Permission denied")
    SB.asyncio.create_subprocess_exec = no_exec
    try:
        await b.start_scan(5)
        err = None
    except Exception as e:
        err = e
    finally:
        SB.asyncio.create_subprocess_exec = orig
    check(err is None and b.last == (BT_SCAN_START, BTR_DENIED, b""), f"scan without permission: result in the window ({err!r}, {b.last})")
    check(b.scan is None and not b.scanning(), "the scan is not reported as running")

asyncio.run(bt_ops())


# ---------- autobias after a calibration from the window does not take samples from before it ----------
sender_ctrl.net.emit = calib.net.emit = lambda *a: None
C.CAL_S = 0
clock = [1000.0]
real_time = sender_streams.time
sender_streams.time = types.SimpleNamespace(monotonic=lambda: clock[0])
E = lambda t, c, v: types.SimpleNamespace(type=t, code=c, value=v)
c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:19", None, None, None)
c.request_send = lambda: None
c.mark_event = lambda e: None
seen = {}

class Motion:
    async def async_read_loop(self):
        def frame(g):
            return [E(ec.EV_ABS, 3, g), E(ec.EV_ABS, 4, 0), E(ec.EV_ABS, 5, 0), E(ec.EV_SYN, ec.SYN_REPORT, 0)]
        for k in range(150):                    # 0.6 s in the hand: gyro 500-700 (tremor within autobias_range)
            clock[0] += 0.004
            for e in frame(500 + (k % 5) * 50):
                yield e
        c.cal_request = 3                       # calibration from the window: pad put down, real bias = 100
        for _ in range(int(3.2 / 0.004)):
            clock[0] += 0.004
            for e in frame(100):
                yield e
        seen["after"] = c.gyro_bias[0]
        for _ in range(int(1.5 / 0.004)):       # pad still lying - autobias only confirms the bias
            clock[0] += 0.004
            for e in frame(100):
                yield e
        seen["later"] = c.gyro_bias[0]

c.motion_dev = Motion()
asyncio.run(sender_streams.stream_motion(c))
sender_streams.time = real_time
check(abs(seen["after"] - 100) < 1, f"right after calibration bias = measured (was 348 - samples from before the calibration): {seen['after']:.0f}")
check(abs(seen["later"] - 100) < 1, f"after the next autobias window the bias is unchanged: {seen['later']:.0f}")

if fails:
    print("FAIL", len(fails), "failures")
    sys.exit(1)
print("ALL OK")
