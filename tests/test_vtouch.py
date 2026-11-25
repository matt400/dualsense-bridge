"""Receiver: the virtual touchpad (rx_vtouch) - which path drives the mouse, lifting without a tap, failures.
    python3 tests/test_vtouch.py receiver"""
import os, sys, threading
sys.path.insert(0, os.path.dirname(__file__))
from chk import check, fails, wait_for
from rxenv import *  # noqa: F403
import rx_vtouch as V
from proto import T_CLICK, T_MOUSE, T_TOUCH, MOUSE_TAP


class Dev:
    def __init__(s): s.frames, s.closed, s.broken = [], False, False
    def submit(s, contacts, click):
        if s.broken: raise RuntimeError("gone")
        s.frames.append((contacts, click))
    def close(s): s.closed = True


devs = []


def make(i):
    devs.append(Dev())
    return devs[-1]


def fingers(a=None, b=None):
    f = lambda p: (0, 0, 0, 0) if p is None else (1, *p)
    feed(T_TOUCH, 0, *f(a), *f(b))


V.init(C.NUM_PADS, make)
M.set_mode(0, True)

# the first contact creates the device; the old mouse works until the next contact
calls.clear()
fingers((1, 100, 100))
check(wait_for(lambda: V.links[0].dev is not None), "the first touch creates the virtual touchpad")
check(not V.live(0), "the running contact stays on the old mouse")
feed(T_MOUSE, 0, 10, 0, 0)
check(any(c[0] == "m" for c in calls), "old mouse moves while the device is new")
fingers()
fingers((2, 200, 100))
check(V.live(0), "a new contact switches to the virtual touchpad")
V.step(0)
dev = devs[0]
check(dev.frames[-1] == ((V.Contact(2, 200, 100),), False), "the finger reaches the device")
n = len(dev.frames)
check(V.step(0) and len(dev.frames) == n + 1 and dev.frames[-1] == dev.frames[-2], "a resting finger is repeated")

# Windows does the clicks and taps: nothing through SendInput
calls.clear()
feed(T_MOUSE, 0, 5, 5, 0)
feed(T_MOUSE, 0, 0, 0, MOUSE_TAP | (1 << 4))
feed(T_CLICK, 0, 1)
V.step(0)
check(dev.frames[-1][1] is True and not calls, "the click goes to the device only")
feed(T_CLICK, 0, 0)
V.step(0)
check(dev.frames[-1][1] is False, "release")

# a short tap between two worker steps is not merged away
fingers()
V.step(0)
dev.frames.clear()
fingers((3, 10, 10))
fingers()
V.step(0)
check([len(f[0]) for f in dev.frames] == [1, 0], "down and up both arrive")
check(not V.step(0), "nothing down: no repeat")

# a mode change lifts the finger as a palm (no tap) and ignores it until it leaves
fingers((4, 50, 50))
V.step(0)
dev.frames.clear()
M.set_mode(0, False)
V.step(0)
check([[c.palm for c in f[0]] for f in dev.frames] == [[True], []], "lifted without a tap")
dev.frames.clear()
fingers((4, 60, 50))
V.step(0)
M.set_mode(0, True)
fingers((4, 70, 50))
V.step(0)
check(not dev.frames, "a finger already down never appears halfway")
fingers()
fingers((5, 70, 50))
V.step(0)
check(dev.frames[-1][0][0].id == 5, "the next contact works")

# PS + click with a finger down toggles into mouse mode without a contact
fingers()
V.step(0)
M.set_mode(0, False)
V.step(0)
dev.frames.clear()
fingers((6, 1, 1))
M.set_mode(0, True)
fingers((6, 2, 1))
V.step(0)
check(not dev.frames, "the toggling finger is ignored")
fingers()
V.step(0)

# a broken device: back to the old mouse, a warning, no new attempt at once
fingers((7, 1, 1))
dev.broken = True
V.step(0)
check(not V.live(0) and dev.closed and not V.in_use(), "failed submit -> old mouse")
fingers()
fingers((8, 1, 1))
check(len(devs) == 1, "no retry at once")
calls.clear()
feed(T_MOUSE, 0, 10, 0, 0)
check(any(c[0] == "m" for c in calls), "the old mouse moves again")

# settings change: another attempt; no HIDMaestro (factory returns None) stays quiet on the old mouse
V.reset_devices()
fingers()
fingers((9, 1, 1))
check(wait_for(lambda: V.links[0].dev is not None), "after a reset the next touch tries again")
last = devs[-1]
V.shutdown()
check(last.closed and not V.live(0), "shutdown removes the device")
fingers()
fingers((10, 1, 1))
check(len(devs) == 2, "nothing is created after shutdown")

# a release while the worker is inside the driver (the queue taken, nothing recorded as sent yet) still lifts the finger
class Interrupted(Dev):
    def submit(s, contacts, click):
        super().submit(contacts, click)
        if len(s.frames) == 1: V.release(0)


V.init(C.NUM_PADS, lambda i: Interrupted())
fingers()
fingers((12, 1, 1))
check(wait_for(lambda: V.links[0].dev is not None), "device for the release race")
fingers()
fingers((13, 1, 1))
dev = V.links[0].dev
V.step(0)
V.step(0)
check(dev.frames[-1] == V.NOTHING and not V.step(0), "no stuck finger after a release during a submit")

# a device created while the settings change is removed, and the next create waits until it is gone
gate, made = threading.Event(), []


def slow(i):
    gate.wait(5)
    made.append(Dev())
    return made[-1]


V.init(C.NUM_PADS, slow)
fingers()
fingers((14, 1, 1))
V.reset_devices()
fingers()
fingers((15, 1, 1))
gate.set()
check(wait_for(lambda: not V.links[0].creating), "the interrupted create ends")
check(len(made) == 1 and made[0].closed and V.links[0].dev is None, "a device from before the reset is not used")
fingers()
fingers((16, 1, 1))
check(wait_for(lambda: V.links[0].dev is not None) and not made[-1].closed, "the next touch creates a fresh one")

V.init(C.NUM_PADS, lambda i: None)
fingers()
fingers((11, 1, 1))
check(wait_for(lambda: not V.links[0].creating) and not V.live(0) and not V.in_use(), "no HIDMaestro: old mouse")
print("VTOUCH", "OK" if not fails else f"FAILURES: {len(fails)}")
sys.exit(1 if fails else 0)
