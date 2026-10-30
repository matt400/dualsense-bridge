"""Sender: a late motion / touchpad device is not attached twice, a borrowed slot is not remembered, small review fixes.
  - _attach_late_devices leaves a pad that is still starting alone (closes the group, _start_pad attaches the devices itself)
  - pick_slot: a slot borrowed from another known pad is not written to slots.json
  - feedback that arrived while the rumble backend was opening is applied once it is open
  - power_off skips a USB pad; send_raw keeps the socket on ENOBUFS / EHOSTUNREACH
python3 tests/test_late_devices_slots_tx.py sender"""
import asyncio, errno, json, os, sys, tempfile, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_control as control
import sender_ctrl
import sender_net as net
import sender_state as state
import sender_streams as streams
import sender_supervisor as sup

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


class Dev:
    path = "/dev/input/event91"
    name = "Wireless Controller"
    closed = False
    info = types.SimpleNamespace(bustype=5)
    def absinfo(self, code):
        return types.SimpleNamespace(value=0, max=1)
    def active_keys(self):
        return []
    def grab(self):
        pass
    def ungrab(self):
        pass
    def close(self):
        self.closed = True


async def late_attach():
    starts = []
    async def idle(_c):
        await asyncio.sleep(3600)
    async def motion(_c):
        starts.append(1)
        await asyncio.sleep(3600)
    async def slow_rumble(_c):
        await asyncio.sleep(0.15)
        return types.SimpleNamespace(set_triggers=lambda t: None, bits=0, why=0, owns_lightbar=False,
                                     set=lambda *a, **k: applied.append(a))
    applied = []
    real = (streams.stream_pad, streams.stream_battery, streams.stream_motion, sup._open_rumble, sup.grab, sup.events.info)
    streams.stream_pad = streams.stream_battery = idle
    streams.stream_motion = motion
    sup._open_rumble = slow_rumble
    sup.grab = lambda *a: None
    sup.events.info = lambda *a: None
    try:
        c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:31", Dev(), None, None)
        c.starting = True
        c.led = None
        start = asyncio.create_task(sup._start_pad(c))
        await asyncio.sleep(0.03)
        c.fb = (10, 20, 1, 2, 3)                      # feedback from the PC while the backend opens
        late = Dev()
        sup._attach_late_devices(c, {"motion": late})
        check(late.closed and c.motion_dev is None and not c.extra, "a starting pad takes no late device (the group is closed)")
        tasks = await start
        check(applied == [(10, 20)], f"feedback received during startup is applied once the rumble backend is open ({applied})")
        late = Dev()
        sup._attach_late_devices(c, {"motion": late})
        await asyncio.sleep(0.03)
        check(c.motion_dev is late and len(c.extra) == 1 and len(starts) == 1, "after the start the late device is attached once")
        for t in [*tasks, *c.extra]:
            t.cancel()
        await asyncio.gather(*tasks, *c.extra, return_exceptions=True)
    finally:
        (streams.stream_pad, streams.stream_battery, streams.stream_motion, sup._open_rumble, sup.grab, sup.events.info) = real

asyncio.run(late_attach())


# ---------- slots ----------
tmp = tempfile.mkdtemp()
C.SLOTS_FILE = os.path.join(tmp, "slots.json")
C.MAX_PADS = 2
A, B, G = "aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02", "ff:ff:ff:ff:ff:09"
json.dump({A: 0, B: 1}, open(C.SLOTS_FILE, "w"))
state.saved_slots = None
state.slots.clear(); state.active.clear()
check(sup.pick_slot(G) == 0, "a guest pad gets a slot while both owners are away")
check(state.saved_slots == {A: 0, B: 1}, f"the borrowed slot is not written to the table ({state.saved_slots})")
state.active[G] = object()                          # the guest is still connected when the owner of slot 0 comes back
check(sup.pick_slot(A) == 1 and state.saved_slots.get(A) == 0, "the owner coming back meanwhile keeps his remembered slot in the file")
state.slots.clear(); state.active.clear(); state.saved_slots = None
json.dump({A: 0}, open(C.SLOTS_FILE, "w"))
check(sup.pick_slot("aa:aa:aa:aa:aa:77") == 1 and state.saved_slots == {A: 0, "aa:aa:aa:aa:aa:77": 1},
      "a new pad still takes a free slot nobody reserved and remembers it")


# ---------- power off on USB, send errors ----------
async def power():
    called = []
    async def disconnect_pad(_mac):
        called.append(1)
        return True
    real = control.sender_bt.bt.disconnect_pad
    control.sender_bt.bt.disconnect_pad = disconnect_pad
    try:
        usb = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:41", Dev(), None, None)
        usb.pad_dev = types.SimpleNamespace(info=types.SimpleNamespace(bustype=sender_ctrl.BUS_USB))
        await control.power_off(usb, "test")
        check(not called, "power off from the window is skipped for a pad on USB")
        bt = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:42", Dev(), None, None)
        await control.power_off(bt, "test")
        check(called == [1], "a Bluetooth pad is still powered off")
    finally:
        control.sender_bt.bt.disconnect_pad = real

asyncio.run(power())

class Sock:
    def __init__(self, err):
        self.err = err
    def send(self, data):
        raise OSError(self.err, "x")

for err, bad in ((errno.ENOBUFS, False), (errno.EHOSTUNREACH, False), (errno.ENETDOWN, True)):
    net.sock, net.sock_bad = Sock(err), False
    net.send_raw(b"x")
    check(net.sock_bad is bad, f"send_raw: {errno.errorcode[err]} -> socket {'rebuilt' if bad else 'kept'}")
net.sock, net.sock_bad = None, False

print("LATE DEVICES / SLOTS", "FAIL" if fails else "OK")
sys.exit(1 if fails else 0)
