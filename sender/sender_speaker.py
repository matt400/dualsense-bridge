"""Sender: the game's sound on the pad's speaker (spk packets -> jitter buffer -> Opus -> HID report 0x36).

The receiver sends 10 ms frames of 48 kHz mono sound (see the spk packet in proto, ~100 per second). This module plays them to the pad:

    spk packet -> SpeakerStream.feed -> PcmRing (jitter buffer) -> Report3x.finish -> OpusEncoder -> Rumble.write_haptics

* One report type for both streams. The speaker travels in report 0x36 next to the haptics sub-packet, so a pad that plays haptics
  and the speaker gets one report per tick. ``Report3x`` is that report: with no speaker stream it is byte for byte report 0x32 (what
  sender_haptics always sent), with one it is 0x36 and every ``finish`` pops one PCM frame from the ring and Opus-encodes it straight
  into the report. The haptics stream is unchanged apart from using this report.
* Timing. The pad takes exactly one Opus frame per report and reports every 10.667 ms (93.75 per second); any other period dropped
  out in P3a. The receiver resamples the sound to that clock (93.75 frames per second, see rx_speaker), so the ring stays shallow. An
  older receiver sends 100 per second (and the pad then plays 6 % too low), so the ring is watched: deeper than ``high`` -> one
  10 ms frame is removed (about one in 16, 6 times a second). It is not cut out: the frame that would play fades out into the one
  after it (``crossfade``), so the waveform stays continuous at both ends of the report. A hard cut was inaudible on a sine tone
  but crackled on speech and music. An empty ring plays a silent frame (never a skipped report) and is refilled to the target
  depth before real audio continues.
  All ring and frame math is in whole 10 ms frames.
* Who ticks. While no haptics stream writes, the speaker has its own timer with absolute deadlines (``due += period``). While a
  haptics stream writes, the haptics tick writes the 0x36 reports and the speaker timer only follows its clock (it re-arms one period
  after the last audio report), so there is one clock and exactly one report per period; when the haptics stream ends, the speaker
  timer is already in phase.
* Lifecycle. The first frame puts the pad into audio mode and sets the volume (Rumble.speaker_begin). SPK_END (or 0.2 s without
  a packet) ends the stream: what is buffered plays, then ~100 ms of silence, then Rumble.speaker_end sets the volume to 0 and leaves
  audio mode (P3a: that removes the pop at the end). While no stream runs, nothing is sent.

Rumble owns the HID file descriptor and the sequence counter; this module builds the report and asks Rumble to write it.
"""
from __future__ import annotations

import asyncio
import struct
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Final

from proto import HAPT_CHUNK, HAPT_FRAMES, HAPT_RATE, SPK_BYTES, SPK_END, SPK_FRAME, SPK_START, UINT32_MASK
from sender_haptics import COUNTER_AT, CRC_AT, DATA_AT, HEADER, REPORT_LEN, SEQ_AT, U32_HALF
from sender_opus import OpusEncoder
from sender_rumble import BT_SEQ_MASK, BT_SEQ_SHIFT, BYTE_MASK, CRC_LEN, CRC_SEED, Rumble

# ---- report 0x36 (measured on the real pad, P3a) ----
REPORT_ID: Final = 0x36
REPORT36_LEN: Final = 398                   # with the report id; the HID descriptor says 397 data bytes
HAPT_END_AT: Final = DATA_AT + HAPT_CHUNK   # 77: the byte after the haptics samples = the speaker sub-packet
SPK_SUB_ID: Final = 0x93                    # speaker sub-packet: 0x13 | "sized" bit, then its length, then the Opus packet
OPUS_AT: Final = HAPT_END_AT + 2
CRC36_AT: Final = REPORT36_LEN - CRC_LEN
_SEED_CRC: Final = zlib.crc32(CRC_SEED)     # zlib.crc32(data, _SEED_CRC) == zlib.crc32(CRC_SEED + data)

# ---- jitter buffer and timing (all in whole 10 ms frames) ----
PERIOD_S: Final = HAPT_FRAMES / HAPT_RATE   # the pad's report clock: 10.667 ms
FRAME_S: Final = 0.010                      # one speaker frame
RING_FRAMES: Final = 64                     # capacity: 640 ms
HIGH_OVER: Final = 2                        # deeper than target + this: one frame is dropped (the PC's 100/s against the pad's 93.75/s)
LIMIT_OVER: Final = 5                       # deeper than target + this: a burst after a stall, the oldest audio is dropped at once
MAX_GAP_FRAMES: Final = 8                   # a gap up to this long (80 ms) is a loss and is filled with silence; a larger one restarts
TIMEOUT_S: Final = 0.2                      # no packet for this long ends the stream
TAIL_TICKS: Final = 10                      # silent reports after the last frame, before the volume goes to 0 (~107 ms)
LATE_S: Final = 0.004                       # a tick later than this is counted as late
RESYNC_S: Final = 0.05                      # a tick later than this (a stalled loop): restart the timeline instead of catching up
PRIME_MAX_TICKS: Final = 8                  # after an underrun wait at most this many ticks for the ring to refill
FOLLOW_S: Final = 0.002                     # the speaker timer wakes this long after the haptics tick it follows
RETRY_S: Final = 1.0                        # a refused start (USB pad, no HID report) is retried after this
_SILENCE: Final = memoryview(bytes(SPK_BYTES))
_NO_HAPTICS: Final = memoryview(bytes(HAPT_CHUNK))
_SAMPLES: Final = struct.Struct(f"<{SPK_FRAME}h")   # one frame as samples (explicit little endian, like the spk packet)
_FADE_LAST: Final = SPK_FRAME - 1
_FADE_IN: Final = tuple(range(SPK_FRAME))           # the weight of the next frame at each sample: 0 .. SPK_FRAME - 1


def crossfade(a: memoryview, b: memoryview) -> None:
    """Overwrite frame ``a`` with ``a`` fading out into ``b`` (a linear ramp over the whole frame), in place.

    This is how the sender removes one 10 ms frame of drift: ``a`` plays, but its end already is the end of ``b``, so the next frame
    (the one after ``b``) continues the waveform without a jump. The first sample stays ``a``'s (it follows the previous report) and
    the last one is ``b``'s (the next report follows it). The blend is a convex combination, so it cannot overflow int16.
    It allocates a few short lists, about 6 times a second (the drift correction), nothing in the normal path.

    Args:
        a: SPK_BYTES of s16 little endian samples, overwritten.
        b: SPK_BYTES of s16 little endian samples, left as is.
    """
    xs, ys = _SAMPLES.unpack_from(a), _SAMPLES.unpack_from(b)
    half = _FADE_LAST // 2
    _SAMPLES.pack_into(a, 0, *[(x * (_FADE_LAST - w) + y * w + half) // _FADE_LAST for x, y, w in zip(xs, ys, _FADE_IN, strict=True)])


class Report3x:
    """The audio report of a pad: 0x32 (haptics only) or 0x36 (haptics + the speaker's Opus frame), built once and reused.

    The two reports share the first 77 bytes (header, counter, the haptics sub-packet); 0x36 adds the speaker sub-packet after them.
    One buffer serves both, so the haptics stream writes its samples to ``payload`` whatever the report becomes.

    Attributes:
        chunks: One haptics chunk per report.
        payload: The 64 bytes of haptics samples inside the report.
        buf: The whole buffer (the Opus encoder writes into it at OPUS_AT).
    """

    chunks = 1

    def __init__(self) -> None:
        """Create the report with its fixed bytes in place (as a plain 0x32)."""
        self.buf = bytearray(REPORT36_LEN)
        self.buf[:DATA_AT] = HEADER
        self._view = memoryview(self.buf)
        self._short = self._view[:REPORT_LEN]
        self.payload = self._view[DATA_AT:DATA_AT + HAPT_CHUNK]
        self._counter = 0
        self._fill: Callable[[], None] | None = None

    def attach(self, fill: Callable[[], None], size: int) -> None:
        """Turn the report into a 0x36: from now on ``fill`` puts an Opus packet of ``size`` bytes at OPUS_AT before every report.

        Args:
            fill: Called by ``finish`` to write the next packet into ``buf``.
            size: The packet length in bytes.
        """
        self.buf[HAPT_END_AT:CRC36_AT] = bytes(CRC36_AT - HAPT_END_AT)
        self.buf[0], self.buf[HAPT_END_AT], self.buf[HAPT_END_AT + 1] = REPORT_ID, SPK_SUB_ID, size
        self._fill = fill

    def detach(self) -> None:
        """Back to a plain 0x32 (the bytes after the haptics sub-packet must be zero again)."""
        self._fill = None
        self.buf[0] = HEADER[0]
        self.buf[HAPT_END_AT:CRC36_AT] = bytes(CRC36_AT - HAPT_END_AT)

    def finish(self, seq: int) -> memoryview:
        """Set the sequence number, the report counter and the CRC32; return the report to write.

        Args:
            seq: The report sequence number shared with every other report of the pad (low 4 bits, high nibble of byte 1).

        Returns:
            A view of the whole report (valid until the next call).
        """
        buf = self.buf
        buf[SEQ_AT] = (seq & BT_SEQ_MASK) << BT_SEQ_SHIFT
        self._counter = (self._counter + 1) & BYTE_MASK
        buf[COUNTER_AT] = self._counter
        if self._fill is None:
            struct.pack_into("<I", buf, CRC_AT, zlib.crc32(self._short[:CRC_AT], _SEED_CRC))
            return self._short
        self._fill()
        struct.pack_into("<I", buf, CRC36_AT, zlib.crc32(self._view[:CRC36_AT], _SEED_CRC))
        return self._view


class PcmRing:
    """Ring buffer of whole 10 ms frames: a frame is copied in and out with slice assignments, nothing is allocated per frame.

    Attributes:
        depth: The frames buffered.
    """

    def __init__(self, frames: int = RING_FRAMES) -> None:
        """Create an empty ring of ``frames`` frames."""
        self._cap = frames
        self._view = memoryview(bytearray(frames * SPK_BYTES))
        self._read = 0
        self.depth = 0

    def clear(self) -> None:
        """Forget everything buffered."""
        self._read = self.depth = 0

    def push(self, frame: bytes | memoryview) -> int:
        """Append one frame; when the ring is full the oldest frame makes room.

        Args:
            frame: SPK_BYTES of samples.

        Returns:
            The frames dropped to make room (0 or 1).
        """
        over = 1 if self.depth == self._cap else 0
        if over:
            self._read = (self._read + 1) % self._cap
            self.depth -= 1
        at = ((self._read + self.depth) % self._cap) * SPK_BYTES
        self._view[at:at + SPK_BYTES] = frame
        self.depth += 1
        return over

    def pop_into(self, dst: memoryview) -> None:
        """Move the oldest frame into ``dst`` (SPK_BYTES); the ring must not be empty."""
        at = self._read * SPK_BYTES
        dst[:SPK_BYTES] = self._view[at:at + SPK_BYTES]
        self._read = (self._read + 1) % self._cap
        self.depth -= 1

    def drop(self, frames: int) -> int:
        """Throw away up to ``frames`` of the oldest frames; return how many were dropped."""
        n = min(frames, self.depth)
        self._read = (self._read + n) % self._cap
        self.depth -= n
        return n


@dataclass(slots=True)
class Counters:
    """What one stream did (for the log line at its end).

    Attributes:
        frames: Frames received.
        played: Reports written with audio.
        silent: Reports written with silence (underruns, refills, the tail).
        underruns: Times the ring ran dry in the middle of a stream.
        dropped: Frames removed (drift: crossfaded away; bursts: dropped).
        late: Ticks later than LATE_S.
        lost: Frames missing between packets (filled with silence).
        stale: Late, repeated or reordered frames refused.
        resyncs: Times the stream restarted after a jump of the frame counter.
        unwritten: Reports not written (a full queue, paused by the game's motors, an error).
        encoder_errors: Frames the encoder refused (the report then repeats the previous packet).
    """

    frames: int = 0
    played: int = 0
    silent: int = 0
    underruns: int = 0
    dropped: int = 0
    late: int = 0
    lost: int = 0
    stale: int = 0
    resyncs: int = 0
    unwritten: int = 0
    encoder_errors: int = 0

    def text(self) -> str:
        """The counters as one log line."""
        return ", ".join(f"{f.name} {getattr(self, f.name)}" for f in fields(self))


class SpeakerStream:
    """The speaker of one pad: takes spk frames, buffers them and plays them at the pad's own pace.

    Attributes:
        idx: The pad slot (for log lines).
        rumble: The pad's rumble backend (owner of the HID descriptor).
        active: A stream is running (the pad is in audio mode and the timer runs).
        stats: The counters of the current (or last) stream.
        target: The wanted depth of the ring [frames].
    """

    def __init__(self, idx: int, rumble: Rumble, report: Report3x, drives: Callable[[], bool], buffer_ms: float,
                 volume: int, opus_bytes: int) -> None:
        """Create an idle stream.

        Args:
            idx: The pad slot (for log lines).
            rumble: The pad's rumble backend.
            report: The pad's audio report (shared with the haptics stream).
            drives: Returns True while a haptics stream is writing reports (then its tick is the clock).
            buffer_ms: The jitter buffer [ms]; at least two frames.
            volume: The speaker volume byte, 0..255.
            opus_bytes: Bytes per 10 ms Opus packet (64 kbit/s = 80).

        Raises:
            OSError: libopus is missing or the encoder cannot be created.
        """
        self.idx, self.rumble, self.report, self.drives = idx, rumble, report, drives
        self.volume = volume
        self.encoder = OpusEncoder(opus_bytes)
        self.encoder.bind(report.buf, OPUS_AT)
        self.target = max(2, round(buffer_ms / (FRAME_S * 1000)))
        self.high, self.limit = self.target + HIGH_OVER, self.target + LIMIT_OVER
        self.ring = PcmRing()
        self._spare = memoryview(bytearray(SPK_BYTES))          # the frame the drift correction fades into
        self.active = self.ending = self.priming = False
        self.stats = Counters()
        self._expected = 0                                      # the frame the next packet must carry
        self._rx = 0.0                                          # when the last packet arrived
        self._due = 0.0                                         # when the next tick is due (absolute)
        self._prime_ticks = 0
        self._tail = 0                                          # silent reports still to send after the last frame
        self._retry = 0.0
        self._timer: asyncio.TimerHandle | None = None
        self._why = "end of stream"

    # ---------- frames from the network ----------
    def feed(self, frame: int, flags: int, data: memoryview) -> None:
        """Take one spk frame (called for every packet; the first one of a stream starts it).

        Args:
            frame: The number of the frame (10 ms frames, modulo 2**32).
            flags: SPK_START / SPK_END.
            data: SPK_BYTES of signed 16-bit little endian samples (copied into the ring, not kept).
        """
        now = time.monotonic()
        if not self.active and not self._begin(frame, now):
            return
        self._rx = now
        st = self.stats
        st.frames += 1
        if flags & SPK_START and st.frames > 1:
            self._restart(frame)                                # the PC began a new stream without ending the old one
        else:
            delta = ((frame - self._expected + U32_HALF) & UINT32_MASK) - U32_HALF     # signed: frames ahead (+) or behind (-)
            if -MAX_GAP_FRAMES <= delta < 0:
                st.stale += 1                                   # late, repeated or reordered: its place in time has passed
                return
            if abs(delta) > MAX_GAP_FRAMES:
                self._restart(frame)
            else:
                for _ in range(delta):                          # a loss keeps the later frames in their place in time
                    st.dropped += self.ring.push(_SILENCE)
                st.lost += delta
        self._expected = (frame + 1) & UINT32_MASK
        st.dropped += self.ring.push(data)
        if self.ring.depth > self.limit:                        # a stall and then a burst: keep the newest audio, not a late echo
            st.dropped += self.ring.drop(self.ring.depth - self.high)
        if flags & SPK_END:
            self.ending, self._why = True, "end of stream"

    def _restart(self, frame: int) -> None:
        """Start the ring over at ``frame``: the stream jumped (a restarted receiver, a long gap) or began again."""
        self.stats.resyncs += 1
        self.ring.clear()
        self.priming, self._prime_ticks = True, 0
        self.ending = False
        self._expected = frame

    def _begin(self, frame: int, now: float) -> bool:
        """Put the pad into audio mode, set the volume and start the timer; False when the pad cannot play (retried after a second)."""
        if now < self._retry:
            return False
        if not self.rumble.speaker_begin(self.volume):
            self._retry = now + RETRY_S
            print(f"Controller {self.idx}: speaker frame ignored - this pad cannot play audio (USB or no HID report)")
            return False
        self.active, self.ending, self.priming, self._prime_ticks, self._tail = True, False, False, 0, TAIL_TICKS
        self.ring.clear()
        self.stats = Counters()
        self._expected = frame
        self._why = "end of stream"
        self.report.attach(self.fill, self.encoder.size)
        self._due = now + self.target * FRAME_S                 # the playout delay: the first tick finds `target` frames
        self._timer = asyncio.get_running_loop().call_at(self._due, self._tick)
        print(f"Controller {self.idx}: speaker start (buffer {self.target * 10} ms, Opus {self.encoder.size} B per frame)")
        return True

    # ---------- the pad's clock ----------
    def _tick(self) -> None:
        """Timer callback: write one report with the speaker's next frame, or follow the haptics clock; arm the next tick."""
        self._timer = None
        if not self.active:
            return
        if not self.rumble.speaker:                             # Rumble ended the stream (a run of write errors, a closed pad)
            self._finish("the pad left audio mode")
            return
        now = time.monotonic()
        if not self.ending and now - self._rx > TIMEOUT_S:
            self.ending, self._why = True, "timeout"
        if self.ending and self.ring.depth == 0:
            if self._tail <= 0:
                self._finish(self._why)
                return
            self._tail -= 1
        if self.drives() and now - self.rumble.t_audio < 2 * PERIOD_S:
            self._due = self.rumble.t_audio + PERIOD_S + FOLLOW_S   # the haptics tick writes and consumes our frames
        else:
            self._write(now)
        self._timer = asyncio.get_running_loop().call_at(self._due, self._tick)

    def _write(self, now: float) -> None:
        """Write one report from the speaker's own clock (no haptics: the haptics part is silent) and advance the deadline."""
        lag = now - self._due
        if lag > LATE_S:
            self.stats.late += 1
        if lag > RESYNC_S:
            self._due = now
        self.report.payload[:] = _NO_HAPTICS
        if not self.rumble.write_haptics(self.report):
            self.stats.unwritten += 1
        self._due += PERIOD_S

    def fill(self) -> None:
        """Called for every audio report while the stream runs: encode the next frame (or silence) into the report."""
        ring, st = self.ring, self.stats
        depth = ring.depth
        if self.priming:
            if depth >= self.target or self._prime_ticks >= PRIME_MAX_TICKS or self.ending:
                self.priming = False
            else:
                self._prime_ticks += 1
        pcm = self.encoder.pcm
        if self.priming or depth == 0:
            pcm[:] = _SILENCE
            st.silent += 1
            if depth == 0 and not self.priming and not self.ending:
                st.underruns += 1
                self.priming, self._prime_ticks = True, 0
        else:
            ring.pop_into(pcm)
            if depth > self.high:                               # more arrives than is played: the PC's clock is faster
                ring.pop_into(self._spare)                      # remove one frame without a jump: this one fades into the next
                crossfade(pcm, self._spare)
                st.dropped += 1
            st.played += 1
        if not self.encoder.encode():
            st.encoder_errors += 1

    # ---------- end ----------
    def _finish(self, why: str) -> None:
        """End the stream: stop the timer, set the volume to 0 and give the actuators back to rumble, log what happened."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self.active = self.ending = False
        self.report.detach()
        self.rumble.speaker_end()
        self.ring.clear()
        print(f"Controller {self.idx}: speaker end ({why}): {self.stats.text()}")

    def close(self) -> None:
        """Stop for good (the pad is going away or the sender is closing); safe to call when idle."""
        if self.active:
            self._finish("closed")
        self.encoder.close()
