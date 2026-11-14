"""Receiver: the pad self-test - one scenario that measures a pad and leaves a report to copy (no Qt; the window is rx_qt_selftest).

The Test view has one button. It runs the steps below on the chosen pad, each with a time limit, all of them abortable, with the output to
the game blocked (the Test lock of the window; the run refuses to start without it):

  meta       versions, pad, virtual pad backend, rumble ways, firmware, Bluetooth adapter (no waiting)
  idle       ~10 s at rest: the network and Bluetooth link numbers of the Diagnostics tab (rx_diag), sampled twice a second
  latency    a window of 5 / 15 / 30 / 60 s in which the user presses buttons and moves the sticks: p50 / p95 / p99 / max of the total and
             of the segments bt / phone / net / pc (rx_latency - the same samples as the pad card, but with the whole window kept)
  vibration  pulses on the small and the large motor at a few levels, then three "who stops it" cases: the pulse ends (a stop command),
             the PC stops refreshing (the sender's watchdog), the game stops sending rumble (GAME_RUMBLE_STALE_S / _GAP_S in rx_fbstate)
  audio      a test stream (tone, silence, sweep) through the haptics path and through the speaker path; the counters of both ends
  cpu        the sender's governor, clocks, load, real-time state, and the hidraw write counters, read when the run ends
  mic        a flag only: the microphone is not supported, nothing is measured

What is measured and what is not. The vibration times run from the PC's send (or from the pulse order) to the end of the write to the pad
on the sender: the writer thread's ``os.write`` returned, or the force-feedback call. When the motor really spins is invisible to
software, so the report says "write", never "motor". The audio step measures the path (frames sent, received, played, underruns,
overruns, drift drops, the depth of the buffer, the time of a write), not the sound: whether it crackles is for the ears. The sender's
times are in the PC clock through the offset it keeps from the pong2 packets, so a segment that spans both machines carries an error of
up to half the round trip.

An older sender (before SELFTEST_SENDER) is never sent a self-test packet: the steps that need it are skipped and the rest runs.

The run is a thread (``SelfTest.start``); the window polls ``snapshot()``. ``Plan`` holds every duration, so tests run the whole
scenario in a second. The report is built by rx_selftest_report.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import rx_diag
import rx_fbstate
import rx_feedback as F
import rx_latency
import rx_state as S
import rx_swap
import rx_testmode as testmode
from proto import (BODY, GOVERNORS, HEADER_LEN, PK_DS4, PK_DUALSENSE, PK_OTHER, PK_XBOX, RUMBLE_EVDEV, RUMBLE_HIDRAW, SELFTEST_SENDER,
                   SR_COUNT, SR_DONE, SR_FB, SR_HAPT, SR_SPK, SR_SYS, SR_WRITES, SRC_REPEAT, SRC_WATCHDOG, SRF_PINNED, SRF_QOS, SRF_RT,
                   ST_BEGIN, ST_END, ST_HAPTICS, ST_RECORDS, ST_SNAP, ST_SPEAKER, T_STRES, UINT32_MASK, nearest_rank)

STEPS: Final = ("meta", "idle", "latency", "vibration", "audio", "cpu", "mic")
OK, SKIPPED, ABORTED, FAILED, NO_DATA = "ok", "skipped", "aborted", "failed", "no_data"
LATENCY_WINDOWS: Final = (5, 15, 30, 60)         # the windows the user may pick [s]
MIN_LATENCY_SAMPLES: Final = 10                  # fewer samples than this is "no data" (as the pad card)
SENDER_WATCHDOG_S: Final = 1.5                   # sender_rumble.WATCHDOG_S: the motors stop this long after the last refresh
MOTORS_OFF: Final = (0, 0)
POLL_S: Final = 0.25                             # how often the latency step reads the samples
TICK_S: Final = 0.05                             # the granularity of every wait (abort reacts within this)
WRAP: Final = 1 << 32
HALF: Final = 1 << 31
NS_PER_S: Final = 1_000_000_000
NS_PER_MS: Final = 1_000_000
SILENCE_S: Final = 0.1                           # the gap between the tone and the sweep (shorter than the silence that ends a stream)
FAMILY_NAMES: Final = {PK_DUALSENSE: "DualSense", PK_DS4: "DualShock 4", PK_XBOX: "Xbox", PK_OTHER: "other"}
BACKEND_NAMES: Final = {"hm": "HIDMaestro (DualSense)", "vigem": "ViGEm (DS4)", "x360": "ViGEm (Xbox 360)"}


class AbortedError(Exception):
    """The user ended the run (or the Test view was left): unwinds the steps to the cleanup."""


@dataclass(slots=True)
class StepResult:
    """What one step produced.

    Attributes:
        id: One of STEPS.
        status: OK, SKIPPED (cannot run here: see ``note``), ABORTED, FAILED (an exception) or NO_DATA (ran, nothing arrived).
        seconds: How long it took.
        note: A short English remark for the report: why skipped, what is unusual.
        data: The measurements (plain numbers, strings and lists: it goes straight into the report's JSON).
    """

    id: str
    status: str = OK
    seconds: float = 0.0
    note: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Plan:
    """Every duration of the scenario [s]; the defaults are the real ones, tests shrink them."""

    window_s: int = 15              # the latency window picked by the user
    idle_s: float = 10.0
    pulse_s: float = 0.5
    gap_s: float = 0.7
    levels: tuple[int, ...] = (70, 160, 255)
    ack_s: float = 1.5              # waiting for the sender's first answer
    result_s: float = 3.0           # waiting for the rest of its answers
    stream_tail_s: float = 1.0      # after an audio stream's last frame: the sender plays out its buffer
    tone_s: float = 1.0             # the audio test stream: a tone ...
    sweep_s: float = 2.0            # ... a short silence and a sweep (the silence is SILENCE_S, shorter than the one that ends a stream)


@dataclass(slots=True)
class Tap:
    """One feedback packet we sent (rx_net.fb_tap): the motors, the packet's sequence number and the send time."""

    small: int
    large: int
    seq: int
    send_ns: int


@dataclass(frozen=True, slots=True)
class FbRec:
    """The sender's record of one rumble write (an SR_FB packet); times are the PC clock [us, low 32 bits]."""

    src: int
    way: int
    seq: int
    recv_us: int
    enq_us: int
    done_us: int
    small: int
    large: int
    dur_us: int
    err: int


class Inbox:
    """What the sender sent back during a run (the network thread adds, the run thread waits)."""

    def __init__(self) -> None:
        """Start empty."""
        self.cond = threading.Condition()
        self.clear()

    def clear(self) -> None:
        """Forget everything (a new run)."""
        with self.cond:
            self.sys: list[tuple[int, ...]] = []
            self.count: tuple[int, ...] | None = None
            self.fb: dict[int, list[FbRec]] = defaultdict(list)       # sequence number -> records (one per way)
            self.watchdog: list[FbRec] = []
            self.repeats = 0
            self.writes: dict[int, tuple[int, ...]] = {}              # 0 = reports, 1 = audio
            self.streams: dict[tuple[int, int], tuple[int, ...]] = {}   # (SR_HAPT / SR_SPK, part) -> values
            self.done: int | None = None
            self._seen: set[tuple[int, int, int, int, int]] = set()

    def add(self, kind: int, flags: int, ref: int, v: Sequence[int]) -> None:
        """One stres record. A record that came twice (the results asked for again) is counted once."""
        with self.cond:
            if kind == SR_SYS:
                self.sys.append((flags, *v))       # (flags, governor, clock, max clock, load, firmware, cpus)
            elif kind == SR_COUNT:
                self.count = tuple(v)
            elif kind == SR_FB:
                self._add_fb(flags, ref, v)
            elif kind == SR_WRITES:
                self.writes[flags] = tuple(v)
            elif kind in (SR_HAPT, SR_SPK):
                self.streams[(kind, flags)] = tuple(v)
            elif kind == SR_DONE:
                self.done = v[0]
            self.cond.notify_all()

    def _add_fb(self, flags: int, ref: int, v: Sequence[int]) -> None:
        """A rumble write record (caller holds the lock)."""
        rec = FbRec(flags & 0x0F, flags >> 4, ref, v[0], v[1], v[2], v[3] & 0xFF, v[3] >> 8 & 0xFF, v[4], v[5])
        key = (rec.src, rec.way, rec.seq, rec.recv_us, rec.enq_us)
        if key in self._seen:
            return
        self._seen.add(key)
        if rec.src == SRC_WATCHDOG:
            self.watchdog.append(rec)
        elif rec.src == SRC_REPEAT:
            self.repeats += 1
        elif rec.seq:
            self.fb[rec.seq].append(rec)

    def wait(self, ready: Callable[[], bool], timeout: float) -> bool:
        """Wait until ``ready()`` (checked under the lock) or the timeout; -> whether it became true."""
        with self.cond:
            return self.cond.wait_for(ready, timeout)


inbox = Inbox()
current: SelfTest | None = None      # the run in progress (rx_net hands the sender's records to it)


def on_stres(idx: int, data: bytes) -> None:
    """The network thread: a stres packet (idx = the pad's slot). Ignored unless a run for that pad is waiting for it."""
    run = current
    if run is None or idx != run.slot or not run.collecting:
        return
    kind, flags, ref, *vals = BODY[T_STRES].unpack_from(data, HEADER_LEN)
    inbox.add(kind, flags, ref, vals)


def _net() -> Any:
    """rx_net (imported late: it imports this module)."""
    import rx_net
    return rx_net


def pc_us(perf_ns: int) -> int:
    """A perf_counter_ns time as the PC clock the sender's records use (the pong2 clock: perf + the process base, us, low 32 bits)."""
    return ((perf_ns + _net().clock.base) // 1000) & UINT32_MASK


def span_us(a: int, b: int) -> int:
    """``b - a`` of two 32-bit microsecond times, as a signed number (a clock offset error can make it a little negative)."""
    d = (b - a) % WRAP
    return d - WRAP if d >= HALF else d


def series_stats(values: Sequence[float]) -> dict[str, Any]:
    """n, min, p50, p95, max of a series ({"n": 0} for an empty one) - the shape of every measured series in the report."""
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {"n": len(v), "min": round(v[0], 2), "p50": round(nearest_rank(v, 50), 2), "p95": round(nearest_rank(v, 95), 2),
            "max": round(v[-1], 2)}


def pct_dict(p: rx_latency.Pct | None) -> dict[str, Any] | None:
    """A latency percentile set as a plain dict (ms), None stays None."""
    return None if p is None else {"n": p.n, "p50": round(p.p50, 2), "p95": round(p.p95, 2), "p99": round(p.p99, 2), "max": round(p.max, 2)}


def latency_dict(stats: rx_latency.WindowStats | None) -> dict[str, Any] | None:
    """A window's latency (total and segments) as a plain dict, None when there were no samples."""
    if stats is None:
        return None
    return {"n": stats.n, "total": pct_dict(stats.total), "bt": pct_dict(stats.bt), "phone": pct_dict(stats.phone),
            "net": pct_dict(stats.net), "pc": pct_dict(stats.pc)}


# ---------------------------------------------------------------------------------------------------------------------
# The rumble analysis (pure: the taps we sent and the records the sender returned).
# ---------------------------------------------------------------------------------------------------------------------
def classify(taps: Sequence[Tap]) -> list[str]:
    """Name every feedback packet: "start" (motors from still to running), "stop" (running to still), "refresh" or "idle"."""
    names: list[str] = []
    running = False
    for t in taps:
        on = bool(t.small or t.large)
        names.append(("refresh" if running else "start") if on else ("stop" if running else "idle"))
        running = on
    return names


def segments(tap: Tap, rec: FbRec) -> dict[str, float] | None:
    """The segments of one write [ms]; None for a write that never finished.

    ``to_sender`` = our send to its arrival (it carries the clock error), ``queue`` = arrival to the handing over of the write,
    ``write`` = the write itself, ``sender`` = arrival to the end of the write, ``total`` = our send to the end of the write.
    """
    if not rec.done_us:
        return None
    send = pc_us(tap.send_ns)
    return {"to_sender": span_us(send, rec.recv_us) / 1000, "queue": span_us(rec.recv_us, rec.enq_us) / 1000,
            "write": span_us(rec.enq_us, rec.done_us) / 1000, "sender": span_us(rec.recv_us, rec.done_us) / 1000,
            "total": span_us(send, rec.done_us) / 1000}


def summarize_writes(taps: Sequence[Tap], recs: Mapping[int, Sequence[FbRec]]) -> dict[str, Any]:
    """Join our packets with the sender's records and summarise the start and stop writes per way (evdev / hidraw).

    Returns:
        ``{"start": {way: {segment: stats}}, "stop": {...}, "refresh": n, "unmatched": n, "failed": n}``; a packet the sender has no record
        of (it merged or dropped it) counts as unmatched, a write that did not finish as failed.
    """
    series: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    out: dict[str, Any] = {"start": {}, "stop": {}, "refresh": 0, "unmatched": 0, "failed": 0}
    for tap, name in zip(taps, classify(taps), strict=True):
        if name == "refresh":
            out["refresh"] += 1
        if name not in ("start", "stop"):
            continue
        found = recs.get(tap.seq, ())
        if not found:
            out["unmatched"] += 1
        for rec in found:
            seg = segments(tap, rec)
            if seg is None:
                out["failed"] += 1
                continue
            for key, ms in seg.items():
                series[(name, "hidraw" if rec.way == RUMBLE_HIDRAW else "evdev", key)].append(ms)
    for (name, way, key), values in series.items():
        out[name].setdefault(way, {})[key] = series_stats(values)
    return out


def first_after(taps: Sequence[Tap], t_ns: int, off: bool = True) -> Tap | None:
    """The first packet sent after ``t_ns`` that has the motors off (or on, with ``off`` False)."""
    return next((t for t in taps if t.send_ns >= t_ns and bool(t.small or t.large) != off), None)


# ---------------------------------------------------------------------------------------------------------------------
# The run.
# ---------------------------------------------------------------------------------------------------------------------
class SelfTest:
    """One run of the scenario for one pad. ``start()`` runs it on a thread; ``snapshot()`` is for the window; ``abort()`` ends it."""

    def __init__(self, slot: int, plan: Plan | None = None) -> None:
        """Prepare a run for the pad in ``slot`` (nothing is sent until start())."""
        self.slot = slot
        self.plan = plan or Plan()
        self.results: list[StepResult] = []
        self.started_at = datetime.now().astimezone()
        self.step = ""
        self.fraction = 0.0              # progress of the current step, 0..1
        self.samples = 0                 # latency samples collected so far (the window shows it while the user presses buttons)
        self.finished = False
        self.collecting = False          # the sender's records are wanted
        self.sender_ok = False           # the sender answered ST_BEGIN: it knows the self-test
        self.error = ""
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None
        self._taps: list[Tap] = []
        self._tap_lock = threading.Lock()
        self._ref = 1
        self._began = False

    # ----- control -----
    @staticmethod
    def refusal(slot: int) -> str | None:
        """Why a run cannot start now (an English reason, the window translates it): None when it can."""
        if not 0 <= slot < len(S.alive) or not S.alive[slot]:
            return "pad"
        if not (testmode.active and S.states[slot]["blocked"]):
            return "block"
        return None

    def start(self) -> None:
        """Run the scenario on its own thread (a one-shot job, not a loop: an exception ends it with a report, it is not restarted)."""
        global current
        inbox.clear()
        current = self
        self._thread = threading.Thread(target=self._main, name="selftest", daemon=True)
        self._thread.start()

    def abort(self) -> None:
        """End the run at the next tick; what was measured so far stays in the report."""
        self._abort.set()

    @property
    def running(self) -> bool:
        """Whether the run is in progress."""
        return not self.finished

    def join(self, timeout: float | None = None) -> None:
        """Wait for the thread to end (tests, shutdown)."""
        if self._thread is not None:
            self._thread.join(timeout)

    def snapshot(self) -> tuple[str, int, float, int]:
        """For the window: (the current step, its number from 0, its progress 0..1, the latency samples so far)."""
        idx = STEPS.index(self.step) if self.step in STEPS else 0
        return self.step, idx, self.fraction, self.samples

    # ----- helpers -----
    def _wait(self, secs: float, progress: Callable[[], None] | None = None) -> None:
        """Sleep ``secs`` in short ticks; an abort raises AbortedError."""
        end = time.monotonic() + secs
        while (left := end - time.monotonic()) > 0:
            if self._abort.wait(min(TICK_S, left)):
                raise AbortedError
            if progress is not None:
                progress()
        if self._abort.is_set():
            raise AbortedError

    def _until(self, ready: Callable[[], bool], timeout: float) -> bool:
        """Wait for ``ready()`` in short ticks (an abort raises AbortedError); -> whether it became true before the timeout."""
        end = time.monotonic() + timeout
        while not ready():
            if time.monotonic() >= end:
                return False
            self._wait(min(TICK_S, max(0.0, end - time.monotonic())))
        return True

    def _tap(self, i: int, vals: Sequence[int], pkt: bytes, send_ns: int) -> None:
        """rx_net.fb_tap: remember our feedback packets for this pad (the network layer calls it from the feedback thread)."""
        if i == self.slot:
            with self._tap_lock:
                self._taps.append(Tap(vals[0], vals[1], int.from_bytes(pkt[2:4], "big"), send_ns))

    def _taps_copy(self) -> list[Tap]:
        """A copy of the taps so far."""
        with self._tap_lock:
            return list(self._taps)

    def _ask(self, op: int, arg: int = 0) -> None:
        """Send a request to the sender (never to one that does not know the self-test)."""
        if self._sender_version() >= SELFTEST_SENDER:
            self._ref += 1
            _net().send_selftest(self.slot, op, arg, self._ref)

    def _sender_version(self) -> int:
        """The sender's version for this pad (0 = an older sender that sent no version packet)."""
        info = S.sender_info[self.slot] if self.slot < len(S.sender_info) else None
        return info[0] if info else 0

    def _result(self, step: str) -> StepResult:
        """Start the result of a step and make it the current one."""
        self.step, self.fraction = step, 0.0
        r = StepResult(step)
        self.results.append(r)
        return r

    # ----- the thread -----
    def _main(self) -> None:
        """Run the steps; whatever happens, restore the pad (cleanup) and mark the run finished."""
        net = _net()
        try:
            net.fb_tap = self._tap
            for name in STEPS:
                self._run_step(name)
        except AbortedError:
            self.error = "aborted"
        finally:
            self._cleanup(net)

    def _run_step(self, name: str) -> None:
        """Run one step, with its timing; an exception in it is that step's failure (the others still run), an abort is not caught here."""
        r = self._result(name)
        t0 = time.monotonic()
        try:
            getattr(self, f"_step_{name}")(r)
        except AbortedError:
            r.status, r.note = ABORTED, r.note or "ended by the user"
            raise
        except Exception as e:  # noqa: BLE001 - a bug or a driver error in one step must not lose the other steps' numbers
            r.status, r.note = FAILED, f"{type(e).__name__}: {e}"
        finally:
            r.seconds = round(time.monotonic() - t0, 2)
            self.fraction = 1.0

    def _cleanup(self, net: Any) -> None:
        """Give everything back: the tap, the latency probe, the feedback hold, the sender's measurement; then mark the run finished."""
        net.fb_tap = None
        testmode.latency_probe = False
        try:
            F.release(self.slot)
            if self._began:
                self._ask(ST_END, 0)
        except Exception as e:  # noqa: BLE001 - the cleanup must reach `finished`
            self.error = self.error or f"cleanup: {e!r}"
        self.collecting = False
        global current
        if current is self:
            current = None
        self.finished = True

    # ----- 1. meta -----
    def _step_meta(self, r: StepResult) -> None:
        """Versions, the pad, the backend, the rumble ways; asks the sender to start collecting and waits for its first answer."""
        i = self.slot
        info = S.sender_info[i]
        ver, ways, why = info if info else (0, 0, 0)
        kind = S.pad_kind[i]
        r.data = {
            "sender_version": ver,
            "rumble_ways": [name for bit, name in ((RUMBLE_EVDEV, "evdev"), (RUMBLE_HIDRAW, "hidraw")) if ways & bit],
            "no_hidraw_reason": why,
            "pad_family": FAMILY_NAMES.get(kind[0], "?") if kind else "DualSense (older sender, no padkind)",
            "pad_mac": _mask_mac(S.pad_id[i]),
            "backend": self._backend(),
            "bluetooth": self._adapter(),
            "channel": None,                # the radio channel / band is not known to the sender
        }
        r.data.update(_versions())
        if ver >= SELFTEST_SENDER:
            self.collecting = True
            self._began = True
            self._ask(ST_BEGIN, 0)
            self.sender_ok = inbox.wait(lambda: bool(inbox.sys), self.plan.ack_s)
        if self.sender_ok:
            r.data["pad_firmware"] = _firmware(inbox.sys[0][5])
        elif ver < SELFTEST_SENDER:
            r.note = f"the sender is older than the self-test (version {ver} < {SELFTEST_SENDER}): its steps are skipped"
        else:
            r.note = "the sender did not answer the self-test request"

    def _backend(self) -> str:
        """The virtual pad of this slot ("HIDMaestro (DualSense)", ...), or "none"."""
        try:
            import rx_ds4
            return BACKEND_NAMES.get(rx_ds4.kinds[self.slot] or "", "none")
        except (ImportError, IndexError):
            return "unknown"

    def _adapter(self) -> dict[str, Any]:
        """The Bluetooth controller of the sender in use and the radio link of this pad (when known)."""
        import rx_bt
        c = rx_bt.active_controller()
        link = rx_diag.link_of(self.slot)
        return {"adapter": None if c is None else {"hci": c.index, "name": c.name, "usb": c.usb},
                "link_hci": None if link is None else link.hci}

    # ----- 2. idle -----
    def _step_idle(self, r: StepResult) -> None:
        """Sample the network and the pad's Bluetooth link at rest."""
        i = self.slot
        keys = ("rtt_ms", "jitter_ms", "loss_pct", "pad_hz", "state_pps", "rssi_dbm", "link_quality_pct")
        series: dict[str, list[float]] = {k: [] for k in keys}
        n0 = rx_diag.net
        lost0, late0, dup0 = n0.lost, n0.late, n0.dup

        def sample() -> None:
            now = time.monotonic()
            link = rx_diag.link_of(i, now)
            info = S.info[i]
            vals = (rx_diag.rtt_ms(), n0.jitter_ms, n0.loss_pct, info[1] if info else None, S.pps[i],
                    link.rssi if link else None, None if link is None or link.quality is None else link.quality * 100 / 255)
            for k, v in zip(keys, vals, strict=True):
                if v is not None:
                    series[k].append(float(v))
            self.fraction = min(1.0, (now - t0) / self.plan.idle_s)

        t0 = time.monotonic()
        steps = max(1, round(self.plan.idle_s / 0.5))
        for _ in range(steps):
            self._wait(self.plan.idle_s / steps, sample)
            sample()
        r.data = {"seconds": self.plan.idle_s, "series": {k: series_stats(v) for k, v in series.items()},
                  "lost": n0.lost - lost0, "late": n0.late - late0, "duplicated": n0.dup - dup0}
        if not series["rtt_ms"]:
            r.status, r.note = NO_DATA, "no round trip measured (the sender is silent or too old)"

    # ----- 3. latency -----
    def _step_latency(self, r: StepResult) -> None:
        """Collect every latency sample of the user's window (they press buttons and move the sticks)."""
        i, window = self.slot, self.plan.window_s
        all_rows: list[rx_latency.Row] = []
        btn_rows: list[rx_latency.Row] = []
        last = {"all": time.perf_counter_ns(), "btn": time.perf_counter_ns()}
        testmode.latency_probe = True       # samples are taken although the output to the game is blocked

        def poll() -> None:
            for key, only in (("all", False), ("btn", True)):
                new = rx_latency.rows_since(i, last[key], only)
                if new:
                    last[key] = new[-1][0]
                    (btn_rows if only else all_rows).extend(row[1:] for row in new)
            self.samples = len(all_rows)
            self.fraction = min(1.0, (time.monotonic() - t0) / window)

        t0 = time.monotonic()
        try:
            self._wait(window, poll)
            poll()
        finally:
            testmode.latency_probe = False
        stats = rx_latency.window_stats(all_rows) if len(all_rows) >= MIN_LATENCY_SAMPLES else None
        r.data = {"window_s": window, "samples": len(all_rows), "all_changes": latency_dict(stats),
                  "buttons": latency_dict(rx_latency.window_stats(btn_rows)), "button_samples": len(btn_rows)}
        if stats is None:
            r.status = NO_DATA
            r.note = (f"{len(all_rows)} samples (< {MIN_LATENCY_SAMPLES}): press buttons and move the sticks, "
                      "or the sender is older than the latency measurement")

    # ----- 4. vibration -----
    def _step_vibration(self, r: StepResult) -> None:
        """Pulses at several levels, then the three ways a rumble ends."""
        if not self.sender_ok:
            r.status, r.note = SKIPPED, "needs a sender with the self-test"
            return
        i, plan = self.slot, self.plan
        plays = [(lvl, 0) for lvl in plan.levels] + [(0, lvl) for lvl in plan.levels] + [(plan.levels[-1], plan.levels[-1])]
        orders: list[tuple[int, int, int]] = []      # (small, large, perf ns when the pulse was ordered)
        for k, (small, large) in enumerate(plays):
            self.fraction = k / (len(plays) + 3)
            ordered = time.perf_counter_ns()
            F.pulse_for(i, small, large, plan.pulse_s)
            orders.append((small, large, ordered))
            self._wait(plan.pulse_s + plan.gap_s)
        r.data["pulses"] = self._pulse_data(orders)
        self.fraction = len(plays) / (len(plays) + 3)
        r.data["watchdog"] = self._watchdog_case()
        self.fraction = (len(plays) + 1) / (len(plays) + 3)
        r.data.update(self._game_cases())

    def _pulse_data(self, orders: Sequence[tuple[int, int, int]]) -> dict[str, Any]:
        """Join the pulses with the sender's records (after asking for the results) and add our own reaction time."""
        taps = self._taps_copy()
        self._fetch_results()
        react: list[float] = []
        for small, large, t_ns in orders:
            tap = next((t for t in taps if t.send_ns >= t_ns and (t.small, t.large) == (small, large)), None)
            if tap is not None:
                react.append((tap.send_ns - t_ns) / NS_PER_MS)
        with inbox.cond:
            recs = {k: list(v) for k, v in inbox.fb.items()}
        out = summarize_writes(taps, recs)
        out.update(levels=[{"small": s, "large": lg} for s, lg, _ in orders], pulse_order_to_send_ms=series_stats(react),
                   sent=len(taps))
        return out

    def _watchdog_case(self) -> dict[str, Any]:
        """Send one rumble command and then nothing: the sender's watchdog must stop the motors by itself."""
        i, net = self.slot, _net()
        wait = SENDER_WATCHDOG_S + 0.7
        f = rx_fbstate.fb[i]
        rgb = (f["r"], f["g"], f["b"])
        F.hold(i, wait + 1.0)                       # our packet must not be refreshed by the feedback thread
        before = len(inbox.watchdog)
        try:
            net.send_feedback(i, (0, 255, *rgb))
            self._wait(wait)
        finally:
            net.send_feedback(i, (0, 0, *rgb))      # whatever happened, the motors stop now
            F.release(i)
        self._fetch_results()
        recs = inbox.watchdog[before:]
        stop = [span_us(rec.recv_us, rec.done_us) / 1000 for rec in recs if rec.done_us]
        return {"expected_ms": SENDER_WATCHDOG_S * 1000, "stop_after_last_command_ms": series_stats(stop), "records": len(recs)}

    def _game_cases(self) -> dict[str, Any]:
        """"The game stopped sending": rumble set like a game would, then silence (STALE) or reports without motors (GAP)."""
        i = self.slot
        if rx_swap.route(i) != i:
            return {"game_stale": {"skipped": "the players are swapped"}, "game_gap": {"skipped": "the players are swapped"}}
        stale = self._game_case(i, F.GAME_RUMBLE_STALE_S, with_reports=False)
        self.fraction = 0.9
        gap = self._game_case(i, F.GAME_RUMBLE_GAP_S, with_reports=True)
        F.reset(i)
        return {"game_stale": stale, "game_gap": gap}

    def _game_case(self, i: int, expected_s: float, with_reports: bool) -> dict[str, Any]:
        """One case: a game rumble of 200, then no motor reports (with_reports: other reports go on). -> the time until the motors stop."""
        self._wait(0.3)                              # the previous stop is out
        t0 = time.perf_counter_ns()
        F.game(i, large=200, small=0)
        deadline = time.monotonic() + expected_s + 1.5
        found: Tap | None = None
        while time.monotonic() < deadline and found is None:
            if with_reports:
                F.game(i, rgb=(0, 90, 255))          # a menu: colour reports, no motors
            self._wait(TICK_S)
            found = first_after(self._taps_copy(), t0)
        F.reset(i)
        return {"expected_ms": expected_s * 1000, "stopped_after_ms": None if found is None else round((found.send_ns - t0) / NS_PER_MS, 1)}

    # ----- 5. audio -----
    def _step_audio(self, r: StepResult) -> None:
        """The haptics path, then the speaker path (each only when the pad and the sender can)."""
        r.data["note"] = "measures the path (frames, buffers, writes), not the sound"
        for name, kind, snap in (("haptics", SR_HAPT, ST_HAPTICS), ("speaker", SR_SPK, ST_SPEAKER)):
            r.data[name] = self._audio_path(name, kind, snap)
        if all("skipped" in v for v in (r.data["haptics"], r.data["speaker"])):
            r.status, r.note = SKIPPED, "neither path is available for this pad"

    def _audio_path(self, name: str, kind: int, snap: int) -> dict[str, Any]:
        """Play the test stream through one path and collect the counters of both ends."""
        i = self.slot
        if not self.sender_ok:
            return {"skipped": "needs a sender with the self-test"}
        link: Any
        if name == "haptics":
            import rx_haptics
            why, link = rx_haptics.unavailable(i), rx_haptics.link(rx_swap.route(i))
            units, seconds = _haptics_stream(self.plan.tone_s, self.plan.sweep_s)
        else:
            import rx_speaker
            why, link = rx_speaker.unavailable(i), rx_speaker.speaker(rx_swap.route(i))
            units, seconds = _speaker_stream(self.plan.tone_s, self.plan.sweep_s)
        if why is not None:
            return {"skipped": "not available for this pad (not connected, an older sender, or not the master pad)"}
        before = (link.sent, link.dropped, link.overruns)
        self._ask_stream_reset(snap)
        link.inject(units)
        self._wait(seconds + self.plan.stream_tail_s)
        sent = (link.sent - before[0], link.dropped - before[1], link.overruns - before[2])
        self._ask(ST_SNAP, snap)
        got = self._until(lambda: (kind, 0) in inbox.streams and (kind, 1) in inbox.streams, self.plan.result_s)
        out: dict[str, Any] = {"receiver": {"sent": sent[0], "dropped": sent[1], "ring_overruns": sent[2]}}
        if got:
            out["sender"] = stream_dict(inbox.streams[(kind, 0)], inbox.streams[(kind, 1)])
            out["sent_minus_received"] = sent[0] - out["sender"]["chunks"]
        else:
            out["sender"] = None
            out["note"] = "the sender sent no counters"
        return out

    def _ask_stream_reset(self, snap: int) -> None:
        """Forget the previous counters of a stream before it is played again (a repeated run keeps no stale numbers)."""
        kind = SR_HAPT if snap == ST_HAPTICS else SR_SPK
        with inbox.cond:
            inbox.streams.pop((kind, 0), None)
            inbox.streams.pop((kind, 1), None)

    # ----- 6. cpu -----
    def _step_cpu(self, r: StepResult) -> None:
        """End the sender's measurement and read the processor, the scheduling and the hidraw write numbers."""
        if not self.sender_ok:
            r.status, r.note = SKIPPED, "needs a sender with the self-test"
            return
        self._fetch_results(final=True)
        r.data = cpu_dict(inbox)
        if inbox.done is None:
            r.status, r.note = NO_DATA, "the sender's final answer did not arrive"

    def _fetch_results(self, final: bool = False) -> None:
        """Ask the sender for its records and wait for the closing one (asked once more when it does not come).

        Args:
            final: End the measurement (ST_END: also the system, counter and stream records); otherwise ST_SNAP / ST_RECORDS: the rumble
                records so far, the measurement goes on.
        """
        for _ in range(2):
            with inbox.cond:
                inbox.done = None
            self._ask(ST_END if final else ST_SNAP, ST_RECORDS)
            if inbox.wait(lambda: inbox.done is not None, self.plan.result_s):
                break
        if final:
            self._began = False                     # ended: the cleanup does not ask again

    # ----- 7. mic -----
    def _step_mic(self, r: StepResult) -> None:
        """The microphone is not supported: a flag, never a measurement."""
        r.status, r.note = SKIPPED, "microphone not supported"
        r.data = {"supported": False}


def _mask_mac(mac: str | None) -> str | None:
    """A pad's MAC with the last three bytes hidden (the report may be pasted anywhere)."""
    return None if not mac else ":".join(mac.split(":")[:3] + ["xx"] * 3)


def _firmware(v: int) -> str | None:
    """The pad's firmware version field of SR_SYS: 0 = unknown."""
    return f"0x{v:04x}" if v else None


def _versions() -> dict[str, Any]:
    """The receiver's and the sender's code versions (commit, as far as they are known)."""
    import rx_update
    build = S.sender_build
    return {"receiver_commit": rx_update.version()[0] or None, "sender_commit": (build[0] if build else None) or None}


def stream_dict(a: Sequence[int], b: Sequence[int]) -> dict[str, Any]:
    """The two SR_HAPT / SR_SPK parts as the named counters of the report (depth and target in ms)."""
    names = ("chunks", "played", "underruns", "overruns", "dropped", "repeated", "late", "lost", "stale", "resyncs", "unwritten")
    out: dict[str, Any] = dict(zip(names, (*a[:6], *b[:5]), strict=True))
    out["depth_ms"] = round((b[5] & 0xFFFF) / 10, 1)
    out["buffer_ms"] = round((b[5] >> 16) / 10, 1)
    return out


def sys_dict(v: Sequence[int]) -> dict[str, Any]:
    """One SR_SYS record ``(flags, governor, clock kHz, max clock kHz, load x100, firmware, cpus | fast cpus << 8)`` as named values."""
    return {"governor": GOVERNORS[v[1] - 1] if 0 < v[1] <= len(GOVERNORS) else None, "cpu_mhz": round(v[2] / 1000),
            "cpu_max_mhz": round(v[3] / 1000), "load1": v[4] / 100, "cpus": v[6] & 0xFF, "fast_cpus": v[6] >> 8,
            "realtime": bool(v[0] & SRF_RT), "pm_qos": bool(v[0] & SRF_QOS), "pinned_to_fast_cores": bool(v[0] & SRF_PINNED)}


def cpu_dict(box: Inbox) -> dict[str, Any]:
    """The sender's system records (the start and the end of the run) and the hidraw write numbers."""
    out: dict[str, Any] = {}
    with box.cond:
        if box.sys:
            out["at_start"], out["at_end"] = sys_dict(box.sys[0]), sys_dict(box.sys[-1])
        if box.count:
            n, errors, mean, top, full = box.count[:5]
            out["hidraw_since_connect"] = {"writes": n, "errors": errors, "mean_us": mean, "max_us": top, "queue_full": full}
        out["hidraw_writes_in_test"] = {
            ("reports", "audio")[k]: {"n": w[0], "p50_us": w[1], "p95_us": w[2], "p99_us": w[3], "max_us": w[4], "errors": w[5]}
            for k, w in sorted(box.writes.items())}
    return out


def _haptics_stream(tone_s: float, sweep_s: float) -> tuple[list[bytes], float]:
    """The haptics test stream (tone, a short silence, a sweep, then the silence that ends it) as chunks, and its length [s]."""
    import rx_haptics as H
    rate, frames = H.HAPT_RATE, H.HAPT_FRAMES
    tone = [0.7 * 32768 * math.sin(2 * math.pi * 160 * k / rate) for k in range(round(tone_s * rate))]
    gap = [0.0] * round(SILENCE_S * rate)
    n = round(sweep_s * rate)
    phase, sweep = 0.0, []
    for k in range(n):                                  # 60 -> 400 Hz, the phase is integrated so the sweep has no jumps
        phase += 2 * math.pi * (60 + 340 * k / n) / rate
        sweep.append(0.7 * 32768 * math.sin(phase))
    values = tone + gap + sweep
    values += [0.0] * (-len(values) % frames)
    side = H.quantize(values, 1.0)
    chunks = [H.make_chunk(side[k:k + frames], side[k:k + frames]) for k in range(0, len(side), frames)]
    chunks += [H.ZERO_CHUNK] * (math.ceil(H.SILENCE_END_S / H.CHUNK_S) + 1)
    return chunks, len(chunks) * H.CHUNK_S


def _speaker_stream(tone_s: float, sweep_s: float) -> tuple[list[Any], float]:
    """The speaker test stream (tone, a short silence, a sweep, silence) as source frames, and its length [s]."""
    from array import array

    import rx_speaker as K
    rate, frame = K.SPK_RATE, K.SRC_FRAME
    tone = [0.4 * 32768 * math.sin(2 * math.pi * 440 * k / rate) for k in range(round(tone_s * rate))]
    gap = [0.0] * round(SILENCE_S * rate)
    n = round(sweep_s * rate)
    phase, sweep = 0.0, []
    for k in range(n):                                  # 200 -> 3000 Hz
        phase += 2 * math.pi * (200 + 2800 * k / n) / rate
        sweep.append(0.4 * 32768 * math.sin(phase))
    values = tone + gap + sweep
    values += [0.0] * (-len(values) % frame)
    ints = [round(v) for v in values]
    mono = K.mix(ints, ints, 1.0)
    frames: list[Any] = [mono[k:k + frame] for k in range(0, len(mono), frame)]
    frames += [array("h", bytes(frame * 2))] * (math.ceil(K.H.SILENCE_END_S / K.FRAME_S) + 2)
    return frames, len(frames) * K.FRAME_S
