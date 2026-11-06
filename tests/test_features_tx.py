"""Sender: trigger resistance in the HID report (sender_rumble), the PC clock from pong2 (PcClock), the Bluetooth delay from
MSC_TIMESTAMP (BtDelay) and the state2 packet with a measurement (Ctrl.send_state)."""
import asyncio, os, sys, tempfile, time, types, zlib
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_ctrl
import sender_net as net
net.clock.base = 0   # pings and the PC clock on perf_counter (the test gives the pong2 times)
import sender_rumble as R
from proto import BODY, CAP_TRIGGERS, LAT_NONE, T_PONG2, T_STATE, T_STATE2, T_TRIG, Codec
from sender_ctrl import Ctrl
from sender_streams import BtDelay

TMP = tempfile.mkdtemp()
from chk import ok

# ---------- efekt spustu ----------
off = R.trigger_effect(0, 0)
assert off == bytes([R.TRIG_OFF]) + bytes(10)
fx = R.trigger_effect(2, 6)
assert len(fx) == 11 and fx[0] == 0x21
zones = int.from_bytes(fx[1:3], "little")
forces = int.from_bytes(fx[3:7], "little")
assert zones == sum(1 << z for z in range(2, 10)), bin(zones)
assert all((forces >> (3 * z)) & 7 == (5 if z >= 2 else 0) for z in range(10)), "strength 6 -> 5 (3 bits) in zones 2-9"
assert not any(fx[7:]) and R.trigger_effect(0, 8)[1:3] == b"\xff\x03" and R.trigger_effect(10, 3)[0] == R.TRIG_OFF
ok("Feedback effect 0x21: zones from the start, 3 strength bits per zone; strength 0 / bad zone = Off")

rep = R.output_report(True, 2, 0, 0, None, None, (R.trigger_effect(0, 0), fx))
c = rep[3:]
assert c[0] & (R.FLAG0_LEFT_TRIGGER | R.FLAG0_RIGHT_TRIGGER) == 0x0C and c[0] & 0x03
assert c[R.RIGHT_TRIGGER_AT:R.RIGHT_TRIGGER_AT + 11] == fx and c[R.LEFT_TRIGGER_AT] == R.TRIG_OFF
assert int.from_bytes(rep[-4:], "little") == zlib.crc32(b"\xa2" + rep[:-4]) & 0xFFFFFFFF
plain = R.output_report(True, 2, 0, 0)[3:]
assert plain[0] & 0x0C == 0 and not any(plain[10:32]), "a report without triggers does not touch the effect on the pad"
ok("report: trigger flags, R2 from byte 10, L2 from 21, CRC; plain rumble without trigger flags")


class Dev:
    def capabilities(self):
        return {}


async def rumble():
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    assert r.bits & CAP_TRIGGERS
    r.rgb = (1, 2, 3)
    r.cur = (10, 20)
    assert r.set_triggers((0, 0, 3, 7))
    data = open(path, "rb").read()
    last = data[-78:][3:]
    assert last[R.RIGHT_TRIGGER_AT] == 0x21 and last[2:4] == bytes([10, 20]) and last[44:47] == bytes([1, 2, 3]), \
        "resistance in the report together with the current rumble and colour (does not zero them)"
    size = len(open(path, "rb").read())
    r.close()
    tail = open(path, "rb").read()[size:]
    assert tail and tail[-78:][3:][R.RIGHT_TRIGGER_AT] == R.TRIG_OFF, "closing the sender removes the resistance"
    assert all(rep[3:][2:4] == b"\0\0" for rep in (tail[k:k + 78] for k in range(0, len(tail), 78))), \
        "reports on close without rumble - removing the resistance must not start the motors again (the pad keeps the power)"
    ev = R.Rumble(1, Dev(), "off")
    assert not ev.bits & CAP_TRIGGERS and ev.set_triggers((1, 1, 1, 1)) is False
asyncio.run(rumble())
ok("Rumble.set_triggers: HID report, CAP_TRIGGERS bit, off on close, no HID - nothing")

# ---------- PC clock (pong2) ----------
pc = net.PcClock()
assert pc.now_us() is None
base = time.perf_counter_ns()
pc.pong(base, 9_000_000, base + 2_000_000)              # RTT 2 ms
pc.pong(base, 9_000_000 + 50, base + 400_000)           # RTT 0.4 ms - more accurate
assert pc.offset_us == 9_000_050 - (base + base + 400_000) // 2000
pc.pong(base + 10, 1, base - 5)                         # ujemny RTT - odrzucony
assert len(pc.samples) == 2
expect = (time.perf_counter_ns() // 1000 + pc.offset_us) & 0xFFFFFFFF
assert abs(pc.now_us() - expect) < 2000
pc.t_sync -= pc.SYNC_MAX_AGE_S + 1
assert pc.now_us() is None, "no pong2 (old receiver / a gap) - no clock"
ok("PcClock: offset from the measurement with the smallest RTT, expires without pong2")

# ---------- Bluetooth delay above the minimum ----------
b = BtDelay()
assert b.sample(1_000_000, 500) == 0
assert b.sample(1_004_000, 4_500) == 0 and b.sample(1_008_800, 9_000) == 300
assert b.sample(1_012_000, 12_500) == 0
assert b.sample(4_000_000, 3_000_500) == 0, "the old minimum drops out of the 2 s window"
b2 = BtDelay()
b2.sample(10_000, 0xFFFFFF00)
assert b2.sample(10_456, 100) == 100, "overflow of the pad's 32-bit counter"
ok("BtDelay: excess over the 2 s minimum, sliding window, pad counter overflow")

# ---------- state2: a measurement only for a fresh change and only with the PC clock ----------
out = []
def emit(t, i, *v):
    BODY[t].pack(*v)
    out.append((t, i, v))
net.emit = sender_ctrl.net.emit = emit
c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
net.pc_clock = types.SimpleNamespace(now_us=lambda: None)
c.send_state(force=True)
assert out[-1][0] == T_STATE, "no PC clock - a plain state packet (older receiver)"
net.pc_clock = types.SimpleNamespace(now_us=lambda: 777)
c.bt_us = 250
c.mark_event(types.SimpleNamespace(sec=(time.time_ns() // 1000 - 1500) // 1_000_000,
                                   usec=(time.time_ns() // 1000 - 1500) % 1_000_000))
c.pad[0] = 1
c.send_state()
t, i, v = out[-1]
assert t == T_STATE2 and v[-3] == 777 and 1000 <= v[-2] < 50_000 and v[-1] == 250, v[-3:]
c.send_state(force=True)
assert out[-1][0] == T_STATE2 and out[-1][2][-2:] == (LAT_NONE, LAT_NONE), "a repeat (heartbeat) without a measurement"
c.mark_event(types.SimpleNamespace())                    # event without a time (tests) - no measurement
assert c.t_event is None
ok("Ctrl.send_state: state2 with the time on the PC clock, phone and Bluetooth only for a fresh change")


def ev_ago(us):
    t = time.time_ns() // 1000 - us
    return types.SimpleNamespace(sec=t // 1_000_000, usec=t % 1_000_000)


c.set_abs(0, c.pad[0] + 1 if c.pad[0] < 255 else 254)    # stick change ...
c.send_state()
c.set_abs(0, 128)
c.send_state()
c.set_abs(0, 129)                                        # ... noise in the dead zone: value 128 unchanged
c.mark_event(ev_ago(40_000))
n = len(out)
c.send_state()
assert len(out) == n and c.t_event is None, "a change with no effect does not leave its time for the next one"
c.mask = 1                                               # a real press
c.mark_event(ev_ago(300))
c.send_state()
assert out[-1][0] == T_STATE2 and 300 <= out[-1][2][-2] < 5_000, f"latency of the press, not of the noise: {out[-1][2][-2]}"
ok("Ctrl.send_state: stick noise in the dead zone does not inflate the measurement of the next press")

pc = net.PcClock()
base = time.perf_counter_ns()
pc.pong(base, 5_000_000, base + 100_000)                 # RTT 0.1 ms, old PC clock
pc.t_sync -= pc.SYNC_MAX_AGE_S + 1                       # a gap (PC off / restart)
pc.pong(base, 9_000_000, base + 2_000_000)               # new clock, worse RTT
assert len(pc.samples) == 1 and pc.offset_us == 9_000_000 - (base + base + 2_000_000) // 2000, \
    "after a gap measurements from before it do not win with a smaller RTT"
ok("PcClock: measurements from before a gap in the pongs are dropped")

# ---------- receive: trig and pong2 from the network ----------
cod = Codec()
got = []
net.on_triggers = lambda idx, vals: got.append((idx, vals))
net.pc_clock = net.PcClock()


class Sock:
    def __init__(self, pkts):
        self.pkts = list(pkts)
    def recv(self, n):
        if not self.pkts:
            raise BlockingIOError
        return self.pkts.pop(0)


net.codec = cod
net.sock = Sock([cod.pack(T_TRIG, 1, 0, 0, 3, 6), cod.pack(T_PONG2, 255, time.perf_counter_ns() - 100_000, 5_000_000)])
net.on_readable()
assert got == [(1, (0, 0, 3, 6))] and net.pc_clock.offset_us is not None
ok("sender_net: trig packet -> callback, pong2 -> PC clock")
print("FEATURES TX OK")
