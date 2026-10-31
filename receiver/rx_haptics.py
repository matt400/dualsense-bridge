"""Receiver: game HD haptics for a Bluetooth DualSense - the game's haptic waveform goes to the sender, the sender plays it.

A game that drives the DualSense voice coils renders the waveform as audio: channels 3 / 4 of a 4-channel 48 kHz stream into
the pad's USB audio endpoint. HIDMaestro's composite profile ("dualsense-composite", opt-in per pad) gives the virtual pad that
endpoint; this module turns what arrives there into T_HAPT packets (the pad takes 3 kHz, 8-bit, stereo):

  Decimator   48 kHz -> 3 kHz (16:1): a Kaiser FIR low-pass (passband ~1 kHz, stopband from ~1.5 kHz, ~50 dB). Stdlib only: the
              taps are a dot product (math.sumprod on a list window), ~2.6 % of a core for the 2 haptic channels at 48 kHz and
              ~0.25 ms per 10 ms block, measured; numpy would be ~0.2 % but is not worth a dependency
  quantize    strength + a soft clip (never wrap-around) -> signed 8-bit
  Link        one per slot: a PCM ring (written on HIDMaestro's audio pump thread - a copy and nothing else) -> decimate -> 32-frame
              chunks -> a short paced queue -> packets. One thread per link, started on first use, asleep while nothing plays
  glue        composite_wanted / create_composite / detach (rx_ds4), the Windows default output guard, test()

A stream: the first chunk with sound is sent with HAPT_START; digital silence sends nothing once SILENCE_END_S has passed (the
chunks of that short tail are sent as zeros, so the sender's stream stays continuous) and the last packet carries HAPT_END. The
sender needs to hear nothing at all while the pad is quiet: a running stream costs the pad about 15 % of its input reports.
Chunks go out at the audio clock (one per 10.667 ms), not in bursts of whatever window HIDMaestro delivered; the sender's own
jitter buffer (haptics_buffer_ms) and its stream timeout do the rest. No packet is repeated.

The same USB endpoint also carries the game's sound for the pad's speaker (channels 1 / 2): rx_speaker is a second consumer of this
link's ring. The composite pad is created when haptics OR the speaker is wanted; both streams are independent after that.

The virtual pad of slot i is fed by the physical pad rx_swap.route(i) (swapped players): the setting, the sender's capability
and the packets all follow that physical pad, like the game feedback does.
"""
from __future__ import annotations

import math
import threading
import time
from array import array
from collections import deque
from collections.abc import Callable, Sequence
from operator import mul
from typing import Any, Final, TypeAlias

import rx_audio
import rx_events as ev
import rx_hm
import rx_settings
import rx_state as S
import rx_swap
from proto import CAP_HAPTICS, HAPT_CHUNK, HAPT_END, HAPT_FRAMES, HAPT_RATE, HAPT_START
from rx_config import C
from rx_i18n import _

HAPTICS_SENDER: Final = 13                  # from this sender version the T_HAPT packet exists (with the CAP_HAPTICS bit)
BYTES_PER_SAMPLE: Final = 2                 # the USB audio stream is 16-bit little-endian PCM
FRAME_BYTES_MIN: Final = 8                  # smallest frame we accept: 4 channels x 2 bytes

TAPS_AT_48K: Final = 255                    # FIR length at 48 kHz; scaled with the rate to keep the same transition band
CUTOFF_HZ: Final = 1250.0                   # -6 dB point: flat to ~1 kHz, ~50 dB down from ~1.5 kHz (the 3 kHz output's Nyquist)
KAISER_BETA: Final = 5.0
FULL_SCALE: Final = 256.0                   # int16 full scale / 256 = the s8 full scale at 100 %
KNEE: Final = 96                            # the soft clip is linear up to this s8 value, then bends towards 127
S8_MAX: Final = 127
CLIP_RANGE: Final = 1023                    # the soft clip table covers +-this (a 200 % gain on a full-scale peak is 256)

CHUNK_S: Final = HAPT_FRAMES / HAPT_RATE    # 10.667 ms of haptics per packet
MIN_SPACING_S: Final = CHUNK_S * 0.9        # while catching up, packets may come this close ...
CATCHUP_DEPTH: Final = 2                    # ... when more than this many chunks wait
MAX_QUEUE: Final = 4                        # chunks waiting for their turn (~43 ms); older ones are dropped (a gap = loss)
SILENCE_END_S: Final = 0.15                 # digital silence this long ends the stream (the sender's own timeout is 200 ms)
STALL_S: Final = 0.12                       # no audio from HIDMaestro this long while streaming = the game stopped rendering
IDLE_WAIT_S: Final = 1.0                    # the link thread's longest sleep (a write wakes it at once)
RING_BYTES: Final = 1 << 17                 # ~340 ms of 4 x 48 kHz x 16-bit; a power of two (the index is a mask)
RING_MASK: Final = RING_BYTES - 1
RING_SAFE: Final = RING_BYTES * 3 // 4      # more unread than this: the writer is about to lap us - skip ahead
WAKE_BYTES: Final = 1536                    # an idle link is woken once this much audio (4 ms) waits: windows of silence stay cheap

SENDER_WAIT_S: Final = 1.0                  # a pad that just connected: how long to wait for its ident and version packets
COMPOSITE_RETRY_S: Final = 600.0            # after a failed composite create: no new attempt for this long (or until the setting changes)
GUARD_S: Final = 8.0                        # after creating a composite pad: how long the default output is watched
GUARD_POLL_S: Final = 0.25

TEST_HZ: Final = 160.0                      # "Test haptics": a 160 Hz burst, near where the voice coils are strongest
TEST_S: Final = 0.4
TEST_LEVEL: Final = 0.7                     # of the full scale, before the pad's strength

ZERO_CHUNK: Final = bytes(HAPT_CHUNK)
SendFn: TypeAlias = Callable[[int, int, int, bytes], None]


# ---------------- DSP ----------------
def _dot_fallback(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product for Python 3.11 (math.sumprod arrived in 3.12 and is ~2x faster)."""
    return sum(map(mul, a, b))


_dot: Callable[[Sequence[float], Sequence[float]], float] = getattr(math, "sumprod", _dot_fallback)


def _bessel_i0(x: float) -> float:
    """Modified Bessel function I0 (the Kaiser window), by its power series."""
    term = total = 1.0
    k = 1
    while term > 1e-12 * total:
        term *= (x / (2 * k)) ** 2
        total += term
        k += 1
    return total


def design_taps(rate: int) -> list[float]:
    """Kaiser-windowed sinc low-pass for decimating `rate` Hz to HAPT_RATE (unity gain at DC).

    The length scales with the decimation factor, so the transition band (~550 Hz) is the same whatever the input rate.
    """
    q = rate // HAPT_RATE
    n = (TAPS_AT_48K * q // 16) | 1
    mid = (n - 1) / 2
    taps = []
    for i in range(n):
        x = i - mid
        sinc = 2 * CUTOFF_HZ / rate if x == 0 else math.sin(2 * math.pi * CUTOFF_HZ / rate * x) / (math.pi * x)
        taps.append(sinc * _bessel_i0(KAISER_BETA * math.sqrt(1 - (x / mid) ** 2)) / _bessel_i0(KAISER_BETA))
    gain = sum(taps)
    return [t / gain for t in taps]


class Decimator:
    """Streaming FIR decimator for the two haptic channels of an interleaved 16-bit PCM stream (rate -> HAPT_RATE).

    feed() takes any number of whole frames and returns the new 3 kHz samples of both channels (floats on the int16 scale);
    the filter state lives between calls. Digital silence costs almost nothing: once the history is all zero, the FIR is
    skipped and only the output count is worked out.
    """

    def __init__(self, rate: int = 48000, channels: int = 4, left: int = 2, right: int = 3) -> None:
        """Design the filter for the input rate and remember which interleaved channels are the left and right grip."""
        self.q = rate // HAPT_RATE
        self.taps = design_taps(rate)
        self.n = len(self.taps)
        self.stride, self.left, self.right = channels, left, right
        self.frame_bytes = channels * BYTES_PER_SAMPLE
        self._buf: tuple[list[int], list[int]] = ([], [])
        self._quiet = 0                 # input frames of digital silence since the last sound (>= n: the history is zero)

    def reset(self) -> None:
        """Forget the history (after a gap in the audio)."""
        self._buf[0].clear()
        self._buf[1].clear()
        self._quiet = 0

    def feed(self, pcm: bytes | memoryview) -> tuple[list[float], list[float]]:
        """Filter and decimate a block of whole frames. -> (left, right) new output samples, the same count in both."""
        samples = memoryview(pcm).cast("h")
        new = (samples[self.left::self.stride].tolist(), samples[self.right::self.stride].tolist())
        silent = not any(new[0]) and not any(new[1])
        n, q = self.n, self.q
        total = len(self._buf[0]) + len(new[0])
        count = (total - n) // q + 1 if total >= n else 0
        if silent and self._quiet >= n:         # the whole window is zero: no FIR, just keep the phase
            self._quiet += len(new[0])
            for buf in self._buf:
                buf[:] = [0] * (total - count * q)
            return [0.0] * count, [0.0] * count
        self._quiet = self._quiet + len(new[0]) if silent else 0
        out: list[list[float]] = []
        for buf, add in zip(self._buf, new, strict=True):
            buf.extend(add)
            out.append([_dot(self.taps, buf[p:p + n]) for p in range(0, count * q, q)])
            del buf[:count * q]
        return out[0], out[1]


def _soft_clip_table() -> list[int]:
    """Output value for every integer input in +-CLIP_RANGE: linear up to KNEE, then a tanh bend that never passes 127."""
    span = S8_MAX - KNEE
    table = []
    for v in range(-CLIP_RANGE, CLIP_RANGE + 1):
        a = abs(v)
        y = a if a <= KNEE else KNEE + span * math.tanh((a - KNEE) / span)
        table.append(int(math.copysign(min(S8_MAX, round(y)), v)))
    return table


_CLIP: Final = _soft_clip_table()


def quantize(values: Sequence[float], gain: float) -> list[int]:
    """Int16-scale samples -> signed 8-bit with the strength `gain` (1.0 = 100 %) and a soft clip instead of wrap-around."""
    k = gain / FULL_SCALE
    return [_CLIP[min(CLIP_RANGE, max(-CLIP_RANGE, round(v * k))) + CLIP_RANGE] for v in values]


def make_chunk(left: Sequence[int], right: Sequence[int]) -> bytes:
    """HAPT_FRAMES s8 samples per channel -> the packet body: interleaved, the left grip first."""
    out = bytearray(HAPT_CHUNK)
    out[0::2] = array("b", left).tobytes()
    out[1::2] = array("b", right).tobytes()
    return bytes(out)


# ---------------- pacing ----------------
class Pacer:
    """When the next chunk may leave: one per CHUNK_S on absolute deadlines, a little faster while chunks pile up.

    The audio clock of the PC and perf_counter drift apart, and HIDMaestro delivers windows of a few ms at once; a queue
    that is allowed to run 10 % faster than nominal while it holds more than CATCHUP_DEPTH chunks neither grows nor bursts.
    """

    def __init__(self, period: float = CHUNK_S) -> None:
        """Start due immediately; `period` is the time one chunk (or speaker frame) stands for."""
        self.due = 0.0
        self.period = period

    def ready(self, now: float) -> bool:
        """Tell whether a chunk may go out now."""
        return now >= self.due

    def sent(self, now: float, left: int) -> None:
        """Record a send; `left` = chunks still waiting. A send more than one period late re-anchors on `now`."""
        step = self.period * (MIN_SPACING_S / CHUNK_S) if left > CATCHUP_DEPTH else self.period
        self.due = self.due + step if now - self.due < self.period else now + step


# ---------------- the per-slot link ----------------
class Link:
    """HD haptics of one virtual pad slot: audio in, T_HAPT packets out. See the module docstring.

    The audio thread of HIDMaestro only calls write(); everything else - decimation, chunking, pacing, the stream state -
    runs on the link's own thread (step() is that thread's whole body, so tests drive it with a fake clock).
    """

    def __init__(self, idx: int, send: SendFn | None = None, clock: Callable[[], float] = time.perf_counter) -> None:
        """Create the idle link of virtual slot idx; `send(slot, first_frame, flags, chunk)` defaults to rx_net.send_haptics."""
        self.idx = idx
        self._send = send
        self._clock = clock
        self._ring = bytearray(RING_BYTES)
        self._w = self._r = 0                       # total bytes written / read (the ring index is the value masked)
        self._wake = threading.Event()
        self._lock = threading.Lock()               # the link's thread vs detach() / stop() from other threads
        self._inject: deque[bytes] = deque()        # chunks from test(): they take the same way out as the game's
        self._dec: Decimator | None = None
        self._pcm_at = 0.0                          # when write() last ran
        self._pend: tuple[list[int], list[int]] = ([], [])
        self._queue: deque[bytes | None] = deque()  # chunks to send; None = "end the stream here"
        self._pacer = Pacer()
        self._counter = 0                           # the first-frame number of the next chunk (wraps at 2**32)
        self._active = False                        # chunks are being produced (a stream is on)
        self._sent_started = False                  # a HAPT_START went out and no HAPT_END since
        self._from_pcm = False                      # the running stream comes from the game's audio (not from test())
        self._quiet = 0                             # consecutive silent chunks while active
        self._noted: set[str] = set()               # event log notes written since the pad was created
        self._thread: threading.Thread | None = None
        self.sent = self.dropped = self.overruns = 0
        self.channels, self.rate = 4, 48000         # the audio format of the endpoint (set by configure)
        self.speaker_channels = (0, 1)              # the interleaved channels of the speaker's left and right (rx_speaker)
        self.on_write: Callable[[int], None] | None = None   # called on the audio thread with the bytes written so far (rx_speaker)

    # ----- the audio thread -----
    def write(self, view: memoryview, n: int) -> None:
        """Take n bytes of PCM (the HIDMaestro audio pump thread: a copy into the ring and a wake-up, nothing else)."""
        w = self._w
        i = w & RING_MASK
        first = RING_BYTES - i
        if n <= first:
            self._ring[i:i + n] = view[:n]
        else:
            self._ring[i:] = view[:first]
            self._ring[:n - first] = view[first:n]
        self._w = w + n                             # published last: the reader never sees bytes that are not written yet
        self._pcm_at = self._clock()
        if (self._active or w + n - self._r >= WAKE_BYTES) and not self._wake.is_set():
            self._wake.set()
        on_write = self.on_write                    # read once: rx_speaker.detach() sets it to None from another thread
        if on_write is not None:
            on_write(w + n)

    # ----- setup -----
    def configure(self, channels: int, rate: int, bits: int, roles: Sequence[str]) -> str | None:
        """Set up the decimator for the audio format of the pad's USB endpoint. -> None, or why this format cannot be used."""
        if bits != BYTES_PER_SAMPLE * 8:
            return f"{bits}-bit audio (16-bit expected)"
        if channels < FRAME_BYTES_MIN // BYTES_PER_SAMPLE or rate % HAPT_RATE or rate < 4 * HAPT_RATE:
            return f"{channels} channels at {rate} Hz (4 channels at a multiple of {HAPT_RATE} Hz expected)"
        roles = list(roles)
        left, right = (roles.index("hapticLeft"), roles.index("hapticRight")) if {"hapticLeft", "hapticRight"} <= set(roles) else (2, 3)
        if max(left, right) >= channels:
            return "the haptic channels are not in the stream"
        spk = [roles.index("speakerLeft"), roles.index("speakerRight")] if {"speakerLeft", "speakerRight"} <= set(roles) else [0, 1]
        with self._lock:
            self._dec = Decimator(rate, channels, left, right)
            self._r = self._w
            self.channels, self.rate = channels, rate
            self.speaker_channels = (spk[0], spk[1]) if max(spk) < channels else (0, 1)
        return None

    @property
    def written(self) -> int:
        """Bytes of PCM written into the ring so far (a position for read_from)."""
        return self._w

    @property
    def pcm_at(self) -> float:
        """When write() last ran (the link's clock)."""
        return self._pcm_at

    def read_from(self, r: int) -> tuple[bytes | None, int]:
        """Audio written since byte position `r` (whole frames) -> (the bytes or None, the new position).

        A second consumer of the ring (the speaker) keeps its own position. When the writer lapped it, it skips ahead to the
        last half of the ring (on a frame boundary: a cut inside one would swap the channels for good).
        """
        frame = self.channels * BYTES_PER_SAMPLE
        w = self._w
        if w - r > RING_SAFE:
            r = w - RING_BYTES // 2 // frame * frame
        n = (w - r) // frame * frame
        if n <= 0:
            return None, r
        i = r & RING_MASK
        first = RING_BYTES - i
        data = bytes(self._ring[i:i + n]) if n <= first else bytes(self._ring[i:]) + bytes(self._ring[:n - first])
        return data, r + n

    def wake(self) -> None:
        """Make the thread look at the settings and the audio now."""
        self._wake.set()

    def ensure_thread(self) -> None:
        """Start the link's thread (once; the thread sleeps while nothing plays)."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = S.start_thread(f"haptics{self.idx}", self.run)

    def run(self) -> None:
        """The link's thread: step until the receiver stops."""
        while not S.stop.is_set():
            self._wake.clear()                      # before the step: a write during it keeps the event set - nothing is lost
            wait = self.step(self._clock())
            self._wake.wait(wait)
        self.stop()

    # ----- the link's thread -----
    def step(self, now: float) -> float:
        """One pass: take new audio, make chunks, send what is due. -> how long the thread may sleep (a write wakes it)."""
        with self._lock:
            gain = self._gain()
            data = self._read() if self._dec is not None else None
            if gain is None:                        # off, master only, no capable sender: the audio is dropped, any stream ends
                self._inject.clear()                # a test tone must not wait for the pad to be allowed later and play then
                self._end()
            else:
                if data:
                    self._feed(data, gain)
                self._take_injected()
                if self._active and self._from_pcm and now - self._pcm_at > STALL_S:
                    self._end()                     # the game stopped rendering: Windows parks the endpoint, no more callbacks
            return self._pace(now)

    def _gain(self) -> float | None:
        """The strength (1.0 = 100 %) when this link may play, None when it must stay quiet."""
        t = rx_swap.route(self.idx)
        cfg = rx_settings.cur.haptics.get(S.profile_key(t))
        if not cfg.enabled and not self._inject:
            return None
        if not sender_haptics(t):
            self._note("sender", _("{pad}: gra daje haptykę - nadajnik w starszej wersji jej nie przekaże (kliknij Aktualizuj)"))
            return None
        import rx_master            # rx_master imports rx_feedback, which imports rx_settings - lazily, like rx_feedback does
        if not rx_master.allowed(t, "haptics"):
            self._note("master", _("{pad}: gra daje haptykę - pominięta (haptyka gry tylko dla pada master)"))
            return None
        return cfg.gain / 100

    def _note(self, why: str, text: str) -> None:
        """One event log line per reason and pad connection (the audio keeps arriving; the line must not repeat)."""
        if why not in self._noted:
            self._noted.add(why)
            t = rx_swap.route(self.idx)
            print(f"Controller {t}: game haptics not used ({why})")
            ev.info(text.format(pad=ev.pad_name(t)))

    def _read(self) -> bytes | None:
        """Take the unread audio out of the ring (whole frames); None when there is none. A writer that lapped us: skip ahead."""
        assert self._dec is not None
        frame = self._dec.frame_bytes
        w, r = self._w, self._r
        if w - r > RING_SAFE:
            r = w - RING_BYTES // 2 // frame * frame    # whole frames: a cut inside one would swap the channels for good
            self.overruns += 1
            self._dec.reset()
            self._pend[0].clear()
            self._pend[1].clear()
        n = (w - r) // frame * frame
        if n <= 0:
            self._r = r
            return None
        i = r & RING_MASK
        first = RING_BYTES - i
        data = bytes(self._ring[i:i + n]) if n <= first else bytes(self._ring[i:]) + bytes(self._ring[:n - first])
        self._r = r + n
        return data

    def _feed(self, data: bytes, gain: float) -> None:
        """Audio -> 3 kHz -> s8 -> chunks."""
        assert self._dec is not None
        left, right = self._dec.feed(data)
        if not left:
            return
        self._pend[0].extend(quantize(left, gain))
        self._pend[1].extend(quantize(right, gain))
        while len(self._pend[0]) >= HAPT_FRAMES:
            chunk = make_chunk(self._pend[0][:HAPT_FRAMES], self._pend[1][:HAPT_FRAMES])
            del self._pend[0][:HAPT_FRAMES], self._pend[1][:HAPT_FRAMES]
            self._chunk(chunk, from_pcm=True)

    def _take_injected(self) -> None:
        """Chunks from test() go through the same path as the game's - as fast as the queue drains (it would drop the rest)."""
        while self._inject and len(self._queue) < MAX_QUEUE - 1:
            self._chunk(self._inject.popleft(), from_pcm=False)

    def _chunk(self, chunk: bytes, from_pcm: bool) -> None:
        """One finished chunk: sound opens / keeps a stream, silence is dropped (idle) or counted towards the end (active)."""
        if chunk == ZERO_CHUNK:
            if not self._active:
                return
            self._quiet += 1
            if self._quiet * CHUNK_S >= SILENCE_END_S:
                self._end()
                return
        else:
            if not self._active:
                self._active, self._from_pcm = True, from_pcm
            self._quiet = 0
        self._enqueue(chunk)

    def _enqueue(self, item: bytes | None) -> None:
        """Queue a chunk (or the end mark); a full queue drops its oldest chunk - the gap in the numbering tells the sender."""
        if len(self._queue) >= MAX_QUEUE:
            for k, queued in enumerate(self._queue):
                if queued is not None:
                    del self._queue[k]
                    self._counter = (self._counter + HAPT_FRAMES) & 0xFFFFFFFF
                    self.dropped += 1
                    break
        self._queue.append(item)

    def _end(self) -> None:
        """Close the running stream: a HAPT_END goes out after the chunks still waiting."""
        if self._active:
            self._active = False
            self._quiet = 0
            self._enqueue(None)

    def _pace(self, now: float) -> float:
        """Send one chunk when it is due. -> how long to sleep."""
        if self._queue and self._pacer.ready(now):
            item = self._queue.popleft()
            self._emit(item)
            self._pacer.sent(now, len(self._queue))
        if self._queue:
            return max(0.0005, self._pacer.due - now)
        return STALL_S / 2 if self._active else IDLE_WAIT_S

    def _emit(self, item: bytes | None) -> None:
        """Send a chunk (START on the first of a stream) or, for the end mark, a silent chunk with HAPT_END."""
        if item is None:
            if not self._sent_started:
                return
            flags, item, self._sent_started = HAPT_END, ZERO_CHUNK, False
            if self.dropped or self.overruns:
                print(f"Controller {rx_swap.route(self.idx)}: haptics stream ended ({self.sent} chunks, {self.dropped} dropped, "
                      f"{self.overruns} ring overruns)")
        else:
            flags, self._sent_started = (0 if self._sent_started else HAPT_START), True
        send = self._send
        if send is None:
            import rx_net
            send = rx_net.send_haptics
        send(rx_swap.route(self.idx), self._counter, flags, item)
        self._counter = (self._counter + HAPT_FRAMES) & 0xFFFFFFFF
        self.sent += 1

    # ----- from other threads -----
    def test(self, gain: float) -> None:
        """A short tone through the same path ("Test haptics"): chunks plus the silence that ends the stream."""
        n = round(TEST_S * HAPT_RATE) // HAPT_FRAMES * HAPT_FRAMES
        tone = [TEST_LEVEL * 32768 * math.sin(2 * math.pi * TEST_HZ * k / HAPT_RATE) for k in range(n)]
        side = quantize(tone, gain)
        chunks = [make_chunk(side[k:k + HAPT_FRAMES], side[k:k + HAPT_FRAMES]) for k in range(0, n, HAPT_FRAMES)]
        chunks += [ZERO_CHUNK] * (math.ceil(SILENCE_END_S / CHUNK_S) + 1)
        self._inject.extend(chunks)
        self.ensure_thread()
        self.wake()

    def detach(self) -> None:
        """The virtual pad is gone: drop the audio, forget the state and send the HAPT_END at once (the sender releases the pad)."""
        with self._lock:
            self._r = self._w
            if self._dec is not None:
                self._dec.reset()
            self._pend[0].clear()
            self._pend[1].clear()
            self._inject.clear()
            self._noted.clear()
            self._active = False
            self._quiet = 0
            self._queue.clear()
            if self._sent_started:
                self._emit(None)

    def stop(self) -> None:
        """The receiver is closing: end a running stream now."""
        self.detach()


# ---------------- the slots ----------------
links: list[Link] = []
_composite_off_until = 0.0              # after a failed composite create: no attempt before this (monotonic time)
_good_defaults: dict[str, str] = {}     # the last default outputs seen that were not a gamepad's endpoint ({role: device id})


def init() -> None:
    """Create the per-slot links (nothing runs until a composite pad attaches its audio or a test tone is asked for)."""
    links[:] = [Link(i) for i in range(C.NUM_PADS)]


def link(i: int) -> Link:
    """The link of slot i (created on demand without init(), as the slot thread tolerates)."""
    while len(links) <= i:
        links.append(Link(len(links)))
    return links[i]


def stop_all() -> None:
    """Receiver shutdown: end every running stream so the senders release their pads."""
    for lk in links:
        lk.stop()
    import rx_speaker
    rx_speaker.stop_all()


def sender_haptics(i: int) -> bool:
    """Tell whether the sender of physical pad i plays haptics (v13+ and the CAP_HAPTICS bit of its version packet)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= HAPTICS_SENDER and bool(info[1] & CAP_HAPTICS)


def _wanted_somewhere() -> bool:
    """Tell whether any pad setting turns the game haptics on (otherwise no pad needs a look at its identity)."""
    return any(p.enabled for p in rx_settings.cur.haptics.pads.values())


def _await_identity(t: int) -> None:
    """A pad that has just connected: wait (a moment) for its ident and version packets - the setting is by MAC, the CAP by version."""
    deadline = time.monotonic() + SENDER_WAIT_S
    while S.alive[t] and (S.pad_id[t] is None or S.sender_info[t] is None) and time.monotonic() < deadline and not S.stop.is_set():
        time.sleep(0.05)


def composite_wanted(idx: int) -> bool:
    """Tell whether the virtual pad of slot idx should be created as the USB composite (game haptics and / or the speaker).

    Haptics: the setting is on for the physical pad behind the slot, its sender announced CAP_HAPTICS and it may use the feature
    (master only). The speaker (rx_speaker) has the same three conditions with CAP_SPEAKER. Either one is enough, and no composite
    create may have failed lately. Called by the slot thread just before it creates the pad.
    """
    import rx_speaker
    if time.monotonic() < _composite_off_until or not (_wanted_somewhere() or rx_speaker.wanted_somewhere()):
        return False
    t = rx_swap.route(idx)
    _await_identity(t)
    return _haptics_wanted(idx, t) or rx_speaker.wanted(idx, t)


def _haptics_wanted(idx: int, t: int) -> bool:
    """The haptics half of composite_wanted for virtual slot idx / physical pad t (says so in the event log when the sender is old)."""
    if not rx_settings.cur.haptics.get(S.profile_key(t)).enabled:
        return False
    if not sender_haptics(t):
        if S.alive[t]:
            link(idx)._note("sender", _("{pad}: haptyka gry jest włączona, ale nadajnik w starszej wersji jej nie obsługuje "
                                        "(kliknij Aktualizuj)"))
        return False
    import rx_master
    return rx_master.allowed(t, "haptics")


def create_composite(backend: Any, idx: int, on_fb: Callable[..., None]) -> Any | None:
    """Create the composite virtual pad of slot idx and hook its audio up. -> the pad, or None (logged) to use the plain one.

    Any failure - usbip-win2 refused, no administrator rights, an old HIDMaestro without the profile, an audio format we cannot
    decimate - falls back to the plain DualSense with one clear line in the event log, and no new attempt for COMPOSITE_RETRY_S
    (a refused driver install is not worth repeating at every reconnect).
    """
    global _composite_off_until
    import rx_speaker
    t = rx_swap.route(idx)
    lk = link(idx)
    before = _clean_defaults()
    pad = None
    try:
        pad = backend.create(idx, on_fb, composite=True)
        # first of all: Windows writes the endpoint's stored volume while it enumerates the pad, and HIDMaestro does not replay it
        if not pad.attach_audio_controls(rx_speaker.speaker(idx).set_host_volume):
            print(f"Controller {t}: this HIDMaestro has no audio controls - the Windows volume of the pad's speaker has no effect")
        why = lk.configure(*pad.attach_audio(lk.write))
        if why:
            raise ValueError(why)
    except Exception as e:  # noqa: BLE001 - any HIDMaestro / usbip / .NET failure: the pad is created again without the haptics
        _composite_off_until = time.monotonic() + COMPOSITE_RETRY_S
        if pad is not None:
            try:
                pad.close()
            except Exception as ce:  # noqa: BLE001 - best effort: the slot's plain pad is created next
                print(f"Controller {idx}: closing the composite pad: {ce!r}")
        print(f"Controller {t}: game haptics: composite pad failed ({e!r}) - using the plain pad")
        ev.warn(_("{pad}: haptyka gry - nie udało się utworzyć pada USB (composite): {err} - pad działa bez haptyki").format(
            pad=ev.pad_name(t), err=_describe(e)))
        return None
    lk.ensure_thread()
    rx_speaker.attach(idx, lk)
    if rx_settings.cur.haptics.get(S.profile_key(t)).enabled:
        ev.info(_("{pad}: pad z haptyką gry (HD haptics przez nadajnik)").format(pad=ev.pad_name(t)))
    if rx_speaker.wanted_now(t):
        ev.info(_("{pad}: pad z głośnikiem (dźwięk gry przez nadajnik)").format(pad=ev.pad_name(t)))
    if before:
        threading.Thread(target=_guard_defaults, args=(t, before), name=f"audio guard {idx}", daemon=True).start()
    return pad


def _describe(e: BaseException) -> str:
    """An error as one line for the event log (the HIDMaestro hint when it is one of its exceptions)."""
    return rx_hm.describe_error(e)


def detach(idx: int) -> None:
    """The virtual pad of slot idx was closed: end its streams and forget the audio."""
    if idx < len(links):
        links[idx].detach()
    import rx_speaker
    rx_speaker.detach(idx)


def test(i: int, gain: float | None = None) -> str | None:
    """The "Test haptics" button for physical pad i. -> None when the tone was sent, else why not (for the window).

    gain (1.0 = 100 %) defaults to the saved strength of the pad; the window passes the value it shows.
    """
    if not (0 <= i < C.NUM_PADS) or not S.alive[i]:
        return _("Pad nie jest podłączony.")
    if not sender_haptics(i):
        return _("Nadajnik w starszej wersji (bez haptyki) - kliknij Aktualizuj.")
    import rx_master
    if not rx_master.allowed(i, "haptics"):     # the link would drop the tone: say so instead of "sent"
        return _("Haptyka gry jest tylko dla pada master (Ustawienia → Master).")
    if gain is None:
        gain = rx_settings.cur.haptics.get(S.profile_key(i)).gain / 100
    link(rx_swap.route(i)).test(gain)           # the virtual slot whose packets go to physical pad i
    return None


def _settings_changed(old: rx_settings.Settings, new: rx_settings.Settings) -> None:
    """Settings listener: another haptics or speaker setting clears the failure pause and wakes the links (off ends a stream)."""
    global _composite_off_until
    if old.haptics != new.haptics or old.speaker != new.speaker:
        _composite_off_until = 0.0
        for lk in links:
            lk.wake()
        import rx_speaker
        rx_speaker.wake_all()


rx_settings.on_change(_settings_changed)


# ---------------- the Windows default output ----------------
def _clean_defaults() -> dict[str, str]:
    """The default outputs now ({role: id}), where one that is already a gamepad's endpoint is replaced by the last good one."""
    try:
        ids = rx_audio.default_output_ids()
        names = dict(rx_audio.list_outputs(controllers=True))
    except Exception:  # noqa: BLE001 - no pycaw / COM trouble: the guard is skipped, the pad still works
        return dict(_good_defaults)
    _good_defaults.update({role: dev for role, dev in ids.items() if not rx_audio.is_controller_endpoint(names.get(dev, ""))})
    return dict(_good_defaults)


def restore_defaults(before: dict[str, str]) -> list[str]:
    """Put back every default output that Windows moved to a gamepad's endpoint. -> the roles restored.

    A default that changed to something else (the user, the console mode) is left alone: only the controller's own endpoint
    is taken back.
    """
    now = rx_audio.default_output_ids()
    moved = [role for role, dev in before.items() if now.get(role) not in (None, dev)]
    if not moved:
        return []
    names = dict(rx_audio.list_outputs(controllers=True))
    restored = []
    for role in moved:
        if rx_audio.is_controller_endpoint(names.get(now[role], "")):
            rx_audio.set_default_id(before[role], (role,))
            restored.append(role)
    return restored


def _guard_defaults(t: int, before: dict[str, str]) -> None:
    """Watch the default output for GUARD_S after a composite pad appeared (Windows may switch to its new endpoint)."""
    deadline = time.monotonic() + GUARD_S
    said = False
    while time.monotonic() < deadline and not S.stop.is_set():
        try:
            roles = restore_defaults(before)
        except Exception as e:  # noqa: BLE001 - a COM / pycaw error: say it once and stop watching
            print(f"Controller {t}: game haptics: default audio output guard failed: {e!r}")
            return
        if roles and not said:
            said = True
            print(f"Controller {t}: Windows switched the default audio output to the pad - restored ({', '.join(roles)})")
            ev.warn(_("{pad}: Windows przełączył domyślne wyjście dźwięku na pada - przywrócono poprzednie").format(pad=ev.pad_name(t)))
        S.stop.wait(GUARD_POLL_S)
