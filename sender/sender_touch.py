"""Sender: the read loop of the pad's touchpad - DS4 / DSU touch packets, the touchpad click and the mouse (cursor, tap, scrolling).

The rules and the way it works are described in ``_TouchStream``; ``stream_touch`` is only ``pump(device, handler.handle)``.
"""
from __future__ import annotations

import array
import asyncio
import fcntl
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, TypeAlias

from evdev import InputDevice
from evdev import ecodes as ec

from proto import CLICK_PRESSED, CLICK_RELEASED, CLICK_TWO_FINGERS, MOUSE_SCROLL, MOUSE_TAP, MOUSE_TAP_RIGHT, clamp
from sender_config import BURST_DELAYS, C, DEVICE_READ_ERRORS, IDLE_TP, TOUCH_H, TOUCH_W
from sender_evread import EVIOC_MAGIC, INT32_BYTES, is_report, pump
from sender_ioctl import IOC_READ, ioc

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

EVIOCGMTSLOTS_NR: Final = 0x0A                          # EVIOCGMTSLOTS(len), see linux/input.h
TOUCH_SLOTS: Final = 2                                  # the DualSense touchpad reports two fingers
TWO_FINGERS: Final = 2                                  # two fingers = scrolling / right button (with gestures on)
TOUCH_ID_MASK: Final = 0x7F                             # the touch packet carries the tracking id in 7 bits (as the DS4 does)
TOUCH_DEFAULT_MAX_X, TOUCH_DEFAULT_MAX_Y = 1919, 1079   # the DualSense touchpad axis range when the device reports none
KIND_NONE, KIND_CURSOR, KIND_SCROLL = 0, 1, 2           # what the pending touchpad movement of a contact means


def _eviocgmtslots(nslots: int) -> int:
    """Build the EVIOCGMTSLOTS ioctl number for a buffer of (axis code + one value per slot) 4-byte ints."""
    return ioc(IOC_READ, EVIOC_MAGIC, EVIOCGMTSLOTS_NR, INT32_BYTES * (nslots + 1))


def read_mt(dev: InputDevice, nslots: int = 2) -> list[tuple[int, int, int]]:
    """Read the current finger state straight from the kernel.

    Needed after SYN_DROPPED - the events were lost, but the kernel still knows which fingers are down and where.
    EVIOCGMTSLOTS returns, for one axis code at a time, the value of that axis in every multitouch slot, so the ioctl
    is issued once per axis (tracking id, X, Y) and the results are zipped per slot.

    Args:
        dev: The touchpad device.
        nslots: How many slots to return.

    Returns:
        ``[(tracking_id, x, y), ...]`` for slots ``0..nslots-1``; a tracking id of -1 means no finger.
    """
    try:
        total = max(nslots, dev.absinfo(ec.ABS_MT_SLOT).max + 1)
    except DEVICE_READ_ERRORS:
        total = nslots
    values = {}
    for code in (ec.ABS_MT_TRACKING_ID, ec.ABS_MT_POSITION_X, ec.ABS_MT_POSITION_Y):
        buf = array.array("i", [code] + [0] * total)
        fcntl.ioctl(dev.fd, _eviocgmtslots(total), buf, True)
        values[code] = buf[1:]
    return [(values[ec.ABS_MT_TRACKING_ID][k], values[ec.ABS_MT_POSITION_X][k], values[ec.ABS_MT_POSITION_Y][k])
            for k in range(nslots)]


class _Finger:
    """One finger of the touchpad as the kernel slot knows it.

    The position survives lifting the finger: the kernel skips an axis event when the value in the slot did not change -
    also for a NEW finger - so a new finger placed at the same x or y (e.g. at an edge: 0 / max) takes it from here; reset to
    zero it lost such a touch and tap.
    """

    __slots__ = ("id", "on", "x", "y")

    def __init__(self, on: bool = False, finger_id: int = 0, x: int | None = None, y: int | None = None) -> None:
        """Create a finger; ``x`` / ``y`` of None mean "position unknown"."""
        self.on, self.id, self.x, self.y = on, finger_id, x, y

    def position(self) -> tuple[int, int] | None:
        """Return the position of a finger that is down, or None when it is up or its position is unknown."""
        if self.on and self.x is not None and self.y is not None:
            return self.x, self.y
        return None


def _same_finger(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """Whether a finger (on, id, x, y) has not changed enough to be worth sending (deadzone)."""
    if a[0] != b[0]:
        return False
    if not a[0]:
        return True
    return a[1] == b[1] and abs(a[2] - b[2]) < C.TOUCH_DEADZONE and abs(a[3] - b[3]) < C.TOUCH_DEADZONE


class _Session:
    """One contact with the touchpad: from the placing of the first finger to the lifting of the last one."""

    __slots__ = ("clicked", "kind", "max_n", "moved", "pend", "scrolled", "started", "t0")

    def __init__(self, now: float) -> None:
        """Start a contact at ``now``."""
        self.t0, self.moved, self.max_n = now, 0, 0
        self.started = self.scrolled = self.clicked = False
        self.kind = KIND_NONE
        self.pend = [0, 0]            # movement held back until touch_start_move is exceeded


def _touch_range(dev: InputDevice | None) -> tuple[int, int]:
    """Return the largest X / Y the touchpad reports; the DualSense defaults when there is no device or it cannot say."""
    if dev is None:
        return TOUCH_DEFAULT_MAX_X, TOUCH_DEFAULT_MAX_Y
    try:
        return (dev.absinfo(ec.ABS_MT_POSITION_X).max or TOUCH_DEFAULT_MAX_X,
                dev.absinfo(ec.ABS_MT_POSITION_Y).max or TOUCH_DEFAULT_MAX_Y)
    except DEVICE_READ_ERRORS:
        return TOUCH_DEFAULT_MAX_X, TOUCH_DEFAULT_MAX_Y


def _apply_touch_axis(code: int, value: int, slot: int, fingers: list[_Finger], prev: list[tuple[int, int] | None]) -> int:
    """Fold one EV_ABS multitouch event (``code``, ``value``) into the per-finger table.

    Returns:
        The (possibly changed) current slot.
    """
    if code == ec.ABS_MT_SLOT:
        return value
    if 0 <= slot < TOUCH_SLOTS:
        finger = fingers[slot]
        if code == ec.ABS_MT_TRACKING_ID:
            prev[slot] = None              # a new / lifted finger: no delta, the position stays (as in the kernel)
            finger.on = value >= 0
            if finger.on:
                finger.id = value & TOUCH_ID_MASK
        elif code == ec.ABS_MT_POSITION_X:
            finger.x = value
        elif code == ec.ABS_MT_POSITION_Y:
            finger.y = value
    return slot


def _frame_deltas(fingers: list[_Finger], prev: list[tuple[int, int] | None]) -> list[tuple[int, int]]:
    """Movement of every placed finger since its previous known position (0, 0 for a fresh finger); updates ``prev``."""
    deltas = []
    for k, finger in enumerate(fingers):
        now = finger.position()
        if now is not None:
            before = prev[k]
            deltas.append((now[0] - before[0], now[1] - before[1]) if before else (0, 0))
            prev[k] = now
    return deltas


def _frame_mouse(c: Ctrl, fingers: list[_Finger], prev: list[tuple[int, int] | None], sess: _Session) -> None:
    """Mouse part of a touchpad frame.

    Finger deltas from the positions known in this frame become a cursor move (1 finger) or a scroll (2 fingers), sent
    once the contact has moved past touch_start_move.
    """
    deltas = _frame_deltas(fingers, prev)
    n = len(deltas)
    sess.max_n = max(sess.max_n, n)
    sess.moved += sum(abs(dx) + abs(dy) for dx, dy in deltas)
    kind = dx = dy = 0
    if C.GESTURES and n >= TWO_FINGERS:
        kind, dx, dy = KIND_SCROLL, deltas[0][0] + deltas[1][0], deltas[0][1] + deltas[1][1]
    elif n >= 1 and not (C.GESTURES and sess.max_n >= TWO_FINGERS):
        kind, (dx, dy) = KIND_CURSOR, deltas[0]
    if kind and (dx or dy):
        if kind != sess.kind:
            sess.kind, sess.pend = kind, [0, 0]
        sess.pend[0] += dx
        sess.pend[1] += dy
        if sess.started or sess.moved >= C.TOUCH_START_MOVE:
            sess.started = True
            if kind == KIND_SCROLL:
                sess.scrolled = True
            c.send_mouse(sess.pend[0], sess.pend[1], MOUSE_SCROLL if kind == KIND_SCROLL else 0)
            sess.pend = [0, 0]


TouchPacket: TypeAlias = tuple[tuple[int, int, int, int], tuple[int, int, int, int]]   # (active, id, x, y) of both fingers


def _finger_packet(finger: _Finger, max_x: int, max_y: int) -> tuple[int, int, int, int]:
    """One finger of the touch packet: ``(active, id, x, y)`` in the DS4 touchpad scale."""
    pos = finger.position()
    if pos is None:
        return 0, finger.id, 0, 0
    return 1, finger.id, clamp(pos[0] * TOUCH_W // max_x, 0, TOUCH_W), clamp(pos[1] * TOUCH_H // max_y, 0, TOUCH_H)


def _frame_touch_packet(c: Ctrl, fingers: list[_Finger], max_x: int, max_y: int, last_touch: TouchPacket) -> TouchPacket:
    """DS4/DSU part of a touchpad frame: send the touch packet unless it only repeats the last one.

    The deadzone checks BOTH fingers (the second must not be swallowed by the first).

    Returns:
        The touch state to remember as ``last_touch``.
    """
    touch = (_finger_packet(fingers[0], max_x, max_y), _finger_packet(fingers[1], max_x, max_y))
    if touch != last_touch and not (_same_finger(touch[0], last_touch[0]) and _same_finger(touch[1], last_touch[1])):
        c.touch = touch
        c.send_touch()
        return touch
    return last_touch


def _frame_tap(c: Ctrl, sess: _Session, now: float) -> None:
    """All fingers are up: decide whether the contact was a tap.

    A short, nearly motionless contact that no physical click cancelled is a tap (1 finger = left button, 2 fingers =
    right button unless it already scrolled).
    """
    if not (C.TAP_CLICK and not sess.clicked and now - sess.t0 < C.TAP_MAX_S and sess.moved < C.TAP_MAX_MOVE):
        return
    if C.GESTURES and sess.max_n >= TWO_FINGERS:
        if not sess.scrolled:
            c.send_tap(MOUSE_TAP_RIGHT)
    elif sess.max_n >= 1:
        c.send_tap(MOUSE_TAP)


def _kernel_slots(dev: InputDevice | None, n: int) -> Sequence[tuple[int, int | None, int | None]]:
    """Read the multitouch slot state from the kernel ``[(tracking_id, x, y)]``.

    Without the ioctl (another platform, tests) the fingers are lifted and the position is unknown (None).
    """
    if dev is None:
        return [(-1, None, None)] * n
    try:
        return read_mt(dev, n)
    except (OSError, ValueError):     # ValueError: a closed device (evdev sets fd = -1 on close)
        return [(-1, None, None)] * n


def _click_state(fingers: list[_Finger], pressed: bool) -> int:
    """The touchpad button state for the click packet: released, pressed, or pressed with two fingers (right button)."""
    if not pressed:
        return CLICK_RELEASED
    return CLICK_TWO_FINGERS if C.GESTURES and fingers[0].on and fingers[1].on else CLICK_PRESSED


def _resync_touch(c: Ctrl, fingers: list[_Finger], prev: list[tuple[int, int] | None]) -> None:
    """Rebuild the fingers and the touchpad button from the kernel state; without the ioctl (another platform) all are lifted."""
    for k, (tracking_id, x, y) in enumerate(_kernel_slots(c.touch_dev, len(fingers))):
        finger = fingers[k]
        finger.on, finger.x, finger.y = tracking_id >= 0, x, y       # the position of a lifted finger too
        if tracking_id >= 0:
            finger.id = tracking_id & TOUCH_ID_MASK
        prev[k] = None                # no delta in the first frame - the cursor will not jump
    if c.touch_dev is None:
        return
    try:
        pressed = ec.BTN_LEFT in c.touch_dev.active_keys()
    except OSError:
        return
    if bool(c.tp) != pressed:
        c.tp = _click_state(fingers, pressed)
        c.send_click()


class _TouchStream:
    """The state of the touchpad read loop.

    Mouse rules: 1 finger = cursor (it only starts moving after touch_start_move, so a tap does not jerk the cursor),
    2 fingers = scrolling, and until all fingers are lifted the cursor stays put (lifting fingers one by one does
    not fire a click or a jump). A short tap with 1 finger = left button, with 2 fingers = right button.

    How it works: the kernel sends multitouch events (slot selection, tracking id, X, Y); they update a small per-finger
    table, and on every SYN_REPORT the frame is processed - deltas from the previous known position drive the mouse
    (``_frame_mouse``), the same table drives the DS4 touch packet (``_frame_touch_packet``, with a per-finger deadzone)
    and the end of the contact decides whether it was a tap (``_frame_tap``). After SYN_DROPPED the table is rebuilt from
    the kernel state.
    """

    def __init__(self, c: Ctrl) -> None:
        """Bind the loop to a pad and read the fingers that are already down."""
        self.c = c
        self.loop = asyncio.get_running_loop()
        self.max_x, self.max_y = _touch_range(c.touch_dev)
        self.slot = 0
        # A finger that is already down (a pad connected with a palm on the touchpad, a touchpad attached later) is also
        # "on": the kernel will not send it ABS_MT_TRACKING_ID until it is lifted - without this its movement gave neither a
        # cursor nor DS4 touch. Such a contact does not end with a tap (as after SYN_DROPPED).
        self.fingers = [_Finger(tid >= 0, tid & TOUCH_ID_MASK if tid >= 0 else 0, x, y)
                        for tid, x, y in _kernel_slots(c.touch_dev, TOUCH_SLOTS)]
        self.prev: list[tuple[int, int] | None] = [None] * TOUCH_SLOTS   # previous finger position; None = a fresh finger, no delta
        self.sess: _Session | None = None
        self.last_touch = IDLE_TP
        self.dropping = False
        self.resynced = any(f.on for f in self.fingers)

    def handle(self, sec: int, usec: int, etype: int, code: int, value: int) -> None:
        """Process one evdev event of the touchpad (the raw fields, see ``pump``; the timestamp is not used)."""
        if self.dropping:
            if not is_report(etype, code):
                return                  # an incomplete frame after a buffer overflow - skipped
            self._recover_from_drop()
        if etype == ec.EV_ABS:
            self.slot = _apply_touch_axis(code, value, self.slot, self.fingers, self.prev)
        elif etype == ec.EV_SYN and code == ec.SYN_DROPPED:
            self.dropping = True
        elif etype == ec.EV_KEY and code == ec.BTN_LEFT:
            self._on_click(bool(value))
        elif is_report(etype, code):
            self._on_report()

    def _recover_from_drop(self) -> None:
        """The incomplete frame is over: rebuild the finger table from the kernel, then handle the report as usual."""
        c = self.c
        self.dropping = False
        _resync_touch(c, self.fingers, self.prev)
        try:
            self.slot = c.touch_dev.absinfo(ec.ABS_MT_SLOT).value if c.touch_dev is not None else 0
        except DEVICE_READ_ERRORS:
            self.slot = 0
        if self.sess is not None:
            self.sess.clicked = True    # this contact can no longer end with a tap
        self.resynced = True

    def _on_click(self, pressed: bool) -> None:
        """The physical touchpad button: sent as the click packet, repeated against a lost packet."""
        c = self.c
        c.tp = _click_state(self.fingers, pressed)      # held until release; two fingers = the right button in mouse mode
        if pressed and self.sess is not None:
            self.sess.clicked = True    # a physical click during the touch cancels the tap until the fingers are lifted
        c.send_click()
        for delay in BURST_DELAYS:
            self.loop.call_later(delay, c.send_click)

    def _on_report(self) -> None:
        """A complete touchpad frame: mouse movement, the touch packet, and the tap decision when the fingers are up."""
        c = self.c
        on = any(f.on for f in self.fingers)
        now = time.monotonic()
        if on:
            c.last_input = now
        if self.sess is None:
            if not on:
                # nothing is down after the resync (or the finger that was down at attach time went up): the flag has no
                # contact to mark any more - left set it swallowed the next real tap
                self.resynced = False
                return
            self.sess = _Session(now)
            if self.resynced:
                self.sess.clicked = True    # a finger placed during lost events - no tap
        self.resynced = False
        _frame_mouse(c, self.fingers, self.prev, self.sess)
        self.last_touch = _frame_touch_packet(c, self.fingers, self.max_x, self.max_y, self.last_touch)
        if not on:
            _frame_tap(c, self.sess, now)
            self.sess = None


async def stream_touch(c: Ctrl) -> None:
    """Turn touchpad events into DS4/DSU touch packets and mouse packets (cursor, tap, 2-finger scrolling).

    See ``_TouchStream`` for the rules and the way it works.
    """
    dev = c.touch_dev
    if dev is None:                 # detached before the task got to run: nothing to read
        return
    stream = _TouchStream(c)
    await pump(dev, stream.handle)
