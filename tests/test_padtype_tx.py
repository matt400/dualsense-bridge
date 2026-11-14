"""Sender: DualShock 4 / Xbox / Switch Pro support - classification, the translation plan, the padkind packet, the stream.

    python3 tests/test_padtype_tx.py sender
"""
import asyncio
import sys

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
sys.path.insert(2, sys.path[0] + "/../tests")
from evdev import ecodes as ec

import sender_config
sender_config.init(["127.0.0.1"])
import sender_ctrl
import sender_net as net
import sender_padtype as pt
import sender_streams as st
from chk import check, fails
from fakedev import SYN, Ev
from proto import KEY_BIT, PF_MOTION, PF_RUMBLE, PK_DS4, PK_DUALSENSE, PK_OTHER, PK_XBOX, T_PADKIND


class Info:
    def __init__(self, vendor, product):
        self.vendor, self.product = vendor, product


class AbsInfo:
    def __init__(self, lo, hi, value=0):
        self.min, self.max, self.value = lo, hi, value


class Pad:
    """An evdev device with ids, capabilities and axis ranges (what classify / plan_for / Ctrl look at)."""

    path = "/dev/input/event9"
    fd = -1

    def __init__(self, vendor, product, name, abs_ranges, keys, evs=()):
        self.info, self.name = Info(vendor, product), name
        self.ranges, self.key_codes, self.evs, self.keys = abs_ranges, keys, list(evs), []

    def capabilities(self):
        return {ec.EV_KEY: list(self.key_codes), ec.EV_ABS: [(c, AbsInfo(*r)) for c, r in self.ranges.items()]}

    def absinfo(self, code):
        lo, hi = self.ranges.get(code, (0, 255))
        return AbsInfo(lo, hi, (lo + hi + 1) // 2)

    def active_keys(self):
        return self.keys

    def close(self):
        pass

    async def async_read_loop(self):
        for dt, e in self.evs:
            if dt:
                await asyncio.sleep(dt)
            yield e


S16, U8, T10 = (-32768, 32767), (0, 255), (0, 1023)
FACE = [ec.BTN_SOUTH, ec.BTN_EAST, ec.BTN_NORTH, ec.BTN_WEST, ec.BTN_TL, ec.BTN_TR, ec.BTN_SELECT, ec.BTN_START, ec.BTN_MODE,
        ec.BTN_THUMBL, ec.BTN_THUMBR]
HAT = {ec.ABS_HAT0X: (-1, 1), ec.ABS_HAT0Y: (-1, 1)}


def xpad():      # xpad (USB Xbox 360 / One): right stick RX / RY, triggers Z / RZ
    return Pad(0x045E, 0x028E, "Microsoft X-Box 360 pad", {ec.ABS_X: S16, ec.ABS_Y: S16, ec.ABS_RX: S16, ec.ABS_RY: S16,
                                                           ec.ABS_Z: T10, ec.ABS_RZ: T10, **HAT}, FACE)


def xpadneo():   # Xbox One / Series over Bluetooth: right stick Z / RZ, triggers BRAKE / GAS
    return Pad(0x045E, 0x0B13, "Xbox Wireless Controller", {ec.ABS_X: S16, ec.ABS_Y: S16, ec.ABS_Z: S16, ec.ABS_RZ: S16,
                                                            ec.ABS_BRAKE: T10, ec.ABS_GAS: T10, **HAT}, FACE)


def ds4(product=0x09CC, name="Wireless Controller"):
    return Pad(0x054C, product, name, {ec.ABS_X: U8, ec.ABS_Y: U8, ec.ABS_RX: U8, ec.ABS_RY: U8, ec.ABS_Z: U8, ec.ABS_RZ: U8, **HAT}, FACE)


def switch_pro():   # hid-nintendo: a D-pad of four buttons, digital ZL / ZR
    return Pad(0x057E, 0x2009, "Nintendo Switch Pro Controller", {ec.ABS_X: S16, ec.ABS_Y: S16, ec.ABS_RX: S16, ec.ABS_RY: S16},
               FACE + [pt.BTN_DPAD_UP, pt.BTN_DPAD_DOWN, pt.BTN_DPAD_LEFT, pt.BTN_DPAD_RIGHT, pt.BTN_TL2, pt.BTN_TR2])


# ---------- classification ----------
k = pt.classify(xpad())
check(k is not None and k.family == PK_XBOX, "xpad pad -> Xbox")
check(pt.classify(xpadneo()).family == PK_XBOX, "Xbox One over Bluetooth -> Xbox")
check(pt.classify(ds4()).family == PK_DS4, "DualShock 4 v2 -> DS4")
check(pt.classify(ds4(0x05C4)).family == PK_DS4, "DualShock 4 v1 -> DS4")
check(pt.classify(ds4(0x09CC, "Wireless Controller Motion Sensors")).family == PK_DS4, "its motion node classifies the same (grouped by uniq)")
check(pt.classify(ds4(0x0BA0, "Sony Interactive Entertainment DUALSHOCK\u00ae4 USB Wireless Adaptor")).family == PK_DS4,
      "the DS4 USB adaptor (no 'Wireless Controller' in its name) is a DS4")
check(pt.classify(Pad(0x054C, 0x0CE6, "DualSense Wireless Controller", {}, [])).family == PK_DUALSENSE, "DualSense stays a DualSense")
check(pt.classify(switch_pro()).family == PK_OTHER, "Switch Pro -> other")
check(pt.classify(Pad(0x054C, 0x0CE6, "Some Keyboard", {}, [])) is None, "a Sony id without the pad name is no pad")
check(pt.classify(Pad(0x045E, 0x0B13, "Xbox Wireless Controller", {}, [])) is None, "an Xbox media node without sticks / face buttons is no pad")
check(pt.classify(Pad(0x046D, 0xC52B, "Logitech USB Receiver", {ec.ABS_X: S16, ec.ABS_Y: S16}, FACE)) is None, "an unknown joystick is left alone")
check(pt.classify(Pad(0x0E6F, 0x0213, "Afterglow Gamepad for Xbox 360", xpad().ranges, FACE)).family == PK_XBOX, "a third-party Xbox 360 pad by its name")
check(pt.kind_of(None).family == PK_DUALSENSE, "no device (a test double) = a DualSense")
check(pt.DUALSENSE.features & PF_MOTION and pt.classify(xpad()).features == PF_RUMBLE, "features: a DualSense has motion, Xbox only rumble")
check(pt.plan_for(Pad(0x054C, 0x0CE6, "Wireless Controller", {}, []), pt.DUALSENSE) is None, "a DualSense needs no plan (the fast path)")

# ---------- plans ----------
p = pt.plan_for(xpad(), pt.classify(xpad()))
check(p.scale(ec.ABS_X, -32768) == (ec.ABS_X, 0) and p.scale(ec.ABS_X, 32767) == (ec.ABS_X, 255), "xpad: the stick ends reach 0 and 255")
check(p.scale(ec.ABS_X, 0) == (ec.ABS_X, 128), "xpad: the stick centre is 128")
check(p.scale(ec.ABS_Z, 1023) == (pt.L2, 255) and p.scale(ec.ABS_RZ, 0) == (pt.R2, 0), "xpad: triggers Z / RZ -> L2 / R2")
check(p.scale(ec.ABS_RX, 32767) == (ec.ABS_RX, 255), "xpad: the right stick stays RX / RY")
check(p.keys[ec.BTN_NORTH] == ec.BTN_WEST and p.keys[ec.BTN_WEST] == ec.BTN_NORTH, "Xbox: X and Y are swapped to the DualSense positions")
p = pt.plan_for(xpadneo(), pt.classify(xpadneo()))
check(p.scale(ec.ABS_Z, 32767) == (ec.ABS_RX, 255) and p.scale(ec.ABS_RZ, -32768) == (ec.ABS_RY, 0), "xpadneo: right stick Z / RZ -> RX / RY")
check(p.scale(ec.ABS_BRAKE, 1023) == (pt.L2, 255) and p.scale(ec.ABS_GAS, 512) == (pt.R2, 128), "xpadneo: BRAKE / GAS -> L2 / R2")
p = pt.plan_for(ds4(), pt.classify(ds4()))
check(p.scale(ec.ABS_RX, 255) == (ec.ABS_RX, 255) and p.keys[ec.BTN_NORTH] == ec.BTN_NORTH, "DS4: identity axes, the face buttons are positional")
p = pt.plan_for(switch_pro(), pt.classify(switch_pro()))
check(not p.hat_axes and p.dpad_keys[pt.BTN_DPAD_UP] == (1, -1) and p.trig_keys == {pt.BTN_TL2: pt.L2, pt.BTN_TR2: pt.R2},
      "Switch Pro: the D-pad and the triggers are buttons")
check(p.scale(ec.ABS_Z, 5) is None, "an axis the plan does not know is ignored")
w360 = Pad(0x045E, 0x0719, "Xbox 360 Wireless Receiver", {ec.ABS_X: S16, ec.ABS_Y: S16, ec.ABS_RX: S16, ec.ABS_RY: S16,
                                                          ec.ABS_Z: U8, ec.ABS_RZ: U8}, FACE + [pt.BTN_HAPPY1, pt.BTN_HAPPY2, pt.BTN_HAPPY3, pt.BTN_HAPPY4])
p = pt.plan_for(w360, pt.classify(w360))
check(not p.hat_axes and p.dpad_keys == {pt.BTN_HAPPY3: (1, -1), pt.BTN_HAPPY4: (1, 1), pt.BTN_HAPPY1: (0, -1), pt.BTN_HAPPY2: (0, 1)},
      "xpad dpad_to_buttons: BTN_TRIGGER_HAPPY1-4 are the D-pad")

# ---------- battery entry through sysfs (any driver's name) ----------
import os, tempfile
root = tempfile.mkdtemp()
bat = os.path.join(root, "class/input/event9/device/device/power_supply/sony_controller_battery_aa")
os.makedirs(bat)
open(os.path.join(bat, "capacity"), "w").write("80")
check(pt.battery_dir(xpad(), root) == bat, "battery_dir finds the pad's battery under its device, whatever its name")
check(pt.battery_dir(xpad(), tempfile.mkdtemp()) is None and pt.battery_dir(None, root) is None, "no entry / no device = None")

# ---------- Ctrl: state from a device, the padkind packet ----------
sent = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: sent.append((t, v))
c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:01", xpadneo(), None, None)
check(c.kind.family == PK_XBOX and c.plan is not None, "Ctrl picks up the kind and the plan from its device")
c.send_ident()
check((T_PADKIND, (PK_XBOX, PF_RUMBLE, 0x045E, 0x0B13)) in sent, f"send_ident announces the pad kind {sent}")
d = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:02", None, None, None)
d.send_ident()
check(any(t == T_PADKIND and v[0] == PK_DUALSENSE for t, v in sent), "a DualSense (or an unknown device) announces PK_DUALSENSE")
c.resync()
check(c.pad[ec.ABS_X] == 128 and c.pad[ec.ABS_Y] == 128 and c.pad[ec.ABS_RX] == 128, f"resync: centred sticks {c.pad}")

# ---------- the read loop ----------
A = lambda code, v: Ev(ec.EV_ABS, code, v)
K = lambda code, v: Ev(ec.EV_KEY, code, v)
evs = [(0, A(ec.ABS_X, 32767)), (0, A(ec.ABS_GAS, 1023)), (0, A(ec.ABS_HAT0X, 1)), (0, K(ec.BTN_NORTH, 1)), (0, K(ec.BTN_MODE, 1)), (0, SYN)]


async def stream():
    pad = xpadneo()
    pad.evs = evs
    x = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:03", pad, None, None)
    sent.clear()
    await st.stream_pad(x)
    await asyncio.sleep(0.02)
    return x

x = asyncio.run(stream())
check(x.pad[ec.ABS_X] == 255 and x.pad[pt.R2] == 255 and x.hat[0] == 1, f"stream: stick, trigger and hat translated {x.pad} {x.hat}")
check(x.mask == KEY_BIT[ec.BTN_WEST] | KEY_BIT[ec.BTN_MODE], f"stream: BTN_NORTH (Xbox X) -> square, Guide -> PS ({x.mask:#x})")
check(any(t == 1 for t, _v in sent), "stream: a state packet went out")


async def switch_stream():
    pad = switch_pro()
    pad.evs = [(0, K(pt.BTN_DPAD_UP, 1)), (0, K(pt.BTN_TR2, 1)), (0, SYN), (0, K(pt.BTN_DPAD_UP, 0)), (0, SYN)]
    y = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:04", pad, None, None)
    await st.stream_pad(y)
    await asyncio.sleep(0.02)
    return y

y = asyncio.run(switch_stream())
check(y.hat == [0, 0] and y.pad[pt.R2] == 255, f"Switch Pro: D-pad press and release, digital ZR is a full trigger ({y.hat} {y.pad})")

sys.exit(1 if fails else 0)
