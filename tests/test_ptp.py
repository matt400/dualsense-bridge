"""Receiver: the HIDMaestro driver's touchpad - what reaches it, when the receiver's own mouse takes over, and its life cycle.
    python3 tests/test_ptp.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from dataclasses import replace
from rxenv import *                                              # noqa
import rx_ds4, rx_mouse, rx_net, rx_ptp, rx_settings
import rx_state as S
import rx_winapi as W
from chk import check, fails


class Ctrl:
    disposed = False

    def Dispose(self) -> None:
        self.disposed = True


sent, moved = [], []
W.send_mouse = lambda *a, **k: moved.append(a)
W.send_click = lambda *a: moved.append(a)
rx_ds4.hm_backend = lambda: object()
rx_ptp.create = lambda _b, _i: rx_ptp.Device(Ctrl(), lambda _c, f, down: sent.append((f, down)))


def engine(name: str, backend: str = "auto") -> None:
    cur = rx_settings.cur
    rx_settings.apply(replace(cur, mouse=replace(cur.mouse, engine=name), vpad=replace(cur.vpad, backend=backend)))
    rx_ptp.sync(rx_mouse.release_own)


S.mouse_mode[0], S.alive[0] = True, True
engine("receiver")
check(not rx_ptp.active(0), "receiver engine: no driver touchpad")
rx_mouse.mouse_rel(0, 20, 20, 0)
check(moved, "receiver engine: the receiver moves the cursor")

engine("driver")
check(rx_ptp.active(0), "driver engine: the touchpad is created")
del moved[:], sent[:]
rx_ptp.touch(0, ((1, 5, 1919, 942), (0, 6, 0, 0)))
check(sent[-1] == (((5, 4095, 4095),), False), f"the finger that is down, scaled: {sent[-1]}")
rx_mouse.mouse_rel(0, 20, 20, 0)
rx_mouse.mouse_button(0, 1)
check(not moved and sent[-1][1], "driver engine: no SendInput, the click goes to the driver")
check(rx_ptp.devices[0].repeat(), "a resting finger is reported again")
rx_mouse.reset(0)
check(sent[-1] == ((), False), "reset lifts the finger and the click")

device = rx_ptp.devices[0]
rx_mouse.set_mode(0, False)
rx_ptp.sync(rx_mouse.release_own)
check(not rx_ptp.devices and device.ctrl.disposed, "DS4 touch mode: the device is removed")
S.mouse_mode[0] = True

engine("driver", "vigem")
check(not rx_ptp.active(0) and not rx_ptp.devices, "ViGEmBus: the receiver handles the touchpad")


def broken(_b: object, _i: int) -> None:
    raise RuntimeError("no driver")


rx_ptp.create = broken
engine("driver")
check(not rx_ptp.active(0) and "no driver" in rx_ptp.error, "a failed create: fallback with the reason")
sys.exit(1 if fails else 0)
