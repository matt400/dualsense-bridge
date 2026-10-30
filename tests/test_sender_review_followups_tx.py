"""Sender: regressions from the second review - touch tap after SYN_DROPPED, calibration request of a lost motion stream, BT trust
during a refresh, lightbar and rumble fallback off the event loop, idle power-off (held stick, USB pads), start errors.
    python3 tests/test_sender_review_followups_tx.py sender"""
import asyncio
import contextlib
import io
import os
import sys
import tempfile
import threading

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from evdev import ecodes as ec

import sender_bt
import sender_calib
import sender_control as ctl
import sender_ctrl
import sender_net as net
import sender_rumble as R
import sender_streams as st
import sender_supervisor as sup
from proto import BTD_PAD, BTD_PAIRED, BTD_TRUSTED, CAL_FEW, T_MOUSE
from sender_config import C

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- 1. a tap right after SYN_DROPPED still clicks ----------
class Ev:
    def __init__(s, t, c, v): s.type, s.code, s.value = t, c, v
SYN = Ev(ec.EV_SYN, ec.SYN_REPORT, 0)
class AI: max, value = 1919, 0
class TouchDev:
    def __init__(s, evs): s.evs = evs
    def absinfo(s, c): return AI()
    async def async_read_loop(s):
        for dt, e in s.evs:
            if dt: await asyncio.sleep(dt)
            yield e
def frame(dt, *pairs): return [(dt if k == 0 else 0, Ev(ec.EV_ABS, c, v)) for k, (c, v) in enumerate(pairs)] + [(0, SYN)]
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


# ---------- 2. a calibration request does not outlive a lost motion stream ----------
c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
c.cal_request = 5
seen = []
sender_calib.report = lambda c_, status, repeat=False: seen.append(status)
sup.calib.report = sender_calib.report
sup._reset_lost_stream(c, "motion")
check(c.cal_request == 0 and seen == [CAL_FEW], f"lost motion stream: request dropped and the window told ({c.cal_request}, {seen})")


# ---------- 3. trust_pads when a refresh replaced the list during the await ----------
async def t_trust():
    d = sender_bt.Device("aa:bb:cc:dd:ee:01", "pad", -50, BTD_PAD | BTD_PAIRED)
    fake = type("F", (), {})()
    fake.devices, fake.untrusted, fake.adapter = [d], set(), "hci0"
    async def set_prop(*a):
        fake.devices = [sender_bt.Device(d.mac, d.name, -70, d.flags)]      # a refresh with a new RSSI
    fake.set_prop = set_prop
    await sender_bt.Bluetooth.trust_pads(fake)
    check(len(fake.devices) == 1 and fake.devices[0].flags & BTD_TRUSTED, "trust_pads: survives a refresh that replaced the list")
asyncio.run(t_trust())


# ---------- 4. lightbar: off the loop, latest colour wins ----------
async def t_led():
    led = os.path.join(TMP, "led"); os.mkdir(led)
    for n, t in (("max_brightness", "255"), ("multi_intensity", ""), ("brightness", "")):
        open(os.path.join(led, n), "w").write(t)
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:02", None, None, None)
    c.led = led
    threads, gate, started = [], threading.Event(), threading.Event()
    real = c._led_io
    def slow(l, rgb):
        threads.append((threading.current_thread() is threading.main_thread(), rgb))
        started.set(); gate.wait(2)
        return real(l, rgb)
    c._led_io = slow
    c._write_led((1, 1, 1))
    await asyncio.sleep(0.05)
    check(started.is_set(), "lightbar: the write runs while the loop is free")
    for rgb in ((2, 2, 2), (3, 3, 3), (4, 4, 4)):
        c._write_led(rgb)
    gate.set()
    await asyncio.sleep(0.2)
    check([t[1] for t in threads] == [(1, 1, 1), (4, 4, 4)] and not any(t[0] for t in threads),
          f"lightbar: one write at a time in a worker, only the newest colour follows ({threads})")
    check(c.led_color == (4, 4, 4) and not c._led_busy, "lightbar: the final colour is recorded")
asyncio.run(t_led())


# ---------- 4b. lightbar: going back to the old colour while a write runs is not skipped ----------
async def t_led_back():
    led = os.path.join(TMP, "led_back"); os.mkdir(led)
    for n, t in (("max_brightness", "255"), ("multi_intensity", ""), ("brightness", "")):
        open(os.path.join(led, n), "w").write(t)
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:03", None, None, None)
    c.led = led
    c._led_done((0, 0, 255), None)                       # the pad is blue
    gate, started, written = threading.Event(), threading.Event(), []
    real = c._led_io
    def slow(l, rgb):
        written.append(rgb)
        started.set(); gate.wait(2)
        return real(l, rgb)
    c._led_io = slow
    c.apply_feedback(0, 0, 255, 0, 0)                    # red is being written
    await asyncio.sleep(0.05)
    c.apply_feedback(0, 0, 0, 0, 255)                    # the PC wants blue again, before red has finished
    gate.set()
    await asyncio.sleep(0.2)
    check(written == [(255, 0, 0), (0, 0, 255)] and c.led_color == (0, 0, 255),
          f"lightbar: back to the old colour during a write is written ({written}, {c.led_color})")
asyncio.run(t_led_back())


# ---------- 5. rumble: the hidraw fallback is opened in a worker thread ----------
class FFBad:
    path = "/dev/input/event0"
    def upload_effect(self, e): raise OSError(5, "EIO")
    def write(self, *a): raise OSError(5, "EIO")
    def erase_effect(self, i): pass
    def capabilities(self): return {ec.EV_FF: [ec.FF_RUMBLE]}

async def t_rumble():
    path = os.path.join(TMP, "hidraw"); open(path, "wb").close()
    where = []
    r = R.Rumble(0, FFBad(), "auto")
    def fake_open(given=None):
        where.append(threading.current_thread() is threading.main_thread())
        r.fd = os.open(path, os.O_RDWR); r.bt = False; r.v2 = False
        return True
    r._open_hidraw = fake_open
    r.methods = ["evdev"]
    r.fails["evdev"] = R.FAIL_MAX - 1
    r.set(10, 20)
    check("hidraw" not in r.methods, "rumble: set() does not wait for the node to be opened")
    await asyncio.sleep(0.1)
    check(where == [False] and "hidraw" in r.methods, f"rumble: the node is opened in a worker ({where}, {r.methods})")
    r.close()
    # closed while the worker was opening: the descriptor must not leak
    r2 = R.Rumble(1, FFBad(), "auto")
    gate = threading.Event()
    def slow_open(given=None):
        gate.wait(2); r2.fd = os.open(path, os.O_RDWR); return True
    r2._open_hidraw = slow_open
    r2.methods = ["evdev"]; r2.fails["evdev"] = R.FAIL_MAX - 1
    r2.set(10, 20)
    await asyncio.sleep(0.02)
    r2.close(); gate.set()
    await asyncio.sleep(0.1)
    check(r2.fd is None and not r2.methods, "rumble: closing during the open leaves no descriptor and no way")
asyncio.run(t_rumble())


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


# ---------- 7. start errors are reported; a finished teardown asks for a scan ----------
async def t_start():
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:06", None, None, None)
    async def boom(c_): raise ValueError("boom")
    buf = io.StringIO()
    evs = []
    sup.events.error = lambda *a: evs.append(a)
    sup._start_pad = boom
    sup.state.spawn = lambda coro: coro.close()
    with contextlib.redirect_stdout(buf):
        await sup.run_ctrl(c)
    check("start error" in buf.getvalue() and evs, "run_ctrl: an unexpected start error is reported")
    class W:
        event = asyncio.Event()
    sup._watch = W()
    sup.kick_scan()
    check(W.event.is_set(), "kick_scan: wakes the supervisor")
    sup._watch = None
    sup.kick_scan()
asyncio.run(t_start())

if fails:
    print("FAIL", len(fails), "errors")
    sys.exit(1)
print("ALL OK")
