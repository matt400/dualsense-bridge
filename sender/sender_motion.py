"""Sender: the read loop of the pad's motion sensor (gyro + accelerometer) and the Bluetooth delay estimate.

Per report the loop feeds ``BtDelay`` from the pad's own timestamp, runs a calibration (startup or from the window),
refines the gyro bias while the pad is still (autobias) and sends the averaged motion (``_MotionStream``).
"""
from __future__ import annotations

import collections
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from evdev import ecodes as ec

import sender_calib as calib
from proto import CAL_RUNNING, LAT_NONE
from sender_config import AUTOBIAS_MAX_STEP, C, DEVICE_READ_ERRORS, US_PER_S
from sender_evread import RateMeter, pump

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

ABS_RANGE: Final = 1 << 60                              # larger than any sample: the start value of a running minimum / maximum
# the packet carries accelerometer x y z and then gyro x y z
MOTION_AXES: Final = 6
GYRO_AT: Final = 3
GYRO_AXES: Final = 3
AUTOBIAS_MIN_SAMPLES: Final = 20                        # a window with fewer samples says nothing about the bias
AUTOBIAS_STEP: Final = 0.5                              # the bias moves this far towards the window mean at once
PAD_TIMESTAMP_BITS: Final = 32                          # the pad's MSC_TIMESTAMP counter is 32-bit (wraps every ~71 min)
PAD_TIMESTAMP_MODULUS: Final = 1 << PAD_TIMESTAMP_BITS
PAD_TIMESTAMP_HALF: Final = PAD_TIMESTAMP_MODULUS >> 1  # a backwards jump larger than this is a wrap-around, not jitter


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

    def handle(self, sec: int, usec: int, etype: int, code: int, value: int) -> None:
        """Process one evdev event of the motion sensor (the raw fields, see ``pump``).

        The most frequent events (the six axes, ~8 per report at up to 1000 reports/s) are tested first.
        """
        if etype == ec.EV_ABS:
            if code < MOTION_AXES and not self.dropping:
                self.sample[code] = value
        elif etype == ec.EV_SYN:
            if code == ec.SYN_REPORT:
                if self.dropping:
                    self.dropping = False
                    self._reread_axes()
                self._on_report(sec * US_PER_S + usec)
            elif code == ec.SYN_DROPPED:
                self.dropping = True
        elif etype == ec.EV_MSC and code == ec.MSC_TIMESTAMP and not self.dropping and sec:
            self.c.bt_us = self.bt.sample(sec * US_PER_S + usec, value)    # without a kernel stamp (sec 0) there is nothing to compare

    def _reread_axes(self) -> None:
        """Read the axes from the device: an axis whose change was lost must not keep its old value."""
        dev = self.c.motion_dev
        if dev is None:
            return
        for k in range(MOTION_AXES):
            try:
                self.sample[k] = dev.absinfo(k).value
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

    def _on_report(self, event_us: int) -> None:
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
        self._send_average(event_us, now)

    def _send_average(self, event_us: int, now: float) -> None:
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
            c.mark_event(event_us)
            c.request_send(now)


async def stream_motion(c: Ctrl) -> None:
    """Read the motion sensor (gyro + accelerometer), correct the gyro bias and send the averaged motion."""
    dev = c.motion_dev
    if dev is None:                 # detached before the task got to run: nothing to read
        return
    stream = _MotionStream(c)
    await pump(dev, stream.handle)
