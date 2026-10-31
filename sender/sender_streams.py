"""Sender: read loops of the evdev devices (pad, motion, touchpad, battery) and the heartbeat.

Every device has one coroutine (``stream_pad``, ``stream_motion``, ``stream_touch``) that turns evdev events into the
fields of the pad's ``Ctrl`` object and asks it to send packets. The per-device state of a loop lives in a small class
(``_PadStream``, ``_MotionStream``, ``_TouchStream``), so that every event handler stays short and the loop itself is
only ``pump(device, handler.handle)``.
"""
from __future__ import annotations

import array
import asyncio
import collections
import fcntl
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Final

from evdev import InputDevice, InputEvent
from evdev import ecodes as ec

import sender_calib as calib
import sender_state as state
import sender_wol as wol
from proto import (BATT_UNKNOWN, CAL_RUNNING, CLICK_PRESSED, CLICK_RELEASED, CLICK_TWO_FINGERS, KEY_BIT, LAT_NONE,
                   MOUSE_SCROLL, MOUSE_TAP, MOUSE_TAP_RIGHT, STATE_CHARGING, clamp)
from sender_config import (AUTOBIAS_MAX_STEP, BURST_DELAYS, C, DEVICE_READ_ERRORS, HAT_AXES, IDLE_TP, PAD_ABS, TOUCH_H,
                           TOUCH_W, US_PER_S)
from sender_ioctl import IOC_READ, ioc

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

EVIOC_MAGIC, EVIOCGMTSLOTS_NR = "E", 0x0A               # EVIOCGMTSLOTS(len), see linux/input.h
INT32_BYTES: Final = 4
ABS_RANGE: Final = 1 << 60                              # larger than any sample: the start value of a running minimum / maximum

# ---- motion: the packet carries accelerometer x y z and then gyro x y z ----
MOTION_AXES: Final = 6
GYRO_AT: Final = 3
GYRO_AXES: Final = 3
AUTOBIAS_MIN_SAMPLES: Final = 20                        # a window with fewer samples says nothing about the bias
AUTOBIAS_STEP: Final = 0.5                              # the bias moves this far towards the window mean at once
PAD_TIMESTAMP_BITS: Final = 32                          # the pad's MSC_TIMESTAMP counter is 32-bit (wraps every ~71 min)
PAD_TIMESTAMP_MODULUS: Final = 1 << PAD_TIMESTAMP_BITS
PAD_TIMESTAMP_HALF: Final = PAD_TIMESTAMP_MODULUS >> 1  # a backwards jump larger than this is a wrap-around, not jitter

# ---- touchpad ----
TOUCH_SLOTS: Final = 2                                  # the DualSense touchpad reports two fingers
TWO_FINGERS: Final = 2                                  # two fingers = scrolling / right button (with gestures on)
TOUCH_ID_MASK: Final = 0x7F                             # the touch packet carries the tracking id in 7 bits (as the DS4 does)
TOUCH_DEFAULT_MAX_X, TOUCH_DEFAULT_MAX_Y = 1919, 1079   # the DualSense touchpad axis range when the device reports none
KIND_NONE, KIND_CURSOR, KIND_SCROLL = 0, 1, 2           # what the pending touchpad movement of a contact means

# ---- periodic jobs ----
HEARTBEAT_SLOW_EVERY: Final = 5                         # every 5th heartbeat also refreshes ident, click and touch
HEARTBEAT_QUIET_FACTOR: Final = 2.0                     # a pad is "quiet" after this many heartbeat periods without a packet
METER_MAX_GAPS: Final = 4096
METER_MIN_GAPS: Final = 10
METER_P99: Final = 0.99
BATTERY_PATH: Final = "/sys/class/power_supply/ps-controller-battery-"
BATTERY_FULL_PERCENT: Final = 100


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


class RateMeter:
    """The real report rate of a device and the gaps between reports (only with --stats).

    Attributes:
        t_prev: Time of the previous report (0 = none yet).
        gaps: The gaps since the last :meth:`report`, in seconds.
    """

    def __init__(self) -> None:
        """Start with no reports."""
        self.t_prev = 0.0
        self.gaps: list[float] = []

    def tick(self, now: float) -> None:
        """Record one report at time ``now``; the gap since the previous report is kept (at most METER_MAX_GAPS of them)."""
        if self.t_prev and len(self.gaps) < METER_MAX_GAPS:
            self.gaps.append(now - self.t_prev)
        self.t_prev = now

    def report(self) -> tuple[float, float, float] | None:
        """Summarise the reports since the last call and reset the gaps.

        Returns:
            ``(Hz, p99 gap ms, max gap ms)``, or None if there are too few reports.
        """
        gaps, self.gaps = sorted(self.gaps), []
        if len(gaps) < METER_MIN_GAPS:
            return None
        return len(gaps) / sum(gaps), gaps[int(len(gaps) * METER_P99)] * 1000, gaps[-1] * 1000


class BtDelay:
    """Bluetooth delay above the minimum.

    The pad stamps every report with its own clock (MSC_TIMESTAMP from the motion device, µs) and the kernel stamps the
    time of reception. The difference = an unknown constant (the clocks) + the radio delay; its minimum over the last
    WINDOW_US is the best case, and the excess over it is BT queueing / retransmissions. A short window: the crystal drift
    of the pad and the phone (50 ppm = 0.1 ms per 2 s) does not pass for delay. The minimum over the window uses a
    monotonic queue, O(1).
    """

    WINDOW_US = 2_000_000

    def __init__(self) -> None:
        """Start with an empty window."""
        self.epoch = 0
        self.last: int | None = None
        self.win: collections.deque[tuple[int, int]] = collections.deque()    # (kernel time, difference), differences increasing

    def sample(self, kernel_us: int, pad_us: int) -> int:
        """Feed one (kernel receive time, pad timestamp) pair.

        The pad counter is 32-bit, so a wrap-around (every ~71 min) is detected as a big backwards jump and added to
        ``epoch``. The deque holds candidates for the window minimum in increasing order: a new sample pops all larger
        ones from the back, and samples older than the window are dropped from the front, so the front is always the
        current minimum.

        Args:
            kernel_us: The kernel's receive time in µs.
            pad_us: The pad's own timestamp in µs.

        Returns:
            The delay above the window minimum in µs (never LAT_NONE).
        """
        pad_us %= PAD_TIMESTAMP_MODULUS
        if self.last is not None and pad_us < self.last - PAD_TIMESTAMP_HALF:
            self.epoch += PAD_TIMESTAMP_MODULUS
        self.last = pad_us
        diff = kernel_us - pad_us - self.epoch
        win = self.win
        while win and win[-1][1] >= diff:
            win.pop()
        win.append((kernel_us, diff))
        while win[0][0] < kernel_us - self.WINDOW_US:
            win.popleft()
        return min(LAT_NONE - 1, diff - win[0][1])


def _send_state_burst(c: Ctrl, loop: asyncio.AbstractEventLoop) -> None:
    """Send the state now and again after each of BURST_DELAYS, then give Wake-on-LAN a look at the buttons.

    The repeats protect a button change against a lost packet (a repeat of an identical state carries no latency
    measurement). After SYN_DROPPED the lost events may have contained a released PS combination, which must not fire
    Wake-on-LAN; while the receiver is silent, PS + a button held is the Wake-on-LAN trigger.
    """
    c.send_state()
    for delay in BURST_DELAYS:
        loop.call_later(delay, c.send_state, True)
    wol.on_mask(c)


async def pump(dev: InputDevice, handler: Callable[[InputEvent], None]) -> None:
    """Feed every event of ``dev`` to ``handler`` until the device goes away.

    python-evdev's ``async_read_loop()`` is convenient, but on this path it is expensive: it creates a Future for every
    single event and, for every batch the kernel hands over, registers and removes the descriptor in the event loop again
    (two ``epoll_ctl`` calls) and creates one more Future. Here the descriptor is registered once and the callback reads
    whole batches with ``dev.read()``, so an event costs one handler call.

    Whatever ends the loop is raised from here exactly as ``async_read_loop`` raised it: ``OSError`` when the device
    disappears, or the exception of a handler that has a bug. The descriptor is unregistered before the function returns
    (also on cancellation) - the caller closes the device afterwards, and a stale registration would break the next device
    that gets the same descriptor number.

    Args:
        dev: The evdev device. Anything that is not an ``InputDevice`` (a stand-in in the tests) is read with its own
            ``async_read_loop()``.
        handler: Called with every event, in the order the kernel delivered them.
    """
    if not isinstance(dev, InputDevice):
        async for e in dev.async_read_loop():
            handler(e)
        return
    loop = asyncio.get_running_loop()
    ended: asyncio.Future[None] = loop.create_future()

    def on_readable() -> None:
        try:
            for e in dev.read():
                handler(e)
        except BlockingIOError:
            pass                        # woken up with nothing to read: wait for the next wake-up
        except Exception as exc:  # noqa: BLE001 - handed to the awaiting task, which treats it like an error of the read loop
            if not ended.done():
                ended.set_exception(exc)

    loop.add_reader(dev.fd, on_readable)
    try:
        await ended
    finally:
        loop.remove_reader(dev.fd)


def _is_report(e: InputEvent) -> bool:
    """Whether an event is SYN_REPORT, which closes one complete report of a device."""
    return e.type == ec.EV_SYN and e.code == ec.SYN_REPORT


class _PadStream:
    """The state of the pad's read loop: buttons, sticks, triggers and the D-pad -> state packets.

    Events are accumulated until SYN_REPORT, which marks a complete pad report. If the report changed a button, the
    state goes out at once, followed by two delayed repeats (BURST_DELAYS) as protection against a lost packet; pure
    axis changes go through request_send, which merges them under the pad_min_dt limit. After SYN_DROPPED (the kernel
    buffer overflowed) the half-finished events are skipped until the next SYN_REPORT and the whole state is re-read.
    """

    def __init__(self, c: Ctrl) -> None:
        """Bind the loop to a pad."""
        self.c = c
        self.loop = asyncio.get_running_loop()
        self.dirty = False          # an axis or a button changed since the last SYN_REPORT
        self.burst = False          # a button changed: send with repeats
        self.dropping = False       # skipping the events up to the next SYN_REPORT
        self.meter = c.meters.setdefault("pad", RateMeter()) if C.STATS else None

    def handle(self, e: InputEvent) -> None:
        """Process one evdev event of the pad."""
        report = _is_report(e)
        if report:
            self.c.reports["pad"] += 1
            if self.meter is not None:
                self.meter.tick(time.monotonic())
        if self.dropping:
            if report:
                self._recover_from_drop()
            return
        if e.type == ec.EV_ABS:
            self._on_axis(e.code, e.value)
        elif e.type == ec.EV_KEY:
            self._on_key(e.code, e.value)
        elif e.type == ec.EV_SYN:
            if e.code == ec.SYN_DROPPED:
                self.dropping = True
            elif report and self.dirty:
                self._on_report(e)

    def _on_axis(self, code: int, value: int) -> None:
        """Store a stick / trigger axis or a D-pad axis of the current report."""
        if code in PAD_ABS:
            self.c.set_abs(code, value)
            self.dirty = True
        elif code in HAT_AXES:
            self.c.hat[code - ec.ABS_HAT0X] = clamp(value, -1, 1)
            self.dirty = True

    def _on_key(self, code: int, value: int) -> None:
        """Set or clear a button bit of the mask."""
        bit = KEY_BIT.get(code)
        if bit:
            if value:
                self.c.mask |= bit
            else:
                self.c.mask &= ~bit
            self.dirty = self.burst = True

    def _recover_from_drop(self) -> None:
        """The incomplete report is over: read the whole state from the device and send it."""
        self.dropping = self.dirty = self.burst = False
        self.c.resync()
        _send_state_burst(self.c, self.loop)

    def _on_report(self, e: InputEvent) -> None:
        """A complete report with changes: send the state (with repeats after a button change)."""
        self.dirty = False
        self.c.mark_event(e)
        self.c.note_input(time.monotonic())
        if self.burst:
            self.burst = False
            _send_state_burst(self.c, self.loop)
        else:
            self.c.request_send()


async def stream_pad(c: Ctrl) -> None:
    """Read the pad's buttons, sticks, triggers and D-pad and turn them into state packets (see ``_PadStream``)."""
    stream = _PadStream(c)
    await pump(c.pad_dev, stream.handle)


async def heartbeat() -> None:
    """Refresh the state periodically so that a lost packet never leaves the receiver with a stale state for long.

    Every HEARTBEAT_S, each pad that has not sent anything for twice that time resends its full state. Every fifth tick
    additionally resends the pad identification, the click state and the touch state (the "finger lifted" packet of a
    pad without activity would otherwise never be repeated).
    """
    tick = 0
    while True:
        now = time.monotonic()
        slow = tick % HEARTBEAT_SLOW_EVERY == 0
        for c in list(state.active.values()):
            if c.starting:                    # still being set up (rumble backend, grab): not ready to send anything
                continue
            if now - c.t_state >= C.HEARTBEAT_S * HEARTBEAT_QUIET_FACTOR:
                c.send_state(True)
            if slow:
                c.send_ident()
                c.send_click()
                c.send_touch(force=True)      # also without a touchpad: a lost "finger lifted" packet will get through
        tick += 1
        await asyncio.sleep(C.HEARTBEAT_S)


def _start_cal(c: Ctrl, now: float) -> calib.GyroCal | None:
    """Decide about the startup calibration: a pad with a saved calibration (from the receiver window) uses it at once.

    Returns:
        A ``GyroCal`` collecting samples, or None when a bias is already in place (saved, or kept from before a stream
        restart) or the startup calibration is disabled.
    """
    if c.bias_known:
        return None         # a restarted stream: gyro_bias is the saved value plus what autobias learned since - do not reset it
    stored = calib.stored(c.key)
    if stored is not None:
        c.gyro_bias, c.bias_known = list(stored["bias"]), True
        print(f"Controller {c.idx}: gyro bias {[round(b) for b in c.gyro_bias]} (saved calibration)")
        return None
    if C.CAL_S <= 0:
        return None
    print(f"Controller {c.idx}: calibrating gyro for {C.CAL_S}s - keep the pad still")
    return calib.GyroCal(now, C.CAL_S, user=False)


class _AutobiasWindow:
    """The gyro samples of the current autobias window.

    While the pad is quiet (a small spread over the window) the bias is nudged half-way towards the window mean; a window
    in which the pad moved leaves the bias alone.
    """

    def __init__(self, now: float) -> None:
        """Start an empty window at ``now``."""
        self.reset(now)

    def reset(self, now: float) -> None:
        """Throw away the samples and start a new window at ``now``."""
        self.t0, self.count = now, 0
        self.total = [0] * GYRO_AXES
        self.low = [ABS_RANGE] * GYRO_AXES
        self.high = [-ABS_RANGE] * GYRO_AXES

    def add(self, sample: Sequence[int]) -> None:
        """Add the gyro part of one motion sample (``sample[GYRO_AT:]``)."""
        for k in range(GYRO_AXES):
            x = sample[GYRO_AT + k]
            self.total[k] += x
            if x < self.low[k]:
                self.low[k] = x
            if x > self.high[k]:
                self.high[k] = x
        self.count += 1

    def settle(self, now: float, bias: list[float]) -> None:
        """At the end of the window, nudge ``bias`` towards the mean if the pad was quiet, and start a new window."""
        if now - self.t0 < C.AUTOBIAS_S:
            return
        quiet = all(self.high[k] - self.low[k] <= C.AUTOBIAS_RANGE for k in range(GYRO_AXES))
        if self.count >= AUTOBIAS_MIN_SAMPLES and quiet:
            for k in range(GYRO_AXES):
                target = self.total[k] / self.count
                if abs(target - bias[k]) <= AUTOBIAS_MAX_STEP:
                    bias[k] += (target - bias[k]) * AUTOBIAS_STEP
        self.reset(now)


class _MotionStream:
    """The state of the motion read loop (gyro + accelerometer).

    Per SYN_REPORT it (1) feeds the Bluetooth delay estimator from the pad's own timestamp, (2) runs a calibration if
    one is active or requested from the window, (3) runs autobias, (4) averages all samples since the last packet and
    sends only if the value moved past the deadband (or a send is already pending). Averaging, not taking the last
    sample, keeps the output smooth when samples arrive faster than MOTION_MIN_DT.
    """

    def __init__(self, c: Ctrl) -> None:
        """Bind the loop to a pad and start its startup calibration."""
        self.c = c
        self.sample = [0] * MOTION_AXES
        self.last_sent = 0.0
        self.cal = _start_cal(c, time.monotonic())
        self.autobias = _AutobiasWindow(time.monotonic())
        self.sums = [0] * MOTION_AXES       # the sum of the samples since the last packet: we send the AVERAGE
        self.count = 0
        self._new = [0] * MOTION_AXES       # scratch for _send_average: the averaged packet is built here, not in a new list
        self.meter = c.meters.setdefault("motion", RateMeter()) if C.STATS else None
        self.bt = BtDelay()
        self.dropping = False

    def handle(self, e: InputEvent) -> None:
        """Process one evdev event of the motion sensor."""
        if e.type == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            self.dropping = True
            return
        report = _is_report(e)
        if self.dropping:
            if not report:
                return
            self.dropping = False
            self._reread_axes()
        if e.type == ec.EV_ABS and 0 <= e.code < MOTION_AXES:
            self.sample[e.code] = e.value
        elif e.type == ec.EV_MSC and e.code == ec.MSC_TIMESTAMP:
            self.c.bt_us = self.bt.sample(e.sec * US_PER_S + e.usec, e.value)
        elif report:
            self._on_report(e)

    def _reread_axes(self) -> None:
        """Read the axes from the device: an axis whose change was lost must not keep its old value."""
        for k in range(MOTION_AXES):
            try:
                self.sample[k] = self.c.motion_dev.absinfo(k).value
            except DEVICE_READ_ERRORS:
                continue    # the axis cannot be read (the device is going away): keep the value we have

    def _start_user_calibration(self, now: float) -> None:
        """Start the calibration requested from the receiver window (the pad lies still)."""
        c = self.c
        self.cal = calib.GyroCal(now, c.cal_request, user=True)
        c.cal_request = 0
        c.cal_running = True              # cal_request is gone now; _reset_lost_stream needs this to report a lost device
        calib.report(c, CAL_RUNNING)
        # the autobias window starts over: samples from before the calibration (pad still in the hand) in the first
        # window after it moved the freshly measured bias half-way to their mean - the camera "drifted" right after calibrating
        self.autobias.reset(now)

    def _advance_calibration(self, now: float) -> None:
        """Feed the running calibration; when it has a result, store it and leave the calibration state."""
        if self.cal is None:
            return
        result = self.cal.feed(now, self.sample[GYRO_AT:])
        if result is not None:
            if self.cal.user:
                self.c.cal_running = False
            (calib.finish if self.cal.user else calib.startup_done)(self.c, result)
            self.cal = None

    def _on_report(self, e: InputEvent) -> None:
        """A complete motion report: calibrate, track the bias and (rate-limited) send the averaged motion."""
        c = self.c
        now = time.monotonic()
        c.reports["motion"] += 1
        if self.meter is not None:
            self.meter.tick(now)
        if c.cal_request:
            self._start_user_calibration(now)
        self._advance_calibration(now)
        if C.AUTOBIAS and self.cal is None:
            self.autobias.add(self.sample)
            self.autobias.settle(now, c.gyro_bias)
        if self.cal is not None and not self.cal.user:
            return                              # startup calibration: motion is not sent yet
        for k in range(MOTION_AXES):
            self.sums[k] += self.sample[k]
        self.count += 1
        if now - self.last_sent < C.MOTION_MIN_DT:
            return                              # samples arrive faster than the limit - they stay averaged until the next packet
        self.last_sent = now
        self._send_average(e)

    def _send_average(self, e: InputEvent) -> None:
        """Send the average of the samples since the last packet (bias-corrected) if it moved past the deadband.

        Runs for every packet (up to 1000 Hz), so it works in place: the sums are zeroed and the result goes to a scratch list
        that is copied into ``c.motion`` only when it is sent - no list or generator is built per call.
        """
        c = self.c
        sums, new, old, bias, n = self.sums, self._new, c.motion, c.gyro_bias, self.count
        moved = c.pending
        for k in range(MOTION_AXES):
            mean = sums[k] / n
            sums[k] = 0
            if k < GYRO_AT:                       # the accelerometer first, then the gyro (bias-corrected)
                v, deadband = round(mean), C.DB_ACC
            else:
                v, deadband = round(mean - bias[k - GYRO_AT]), C.DB_GYRO
            new[k] = v
            if abs(v - old[k]) > deadband:
                moved = True
        self.count = 0
        if moved:
            old[:] = new
            c.mark_event(e)
            c.request_send()


async def stream_motion(c: Ctrl) -> None:
    """Read the motion sensor (gyro + accelerometer), correct the gyro bias and send the averaged motion."""
    dev = c.motion_dev
    if dev is None:                 # detached before the task got to run: nothing to read
        return
    stream = _MotionStream(c)
    await pump(dev, stream.handle)


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


def _apply_touch_axis(e: InputEvent, slot: int, fingers: list[_Finger], prev: list[tuple[int, int] | None]) -> int:
    """Fold one EV_ABS multitouch event into the per-finger table.

    Returns:
        The (possibly changed) current slot.
    """
    if e.code == ec.ABS_MT_SLOT:
        return e.value
    if 0 <= slot < TOUCH_SLOTS:
        finger = fingers[slot]
        if e.code == ec.ABS_MT_TRACKING_ID:
            prev[slot] = None              # a new / lifted finger: no delta, the position stays (as in the kernel)
            finger.on = e.value >= 0
            if finger.on:
                finger.id = e.value & TOUCH_ID_MASK
        elif e.code == ec.ABS_MT_POSITION_X:
            finger.x = e.value
        elif e.code == ec.ABS_MT_POSITION_Y:
            finger.y = e.value
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


def _finger_packet(finger: _Finger, max_x: int, max_y: int) -> tuple[int, int, int, int]:
    """One finger of the touch packet: ``(active, id, x, y)`` in the DS4 touchpad scale."""
    pos = finger.position()
    if pos is None:
        return 0, finger.id, 0, 0
    return 1, finger.id, clamp(pos[0] * TOUCH_W // max_x, 0, TOUCH_W), clamp(pos[1] * TOUCH_H // max_y, 0, TOUCH_H)


def _frame_touch_packet(c: Ctrl, fingers: list[_Finger], max_x: int, max_y: int, last_touch: tuple) -> tuple:
    """DS4/DSU part of a touchpad frame: send the touch packet unless it only repeats the last one.

    The deadzone checks BOTH fingers (the second must not be swallowed by the first).

    Returns:
        The touch state to remember as ``last_touch``.
    """
    touch = tuple(_finger_packet(f, max_x, max_y) for f in fingers)
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
    except (OSError, AttributeError, TypeError, ValueError):
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
    try:
        pressed = ec.BTN_LEFT in c.touch_dev.active_keys()
    except (OSError, AttributeError):
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

    def handle(self, e: InputEvent) -> None:
        """Process one evdev event of the touchpad."""
        if self.dropping:
            if not _is_report(e):
                return                  # an incomplete frame after a buffer overflow - skipped
            self._recover_from_drop()
        if e.type == ec.EV_ABS:
            self.slot = _apply_touch_axis(e, self.slot, self.fingers, self.prev)
        elif e.type == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            self.dropping = True
        elif e.type == ec.EV_KEY and e.code == ec.BTN_LEFT:
            self._on_click(bool(e.value))
        elif _is_report(e):
            self._on_report()

    def _recover_from_drop(self) -> None:
        """The incomplete frame is over: rebuild the finger table from the kernel, then handle the report as usual."""
        c = self.c
        self.dropping = False
        _resync_touch(c, self.fingers, self.prev)
        try:
            self.slot = c.touch_dev.absinfo(ec.ABS_MT_SLOT).value
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


async def stream_battery(c: Ctrl) -> None:
    """Poll the kernel power-supply entry of the pad every BATT_POLL_S: battery percentage and charging flag.

    The sysfs name contains the pad's MAC. When the entry cannot be read (USB pad, no driver support) the level is
    reported as BATT_UNKNOWN.
    """
    path = f"{BATTERY_PATH}{(c.key or '').lower()}"
    while True:
        try:
            with open(path + "/capacity") as f:
                percent = int(f.read().strip())
            with open(path + "/status") as f:
                status = f.read().strip().lower()
            c.batt = clamp(percent, 0, BATTERY_FULL_PERCENT)
            c.flags = STATE_CHARGING if status in ("charging", "full") else 0
        except (OSError, ValueError):
            c.batt, c.flags = BATT_UNKNOWN, 0
        await asyncio.sleep(C.BATT_POLL_S)
