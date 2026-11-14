"""Sender: writes to a pad's hidraw node from a thread of its own, so a slow write never stalls the event loop.

A write to /dev/hidrawN of a Bluetooth pad usually takes a few microseconds, but now and then it waits for the kernel's Bluetooth
queue: P3a measured p999 5.5-6.7 ms and a 17 ms worst case (O_NONBLOCK does not help, the hidraw write path ignores it). On the event
loop that would freeze every evdev read, the UDP receive and every timer of every pad for as long - the audio tick of the next report too.

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
import time
from collections.abc import Callable
from typing import Any, Final

MAX_PENDING: Final = 16         # reports waiting for the thread: a few 0x31 reports go out back to back (a stop, the triggers, a setup)
AUDIO_PENDING: Final = 4        # an audio report is refused above this depth (40 ms): a late burst would overrun the pad's buffer
SLOTS: Final = MAX_PENDING + 2  # copy buffers: the queued ones, the one being written and the one being filled
SLOT_BYTES: Final = 1024        # the largest output report of the pad is 547 B (0x39)
CLOSE_TIMEOUT_S: Final = 0.1    # how long close(wait=True) blocks the loop for the last reports (a few us normally, a stall is cut short)

ErrorSink = Callable[[str, OSError], None]
OkSink = Callable[[str], None]
_STOP: Final = (b"", "", None)  # the queue item that ends the thread


class WriteStats:
    """The counters of a writer, always on (a few integer additions per write, on the writer thread).

    Attributes:
        n: Reports written (a failed write is not counted here).
        errors: Writes that failed.
        total_ns: The time all those writes took together (``os.write`` only, not the wait in the queue).
        max_ns: The longest one.
        full: Reports refused because the queue was full (written by the loop; the thread never touches it).
    """

    __slots__ = ("n", "errors", "total_ns", "max_ns", "full")

    def __init__(self) -> None:
        """Start at zero."""
        self.n = self.errors = self.total_ns = self.max_ns = self.full = 0


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
        self._q: queue.SimpleQueue[tuple[bytes | memoryview, str, Any]] = queue.SimpleQueue()
        self._slots = [memoryview(bytearray(SLOT_BYTES)) for _ in range(SLOTS)]
        self._next = 0
        self._closed = False
        self._failing: set[str] = set()         # kinds whose last write failed (thread side)
        self.stats = WriteStats()               # always on, see the class
        self._collect = False                   # the self-test is collecting the time of every write (start_detail)
        self._detail: dict[str, list[int]] = {}         # kind -> the time of each write [us] while collecting
        self._detail_errors: dict[str, int] = {}        # kind -> failed writes while collecting
        self._detail_limit = 0
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def write(self, data: bytes | bytearray | memoryview, kind: str, reused: bool = False, limit: int = MAX_PENDING,
              tag: Any = None) -> None:
        """Queue one report (called on the loop).

        Args:
            data: The whole report.
            kind: A tag handed back to on_error / on_ok (which error counter it belongs to).
            reused: The caller overwrites ``data`` later (the audio report): copy it into a slot first.
            limit: Refuse the report when this many are already queued (AUDIO_PENDING for audio; at most MAX_PENDING).
            tag: An object with ``finish(duration_ns, errno)``, called by the thread when this write returned (the self-test's
                record of a rumble write); None for the usual report.

        Raises:
            BlockingIOError: The queue is full (the thread is stuck in a write) - the report is not written.
            OSError: The writer is closed (EBADF).
        """
        if self._closed:
            raise OSError(9, "hidraw writer closed")          # EBADF: like a write to a closed descriptor
        if self._q.qsize() >= min(limit, MAX_PENDING):
            self.stats.full += 1
            raise BlockingIOError(11, "hidraw writer queue full")
        if reused or not isinstance(data, bytes):
            n = len(data)
            slot = self._slots[self._next]
            self._next = (self._next + 1) % SLOTS
            slot[:n] = data
            data = slot[:n]
        self._q.put((data, kind, tag))

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
    def start_detail(self, limit: int) -> None:
        """Self-test: collect the time of every write (up to ``limit`` per kind) until stop_detail(); earlier numbers are dropped."""
        self._detail = {"hid": [], "audio": []}
        self._detail_errors = {"hid": 0, "audio": 0}
        self._detail_limit = limit
        self._collect = True

    def stop_detail(self) -> None:
        """Self-test: stop collecting; what was collected stays readable until the next start_detail()."""
        self._collect = False

    def detail_of(self, kind: str) -> list[int]:
        """The times [us] collected for ``kind`` ("hid" / "audio"); empty when none."""
        return list(self._detail.get(kind, ()))

    def detail_errors(self, kind: str) -> int:
        """How many writes of ``kind`` failed while collecting."""
        return self._detail_errors.get(kind, 0)

    def _note(self, kind: str, dt_ns: int, ok: bool) -> None:
        """Count one write (the thread). The always-on counters are plain additions; the detail only while the self-test collects."""
        st = self.stats
        if ok:
            st.n += 1
            st.total_ns += dt_ns
            st.max_ns = max(st.max_ns, dt_ns)
        else:
            st.errors += 1
        if self._collect and kind in self._detail:
            if not ok:
                self._detail_errors[kind] += 1
            elif len(self._detail[kind]) < self._detail_limit:
                self._detail[kind].append(dt_ns // 1000)

    # ---------- the thread ----------
    def _run(self) -> None:
        """Write queued reports in order until close(); then close the descriptor."""
        fd, q = self.fd, self._q
        while True:
            data, kind, tag = q.get()
            if not kind:
                break
            t0 = time.perf_counter_ns()
            try:
                os.write(fd, data)
            except (BlockingIOError, InterruptedError):
                continue                        # a full kernel queue is not an error: the next report / refresh follows
            except OSError as e:
                self._note(kind, 0, False)
                if tag is not None:
                    tag.finish(time.perf_counter_ns() - t0, e.errno or 0)
                self._failing.add(kind)
                self._post(self._on_error, kind, e)
                continue
            dt = time.perf_counter_ns() - t0
            self._note(kind, dt, True)
            if tag is not None:
                tag.finish(dt)
            if kind in self._failing:
                self._failing.discard(kind)
                self._post(self._on_ok, kind)
        with contextlib.suppress(OSError):      # the pad may be gone already; the descriptor is dropped either way
            os.close(fd)

    def _post(self, fn: Callable[..., None], *args: object) -> None:
        """Run ``fn(*args)`` on the owner's loop; a loop that is closed (the sender is exiting) drops it."""
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(fn, *args)
