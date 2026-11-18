"""Sender: the evdev read loop shared by every input device (``pump``) and the per-device report rate meter.

``pump`` reads the kernel's ``struct input_event`` records straight from the descriptor and hands their fields to a
handler as plain ints; the stream modules (``sender_padstream``, ``sender_motion``, ``sender_touch``) are such handlers.
"""
from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import struct
import time
from typing import Final, Protocol

from evdev import InputDevice
from evdev import ecodes as ec

from sender_ioctl import IOC_WRITE, ioc

EVIOC_MAGIC: Final = "E"                                # the ioctl type letter of evdev (linux/input.h)
INT32_BYTES: Final = 4
# struct input_event: timeval (two C longs: 8 + 8 B on 64-bit, 4 + 4 B on 32-bit, also with a 64-bit time_t) + type, code, value
EVENT: Final = struct.Struct("llHHi")
READ_EVENTS: Final = 64                                 # one read takes up to this many events (as python-evdev does)
READ_BYTES: Final = EVENT.size * READ_EVENTS
EVIOCSCLOCKID: Final = ioc(IOC_WRITE, EVIOC_MAGIC, 0xA0, INT32_BYTES)   # pick the clock of this client's event timestamps
CLOCK_ID_MONOTONIC: Final = struct.pack("i", time.CLOCK_MONOTONIC)
METER_MAX_GAPS: Final = 4096
METER_MIN_GAPS: Final = 10
METER_P99: Final = 0.99


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


class Handler(Protocol):
    """An event handler of a read loop: called with the fields of one raw ``struct input_event``."""

    def __call__(self, sec: int, usec: int, etype: int, code: int, value: int, /) -> None:
        """Handle one event; ``sec`` / ``usec`` are the kernel timestamp (CLOCK_MONOTONIC, 0 = unknown)."""


def _use_monotonic_clock(dev: InputDevice) -> bool:
    """Ask the kernel to stamp this client's events with CLOCK_MONOTONIC instead of CLOCK_REALTIME.

    The clock is per open descriptor (EVIOCSCLOCKID), so nobody else reading the device is affected. A monotonic stamp
    can be compared with ``time.monotonic()`` directly: the phone delay of a change then costs one clock read at send
    time instead of an extra ``time.time_ns()``, and an NTP step can no longer turn into a bogus delay. If the kernel
    had events queued, it drops them and queues SYN_DROPPED, which the streams already handle.

    Returns:
        True when the events now carry monotonic stamps; False means the stamps are unusable for latency.
    """
    try:
        fcntl.ioctl(dev.fd, EVIOCSCLOCKID, CLOCK_ID_MONOTONIC)
    except (OSError, ValueError, TypeError):
        return False
    return True


async def pump(dev: InputDevice, handler: Handler) -> None:
    """Feed every event of ``dev`` to ``handler`` until the device goes away.

    python-evdev's ``async_read_loop()`` is convenient, but on this path it is expensive: it creates a Future for every
    single event and, for every batch the kernel hands over, registers and removes the descriptor in the event loop again
    (two ``epoll_ctl`` calls) and creates one more Future. Even ``dev.read()`` builds an ``InputEvent`` object per event,
    which at the motion sensor's ~250-1000 reports/s (8 events each) was the largest CPU cost of the sender. Here the
    descriptor is registered once, the callback reads a whole batch with one ``os.read`` and ``struct.iter_unpack`` hands
    the raw ``struct input_event`` fields to the handler as plain ints - no object per event.

    Whatever ends the loop is raised from here exactly as ``async_read_loop`` raised it: ``OSError`` when the device
    disappears, or the exception of a handler that has a bug. The descriptor is unregistered before the function returns
    (also on cancellation) - the caller closes the device afterwards, and a stale registration would break the next device
    that gets the same descriptor number.

    Args:
        dev: The evdev device. Anything that is not an ``InputDevice`` (a stand-in in the tests) is read with its own
            ``async_read_loop()``; its events carry no timestamp (0, 0).
        handler: Called with ``(sec, usec, type, code, value)`` of every event, in the order the kernel delivered them.
    """
    if not isinstance(dev, InputDevice):
        async for e in dev.async_read_loop():
            handler(getattr(e, "sec", 0), getattr(e, "usec", 0), e.type, e.code, e.value)
        return
    loop = asyncio.get_running_loop()
    ended: asyncio.Future[None] = loop.create_future()
    fd, timed = dev.fd, _use_monotonic_clock(dev)
    read, unpack = os.read, EVENT.iter_unpack

    def on_readable() -> None:
        try:
            data = read(fd, READ_BYTES)
            if not data:
                raise OSError(errno.ENODEV, "input device closed")
            if timed:
                for ev in unpack(data):
                    handler(*ev)
            else:
                for _sec, _usec, etype, code, value in unpack(data):
                    handler(0, 0, etype, code, value)
        except BlockingIOError:
            pass                        # woken up with nothing to read: wait for the next wake-up
        except Exception as exc:  # noqa: BLE001 - handed to the awaiting task, which treats it like an error of the read loop
            if not ended.done():
                ended.set_exception(exc)

    loop.add_reader(fd, on_readable)
    try:
        await ended
    finally:
        loop.remove_reader(fd)


def is_report(etype: int, code: int) -> bool:
    """Whether an event is SYN_REPORT, which closes one complete report of a device."""
    return etype == ec.EV_SYN and code == ec.SYN_REPORT
