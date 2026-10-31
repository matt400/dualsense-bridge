"""Sender: HD haptics from the game to a Bluetooth DualSense (hapt packets -> jitter buffer -> HID report 0x32).

The receiver sends the game's haptics as chunks of 32 stereo frames (3 kHz, signed 8-bit, 10.667 ms each), see the hapt packet in
proto. This module plays them to the pad:

    hapt packet -> HapticsStream.feed -> FrameRing (jitter buffer) -> timer tick -> Report32 -> Rumble.write_haptics

* Timing. The pad plays 3 kHz audio, so one report must go out every 10.667 ms - not earlier (the pad's buffer would overflow), not
  later (it would run dry). The tick is a timer with absolute deadlines (``due += period``, so a late tick is made up for by the
  next one and errors do not add up; ``TickTimer``: a timerfd, which is not late by up to a millisecond like ``call_at``); the jitter
  buffer in front of it absorbs the network. A free-running timer drifts against the
  PC's audio clock, so the depth of the buffer is watched: too deep -> one frame is dropped per tick, too shallow -> one frame is
  repeated (a frame is 0.33 ms - nothing to feel). An empty buffer plays a silence chunk (the stream never stalls) and is filled up
  to the target depth again before the next real chunk is played.
* Packets. A chunk carries the number of its first frame: a gap (a lost packet) is filled with silence, so that later chunks keep
  their place in time; a late or repeated chunk is dropped; a jump too large to be a loss starts the stream over.
* Lifecycle. The first chunk puts the pad into audio mode (Rumble.haptics_begin); the stream ends with HAPT_END (what is still
  buffered is played) or after TIMEOUT_S without a packet; then Rumble.haptics_end brings the normal mode (rumble) back. While no
  stream runs, nothing is sent: every report costs the pad some input rate (measured: ~15-19 % fewer input reports while streaming).
* Joining a running speaker. When the speaker already plays on its own clock, the first tick is put on that clock (whole periods
  from the speaker's next deadline, ``on_clock``), and from that deadline on the speaker leaves the writing to this tick (``drives``);
  at the end the deadline this tick will not use goes back to the speaker (``on_end``). Started at its own phase, the first haptics
  report came a fraction of a period after the speaker's last one, and the speaker took over a couple of ms late: every haptics burst
  (the receiver starts haptics anew after every pause) moved the pad's clock, so the speaker's buffer drained into an underrun or
  grew into drops.
* Fewer, larger reports. The input cost follows the report rate (94/s). A report that carries several chunks is one more
  ChunkReport (``chunks`` = how many it holds): the tick, the thresholds and the buffer follow ``frames`` - nothing else changes.
  The layout of such reports (0x33..0x36) has not been checked on a pad yet, so only 0x32 (``Report32``) exists.

Rumble owns the HID file descriptor and the sequence counter; this module only builds the report and asks Rumble to write it.
"""
from __future__ import annotations

import math
import struct
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Final, Protocol

from proto import HAPT_CHUNK, HAPT_END, HAPT_FRAMES, HAPT_RATE, HAPT_START, UINT32_MASK
from sender_rumble import BT_SEQ_MASK, BT_SEQ_SHIFT, BYTE_MASK, CRC_LEN, CRC_SEED, HapticsReport, Rumble
from sender_tick import TickTimer

# ---- report 0x32 (measured on the real pad, P0) ----
REPORT_ID: Final = 0x32
REPORT_LEN: Final = 142                     # with the report id; the HID descriptor says 141 data bytes
SEQ_AT, COUNTER_AT, DATA_AT = 1, 10, 13
# bytes 2-4: control sub-packet (0x11 | "sized" bit, length 7, flags); 5-9: the pad's audio buffer length x5 (0x10 = 16, the lowest value
# it takes; the community notes' 16,0,0,0,0 was silent); 11-12: haptics sub-packet (0x12 | "sized" bit, length 64) followed by the samples
# at DATA_AT
HEADER: Final = bytes([REPORT_ID, 0x00, 0x91, 0x07, 0xFE, 0x10, 0x10, 0x10, 0x10, 0x10, 0x00, 0x92, HAPT_CHUNK])
BUFFER_AT, BUFFER_BYTES = 5, 5              # the pad's audio buffer length: 16 (least delay) .. 128 (most resistant to Bluetooth jitter)
PAD_BUFFER_DEFAULT: Final = 0x10            # what the sender always sent; the haptics-only report keeps it
CRC_AT: Final = REPORT_LEN - CRC_LEN        # the CRC32 is little endian, seeded with 0xA2 (like 0x31), over bytes 0..CRC_AT-1
_SEED_CRC: Final = zlib.crc32(CRC_SEED)     # zlib.crc32(data, _SEED_CRC) == zlib.crc32(CRC_SEED + data) without building the sum

# ---- jitter buffer and timing ----
FRAME_BYTES: Final = 2                      # one stereo frame: left grip, right grip (signed 8-bit)
RING_FRAMES: Final = 2048                   # capacity: 683 ms, far above any buffer_ms (the limit for the depth is below)
MAX_GAP_FRAMES: Final = 8 * HAPT_FRAMES     # a gap up to this long (85 ms) is a loss and is filled with silence; a larger one restarts
OVERRUN_CHUNKS: Final = 3                   # deeper than target + this many reports: the oldest audio is dropped (a stall, then a burst)
TIMEOUT_S: Final = 0.2                      # no packet for this long ends the stream
LATE_S: Final = 0.004                       # a tick later than this is counted as late
RESYNC_S: Final = 0.05                      # a tick later than this (a stalled loop): no burst of catch-up reports, restart the timeline
PRIME_MAX_TICKS: Final = 8                  # after an underrun wait at most this many ticks (85 ms) for the buffer to refill
U32_HALF: Final = 1 << 31                  # a frame-counter step below this is "ahead", above it "behind" (modulo 2**32)
HANDOVER_S: Final = 0.0005                  # float slack when the speaker asks whether this stream's first tick is due at its deadline
_SILENCE: Final = memoryview(bytes(HAPT_CHUNK * 8))


def on_clock(earliest: float, ref: float | None, period: float) -> float:
    """The first deadline of a stream that starts while the other audio stream of the pad has its clock: ``ref`` +/- whole periods.

    Args:
        earliest: The deadline the stream wants on its own (its playout delay).
        ref: A deadline of the running clock (it may lie after ``earliest``), or None (nothing runs: ``earliest`` is the answer).
        period: The report period.

    Returns:
        The first deadline of the running clock at or after ``earliest``.
    """
    if ref is None:
        return earliest
    return ref + math.ceil((earliest - ref) / period - 1e-9) * period


class ChunkReport(HapticsReport, Protocol):
    """A haptics report the stream can fill: ``payload`` is where the next ``chunks`` chunks of audio are copied."""

    chunks: int                             # how many chunks (HAPT_FRAMES stereo frames each) one report carries
    payload: memoryview                     # chunks * HAPT_CHUNK bytes inside the report


class Report32:
    """Bluetooth output report 0x32: one chunk (32 stereo frames) of audio-haptics, built once and reused.

    The header, the zero padding and the buffer live as long as the object; per report only the sequence number, the counter,
    the samples (written by the stream through ``payload``) and the CRC change - nothing is allocated.

    Attributes:
        chunks: One chunk per report.
        payload: The 64 bytes of samples inside the report, to be overwritten before ``finish``.
    """

    chunks = 1

    def __init__(self) -> None:
        """Create the report with its fixed bytes in place."""
        self._buf = bytearray(REPORT_LEN)
        self._buf[:DATA_AT] = HEADER
        self._view = memoryview(self._buf)
        self.payload = self._view[DATA_AT:DATA_AT + HAPT_CHUNK]
        self._counter = 0

    def finish(self, seq: int) -> memoryview:
        """Set the sequence number, the report counter and the CRC32; return the report to write.

        Args:
            seq: The report sequence number shared with every other report of the pad (low 4 bits, high nibble of byte 1).

        Returns:
            A view of the whole report (valid until the next call).
        """
        buf = self._buf
        buf[SEQ_AT] = (seq & BT_SEQ_MASK) << BT_SEQ_SHIFT
        self._counter = (self._counter + 1) & BYTE_MASK
        buf[COUNTER_AT] = self._counter
        struct.pack_into("<I", buf, CRC_AT, zlib.crc32(self._view[:CRC_AT], _SEED_CRC))
        return self._view


class FrameRing:
    """Ring buffer of stereo frames: a chunk is copied in and out with slice assignments, nothing is allocated per chunk.

    Attributes:
        size: The bytes buffered.
    """

    def __init__(self, frames: int = RING_FRAMES) -> None:
        """Create an empty ring of ``frames`` stereo frames."""
        self._cap = frames * FRAME_BYTES
        self._view = memoryview(bytearray(self._cap))
        self._read = 0
        self.size = 0

    @property
    def depth(self) -> int:
        """The frames buffered."""
        return self.size // FRAME_BYTES

    def clear(self) -> None:
        """Forget everything buffered."""
        self._read = self.size = 0

    def push(self, data: bytes | memoryview) -> int:
        """Append ``data`` (whole frames); when the ring is full the oldest frames make room.

        Args:
            data: The samples, a multiple of FRAME_BYTES long.

        Returns:
            The frames dropped from the front to make room (0 normally).
        """
        n = len(data)
        over = max(0, self.size + n - self._cap)
        if over:
            self._read = (self._read + over) % self._cap
            self.size -= over
        start = (self._read + self.size) % self._cap
        first = self._cap - start
        if n <= first:
            self._view[start:start + n] = data
        else:
            self._view[start:] = data[:first]
            self._view[:n - first] = data[first:]
        self.size += n
        return over // FRAME_BYTES

    def pop_into(self, dst: memoryview, frames: int) -> None:
        """Move the oldest ``frames`` frames to the start of ``dst``.

        Args:
            dst: The destination, at least ``frames`` frames long.
            frames: How many frames; at most ``depth``.
        """
        n = frames * FRAME_BYTES
        start = self._read
        first = self._cap - start
        if n <= first:
            dst[:n] = self._view[start:start + n]
        else:
            dst[:first] = self._view[start:]
            dst[first:n] = self._view[:n - first]
        self._read = (start + n) % self._cap
        self.size -= n

    def drop(self, frames: int) -> int:
        """Throw away up to ``frames`` of the oldest frames; return how many were dropped."""
        n = min(frames * FRAME_BYTES, self.size)
        self._read = (self._read + n) % self._cap
        self.size -= n
        return n // FRAME_BYTES


@dataclass(slots=True)
class Counters:
    """What one stream did (for the log line at its end).

    Attributes:
        chunks: Chunks received.
        played: Reports written with audio.
        underruns: Times the buffer ran dry (a silence chunk was played).
        overruns: Times the buffer was too deep and the oldest audio was dropped.
        dropped: Frames dropped (overruns and drift).
        repeated: Frames repeated to fill a report (drift).
        late: Ticks later than LATE_S.
        lost: Frames missing between chunks (filled with silence).
        stale: Late, repeated or reordered chunks refused.
        resyncs: Times the stream restarted after a jump of the frame counter.
        unwritten: Reports not written (a full queue, paused by the game's motors, an error).
    """

    chunks: int = 0
    played: int = 0
    underruns: int = 0
    overruns: int = 0
    dropped: int = 0
    repeated: int = 0
    late: int = 0
    lost: int = 0
    stale: int = 0
    resyncs: int = 0
    unwritten: int = 0

    def text(self) -> str:
        """The counters as one log line."""
        return ", ".join(f"{f.name} {getattr(self, f.name)}" for f in fields(self))


class HapticsStream:
    """The haptics of one pad: takes hapt chunks, buffers them and writes them to the pad at the pad's own pace.

    Attributes:
        idx: The pad slot (for log lines).
        rumble: The pad's rumble backend (owner of the HID descriptor).
        active: A stream is running (the pad is in audio mode and the tick timer runs).
        writing: The tick has written reports in this stream (the speaker stream follows this clock, see sender_speaker).
        stats: The counters of the current (or last) stream.
        target: The wanted depth of the buffer [frames].
        buffer_ms: The jitter buffer this stream was made with (the pad rebuilds an idle stream when the setting changes).
    """

    def __init__(self, idx: int, rumble: Rumble, buffer_ms: float, report: ChunkReport | None = None,
                 clock: Callable[[], float | None] | None = None, on_end: Callable[[float], None] | None = None) -> None:
        """Create an idle stream.

        Args:
            idx: The pad slot (for log lines).
            rumble: The pad's rumble backend.
            buffer_ms: The jitter buffer [ms]: the playout starts this long after the first chunk, and an underrun is refilled to
                this depth. At least one report.
            report: The report builder; the default is ``Report32``.
            clock: Returns the deadline of the next report of an audio clock that already runs on the pad (the speaker's own), or
                None; a stream that starts then ticks on that clock (see the module docstring).
            on_end: Called with the deadline of the report this stream will not write when a stream that wrote ends, so the speaker
                can write it on the same clock.
        """
        self.idx, self.rumble, self.buffer_ms, self.clock, self.on_end = idx, rumble, buffer_ms, clock, on_end
        self.report: ChunkReport = report if report is not None else Report32()
        self.frames = HAPT_FRAMES * self.report.chunks          # frames one report plays
        self.period = self.frames / HAPT_RATE                   # seconds between reports
        self.target = max(self.frames, round(buffer_ms * HAPT_RATE / 1000))
        self.high = self.target + self.frames                   # deeper: drop one frame per tick (drift)
        self.low = self.target - self.frames // 2               # shallower: repeat one frame per tick (drift)
        self.limit = self.target + OVERRUN_CHUNKS * self.frames  # deeper: drop the oldest audio at once (a burst)
        self.ring = FrameRing()
        self.active = self.ending = self.priming = self.writing = False
        self.stats = Counters()
        self._expected = 0                                      # the frame the next chunk must start at
        self._rx = 0.0                                          # when the last chunk arrived
        self._due = 0.0                                         # when the next tick is due (absolute)
        self._prime_ticks = 0
        self._retry = 0.0                                       # a refused start (the pad cannot play haptics) is retried after this
        self._ticker: TickTimer | None = None                   # the tick timer of the running stream (one descriptor, closed at its end)
        self._timer: TickTimer | None = None                    # the same object while a tick is pending, None otherwise
        self._why = "end of stream"

    # ---------- chunks from the network ----------
    def feed(self, frame: int, flags: int, data: memoryview) -> None:
        """Take one hapt chunk (called for every packet; the first one of a stream starts it).

        Args:
            frame: The number of the chunk's first frame (3 kHz frames, modulo 2**32).
            flags: HAPT_START / HAPT_END.
            data: HAPT_CHUNK bytes of signed 8-bit stereo samples (copied into the buffer, not kept).
        """
        now = time.monotonic()
        if not self.active and not self._begin(frame, now):
            return
        self._rx = now
        st = self.stats
        st.chunks += 1
        if flags & HAPT_START and st.chunks > 1:
            self._restart(frame)                                # the PC began a new stream without ending the old one
        else:
            delta = ((frame - self._expected + U32_HALF) & UINT32_MASK) - U32_HALF     # signed: frames ahead (+) or behind (-)
            if delta < 0 and delta >= -MAX_GAP_FRAMES:
                st.stale += 1                                   # late, repeated or reordered: its place in time has passed
                return
            if delta > MAX_GAP_FRAMES or delta < -MAX_GAP_FRAMES:
                self._restart(frame)
            elif delta:
                st.lost += delta
                self.ring.push(_SILENCE[:delta * FRAME_BYTES])  # a loss keeps the later chunks in their place in time
        self._expected = (frame + HAPT_FRAMES) & UINT32_MASK
        dropped = self.ring.push(data)
        depth = self.ring.depth
        if depth > self.limit:                                  # a stall and then a burst: keep the newest audio, not a late echo
            dropped += self.ring.drop(depth - self.target - self.frames)
        if dropped:
            st.overruns += 1
            st.dropped += dropped
        if flags & HAPT_END:
            self.ending, self._why = True, "end of stream"

    def _restart(self, frame: int) -> None:
        """Start the buffer over at ``frame``: the stream jumped (a restarted receiver, a very long gap) or began again."""
        self.stats.resyncs += 1
        self.ring.clear()
        self.priming, self._prime_ticks = True, 0
        self.ending = False
        self._expected = frame

    def _begin(self, frame: int, now: float) -> bool:
        """Put the pad into audio mode and start the tick timer; False when the pad cannot play haptics (retried after a second)."""
        if now < self._retry:
            return False
        if not self.rumble.haptics_begin():
            self._retry = now + 1.0
            print(f"Controller {self.idx}: haptics chunk ignored - this pad cannot play haptics (USB or no HID report)")
            return False
        self.active, self.ending, self.priming, self._prime_ticks = True, False, False, 0
        self.writing = False
        self.ring.clear()
        self.stats = Counters()
        self._expected = frame
        self._why = "end of stream"
        # the playout delay (the first tick finds `target` frames), on the speaker's clock if it already plays
        self._due = on_clock(now + self.target / HAPT_RATE, self.clock() if self.clock is not None else None, self.period)
        self._ticker = TickTimer(self._tick)
        self._arm()
        print(f"Controller {self.idx}: haptics start (buffer {self.target * 1000 / HAPT_RATE:.0f} ms, "
              f"{self.report.chunks} chunk(s) per report)")
        return True

    def drives(self) -> bool:
        """Whether this tick writes the pad's audio reports: it has written in this stream, or its first tick is due now.

        The speaker asks at its own deadline: True there means "leave this report to the haptics tick" (both are on one clock, see
        ``on_clock``).
        """
        return self.writing or (self.active and self._due <= time.monotonic() + HANDOVER_S)

    def clock_due(self) -> float | None:
        """The deadline of this stream's next tick (a speaker stream that starts joins this clock), or None while idle."""
        return self._due if self.active else None

    # ---------- the pad's clock ----------
    def _tick(self) -> None:
        """Timer callback: write one report (audio, or silence when the buffer is empty) and arm the next tick."""
        self._timer = None
        if not self.active:
            return
        if not self.rumble.haptics:                             # Rumble ended the stream (a run of write errors, a closed pad)
            self._finish("the pad left audio mode")
            return
        now = time.monotonic()
        lag = now - self._due
        if lag > LATE_S:
            self.stats.late += 1
        if lag > RESYNC_S:
            self._due = now
        if not self.ending and now - self._rx > TIMEOUT_S:
            self.ending, self._why = True, "timeout"
        if self.ending and self.ring.depth == 0:
            self._finish(self._why)
            return
        self._play_or_silence()
        if not self.rumble.write_haptics(self.report):
            self.stats.unwritten += 1
        self.writing = True
        self._due += self.period
        self._arm()

    def _arm(self) -> None:
        """Arm the tick timer for the next deadline (``_due``)."""
        if self._ticker is not None:
            self._timer = self._ticker.arm(self._due)

    def _play_or_silence(self) -> None:
        """Fill the report's payload for this tick: the oldest audio, or silence while the buffer refills."""
        ring, st = self.ring, self.stats
        depth = ring.depth
        if self.priming:
            if depth >= self.target or self._prime_ticks >= PRIME_MAX_TICKS or self.ending:
                self.priming = False
            else:
                self._prime_ticks += 1
        if self.priming:
            self.report.payload[:] = _SILENCE[:len(self.report.payload)]
        elif depth >= self.frames or (self.ending and depth):
            self._play(depth)
            st.played += 1
        else:
            self.report.payload[:] = _SILENCE[:len(self.report.payload)]
            st.underruns += 1
            self.priming, self._prime_ticks = True, 0

    def _play(self, depth: int) -> None:
        """Copy the next report's worth of audio into the payload, trimming or stretching it by one frame when the depth drifts."""
        payload, ring, st = self.report.payload, self.ring, self.stats
        if depth < self.frames:                                 # the tail of an ended stream: pad it with silence
            ring.pop_into(payload, depth)
            payload[depth * FRAME_BYTES:] = _SILENCE[:len(payload) - depth * FRAME_BYTES]
            return
        if depth > self.high:                                   # more arrives than is played: the PC's clock is faster
            st.dropped += ring.drop(1)
            depth -= 1
        if depth < self.low:                                    # less arrives than is played: repeat the last frame
            ring.pop_into(payload, self.frames - 1)
            payload[-FRAME_BYTES:] = payload[-2 * FRAME_BYTES:-FRAME_BYTES]
            st.repeated += 1
        else:
            ring.pop_into(payload, self.frames)

    # ---------- end ----------
    def _finish(self, why: str) -> None:
        """End the stream: stop the timer, give the actuators back to rumble and log what happened."""
        if self._ticker is not None:
            self._ticker.close()
            self._ticker = None
        self._timer = None
        wrote = self.writing
        self.active = self.ending = self.writing = False
        self.rumble.haptics_end()
        self.ring.clear()
        print(f"Controller {self.idx}: haptics end ({why}): {self.stats.text()}")
        if wrote and self.on_end is not None:
            self.on_end(self._due)                              # the report due now goes out from the speaker, on this clock

    def close(self) -> None:
        """Stop for good (the pad is going away or the sender is closing); safe to call when idle."""
        if self.active:
            self._finish("closed")
