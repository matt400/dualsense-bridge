"""Sender: events for the log in the receiver window (event packet).

Pad connected / disconnected, Wake-on-LAN, receiver missing, errors.

Only active when the receiver has enabled them (the SCFG_EVENTS flag in the scfg packet, remembered in wol.json - so
it works from startup, before the receiver says anything). An event waits in a queue (at most KEEP entries) until the
receiver answers pings, and is then sent SENDS times on consecutive pings (UDP is lossy); the receiver drops the
repeats by sequence number. This way, for example, a Wake-on-LAN that was sent shows up in the log once the PC
wakes up - with its real time (the age field).
"""
from __future__ import annotations

import collections
import random
import time
from collections.abc import Callable
from typing import Any, Final

from proto import BODY, EV_ERROR, EV_INFO, EV_WARN, T_EVENT, utf8_field

KEEP: Final = 32                 # events kept while the receiver is silent
SENDS: Final = 2                 # how many consecutive pings carry each event
ARG_MAX: Final = BODY[T_EVENT].size - 6      # the body minus its fixed fields (number, level, code, age)
SENDS_LEFT: Final = 6            # index of the "sends left" counter in a queue entry
U16_MAX: Final = 0xFFFF          # sequence numbers and ages are 16-bit fields
NO_SLOT: Final = 255             # the idx of an event that belongs to no pad
enabled = False
_queue: collections.deque[list[Any]] = collections.deque(maxlen=KEEP)   # [number, time, level, code, slot, argument, sends left]
_next = random.getrandbits(16)


def add(level: int, code: int, idx: int = NO_SLOT, arg: str = "") -> None:
    """Queue an event for the receiver (no-op while events are disabled).

    Every event gets a 16-bit sequence number (so the receiver can drop duplicates) and a monotonic timestamp (so the
    receiver can compute the event's age when it finally arrives). The argument text is cut on a UTF-8 character
    boundary to fit the packet. The deque has a fixed length: if the receiver stays silent, the oldest events drop out.
    """
    global _next
    if not enabled:
        return
    _next = (_next + 1) & U16_MAX
    _queue.append([_next, time.monotonic(), level, code, idx, utf8_field(arg, ARG_MAX, "…"), SENDS])   # cut on a character boundary


def info(code: int, idx: int = NO_SLOT, arg: str = "") -> None:
    """Queue an informational event."""
    add(EV_INFO, code, idx, arg)


def warn(code: int, idx: int = NO_SLOT, arg: str = "") -> None:
    """Queue a warning event."""
    add(EV_WARN, code, idx, arg)


def error(code: int, idx: int = NO_SLOT, arg: str = "") -> None:
    """Queue an error event."""
    add(EV_ERROR, code, idx, arg)


def set_enabled(on: object) -> None:
    """Turn event reporting on or off; turning it off also drops everything still waiting in the queue."""
    global enabled
    enabled = bool(on)
    if not enabled:
        _queue.clear()


def flush(emit: Callable[..., None], now: float | None = None) -> None:
    """Called from the ping loop when the receiver answers: send the waiting events, each one SENDS times in total.

    Every call sends each queued event once, with its age in seconds (capped to 16 bits), and decrements its counter;
    an event is removed once its counter reaches zero. Because the call happens on every ping, the repeats are spread
    over consecutive pings, which makes a single lost UDP packet harmless.
    """
    now = time.monotonic() if now is None else now
    for ev in list(_queue):
        num, t, level, code, idx, arg, left = ev
        emit(T_EVENT, idx, num, level, code, min(U16_MAX, int(now - t)), arg)
        ev[SENDS_LEFT] = left - 1
        if ev[SENDS_LEFT] <= 0:
            _queue.remove(ev)


def pending() -> int:
    """Return the number of events still waiting to be (re)sent."""
    return len(_queue)
