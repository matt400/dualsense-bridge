"""Sender: regressions from a bug review.
  - btlink: a controller without Read RSSI / Link Quality refuses at once; the reading must not wait the whole deadline
    and must keep its socket
  - a motion device lost in the middle of a calibration from the window is reported (CAL_FEW)
  - sender_update.run(): an exception does not leave the state "running"
  - Bluetooth.loop: the wake event is cleared before the work, not before the wait
  - DevWatch.start closes the inotify descriptor when add_reader fails
  - a scan cancelled in its worker thread closes the devices it opened
  - the pad is grabbed before the rumble backend opens, and the heartbeat leaves a starting pad alone
  - config: port / max_pads are validated, rumble is case-insensitive
  - btadapter: USB is recognised by path components, not by the substring "/usb"
python3 tests/test_sender_bug_review_tx.py sender"""
import asyncio, inspect, os, struct, sys, tempfile, time, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import proto
import sender_btadapter as BA
import sender_btlink as BL
import sender_bt as SB
import sender_calib as calib
import sender_ctrl
import sender_net
import sender_state
import sender_streams as streams
import sender_supervisor as sup
import sender_update as UPD
from pathlib import Path
from proto import CAL_FEW, T_CALRES

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


OP = BL.OPCODE_RSSI
def complete(opcode, status, handle=None, tail=b""):
    body = struct.pack("<BHB", 1, opcode, status) + (struct.pack("<H", handle) + tail if handle is not None else b"")
    return bytes([4, 0x0E, len(body)]) + body
def cmd_status(opcode, status):
    return bytes([4, 0x0F, 4, status, 1]) + struct.pack("<H", opcode)


# ---------- btlink: a refusal is an answer ----------
check(BL.classify_reply(complete(OP, 1), OP, 42) == (True, None), "Complete 'unknown command' (status only, no handle): final, no value")
check(BL.classify_reply(complete(OP, 1, 42, b"\x00"), OP, 42) == (True, None), "Complete with an error status and the handle: final, no value")
check(BL.classify_reply(complete(OP, 1, 43, b"\x00"), OP, 42) == (False, None), "an error for another handle: not ours, keep waiting")
check(BL.classify_reply(complete(BL.OPCODE_LINK_QUALITY, 1), OP, 42) == (False, None), "an error for another command: keep waiting")
check(BL.classify_reply(cmd_status(OP, 1), OP, 42) == (True, None), "Command Status with an error for this opcode: final")
check(BL.classify_reply(cmd_status(OP, 0), OP, 42) == (False, None), "Command Status 0 (accepted): keep waiting for the Complete")
check(BL.classify_reply(cmd_status(BL.OPCODE_LINK_QUALITY, 1), OP, 42) == (False, None), "Command Status for another opcode: keep waiting")
check(BL.classify_reply(complete(OP, 0, 42, b"\xfb"), OP, 42) == (True, b"\xfb"), "a good Complete: final, with the value")


class Refusing:
    """Answers every command at once with an error; any wait beyond a few ms would mean it was ignored."""
    def __init__(self, reply):
        self.reply, self.closed = reply, False
    def send(self, data):
        pass
    def settimeout(self, value):
        pass
    def recv(self, n):
        return self.reply
    def close(self):
        self.closed = True


for name, reply in (("Complete with an error", complete(OP, 1)), ("Command Status with an error", cmd_status(OP, 1))):
    raw = BL.RawHci(Path("/nonexistent"))
    sock = Refusing(reply)
    raw.socks[3] = sock
    t0 = time.monotonic()
    got = raw.command(3, OP, 42)
    check(got is None and time.monotonic() - t0 < BL.REPLY_WAIT_S / 2, f"RawHci.command: {name} returns None at once")
    check(raw.socks.get(3) is sock and not sock.closed, f"RawHci.command: {name} keeps the socket open")


# ---------- calibration: the motion device vanishes while the pad is calibrating ----------
sent = []
sender_net.emit = sender_ctrl.net.emit = calib.net.emit = lambda t, i, *v: sent.append((t, i, v))
sender_state.saved_cal = None


class Dev:
    path = "/dev/input/event91"
    name = "Wireless Controller"
    def absinfo(self, code):
        return types.SimpleNamespace(value=0)
    def active_keys(self):
        return []
    def grab(self):
        pass


c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:20", Dev(), None, None)
c.cal_request = 2
stream = streams._MotionStream.__new__(streams._MotionStream)
stream.c, stream.cal = c, None
stream.autobias = types.SimpleNamespace(reset=lambda now: None)
stream._start_user_calibration(time.monotonic())
check(c.cal_request == 0 and c.cal_running, "starting a calibration zeroes cal_request and marks it running")
sent.clear()
sup._reset_lost_stream(c, "motion")
check(any(t == T_CALRES and v[0] == CAL_FEW for t, _i, v in sent), "motion lost mid-calibration: CAL_FEW goes to the window")
check(not c.cal_running and c.cal_request == 0, "the flags are cleared afterwards")
sent.clear()
sup._reset_lost_stream(c, "motion")
check(not any(t == T_CALRES for t, _i, _v in sent), "no calibration pending: nothing is reported")

c.cal_running = True
stream.cal = types.SimpleNamespace(user=True, feed=lambda now, gyro: (proto.CAL_OK, [1.0, 2.0, 3.0]))
real_finish, calib.finish = calib.finish, lambda c_, result: None
try:
    stream.sample = [0] * 12
    stream._advance_calibration(5.0)
finally:
    calib.finish = real_finish
check(not c.cal_running and stream.cal is None, "a finished calibration clears the running flag")


# ---------- sender_update.run(): no stuck "running" ----------
async def update_cases():
    real_pull, real_build = proto.git_pull, UPD.send_build
    calls = []
    def boom(*_a):
        raise RuntimeError("git exploded")
    proto.git_pull = boom
    UPD.send_build = lambda: calls.append(UPD.status)
    try:
        UPD._last = float("-inf")
        try:
            await UPD.run()
        except RuntimeError:
            pass
        check(UPD.status != proto.UPD_RUNNING and UPD.status == proto.UPD_FAILED, f"an exception leaves a failure, not 'running' (status {UPD.status})")
        check(UPD.fail_msg == UPD.INTERRUPTED, "the failure reason is set for the window")
        check(calls and calls[-1] == proto.UPD_FAILED, "the final state is sent")
        UPD._last = float("-inf")
        proto.git_pull = lambda *_a: (proto.UPD_NO_CHANGE, "", "", "", [])
        await UPD.run()
        check(UPD.status == proto.UPD_NO_CHANGE, "the next update can run")
    finally:
        proto.git_pull, UPD.send_build = real_pull, real_build

asyncio.run(update_cases())


# ---------- Bluetooth.loop: wake cleared before the work ----------
src = inspect.getsource(SB.Bluetooth.loop)
check(0 <= src.index("self.wake.clear()") < src.index("self.active(now)") < src.index("self.wake.wait()"),
      "Bluetooth.loop clears the wake event at the top of an iteration, before the work and the wait")
check(src.count("self.wake.clear()") == 1, "...and only there")


# ---------- DevWatch.start: no descriptor leak ----------
def open_fds():
    return len(os.listdir("/proc/self/fd"))

before = open_fds()
w = sup.DevWatch()
ok_start = w.start(tempfile.mkdtemp().encode())           # no running loop here: add_reader cannot even be reached
check(ok_start is False and w.fd is None and open_fds() == before, f"DevWatch.start: failure after inotify_init closes the descriptor ({before} -> {open_fds()})")


async def reader_fails():
    loop = asyncio.get_running_loop()
    real = loop.add_reader
    def refuse(*_a, **_k):
        raise OSError("no reader")
    loop.add_reader = refuse
    n = open_fds()
    try:
        w = sup.DevWatch()
        res = w.start(tempfile.mkdtemp().encode())
    finally:
        loop.add_reader = real
    check(res is False and w.fd is None and open_fds() == n, "DevWatch.start: add_reader fails -> False and the descriptor is closed")

asyncio.run(reader_fails())


# ---------- a cancelled scan closes the devices it opened ----------
class Opened:
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


async def cancelled_scan():
    devs = [Opened(), Opened(), Opened()]
    def slow_discover():
        time.sleep(0.15)
        return {"k": {"pad": devs[0], "motion": devs[1], "touch": devs[2]}}
    real = sup.discover
    sup.discover = slow_discover
    try:
        task = asyncio.create_task(sup._scan_once())
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.3)               # the worker thread finishes and the callback runs
    finally:
        sup.discover = real
    check(all(d.closed for d in devs), "a scan cancelled in its thread: every device it opened is closed")

asyncio.run(cancelled_scan())


# ---------- grab before rumble, heartbeat skips a starting pad ----------
async def start_order():
    order = []
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:21", Dev(), None, None)
    c.starting = True
    real = (sup.grab, sup._open_rumble, streams.stream_pad, streams.stream_battery, sup.events.info)
    async def fake_rumble(_c):
        order.append("rumble")
        assert c.starting, "still starting while the rumble backend opens"
        return types.SimpleNamespace(set_triggers=lambda t: None, bits=0, why=0)
    async def idle(_c):
        await asyncio.sleep(3600)
    sup.grab = lambda dev, label: order.append("grab")
    sup._open_rumble = fake_rumble
    streams.stream_pad = streams.stream_battery = idle
    sup.events.info = lambda *a: None
    C.GRAB_PAD = True
    try:
        tasks = await sup._start_pad(c)
    finally:
        sup.grab, sup._open_rumble, streams.stream_pad, streams.stream_battery, sup.events.info = real
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    check(order == ["grab", "rumble"], f"_start_pad grabs the pad before the rumble backend opens ({order})")
    check(not c.starting, "_start_pad clears 'starting' once the pad is ready")

    # the heartbeat
    seen = []
    hb_pad = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:22", Dev(), None, None)
    hb_pad.t_state = float("-inf")
    hb_pad.send_state = lambda force=False: seen.append(("state", hb_pad.starting))
    hb_pad.send_ident = hb_pad.send_click = lambda *a, **k: seen.append(("other", hb_pad.starting))
    hb_pad.send_touch = lambda *a, **k: seen.append(("other", hb_pad.starting))
    sender_state.active.clear()
    sender_state.active[hb_pad.key] = hb_pad
    old = C.HEARTBEAT_S
    C.HEARTBEAT_S = 0.01
    try:
        hb_pad.starting = True
        task = asyncio.create_task(streams.heartbeat())
        await asyncio.sleep(0.08)
        check(not seen, "the heartbeat sends nothing for a pad that is still starting")
        hb_pad.starting = False
        await asyncio.sleep(0.08)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        C.HEARTBEAT_S = old
        sender_state.active.clear()
    check(any(k == "state" for k, _s in seen), "...and resumes once the pad is ready")

asyncio.run(start_order())
src = inspect.getsource(sup._start_new_pad)
check("c.starting = True" in src and src.index("c.starting = True") < src.index("state.active[key] = c"),
      "a new pad is marked 'starting' before it becomes visible in state.active")


# ---------- config validation ----------
def net_cfg(**kw):
    return dict(sender_config.DEFAULTS, host="127.0.0.1", **kw)

for key, bad in (("port", 0), ("port", -5), ("port", 70000), ("port", "abc"), ("max_pads", 0), ("max_pads", -1)):
    try:
        sender_config._apply_network(net_cfg(**{key: bad}))
        check(False, f"{key} = {bad!r} is rejected")
    except SystemExit as e:
        check(key in str(e), f"{key} = {bad!r} is rejected with the setting's name")
sender_config._apply_network(net_cfg(port=65535, max_pads=4))
check(C.PORT == 65535 and C.MAX_PADS == 4, "valid port / max_pads pass")
sender_config._apply_network(net_cfg(port="5005", max_pads=2))
check(C.PORT == 5005 and C.MAX_PADS == 2, "numbers given as text pass")
for typed, want in (("Auto", "auto"), (" EVDEV ", "evdev"), ("HidRaw", "hidraw"), ("OFF", "off"), ("nonsense", "auto")):
    sender_config._apply_runtime(dict(sender_config.DEFAULTS, rumble=typed))
    check(C.RUMBLE == want, f"rumble {typed!r} -> {want!r}")
sender_config.init(["127.0.0.1"])


# ---------- btadapter: USB by path components ----------
root = Path(tempfile.mkdtemp())
plain = root / "devices/platform/soc/usbphy-bridge/serial0/bluetooth/hci0"
plain.mkdir(parents=True)
check(not BA._is_usb(plain), "a built-in controller on a path that merely contains 'usb' is not USB")
hub = root / "devices/pci0000:00/usb3/3-10/3-10:1.0/bluetooth/hci1"
hub.mkdir(parents=True)
check(BA._is_usb(hub), "a path through a usbN root hub is USB")
desc = root / "devices/odd/bus/1-2"
(desc / "1-2:1.0/bluetooth/hci2").mkdir(parents=True)
(desc / "idVendor").write_text("0a12\n")
check(BA._is_usb(desc / "1-2:1.0/bluetooth/hci2"), "a parent with USB descriptors (idVendor) is USB")

print("SENDER BUG REVIEW", "FAIL" if fails else "OK")
sys.exit(1 if fails else 0)
