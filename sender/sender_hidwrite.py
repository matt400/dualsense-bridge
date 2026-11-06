"""Sender: writes to a pad's hidraw node from a thread of its own, so a slow write never stalls the event loop.

A write to /dev/hidrawN of a Bluetooth pad usually takes a few microseconds, but now and then it waits for the kernel's Bluetooth
queue: P3a measured p999 5.5-6.7 ms and a 17 ms worst case (O_NONBLOCK does not help, the hidraw write path ignores it). On the event
loop that froze every evdev read, the UDP receive and every timer of every pad for as long - the audio tick of the next report too.

    loop: Rumble builds a report -> HidWriter.write (copy into a slot, queue) -> writer thread: os.write

* Order. One writer per descriptor and one FIFO: reports reach the pad in the order Rumble numbered them (the sequence nibble), the
  0x31 state reports and the 0x32 / 0x36 audio reports included.
* No allocation per report. A report that the caller reuses (the audio report) is copied into one of SLOTS preallocated buffers;
  a slot is reused only after SLOTS later reports, and at most MAX_PENDING wait in the queue (plus one being written), so a slot
  is never overwritten before its write. Reports built as ``bytes`` (0x31) are immutable and are queued as they are.
* Back pressure. A full queue raises BlockingIOError, like a full kernel queue did: the callers already treat that as "not
  written" (the repeat / refresh / next tick delivers the state). Audio reports have a lower limit (AUDIO_PENDING) than state
  reports: a stalled writer must not turn into a late burst of audio, but a stop report behind a few others must still go out.
* Errors. The thread cannot touch Rumble (it lives on the loop), so it hands every failed write to ``on_error`` through
  ``loop.call_soon_threadsafe``; the first good write after errors reports ``on_ok`` the same way (the error counters of Rumble
  count consecutive errors). A success is not reported otherwise: one wake-up of the loop per report is what this module saves.
* Close. ``close()`` lets the thread write what is queued (the stop report of a closing pad) and then close the descriptor - the
  descriptor belongs to the writer from ``open`` on.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import threading
from collections.abc import Callable
from typing import Final

MAX_PENDING: Final = 16         # reports waiting for the thread: a few 0x31 reports go out back to back (a stop, the triggers, a setup)
AUDIO_PENDING: Final = 4        # an audio report is refused above this depth (40 ms): a late burst would overrun the pad's buffer
SLOTS: Final = MAX_PENDING + 2  # copy buffers: the queued ones, the one being written and the one being filled
SLOT_BYTES: Final = 1024        # the largest output report of the pad is 547 B (0x39)
CLOSE_TIMEOUT_S: Final = 0.1    # how long close(wait=True) blocks the loop for the last reports (a few us normally, a stall is cut short)

ErrorSink = Callable[[str, OSError], None]
OkSink = Callable[[str], None]
_STOP: Final = (b"", "")        # the queue item that ends the thread


class HidWriter:
    """The writer thread of one hidraw descriptor.

    Attributes:
        fd: The descriptor; owned by the writer (closed by its thread after the last write).
    """

    def __init__(self, fd: int, name: str, on_error: ErrorSink, on_ok: OkSink, loop: asyncio.AbstractEventLoop) -> None:
        """Start the thread for ``fd``.

        Args:
            fd: An open hidraw descriptor; the writer closes it at the end.
            name: The thread name (for logs and debuggers).
            on_error: Called on the loop with (kind, error) for every failed write; ``kind`` is what the caller passed to write.
            on_ok: Called on the loop with ``kind`` for the first good write after a failed one of that kind.
            loop: The loop that owns the caller (where on_error / on_ok run).
        """
        self.fd = fd
        self._on_error, self._on_ok, self._loop = on_error, on_ok, loop
        self._q: queue.SimpleQueue[tuple[bytes | memoryview, str]] = queue.SimpleQueue()
        self._slots = [memoryview(bytearray(SLOT_BYTES)) for _ in range(SLOTS)]
        self._next = 0
        self._closed = False
        self._failing: set[str] = set()         # kinds whose last write failed (thread side)
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def write(self, data: bytes | bytearray | memoryview, kind: str, reused: bool = False, limit: int = MAX_PENDING) -> None:
        """Queue one report (called on the loop).

        Args:
            data: The whole report.
            kind: A tag handed back to on_error / on_ok (which error counter it belongs to).
            reused: The caller overwrites ``data`` later (the audio report): copy it into a slot first.
            limit: Refuse the report when this many are already queued (AUDIO_PENDING for audio; at most MAX_PENDING).

        Raises:
            BlockingIOError: The queue is full (the thread is stuck in a write) - the report is not written.
            OSError: The writer is closed (EBADF).
        """
        if self._closed:
            raise OSError(9, "hidraw writer closed")          # EBADF: like a write to a closed descriptor
        if self._q.qsize() >= min(limit, MAX_PENDING):
            raise BlockingIOError(11, "hidraw writer queue full")
        if reused or not isinstance(data, bytes):
            n = len(data)
            slot = self._slots[self._next]
            self._next = (self._next + 1) % SLOTS
            slot[:n] = data
            data = slot[:n]
        self._q.put((data, kind))

    def close(self, wait: bool = False) -> None:
        """Stop taking reports; the thread writes what is queued, then closes the descriptor.

        Args:
            wait: Wait (up to CLOSE_TIMEOUT_S) until it is done - for a caller that exits right after.
        """
        if self._closed:
            return
        self._closed = True
        self._q.put(_STOP)
        if wait:
            self._thread.join(CLOSE_TIMEOUT_S)

    # ---------- the thread ----------
    def _run(self) -> None:
        """Write queued reports in order until close(); then close the descriptor."""
        fd, q = self.fd, self._q
        while True:
            data, kind = q.get()
            if not kind:
                break
            try:
                os.write(fd, data)
            except (BlockingIOError, InterruptedError):
                continue                        # a full kernel queue is not an error: the next report / refresh follows
            except OSError as e:
                self._failing.add(kind)
                self._post(self._on_error, kind, e)
                continue
            if kind in self._failing:
                self._failing.discard(kind)
                self._post(self._on_ok, kind)
        with contextlib.suppress(OSError):      # the pad may be gone already; the descriptor is dropped either way
            os.close(fd)

    def _post(self, fn: Callable[..., None], *args: object) -> None:
        """Run ``fn(*args)`` on the owner's loop; a loop that is closed (the sender is exiting) drops it."""
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(fn, *args)
