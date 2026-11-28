"""Receiver: DualShock 4 and Xbox 360 pads on HIDMaestro - the Xbox state block, the DS4 / XInput output parsing, the pad classes
over a fake controller, the choice of the virtual pad, the placeholder keeping the slot's last kind.
    python3 tests/test_hm_pads.py receiver"""
import ctypes, os, sys, types
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_ds4, rx_hm, rx_report, rx_settings, rx_xbox
import rx_state as S
from proto import PF_MOTION, PF_RUMBLE, PK_DS4, PK_DUALSENSE, PK_OTHER, PK_XBOX

from chk import check                                         # noqa: E402


def vpad(**kw):
    rx_settings.apply(replace(rx_settings.cur, vpad=replace(rx_settings.VpadConfig(), **kw)))


def state(**kw):
    s = dict(S.NEUTRAL_OUT)
    s.update(kw)
    return s


# ---------- the Xbox 360 state block (rx_xbox.build_state) ----------
def unpack(b):
    return rx_xbox.STATE.unpack_from(b)


v = unpack(rx_xbox.build_state(state(), 0, False))
check(v[:6] == (0.5, 0.5, 0.5, 0.5, 0.0, 0.0) and v[6:] == (0, 0), f"neutral: sticks at exactly 0.5, triggers 0, no buttons, no hat {v}")
v = unpack(rx_xbox.build_state(state(lx=0, ly=255, rx=255, ry=0, lt=255, rt=128), 0, False))
f32 = __import__("struct").unpack("<f", __import__("struct").pack("<f", 128 / 255))[0]
check(v[:6] == (0.0, 1.0, 1.0, 0.0, 1.0, f32) and round(f32 * 255) == 128, f"full range both ways, Y grows downward as in the DualSense {v[:6]}")
check(rx_xbox.STICK[127] < 0.5 < rx_xbox.STICK[129] and len(set(rx_xbox.STICK)) == 256, "the stick table is strictly increasing around the centre")
v = unpack(rx_xbox.build_state(state(mask=BIT[304] | BIT[307] | BIT[310] | BIT[316], hx=1, hy=-1), 0, False))
check(v[6] == rx_xbox.HM_A | rx_xbox.HM_Y | rx_xbox.HM_LB | rx_xbox.HM_GUIDE and v[7] == 2,
      f"cross = A, triangle = Y, L1 = LB, PS = Guide; hat up-right = NorthEast (2): {v[6]:#x} {v[7]}")
v = unpack(rx_xbox.build_state(state(mask=BIT[308] | BIT[305] | BIT[314] | BIT[315] | BIT[317] | BIT[318] | BIT[311], hx=-1, hy=-1), 0, False))
check(v[6] == rx_xbox.HM_X | rx_xbox.HM_B | rx_xbox.HM_BACK | rx_xbox.HM_START | rx_xbox.HM_LS | rx_xbox.HM_RS | rx_xbox.HM_RB and v[7] == 8,
      "square = X, circle = B, create = Back, options = Start, L3 / R3, R1 = RB; up-left = NorthWest (8)")
check(rx_xbox.STATE.size <= rx_xbox.STATE_LEN and rx_xbox.STATE.unpack_from(bytes(range(32)), 0) and
      (rx_xbox.STATE_BUTTONS, rx_xbox.STATE_HAT) == (24, 28), "the block layout the IL fill method reads")
buf = bytearray(rx_xbox.STATE_LEN)
s_hot = state(lx=3, ly=250, mask=BIT[304], hx=0, hy=1)
for _unused in range(50):
    rx_xbox.build_state(s_hot, 0, False, buf)            # warm the button cache
before = sys.getallocatedblocks()
for _unused in range(2000):
    rx_xbox.build_state(s_hot, 0, False, buf)
check(sys.getallocatedblocks() - before < 20, f"hot path: building the Xbox state allocates nothing ({sys.getallocatedblocks() - before} blocks)")

# ---------- output parsing ----------
out = [0] * 31
out[0], out[3], out[4], out[5:8] = 0x03, 40, 200, [9, 8, 7]
check(rx_hm.parse_ds4_output(0x05, out) == ((200, 40), (9, 8, 7)), "DS4 0x05: left (byte 4) = large, right (byte 3) = small, colour 5..7")
out[0] = 0x01
check(rx_hm.parse_ds4_output(0x05, out) == ((200, 40), None), "motors only: the colour flag decides")
out[0] = 0x02
check(rx_hm.parse_ds4_output(0x05, out) == (None, (9, 8, 7)), "colour only")
check(rx_hm.parse_ds4_output(0x11, out) == (None, None) and rx_hm.parse_ds4_output(0x05, out[:5]) == (None, None),
      "another report or too short: nothing")
check(rx_hm.parse_xinput([0, 8, 255, 30, 0]) == (255, 30), "XUSB vibration packet: bytes 2 / 3 = large / small")
check(rx_hm.parse_xinput([0x00, 0x80, 0xFF, 0x10]) == (0x80, 0x10), "bare XINPUT_VIBRATION: the high bytes of the two speeds")
check(rx_hm.parse_xinput([1, 2]) is None, "too short: None")


# ---------- the pad classes over a fake controller ----------
class Event:
    def __init__(self):
        self.handlers = []
    def __iadd__(self, h):
        self.handlers.append(h)
        return self
    def __isub__(self, h):
        self.handlers.remove(h)
        return self


class Ctrl:
    def __init__(self):
        self.OutputReceived = Event()
        self.disposed = False
    def Dispose(self):
        self.disposed = True


class Pkt:
    def __init__(self, source, rid, data):
        self.Source, self.ReportId = source, rid
        self.Data = types.SimpleNamespace(ToArray=lambda: list(data))


sent = []
backend = types.SimpleNamespace(IntPtr=int, submit_fn=lambda c, p, n: sent.append(ctypes.string_at(p, n)))
fb = []
ctrl = Ctrl()
pad = rx_hm.DS4Pad(backend, ctrl, lambda *a: fb.append(a))
check(sent == [bytes(rx_report.build_report(S.NEUTRAL_OUT, 0, False))] and len(sent[0]) == rx_hm.REPORT_LEN,
      "DS4: a neutral 63-byte DS4 report is sent at creation")
rx_report.build_report(state(lx=7), 1, False, pad.buf)
pad.submit()
check(sent[-1][0] == 7, "DS4: built in place, sent from the pad's own buffer")
h = ctrl.OutputReceived.handlers[0]
h(ctrl, Pkt(rx_hm.HID_OUTPUT, 0x05, [0x03, 0, 0, 40, 200, 1, 2, 3] + [0] * 23))
h(ctrl, Pkt(rx_hm.HID_OUTPUT, 0x05, [1, 2]))
h(ctrl, Pkt(rx_hm.XINPUT_OUTPUT, 0, [0, 8, 9, 9, 0]))
check(fb == [((200, 40), (1, 2, 3))], f"DS4: rumble + lightbar from 0x05 only, short / other reports ignored {fb}")
pad.close()
check(ctrl.disposed and not ctrl.OutputReceived.handlers, "DS4: close unsubscribes and disposes")

fake_hm = types.ModuleType("HIDMaestro")
fake_hm.HMAxis = object
fake_sys = types.ModuleType("System")
fake_sys.Single = float
fake_gen = types.ModuleType("System.Collections.Generic")
fake_gen.Dictionary = {(object, float): dict}
saved = {k: sys.modules.get(k) for k in ("HIDMaestro", "System", "System.Collections.Generic")}
sys.modules.update({"HIDMaestro": fake_hm, "System": fake_sys, "System.Collections.Generic": fake_gen})
states, fb = [], []
ctrl = Ctrl()
xpad = rx_hm.X360Pad(backend, ctrl, lambda *a: fb.append(a),
                     lambda c, p, axes: states.append((c, rx_xbox.STATE.unpack(ctypes.string_at(p, rx_xbox.STATE.size)), axes)))
for k, m in saved.items():
    if m is None:
        sys.modules.pop(k, None)
    else:
        sys.modules[k] = m
check(len(states) == 1 and states[0][0] is ctrl and states[0][1][:4] == (0.5,) * 4 and states[0][2] == {},
      f"Xbox: a neutral state through the SubmitState delegate at creation, with the reused axis dictionary {states}")
rx_xbox.build_state(state(lt=255, mask=BIT[304]), 0, False, xpad.buf)
xpad.submit()
check(states[-1][1][4] == 1.0 and states[-1][1][6] == rx_xbox.HM_A and states[-1][2] is states[0][2] and not sent[2:],
      "Xbox: the state block from the pad's buffer, the same dictionary, never SubmitRawReport")
h = ctrl.OutputReceived.handlers[0]
h(ctrl, Pkt(rx_hm.XINPUT_OUTPUT, 0, [0, 8, 255, 30, 0]))
h(ctrl, Pkt(rx_hm.HID_OUTPUT, 0, [0, 8, 1, 1, 0]))
check(fb == [((255, 30), None)], f"Xbox: XInput rumble only {fb}")
xpad.close()
check(ctrl.disposed, "Xbox: close disposes")


# ---------- which virtual pad a slot gets ----------
class HM:
    def __init__(self, fail=None):
        self.made, self.fail = [], fail
    def _make(self, what, slot, on_fb):
        if what == self.fail:
            raise RuntimeError(f"{what} refused")
        self.made.append((what, slot))
        return types.SimpleNamespace(what=what, on_fb=on_fb, buf=bytearray(63), submit=lambda: None, close=lambda: None)
    def create(self, slot, on_fb, composite=False):
        return self._make("ds5", slot, on_fb)
    def create_ds4(self, slot, on_fb):
        return self._make("ds4", slot, on_fb)
    def create_x360(self, slot, on_fb):
        return self._make("x360", slot, on_fb)


rx_ds4.init()
real_get = rx_hm.get
hm = HM()
rx_hm.get = lambda path="": hm
vpad(backend="auto")
cases = (((PK_DUALSENSE, PF_MOTION), "hm", "ds5"), ((PK_DS4, PF_MOTION), "hm_ds4", "ds4"), ((PK_XBOX, PF_RUMBLE), "hm_x360", "x360"),
         ((PK_OTHER, PF_RUMBLE), "hm_x360", "x360"))
for kind, label, made in cases:
    S.pad_kind[0] = kind
    pad, got = rx_ds4.create_pad(0)
    check(got == label and pad.what == made, f"family {kind[0]} -> {label} ({got}, {pad.what})")
check(all(lbl in rx_ds4.LABELS for _k, lbl, _m in cases), "every kind has a label for the window")
S.pad_kind[0] = (PK_DS4, PF_MOTION)
pad, _label = rx_ds4.create_pad(0)
import rx_feedback as F                                               # noqa: E402
F.reset(0)
pad.on_fb((180, 20), (5, 6, 7))
check((F.fb[0]["large"], F.fb[0]["small"]) == (180, 20), "the DS4's rumble reaches the game feedback of its slot")
vpad(backend="auto", mirror=False)
S.pad_kind[0] = (PK_XBOX, PF_RUMBLE)
check(rx_ds4.create_pad(0)[1] == "hm", "mirror off: an Xbox pad gets a DualSense")
vpad(backend="auto")

# a HIDMaestro error falls back to ViGEm (until PR 2 removes it)
hm.fail = "x360"
rx_ds4.vg.VX360Gamepad = lambda: types.SimpleNamespace(register_notification=lambda cb: None, report=None, update=lambda: None)
check(rx_ds4.create_pad(0)[1] == "x360", "HIDMaestro Xbox refused: the ViGEm Xbox 360 pad")
hm.fail = None

# the placeholder (and a pad before its padkind) keeps the slot's last kind
S.pad_kind[0] = (PK_XBOX, PF_RUMBLE)
check(rx_ds4.physical_family(0) == PK_XBOX, "padkind known: its family")
S.pad_kind[0] = None
check(rx_ds4.physical_family(0) == PK_XBOX and rx_ds4.create_pad(0)[1] == "hm_x360",
      "padkind not known (placeholder / just connected): the slot's last family, no re-plug of an Xbox placeholder")
check(rx_ds4.physical_family(1) == PK_DUALSENSE, "a slot that never had a pad: a DualSense")
rx_ds4.init()
check(rx_ds4.physical_family(0) == PK_DUALSENSE, "init() starts every slot as a DualSense")
rx_hm.get = real_get
print("HM PADS OK")
