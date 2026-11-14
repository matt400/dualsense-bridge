"""Sender: the measurements behind the receiver's pad self-test (stcmd in, stres out).

The receiver window runs a scenario (rumble pulses, an audio stream, a quiet spell) and asks the sender for what only the sender can
see. Two kinds of numbers come back:

* Always on, almost free: the counters of the hidraw writer (writes, errors, the time of a write, refused writes). They exist whether
  or not a test runs and are read by ST_END.
* Only while armed (ST_BEGIN .. ST_END): one record per rumble write - when the command arrived, when the write was queued and when it
  finished - and the time of every hidraw write of the pad, so the test can give percentiles. Nothing is collected outside a test:
  the hot paths only look at ``armed``.

What is measured is the sender's side of the path: from the arrival of the command to the end of the write (hidraw: the writer thread's
``os.write`` returned; evdev: the force-feedback call returned). It is not the moment the motor spins - that is invisible to software.
The receiver says so in the report.

Every time in a record is in the PC clock (the offset the sender keeps from the pong2 packets, low 32 bits of microseconds), so the
receiver can subtract its own send time. Without a synchronised clock the times are 0.

The module has no imports from the rest of the sender but ``proto``: the callers bind what it needs (``bind``), and the pad objects are
looked up through ``ctrl_for``, so sender_rumble can import it without a cycle.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from typing import Any, Final

import sender_rt
from proto import (GOVERNORS, HAPT_RATE, SR_COUNT, SR_DONE, SR_FB, SR_HAPT, SR_SPK, SR_SYS, SR_WRITES, SRC_PC, SRC_WATCHDOG,
                   SRF_LOW_LATENCY, SRF_PINNED, SRF_QOS, SRF_RT, ST_BEGIN, ST_END, ST_HAPTICS, ST_MAX_S, ST_RECORDS, ST_SNAP,
                   ST_SPEAKER, T_STRES, UINT32_MASK, nearest_rank)

REC_MAX: Final = 256                    # rumble write records kept per test (a scenario sends a few dozen)
WRITES_MAX: Final = 8192                # hidraw write times kept per kind (a minute of audio at ~94 reports a second is ~5600)
PENDING_S: Final = 0.05                 # a command that arrived longer ago than this belongs to no write (a refused or merged one)
NS_PER_US: Final = 1000
NS_PER_S: Final = 1_000_000_000
TENTHS_MS_PER_S: Final = 10_000         # stream depth is reported in 0.1 ms
SYS_CPU: Final = "devices/system/cpu"
SPEAKER_FRAME_S: Final = 0.010          # the speaker's frame (10 ms)

armed = False                           # a test is collecting (checked by the hot paths: one attribute read)
slot = -1                               # the pad slot being measured
ref = 0                                 # the reference of the request that armed it (echoed in every record)
_t_armed = 0.0                          # monotonic time of ST_BEGIN (the auto-disarm after ST_MAX_S)

emit: Callable[..., None] | None = None                 # sender_net.emit, bound at start
now_ns: Callable[[], int] = time.monotonic_ns           # the sender's clock (sender_net.clock.ns), bound at start


def _no_offset() -> int | None:
    """The default PC clock offset: not synchronised."""
    return None


def _no_pad(_idx: int) -> Any:
    """The default pad lookup: no pad."""
    return None


pc_offset_us: Callable[[], int | None] = _no_offset     # PC clock minus the sender clock [us], None = not synchronised
ctrl_for: Callable[[int], Any] = _no_pad                # the pad in a slot (state.ctrl_by_idx), None = no pad

_pending: dict[int, tuple[int, int]] = {}               # slot -> (sequence of the last feedback packet, arrival ns)
_records: list[FbRecord] = []


def bind(emit_fn: Callable[..., None], clock_ns: Callable[[], int], offset_us: Callable[[], int | None],
         find: Callable[[int], Any]) -> None:
    """Connect the module to the sender (called once from the entry point; tests bind fakes).

    Args:
        emit_fn: Sends a packet: ``emit(ptype, idx, *values)``.
        clock_ns: The sender's clock in ns (the one the PC offset refers to).
        offset_us: The PC clock minus the sender clock in us, None while the clock is not synchronised.
        find: Returns the pad object of a slot or None.
    """
    global emit, now_ns, pc_offset_us, ctrl_for
    emit, now_ns, pc_offset_us, ctrl_for = emit_fn, clock_ns, offset_us, find


def to_pc_us(ns: int) -> int:
    """Convert a sender clock time [ns] into the PC clock [us, low 32 bits]; 0 when the clock is not synchronised or ``ns`` is 0."""
    off = pc_offset_us()
    return 0 if off is None or not ns else (ns // NS_PER_US + off) & UINT32_MASK


class FbRecord:
    """One rumble write of the pad being measured (a slotted object: it is filled in by the loop and by the writer thread).

    Attributes:
        src: Where the write came from: SRC_PC (a command), SRC_REPEAT (the sender's own second copy), SRC_WATCHDOG (the stop).
        seq: The sequence number of the packet that caused it (0 = none known).
        small: The weak motor, 0..255.
        large: The strong motor, 0..255.
        way: 1 = evdev force feedback, 2 = hidraw report.
        recv_ns: When the command arrived (the last refresh for the watchdog), sender clock.
        enq_ns: When the write was handed over (queued to the writer thread, or made directly).
        done_ns: When the write finished; 0 = it never did (a full queue, an error before the end).
        dur_ns: How long the write call itself took.
        err: The errno of a failed write, else 0.
    """

    __slots__ = ("src", "seq", "small", "large", "way", "recv_ns", "enq_ns", "done_ns", "dur_ns", "err")

    def __init__(self, src: int, seq: int, small: int, large: int, way: int, recv_ns: int, enq_ns: int) -> None:
        """Start a record; the write has not finished."""
        self.src, self.seq, self.small, self.large, self.way = src, seq, small, large, way
        self.recv_ns, self.enq_ns, self.done_ns, self.dur_ns, self.err = recv_ns, enq_ns, 0, 0, 0

    def finish(self, dur_ns: int, err: int = 0) -> None:
        """The write returned (on the writer thread for hidraw): note the time it took and whether it failed."""
        self.dur_ns, self.err = dur_ns, err
        self.done_ns = now_ns()


def _expire() -> None:
    """Disarm a measurement that nobody ended (the receiver went away mid-test)."""
    if armed and time.monotonic() - _t_armed > ST_MAX_S:
        disarm()


def fb_arrived(idx: int, seq: int) -> None:
    """A feedback packet arrived (the network layer, only while armed): remember its sequence number for the write it causes."""
    if idx == slot:
        _expire()
        _pending[idx] = (seq, now_ns())


def new_fb(idx: int, small: int, large: int, way: int, src: int, age_s: float = 0.0) -> FbRecord | None:
    """Open the record of a rumble write (the caller checked ``armed``); None when this pad is not measured or the list is full.

    Args:
        idx: The pad slot.
        small: The weak motor.
        large: The strong motor.
        way: 1 = evdev, 2 = hidraw.
        src: SRC_PC / SRC_REPEAT / SRC_WATCHDOG.
        age_s: For SRC_WATCHDOG: how long ago the last command arrived (the timer that fired was armed by it).

    Returns:
        The record, to be finished by the write; None = do not measure.
    """
    if idx != slot or len(_records) >= REC_MAX:
        return None
    now = now_ns()
    seq, recv = 0, now
    if src == SRC_WATCHDOG:
        recv = now - round(age_s * NS_PER_S)
    elif src == SRC_PC and (p := _pending.get(idx)) is not None and now - p[1] < PENDING_S * NS_PER_S:
        seq, recv = p
    rec = FbRecord(src, seq, small, large, way, recv, now)
    _records.append(rec)
    return rec


def begin(idx: int, request: int) -> None:
    """ST_BEGIN: start collecting for the pad in slot ``idx``; the answer is the system record."""
    global armed, slot, ref, _t_armed
    disarm()
    _records.clear()
    _pending.clear()
    armed, slot, ref, _t_armed = True, idx, request, time.monotonic()
    writer = _writer(ctrl_for(idx))
    if writer is not None:
        writer.start_detail(WRITES_MAX)
    send_sys(idx)


def disarm() -> None:
    """Stop collecting (the records stay until the next ST_BEGIN, so the results can be asked for again)."""
    global armed
    armed = False
    c = ctrl_for(slot) if slot >= 0 else None
    writer = _writer(c)
    if writer is not None:
        writer.stop_detail()


def _writer(c: Any) -> Any:
    """The pad's hidraw writer thread object, or None (no pad, no rumble, written directly)."""
    r = getattr(c, "rumble", None)
    return getattr(r, "_writer", None)


def _put(kind: int, flags: int, request: int, idx: int, *values: int) -> None:
    """Send one stres record (six values, padded with zeros)."""
    if emit is not None:
        emit(T_STRES, idx, kind, flags, request & 0xFFFF, *values, *(0,) * (6 - len(values)))


# ---------------------------------------------------------------------------------------------------------------------
# The system record: processor, scheduling, the pad's firmware.
# ---------------------------------------------------------------------------------------------------------------------
def _read(path: str) -> str | None:
    """Read a small sysfs file; None when it is missing or unreadable."""
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return None


def _number(text: str | None) -> int:
    """Return the integer in ``text``, 0 when there is none."""
    try:
        return int(text) if text is not None else 0
    except ValueError:
        return 0


def cpu_info(sys_root: str = "/sys", fast: Iterable[int] = ()) -> tuple[int, int, int, int]:
    """Read what the processor is doing, from the first fast core (cpu0 when the cores are all alike).

    Args:
        sys_root: Where sysfs is (tests give a fake tree).
        fast: The fast cores (sender_rt.fast_cpus), empty = all alike.

    Returns:
        ``(GOV_* code, current clock kHz, maximum clock kHz, number of CPUs)``; 0 for what cannot be read.
    """
    cpu = min(fast, default=0)
    base = f"{sys_root}/{SYS_CPU}/cpu{cpu}/cpufreq"
    name = _read(f"{base}/scaling_governor")
    code = GOVERNORS.index(name) + 1 if name in GOVERNORS else 0
    cpus = len([d for d in os.listdir(f"{sys_root}/{SYS_CPU}") if d.startswith("cpu") and d[3:].isdigit()]) if os.path.isdir(
        f"{sys_root}/{SYS_CPU}") else 0
    return code, _number(_read(f"{base}/scaling_cur_freq")), _number(_read(f"{base}/cpuinfo_max_freq")), cpus


def send_sys(idx: int, sys_root: str = "/sys") -> None:
    """Send the SR_SYS record: the low-latency state, the clocks, the load and the pad's firmware."""
    rt, qos, pinned = sender_rt.status()
    fast = sender_rt.fast_cpus(sys_root)
    gov, cur, top, cpus = cpu_info(sys_root, fast)
    flags = (SRF_RT if rt else 0) | (SRF_QOS if qos else 0) | (SRF_PINNED if pinned else 0) | (SRF_LOW_LATENCY if qos or pinned else 0)
    r = getattr(ctrl_for(idx), "rumble", None)
    load = round(os.getloadavg()[0] * 100) if hasattr(os, "getloadavg") else 0
    _put(SR_SYS, flags, ref, idx, gov, cur, top, load, getattr(r, "fw", None) or 0, cpus | len(fast) << 8)


# ---------------------------------------------------------------------------------------------------------------------
# Results.
# ---------------------------------------------------------------------------------------------------------------------
def write_summary(durations_us: list[int]) -> tuple[int, int, int, int, int]:
    """Return ``(count, p50, p95, p99, max)`` of write times in us; zeros for none."""
    if not durations_us:
        return 0, 0, 0, 0, 0
    d = sorted(durations_us)
    return len(d), nearest_rank(d, 50), nearest_rank(d, 95), nearest_rank(d, 99), d[-1]


def _send_writes(idx: int, writer: Any) -> None:
    """SR_COUNT (always on) and SR_WRITES (this test) of the pad's hidraw writer."""
    if writer is None:
        return
    st = writer.stats
    mean = st.total_ns // NS_PER_US // st.n if st.n else 0
    _put(SR_COUNT, 0, ref, idx, st.n, st.errors, mean, st.max_ns // NS_PER_US, st.full)
    for kind, flag in (("hid", 0), ("audio", 1)):
        times = writer.detail_of(kind)
        if times:
            _put(SR_WRITES, flag, ref, idx, *write_summary(times), writer.detail_errors(kind))


def _send_fb(idx: int) -> int:
    """Send every rumble write record of the test (oldest first); return how many."""
    for rec in _records:
        _put(SR_FB, rec.src | rec.way << 4, rec.seq, idx, to_pc_us(rec.recv_ns), to_pc_us(rec.enq_ns), to_pc_us(rec.done_ns),
             rec.small | rec.large << 8, rec.dur_ns // NS_PER_US, rec.err)
    return len(_records)


def stream_values(stream: Any, rate_hz: float) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """The two parts of an audio stream's record: its counters, then the rest and the buffer (depth, target in 0.1 ms).

    Args:
        stream: A HapticsStream / SpeakerStream (``stats``, ``ring.depth``, ``target``).
        rate_hz: How many of the stream's frames make a second (the depth is counted in frames).
    """
    st = stream.stats
    scale = TENTHS_MS_PER_S / rate_hz
    depth, target = round(stream.ring.depth * scale), round(stream.target * scale)
    return ((st.chunks, st.played, st.underruns, st.overruns, st.dropped, st.repeated),
            (st.late, st.lost, st.stale, st.resyncs, st.unwritten, min(depth, 0xFFFF) | min(target, 0xFFFF) << 16))


def send_stream(idx: int, which: int) -> None:
    """ST_SNAP: the counters of the pad's haptics (ST_HAPTICS) or speaker (ST_SPEAKER) stream as they are now."""
    c = ctrl_for(idx)
    if which == ST_HAPTICS:
        kind, stream, rate = SR_HAPT, getattr(c, "haptics", None), float(HAPT_RATE)
    else:
        kind, stream, rate = SR_SPK, getattr(c, "speaker", None), 1 / SPEAKER_FRAME_S
    if stream is None:
        return
    for part, values in enumerate(stream_values(stream, rate)):
        _put(kind, part, ref, idx, *values)


def end(idx: int, request: int) -> None:
    """ST_END (also a request to send the results again): stop collecting and send everything that was collected."""
    global ref
    disarm()
    ref = request
    send_sys(idx)
    _send_writes(idx, _writer(ctrl_for(idx)))
    for which in (ST_HAPTICS, ST_SPEAKER):
        send_stream(idx, which)
    _put(SR_DONE, 0, ref, idx, _send_fb(idx))


def on_command(idx: int, op: int, arg: int, request: int, _a: int, _b: int) -> None:
    """Handle a stcmd packet (net.on_selftest); a slot without a pad is ignored.

    Args:
        idx: The pad slot.
        op: ST_BEGIN / ST_END / ST_SNAP.
        arg: ST_SNAP: ST_RECORDS or which stream.
        request: The reference to echo.
        _a: Reserved (a 32-bit value).
        _b: Reserved (a 16-bit value).
    """
    if ctrl_for(idx) is None:
        return
    if op == ST_BEGIN:
        begin(idx, request)
    elif op == ST_END:
        end(idx, request)
    elif op == ST_SNAP and arg == ST_RECORDS:
        _put(SR_DONE, 0, request, idx, _send_fb(idx))   # the rumble records so far; the measurement goes on
    elif op == ST_SNAP:
        send_stream(idx, arg)
