"""Sender: regressions from the seventh review - Bluetooth scan start / stop during startup (orphaned bluetoothctl),
a finger resting on the touchpad at connect, the loop error counter reset after quiet operation.
    python3 tests/test_bt_scan_lifecycle_tx.py sender"""
import asyncio, contextlib, io, json, os, stat, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
from evdev import ecodes as ec
from fakedev import SYN, Dev, Ev
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_bt as SB
import sender_ctrl, sender_net as net, sender_streams as st
from proto import BT_SCAN_START, BT_SCAN_STOP, T_MOUSE, T_TOUCH

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- skan Bluetooth: start / stop w trakcie startu ----------
BIN = tempfile.mkdtemp()
fake = os.path.join(BIN, "bluetoothctl")
with open(fake, "w") as fh:
    fh.write('#!/bin/sh\nexec sleep "$2"\n')
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
OBJECTS = json.dumps({"type": "a{oa{sa{sv}}}", "data": [{"/org/bluez/hci0": {"org.bluez.Adapter1": {
    "Address": {"type": "s", "data": "00:11:22:33:44:55"}, "Powered": {"type": "b", "data": True}}}}]})


async def slow_busctl(argv, timeout):
    await asyncio.sleep(0.15)                  # GetManagedObjects takes a while on the phone - a race window
    return 0, OBJECTS, ""


async def t_scan():
    SB.BLUETOOTHCTL = fake
    procs = []
    real = asyncio.create_subprocess_exec

    async def spawn(*a, **k):
        p = await real(*a, **k)
        procs.append(p)
        return p
    SB.asyncio.create_subprocess_exec = spawn
    try:
        b = SB.Bluetooth(run=slow_busctl, emit=lambda *a: None)
        b.on_command(BT_SCAN_START, 20, bytes(6))
        await asyncio.sleep(0.05)              # start still inside refresh() - the scan process does not exist yet
        b.on_command(BT_SCAN_STOP, 0, bytes(6))
        await asyncio.sleep(0.6)
        alive = [p for p in procs if p.returncode is None]
        check(not alive and not b.scanning(),
              f"stop during scan startup stops it (no orphaned bluetoothctl): alive {len(alive)}")

        procs.clear(); b.dups.clear()
        b.on_command(BT_SCAN_START, 20, bytes(6))
        await asyncio.sleep(0.05)
        b.dups.clear()                         # second "Scan" click after DUP_S (the window has not shown the scan yet)
        b.on_command(BT_SCAN_START, 20, bytes(6))
        await asyncio.sleep(0.6)
        alive = [p for p in procs if p.returncode is None]
        check(len(alive) == 1 and b.scan is alive[0], f"two starts at once = one scan process, known to the window: alive {len(alive)}")
        b.dups.clear()
        b.on_command(BT_SCAN_STOP, 0, bytes(6))
        await asyncio.sleep(0.3)
        check(all(p.returncode is not None for p in procs) and not b.scanning(), "stop from the window ends all scan processes")
    finally:
        SB.asyncio.create_subprocess_exec = real
        for p in procs:
            if p.returncode is None:
                p.kill()
                await p.wait()

asyncio.run(t_scan())


# ---------- touchpad: a finger already resting at connect ----------
out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))


def move(dt, x, y):
    return [(dt, Ev(ec.EV_ABS, ec.ABS_MT_POSITION_X, x)), (0, Ev(ec.EV_ABS, ec.ABS_MT_POSITION_Y, y)), (0, SYN)]


lift = [(0.01, Ev(ec.EV_ABS, ec.ABS_MT_TRACKING_ID, -1)), (0, SYN)]


def run(evs, kernel):
    out.clear()
    orig = st._kernel_slots
    st._kernel_slots = lambda dev, n: kernel
    try:
        async def go():
            c = sender_ctrl.Ctrl(0, "aa", None, None, Dev(evs))
            await st.stream_touch(c)
            await asyncio.sleep(0.03)
            return c
        return asyncio.run(go())
    finally:
        st._kernel_slots = orig


C.TOUCH_COALESCE_S = 0.0
c = run(move(0, 900, 500) + move(0.005, 960, 520) + move(0.005, 1020, 540), [(7, 880, 500), (-1, None, None)])
cursor = [v for t, v in out if t == T_MOUSE and not v[2] & 5]
touch = [v for t, v in out if t == T_TOUCH]
check(sum(v[0] for v in cursor) > 0, f"a finger placed before the stream starts moves the cursor: {cursor}")
check(touch and touch[-1][0] == 1 and touch[-1][1] == 7, f"... and shows up as a DS4 touch (id from the kernel slot): {touch[-1:]}")
run(move(0, 900, 500) + lift, [(7, 900, 500), (-1, None, None)])
check(not [v for t, v in out if t == T_MOUSE and v[2] & 5], "lifting such a finger is not a tap (the contact started earlier)")
run(move(0, 900, 500), [(-1, 880, 500), (-1, None, None)])
check(not [v for t, v in out if t == T_TOUCH and v[0]], "without a finger in the kernel an axis move (old slot) creates no touch")


# ---------- sender loop: the error counter resets after quiet operation ----------
import sender_controller as SC
SC.STABLE_S = 0.05
calls = [0]


async def flaky():
    calls[0] += 1
    if calls[0] <= 2:
        raise RuntimeError(f"error {calls[0]}")
    if calls[0] == 3:
        await asyncio.sleep(0.08)              # longer than STABLE_S without an error
        raise RuntimeError("rare error")

buf = io.StringIO()
t0 = time.monotonic()
with contextlib.redirect_stdout(buf):
    asyncio.run(SC.forever("test", flaky))
log = buf.getvalue()
check(log.count("error - restarting:\n") == 2 and "#3" not in log,
      f"an error after quiet operation counted from scratch (full trace, short pause): {log.splitlines()[-1]!r}")
check(time.monotonic() - t0 < 1.08, f"pauses 0.2 + 0.4 + 0.2 s, not 0.6 s the third time: {time.monotonic() - t0:.2f} s")

print("ALL OK" if not fails else f"FAILURES: {len(fails)}")
sys.exit(1 if fails else 0)
