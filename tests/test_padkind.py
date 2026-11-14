"""Receiver: the virtual pad follows the physical one - the padkind packet, the Xbox 360 report, the choice of the virtual pad,
re-plugging when another kind of pad arrives in a slot, the mirror setting.
    python3 tests/test_padkind.py receiver"""
import os, sys, threading, time
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_ds4, rx_settings, rx_xbox
import rx_state as S
from proto import PF_MOTION, PF_RUMBLE, PK_DS4, PK_DUALSENSE, PK_OTHER, PK_XBOX, T_PADKIND, PADKIND_SENDER

from chk import check, fails                                  # noqa: E402


def vpad(**kw):
    rx_settings.apply(replace(rx_settings.cur, vpad=replace(rx_settings.VpadConfig(), **kw)))


def wait(cond, t=3.0):
    end = time.monotonic() + t
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


# ---------- the Xbox 360 report ----------
class Rep:
    pass


def report(**kw):
    s = {"mask": 0, "hx": 0, "hy": 0, "lt": 0, "rt": 0, "lx": 128, "ly": 128, "rx": 128, "ry": 128}
    s.update(kw)
    r = Rep()
    rx_xbox.fill(r, s)
    return r

r = report()
check((r.wButtons, r.sThumbLX, r.sThumbLY, r.sThumbRX, r.sThumbRY, r.bLeftTrigger, r.bRightTrigger) == (0, 0, 0, 0, 0, 0, 0), "a centred pad = a neutral report")
r = report(lx=0, ly=0, rx=255, ry=255, lt=200, rt=7)
check((r.sThumbLX, r.sThumbLY, r.sThumbRX, r.sThumbRY) == (-32768, 32767, 32767, -32767), "sticks: full range, up is positive")
check((r.bLeftTrigger, r.bRightTrigger) == (200, 7), "triggers pass through")
r = report(mask=BIT[304] | BIT[307] | BIT[310] | BIT[316], hx=1, hy=-1)
check(r.wButtons == rx_xbox.BTN_A | rx_xbox.BTN_Y | rx_xbox.LEFT_SHOULDER | rx_xbox.GUIDE | rx_xbox.DPAD_UP | rx_xbox.DPAD_RIGHT,
      f"buttons by position: cross = A, triangle = Y, L1 = LB, PS = Guide, hat up-right ({r.wButtons:#x})")
r = report(mask=BIT[308] | BIT[305] | BIT[314] | BIT[315] | BIT[317] | BIT[318] | BIT[311])
check(r.wButtons == rx_xbox.BTN_X | rx_xbox.BTN_B | rx_xbox.BACK | rx_xbox.START | rx_xbox.LEFT_THUMB | rx_xbox.RIGHT_THUMB | rx_xbox.RIGHT_SHOULDER,
      "square = X, circle = B, create = Back, options = Start, L3 / R3, R1 = RB")


# ---------- ViGEm stubs ----------
log = []                                      # ("x360" | "ds4", id, event)


class X360:
    n = 0

    def __init__(self):
        X360.n += 1
        self.id, self.report, self.updates = X360.n, Rep(), 0
        log.append(("x360", self.id, "plug"))

    def register_notification(self, cb):
        self.cb = cb

    def unregister_notification(self):
        log.append(("x360", self.id, "unplug"))

    def update(self):
        self.updates += 1


class DS4:
    n = 0

    def __init__(self):
        DS4.n += 1
        self.id = DS4.n
        log.append(("ds4", self.id, "plug"))

    def register_notification(self, cb):
        pass

    def unregister_notification(self):
        log.append(("ds4", self.id, "unplug"))

    def update_extended_report(self, rep):
        pass


rx_ds4.vg.VX360Gamepad, rx_ds4.vg.VDS4Gamepad = X360, DS4
rx_ds4.init()
vpad(backend="vigem")


# ---------- the packet ----------
check(S.pad_kind[0] is None, "no padkind packet yet = unknown (a DualSense)")
feed(T_PADKIND, 0, PK_XBOX, PF_RUMBLE, 0x045E, 0x0B13)
check(S.pad_kind[0] == (PK_XBOX, PF_RUMBLE), f"the packet is stored ({S.pad_kind[0]})")
check(rx_ds4.physical_family(0) == PK_XBOX and rx_ds4.physical_family(1) == PK_DUALSENSE, "the slot's family; an empty slot = a DualSense")
check(rx_ds4.virtual_family(0) == PK_XBOX, "mirror on (the default): the virtual pad is an Xbox pad")
vpad(backend="vigem", mirror=False)
check(rx_ds4.virtual_family(0) == PK_DUALSENSE, "mirror off: always a DualSense")
vpad(backend="vigem")

# ---------- which virtual pad ----------
def kind_for(kind):
    S.pad_kind[0] = kind
    pad, label = rx_ds4.create_pad(0)
    rx_ds4.close_pad(pad)
    return label

check(kind_for((PK_XBOX, PF_RUMBLE)) == "x360", "Xbox pad -> an Xbox 360 pad")
check(kind_for((PK_OTHER, PF_RUMBLE)) == "x360", "another pad (Switch Pro) -> an Xbox 360 pad")
check(kind_for((PK_DS4, PF_MOTION)) == "vigem", "DualShock 4 -> a DS4")
check(kind_for((PK_DUALSENSE, 0)) == "vigem", "DualSense with the ViGEm backend -> a DS4 (as before)")
check(kind_for(None) == "vigem", "an older sender (no packet) -> as before")
vpad(backend="vigem", mirror=False)
check(kind_for((PK_XBOX, PF_RUMBLE)) == "vigem", "mirror off: an Xbox pad is served as before")
vpad(backend="vigem")
real_vg, rx_ds4.vg = rx_ds4.vg, None
try:
    rx_ds4.create_pad(0)
    check(False, "without ViGEmBus an Xbox pad cannot get a virtual pad (HIDMaestro is not there either)")
except RuntimeError:
    check(True, "without ViGEmBus and HIDMaestro there is no virtual pad (an error the slot thread reports)")
rx_ds4.vg = real_vg


class BrokenX360:
    def __init__(self):
        raise OSError("vigem_target_add failed")

rx_ds4.vg.VX360Gamepad = BrokenX360
S.pad_kind[0] = (PK_XBOX, PF_RUMBLE)
pad, label = rx_ds4.create_pad(0)
check(label == "vigem", f"a failing Xbox 360 target falls back to the DualSense path (here ViGEm DS4), no error loop ({label})")
rx_ds4.close_pad(pad)
rx_ds4.vg.VX360Gamepad = X360
g = rx_ds4.gen
vpad(backend="vigem", mirror=False)
vpad(backend="vigem")
check(rx_ds4.gen == g, "the mirror switch does not re-plug every pad (gen unchanged)")

# ---------- a pad's virtual pad: plugged, driven, replaced when another kind arrives ----------
S.pad_kind[0] = None
S.sender_info[0] = (PADKIND_SENDER - 1, 0, 0)               # an older sender never sends the packet: no waiting for it
log.clear()
t = threading.Thread(target=rx_ds4.slot_thread, args=(0,), daemon=True)
t.start()
S.want[0].set()
check(wait(lambda: rx_ds4.kinds[0] == "vigem"), "a pad of an older sender gets a DS4")
feed(T_PADKIND, 0, PK_XBOX, PF_RUMBLE, 0x045E, 0x0B13)
check(wait(lambda: rx_ds4.kinds[0] == "x360"), f"the padkind packet re-plugs the slot as an Xbox 360 pad ({rx_ds4.kinds[0]})")
check(any(e[0] == "ds4" and e[2] == "unplug" for e in log), "the DS4 was unplugged first")
x = rx_ds4.outs[0].pad.pad if rx_ds4.outs[0] is not None and rx_ds4.outs[0].pad is not None else None
S.states[0]["lx"], S.states[0]["rt"] = 255, 99
S.notify(0)
check(wait(lambda: x is not None and x.updates > 0 and x.report.sThumbLX == 32767 and x.report.bRightTrigger == 99),
      "state changes reach the Xbox 360 report")
vpad(backend="vigem", mirror=False)
check(wait(lambda: rx_ds4.kinds[0] == "vigem"), "turning the mirror off re-plugs a DS4")
vpad(backend="vigem")
check(wait(lambda: rx_ds4.kinds[0] == "x360"), "turning it on again returns the Xbox 360 pad")
S.want[0].clear()
S.wake[0].set()
check(wait(lambda: rx_ds4.kinds[0] is None), "the pad leaves: the virtual pad is unplugged")
S.stop.set()
S.wake[0].set()

sys.exit(1 if fails else 0)
