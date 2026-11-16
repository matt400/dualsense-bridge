"""Sender: the read loop of the pad's main evdev device - buttons, sticks, triggers, D-pad -> state packets.

The state of the loop lives in ``_PadStream``; ``stream_pad`` is only ``pump(device, handler.handle)``.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from evdev import ecodes as ec

import sender_wol as wol
from proto import KEY_BIT, clamp
from sender_config import BURST_DELAYS, C, HAT_AXES, PAD_ABS, US_PER_S
from sender_evread import RateMeter, pump

if TYPE_CHECKING:
    from sender_ctrl import Ctrl


def _send_state_burst(c: Ctrl, loop: asyncio.AbstractEventLoop, now: float | None = None) -> None:
    """Send the state now and again after each of BURST_DELAYS, then give Wake-on-LAN a look at the buttons.

    The repeats protect a button change against a lost packet (a repeat of an identical state carries no latency
    measurement). After SYN_DROPPED the lost events may have contained a released PS combination, which must not fire
    Wake-on-LAN; while the receiver is silent, PS + a button held is the Wake-on-LAN trigger. ``now`` is the
    caller's ``time.monotonic()`` reading, if it has one.
    """
    c.send_state(now=now)
    for delay in BURST_DELAYS:
        loop.call_later(delay, c.send_state, True)
    wol.on_mask(c)


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

    def handle(self, sec: int, usec: int, etype: int, code: int, value: int) -> None:
        """Process one evdev event of the pad (the raw fields, see ``pump``)."""
        if etype == ec.EV_SYN:
            if code == ec.SYN_REPORT:
                self._on_syn_report(sec * US_PER_S + usec)
            elif code == ec.SYN_DROPPED:
                self.dropping = True
        elif self.dropping:
            return
        elif etype == ec.EV_ABS:
            self._on_axis(code, value)
        elif etype == ec.EV_KEY:
            self._on_key(code, value)

    def _on_syn_report(self, event_us: int) -> None:
        """SYN_REPORT: count it, end a SYN_DROPPED gap, or send the report if it changed something."""
        self.c.reports["pad"] += 1
        if self.meter is not None:
            self.meter.tick(time.monotonic())
        if self.dropping:
            self._recover_from_drop()
        elif self.dirty:
            self._on_report(event_us)

    def _on_axis(self, code: int, value: int) -> None:
        """Store a stick / trigger axis or a D-pad axis of the current report."""
        c = self.c
        if c.plan is None:                       # a DualSense: its axes are already canonical (the fast path)
            if code in PAD_ABS:
                c.set_abs(code, value)
                self.dirty = True
            elif code in HAT_AXES:
                c.hat[code - ec.ABS_HAT0X] = clamp(value, -1, 1)
                self.dirty = True
        elif c.apply_axis(code, value):          # another pad: translated by its plan (sender_padtype)
            self.dirty = True
        elif code in HAT_AXES and c.plan.hat_axes:
            c.hat[code - ec.ABS_HAT0X] = clamp(value, -1, 1)
            self.dirty = True

    def _on_key(self, code: int, value: int) -> None:
        """Set or clear a button bit of the mask."""
        c = self.c
        if c.plan is not None:                   # another pad: the key is translated (a button, a D-pad direction, a digital trigger)
            self._on_plan_key(code, value)
            return
        bit = KEY_BIT.get(code)
        if bit:
            if value:
                c.mask |= bit
            else:
                c.mask &= ~bit
            self.dirty = self.burst = True

    def _on_plan_key(self, code: int, value: int) -> None:
        """A key of a pad with a translation plan (Xbox, DualShock 4, Switch Pro)."""
        c, plan = self.c, self.c.plan
        if plan is None:
            return
        if code in plan.dpad_keys or code in plan.trig_keys:
            c.apply_key(code, value)
            self.dirty = self.burst = True
            return
        canon = plan.keys.get(code)
        bit = KEY_BIT.get(canon, 0) if canon is not None else 0
        if bit:
            if value:
                c.mask |= bit
            else:
                c.mask &= ~bit
            self.dirty = self.burst = True

    def _recover_from_drop(self) -> None:
        """The incomplete report is over: read the whole state from the device and send it."""
        self.dropping = self.dirty = self.burst = False
        self.c.resync()
        _send_state_burst(self.c, self.loop)

    def _on_report(self, event_us: int) -> None:
        """A complete report with changes: send the state (with repeats after a button change).

        The clock is read once here and handed down: the idle timer, the send time and the latency of the change all
        use the same reading.
        """
        c, now = self.c, time.monotonic()
        self.dirty = False
        c.mark_event(event_us)
        c.note_input(now)
        if self.burst:
            self.burst = False
            _send_state_burst(c, self.loop, now)
        else:
            c.request_send(now)


async def stream_pad(c: Ctrl) -> None:
    """Read the pad's buttons, sticks, triggers and D-pad and turn them into state packets (see ``_PadStream``)."""
    stream = _PadStream(c)
    await pump(c.pad_dev, stream.handle)
