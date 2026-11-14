"""Receiver: virtual pads - HIDMaestro (DualSense) / ViGEmBus (DS4) backend, fixed player order (placeholder pad),
the DualSense report, feedback from the output report, backend change in the settings.
    python3 tests/test_vpad.py receiver"""
import os, sys, threading, time, types
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_ds4, rx_events as ev, rx_feedback as F, rx_hm, rx_report as R, rx_settings
import rx_state as S

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


def vpad(**kw):
    rx_settings.apply(replace(rx_settings.cur, vpad=replace(rx_settings.VpadConfig(), **kw)))


def wait(cond, t=3.0):
    end = time.monotonic() + t
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


# ---------- ViGEm (stub): creation order ----------
created = []                                       # (pad number, slot) in creation order
alive_pads = set()
class VPad:
    n = 0
    def __init__(self):
        VPad.n += 1
        self.id = VPad.n
        time.sleep(0.05)                           # creation takes time - a higher slot would have to wait
        created.append(self.id)
        alive_pads.add(self.id)
    def register_notification(self, cb): pass
    def unregister_notification(self): alive_pads.discard(self.id)
    def update_extended_report(self, rep): pass

rx_ds4.vg.VDS4Gamepad = VPad
rx_ds4.init()
vpad(backend="vigem", slot_grace_ms=300)
threads = [threading.Thread(target=rx_ds4.slot_thread, args=(i,), daemon=True) for i in range(2)]
for t in threads:
    t.start()


def pad_id(i):
    o = rx_ds4.outs[i]
    return o.pad.id if o is not None and o.pad is not None else None


# pad 2 turned on first: slot 0 gets a placeholder pad BEFORE the slot 1 pad
S.want[1].set(); S.wake[1].set()
check(wait(lambda: S.plugged[0] and S.plugged[1]), "pad 2 alone: both slots have a virtual pad (0 the placeholder)")
first0, first1 = pad_id(0), pad_id(1)
check(created.index(first0) < created.index(first1), f"the placeholder slot 0 was created before slot 1: {created}")
check(rx_ds4.kinds[0] == rx_ds4.kinds[1] == "vigem", "pad kind in kinds (window)")
# pad 1 joins: slot 0 is the same virtual pad (player 1 without reconnecting)
S.want[0].set(); S.wake[0].set()
time.sleep(0.3)
check(pad_id(0) == first0 and pad_id(1) == first1, "pad 1 joins the existing placeholder - no switching")
# pad 2 wychodzi: slot 1 znika, slot 0 zostaje (to pad 1)
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[1]) and S.plugged[0] and pad_id(0) == first0, "pad 2 disconnected: slot 0 stays")
# pad 2 comes back, pad 1 leaves: slot 0 becomes the placeholder, does not vanish (pad 2 stays player 2)
S.want[1].set(); S.wake[1].set()
check(wait(lambda: S.plugged[1]), "pad 2 comes back")
S.want[0].clear(); S.wake[0].set()
time.sleep(0.3)
check(S.plugged[0] and pad_id(0) == first0, "pad 1 disconnected with 2 connected: slot 0 holds the place")
# pad 2 leaves too: nothing stays
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[0] and not S.plugged[1]), "both disconnected: no virtual pads")
check(wait(lambda: not alive_pads), f"all pads detached from ViGEm: {alive_pads}")

# without the fixed order: pad 2 alone = one virtual pad
vpad(backend="vigem", fixed_slots=False, slot_grace_ms=300)
S.want[1].set(); S.wake[1].set()
check(wait(lambda: S.plugged[1]) and not S.plugged[0], "fixed_slots=false: only slot 1 (the old behaviour)")
# turning the fixed order on midway: slot 0 gets the placeholder at once (the slot thread checks needed)
vpad(backend="vigem", fixed_slots=True, slot_grace_ms=300)
check(wait(lambda: S.plugged[0]), "fixed_slots turned on midway: placeholder slot 0")
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[0] and not S.plugged[1]), "cleanup")

# placeholder mode, pad 1 joins within the grace time (slot_grace_ms): no placeholder device is ever created
created.clear()
S.want[1].set(); S.wake[1].set()
time.sleep(0.1)
S.want[0].set(); S.wake[0].set()
check(wait(lambda: S.plugged[0] and S.plugged[1]), "grace: both pads plugged in")
check(len(created) == 2 and created.index(pad_id(0)) < created.index(pad_id(1)), f"grace: exactly two devices, slot 0 first: {created}")
S.want[0].clear(); S.want[1].clear(); S.wake[0].set(); S.wake[1].set()
check(wait(lambda: not S.plugged[0] and not S.plugged[1]), "grace: cleanup")
check(not rx_ds4.holding, "grace: nothing left in holding")

# hold mode: no placeholder device ever; pad 2 waits for pad 1, then both are plugged in slot order
vpad(backend="vigem", slot_mode="hold")
created.clear()
S.want[1].set(); S.wake[1].set()
time.sleep(0.5)
check(not S.plugged[0] and not S.plugged[1], "hold: pad 2 alone waits, no placeholder and no early pad 2")
S.want[0].set(); S.wake[0].set()
check(wait(lambda: S.plugged[0] and S.plugged[1]), "hold: pad 1 arrived, both plugged in")
check(created.index(pad_id(0)) < created.index(pad_id(1)), f"hold: slot 0 created before slot 1: {created}")
S.want[0].clear(); S.wake[0].set()
check(wait(lambda: not S.plugged[0]) and S.plugged[1], "hold: pad 1 leaving does not leave a placeholder behind")
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[1]), "hold: cleanup")
# pad 2 leaves while waiting: nothing is plugged afterwards
S.want[1].set(); S.wake[1].set()
time.sleep(0.2)
S.want[1].clear(); S.wake[1].set()
time.sleep(0.3)
check(not S.plugged[1], "hold: pad 2 left while waiting - nothing plugged")
# the wait is bounded: pad 1 never comes -> pad 2 plugs in alone
rx_ds4.HOLD_WAIT = 0.4
S.want[1].set(); S.wake[1].set()
check(wait(lambda: S.plugged[1]) and not S.plugged[0], "hold: pad 1 never came - pad 2 plugs in alone after the timeout")
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[1]), "hold: cleanup 2")


# ---------- HIDMaestro (backend stub): DualSense report, feedback, backend change ----------
class HMPad:
    kind = "hm"
    build = staticmethod(R.build_ds5_report)
    def __init__(self, slot, on_fb):
        self.slot, self.on_fb = slot, on_fb
        self.buf = bytearray(63)
        self.sent = []
        self.closed = False
    def submit(self):
        self.sent.append(bytes(self.buf))
    def close(self):
        self.closed = True

class HMBackend:
    def __init__(self):
        self.pads = []
    def create(self, slot, on_fb):
        p = HMPad(slot, on_fb)
        self.pads.append(p)
        return p

hm = HMBackend()
real_get = rx_hm.get
rx_hm.get = lambda path="": hm
vpad(backend="auto")
S.want[0].set(); S.wake[0].set()
check(wait(lambda: S.plugged[0]) and rx_ds4.kinds[0] == "hm", "auto + HIDMaestro available: DualSense")
with S.lock:
    S.states[0].update(lx=10, ly=20, rx=30, ry=40, lt=200, rt=0, mask=BIT[304])
S.notify(0)
p = hm.pads[-1]
check(wait(lambda: p.sent and p.sent[-1][0] == 10), "the report goes through the pad's submit() (pad buffer, no copy)")
r = p.sent[-1]
check(r[:6] == bytes((10, 20, 30, 40, 200, 0)) and r[7] & 0x20 and r[8] & 0x04, f"DualSense report layout: {r[:10].hex()}")

# game feedback: output report 0x02 (motors + lightbar with validity flags)
data = bytearray(47)
data[0], data[1], data[2], data[3] = 0x03, 0x04, 40, 200
data[44:47] = bytes((1, 2, 3))
motors, rgb, trig = rx_hm.parse_output(0x02, data)
check(motors == (200, 40) and rgb == (1, 2, 3) and trig is None, f"parse_output: left = big, right = small, colour: {motors} {rgb}")
check(rx_hm.parse_output(0x02, bytearray(47)) == (None, None, None), "without validity flags nothing changes")
check(rx_hm.parse_output(0x05, data) == (None, None, None), "another report ignored")
tdata = bytearray(47)
tdata[0] = 0x08                                     # only the left trigger (L2)
tdata[21:32] = bytes([0x25, 0x30, 0, 5] + [0] * 7)
tdata[10:21] = bytes([0x21] + [9] * 10)             # no right flag - not read
check(rx_hm.parse_output(0x02, tdata) == (None, None, (bytes(tdata[21:32]), None)),
      "parse_output: L2 effect with the 0x08 flag, R2 without the flag = None")
tdata[0] = 0x0C
check(rx_hm.parse_output(0x02, tdata)[2] == (bytes(tdata[21:32]), bytes(tdata[10:21])), "both triggers (0x04 = R2, 0x08 = L2)")
class NetArray(list):              # .NET array: every element read is a call through pythonnet
    reads = 0
    def __getitem__(self, k):
        NetArray.reads += 1
        return list.__getitem__(self, k)
class OutPkt:
    Source, ReportId = rx_hm.HID_OUTPUT, rx_hm.OUT_REPORT
    def __init__(self, raw):
        self.Data = types.SimpleNamespace(ToArray=lambda: NetArray(raw))
hm_pad, got_fb = object.__new__(rx_hm.Pad), []
hm_pad.on_fb = lambda m, c, t: got_fb.append((m, c, t))
hm_pad._on_output(None, OutPkt(list(data) + [0] * 16))
check(got_fb == [((200, 40), (1, 2, 3), None)] and NetArray.reads <= len(rx_hm.OUT_BASE) + len(rx_hm.OUT_RGB),
      f"HIDMaestro output: only the needed bytes ({NetArray.reads} reads) - {got_fb}")
got_fb.clear()
NetArray.reads = 0
hm_pad._on_output(None, OutPkt(list(tdata) + [0] * 16))
check(got_fb == [(None, None, (bytes(tdata[21:32]), bytes(tdata[10:21])))] and NetArray.reads == 4 + 22,
      f"HIDMaestro output: trigger bytes read only with the flag ({NetArray.reads} reads)")
got_fb.clear()
hm_pad._on_output(None, OutPkt(list(data[:10])))
check(got_fb == [], "too short an output report - no feedback and no exception")
p.on_fb(motors, rgb)
f = F.fb[0]
check((f["large"], f["small"], f["r"], f["g"], f["b"], f["rgb"], f["set"]) == (200, 40, 1, 2, 3, True, True), f"F.game: {f}")
p.on_fb(None, (9, 9, 9))
check((f["large"], f["small"], f["r"]) == (200, 40, 9), "the colour alone does not zero the rumble")

# backend change in the window: pads recreated in the new one (no restart)
vpad(backend="vigem")
check(wait(lambda: p.closed and S.plugged[0] and rx_ds4.kinds[0] == "vigem"), "change to ViGEm: DualSense closed, DS4 attached")
vpad(backend="auto")
check(wait(lambda: rx_ds4.kinds[0] == "hm" and len(hm.pads) == 2), "i z powrotem na HIDMaestro")

# HIDMaestro create error: this pad through ViGEm, a warning in the log
S.want[0].clear(); S.wake[0].set()
check(wait(lambda: not S.plugged[0]), "detached")
class BadHM:
    def create(self, slot, on_fb):
        raise OSError("PnP timeout")
rx_hm.get = lambda path="": BadHM()
seq = ev.since(0)[-1].seq if ev.since(0) else 0
S.want[0].set(); S.wake[0].set()
check(wait(lambda: S.plugged[0]) and rx_ds4.kinds[0] == "vigem", "HIDMaestro create error: ViGEm fallback")
check(any(e.level == "warn" and "HIDMaestro" in e.text for e in ev.since(seq)), "a warning in the log")
S.want[0].clear(); S.wake[0].set()
check(wait(lambda: not S.plugged[0]), "detached")
rx_hm.get = real_get

# no DLL: auto = info only, hidmaestro = a warning
rx_ds4._hm_warned = None
seq = ev.since(0)[-1].seq if ev.since(0) else 0
vpad(backend="auto", hm_dll="/nie/ma/tego")
check(rx_ds4.hm_backend() is None and rx_ds4.hm_note, "auto without the DLL: ViGEm, the reason in hm_note")
check([e.level for e in ev.since(seq) if "HIDMaestro" in e.text] == ["info"], "auto without the DLL: info, not a warning")
seq = ev.since(0)[-1].seq if ev.since(0) else 0
vpad(backend="hidmaestro", hm_dll="/nie/ma/tego")
rx_ds4.hm_backend()
rx_ds4.hm_backend()
check([e.level for e in ev.since(seq) if "HIDMaestro" in e.text] == ["warn"], "HIDMaestro chosen without the DLL: one warning")
vpad(backend="vigem")
check(rx_ds4.hm_backend() is None and rx_ds4.hm_note is None, "vigem: no reason in the window")

# a folder in hm_dll = the DLL in that folder
import tempfile
d = tempfile.mkdtemp()
open(os.path.join(d, rx_hm.DLL), "wb").close()
check(rx_hm.find_dll(d) == os.path.join(d, rx_hm.DLL), "hm_dll may point to a folder")


# ---------- raport DualSense ----------
s = S.new_state()
s.update(lx=1, ly=2, rx=3, ry=4, lt=5, rt=255, hx=1, hy=0, mask=BIT[304] | BIT[310] | R.PS_BIT,
         gyro=(0, 0, 0), acc=(1, 2, 3), touch=((1, 7, 1919, 942), (0, 0, 0, 0)), tp=True, batt_ds5=0x10 | 5)
b = R.build_ds5_report(s, 300, False)
check(len(b) == 63 and b[:7] == bytes((1, 2, 3, 4, 5, 255, 300 & 0xFF)), f"sticks, triggers, counter: {b[:7].hex()}")
check(b[7] == (2 | 0x20) and b[8] == (0x01 | 0x04 | 0x08) and b[9] == 0x03, f"D-pad + cross, L1 + L2 and R2 digital, PS + click: {b[7:10].hex()}")
check(b[21:27] == bytes((1, 0, 2, 0, 3, 0)), "akcelerometr w bajtach 21..26")
check(b[32] == 7 and b[33] == 0x7F and (b[34] & 0x0F) == 0x07 and ((b[34] >> 4) | (b[35] << 4)) == 1079,
      "dotyk w skali DualSense (1920x1080)")
check(b[36] & 0x80, "second finger inactive")
check(b[52] == 0x15, f"battery: level 5, charging: {b[52]:#x}")
import rx_net as N
check(N.ds5_batt_byte(100, 1) == 0x2A, "full on the cable")
# the pad battery in 10 % steps (kernel: level * 10 + 5) comes back unchanged in the game: 35 % does not drop to 25 %, 75 % does not rise to 85 %
check([N.ds5_batt_byte(p, 0) & 0x0F for p in range(5, 100, 10)] == list(range(10)), "level = pct // 10 for every pad step")
check([N.ds5_batt_byte(p, 0) * 10 + 5 for p in (35, 75)] == [35, 75], "35 % and 75 % without distortion")
check(N.ds5_batt_byte(100, 0) == 10 and N.ds5_batt_byte(0, 0) == 0, "100 % = poziom 10, 0 % = poziom 0")
check(N.ds5_batt_byte(55, 1) == 0x15 and N.ds5_batt_byte(255, 0) == 0x2A, "charging; unknown level (pad on USB) = full")
b = R.build_ds5_report(s, 1, True)
check(b[9] == 0x01 and b[32] & 0x80, "mouse mode: no touchpad click and no touch in the game")


# ---------- settings.json ----------
path = os.path.join(TMP, "s_vpad.json")
cur = replace(rx_settings.defaults(), vpad=rx_settings.VpadConfig(backend="hidmaestro", fixed_slots=False, hm_dll="C:\\HM"))
rx_settings.save(cur, path)
check(rx_settings.load(path).vpad == cur.vpad, "vpad write / read (path without case change)")
import json
json.dump({"vpad": {"backend": "xinput", "fixed_slots": "tak"}}, open(path, "w"))
check(rx_settings.load(path).vpad == rx_settings.VpadConfig(), "bad values -> defaults")

S.stop.set()
for t in threads:
    t.join(3)
print("VPAD", "OK" if not fails else f"FAILURES: {len(fails)}")
if fails:
    sys.exit(1)
