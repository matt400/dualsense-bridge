"""Sender: a one-shot timer with an absolute deadline for the audio ticks (timerfd in the event loop, ``call_at`` as the fallback).

The haptics and the speaker streams must send a report every 10.667 ms. ``loop.call_at`` is late by 0-1 ms at random on a loop that has
nothing else to do: the selector hands ``epoll_wait`` a timeout in whole milliseconds and rounds it up (selectors.EpollSelector). A
timerfd has a nanosecond resolution: it is a file descriptor that becomes readable at the deadline, so it is registered in the same loop
with ``add_reader`` and the tick runs where it always did - no thread, no lock, no shared state.

* The deadline is an absolute time in ``time.monotonic()`` seconds, like ``call_at`` takes it. The timerfd wants CLOCK_MONOTONIC in
  nanoseconds, which is the same clock; the deadline is converted through its distance from now, so the timer also works when the
  caller's clock is a fake one (the tests).
* One timer is one file descriptor and lives from the first ``arm`` to ``close``; every ``arm`` is one ``timerfd_settime`` call (about a
  microsecond, nothing is allocated).
* timerfd exists in Python 3.13+ on Linux. Anywhere else, or when the descriptor cannot be created or set, the timer falls back to
  ``loop.call_at`` - the behaviour of before - and keeps working.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from collections.abc import Callable
from typing import Any, Final

NS_PER_S: Final = 1_000_000_000
# looked up by name: os.timerfd_* exist only on Linux with Python 3.13+, and the type checker targets 3.11
_create: Any = getattr(os, "timerfd_create", None)
_settime: Any = getattr(os, "timerfd_settime_ns", None)
_FLAGS: Final = getattr(os, "TFD_NONBLOCK", 0) | getattr(os, "TFD_CLOEXEC", 0)
_ABSOLUTE: Final = getattr(os, "TFD_TIMER_ABSTIME", 1)
use_timerfd: bool = sys.platform == "linux" and _create is not None and _settime is not None   # tests switch it off to cover the fallback


class TickTimer:
    """A one-shot timer that calls ``callback`` at an absolute deadline; ``arm`` it again from the callback for a periodic tick.

    Attributes:
        precise: The timer runs on a timerfd (False: it uses ``loop.call_at``).
    """

    def __init__(self, callback: Callable[[], None]) -> None:
        """Create a timer that is not armed and holds no descriptor yet (the first ``arm`` opens it).

        Args:
            callback: Called in the event loop, with no arguments, when the deadline comes.
        """
        self._callback = callback
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fd = -1
        self._handle: asyncio.TimerHandle | None = None         # the call_at fallback
        self._count = bytearray(8)                              # the expiration counter that has to be read from a timerfd
        self.precise = False

    def arm(self, due: float) -> TickTimer:
        """(Re)arm the timer: ``callback`` runs once at ``due``; a deadline that has passed runs it at the next turn of the loop.

        Args:
            due: The deadline in ``time.monotonic()`` seconds. Needs a running event loop.

        Returns:
            The timer itself, so a stream can keep it as the handle of its pending tick.
        """
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
            self._open()
        if self._fd >= 0:
            # the distance from now, on the caller's clock, added to the kernel's clock: the same clock, a fake one in the tests
            target = time.monotonic_ns() + round((due - time.monotonic()) * NS_PER_S)
            try:
                _settime(self._fd, flags=_ABSOLUTE, initial=max(1, target))     # an initial value of 0 would disarm the timer
            except OSError:
                self._drop_fd()                                 # the descriptor is unusable: carry on with call_at
            else:
                return self
        if self._handle is not None:
            self._handle.cancel()                               # arming again replaces the earlier deadline, as with a timerfd
        self._handle = self._loop.call_at(due, self._callback)
        return self

    def cancel(self) -> None:
        """Disarm the timer (nothing happens when it is not armed); it can be armed again."""
        if self._fd >= 0:
            with contextlib.suppress(OSError):
                _settime(self._fd, flags=0, initial=0)          # also clears an expiration that was not read yet
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def close(self) -> None:
        """Disarm the timer and release its descriptor; safe to call twice, the timer cannot be used afterwards."""
        self.cancel()
        self._drop_fd()

    def _open(self) -> None:
        """Create the timerfd and register it with the loop; on any failure the timer stays on call_at."""
        if not use_timerfd or self._loop is None:
            return
        try:
            fd = _create(time.CLOCK_MONOTONIC, flags=_FLAGS)
        except OSError:
            return
        try:
            self._loop.add_reader(fd, self._fire)
        except (OSError, RuntimeError, NotImplementedError):
            os.close(fd)
            return
        self._fd, self.precise = fd, True

    def _drop_fd(self) -> None:
        """Unregister and close the descriptor (if there is one); the timer then uses call_at."""
        fd, self._fd, self.precise = self._fd, -1, False
        if fd < 0:
            return
        if self._loop is not None:
            with contextlib.suppress(RuntimeError, ValueError, OSError):    # a loop that is already closed has dropped it
                self._loop.remove_reader(fd)
        with contextlib.suppress(OSError):
            os.close(fd)

    def _fire(self) -> None:
        """Reader callback of the timerfd: read the expiration counter (that re-arms the readiness) and run the callback.

        A read that finds nothing means the timer was cancelled or re-armed after the loop had already seen the descriptor ready:
        the callback does not run then.
        """
        if self._fd < 0:
            return
        try:
            os.readv(self._fd, [self._count])
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._drop_fd()
            return
        self._callback()
