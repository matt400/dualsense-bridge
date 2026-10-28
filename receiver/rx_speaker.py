"""Receiver: the game's sound on the speaker of a Bluetooth DualSense - channels 1 / 2 of the pad's USB audio go to the sender.

The composite virtual pad (rx_haptics creates it when haptics OR the speaker is wanted) gives the game a "Speakers (Wireless
Controller)" output: 4 channels, 48 kHz, 16-bit; channels 1 / 2 are the speaker (speakerLeft / speakerRight), 3 / 4 the haptics.
rx_haptics.Link already copies the whole window into a ring on HIDMaestro's audio thread; this module is a second consumer of that
ring (its own read position, no second copy of the audio and no second .NET callback):

  mix         left + right -> mono, the pad's gain, a soft clip above 75 % of the full scale (never wrap-around), 48 kHz s16
  SpeakerLink one per slot: ring -> mix -> 480-sample frames (10 ms) -> a short paced queue -> spk packets. One thread per link,
              started on first use, asleep while nothing plays (rx_net.send_speaker)
  glue        wanted / attach / detach / test(), used by rx_haptics (composite pad), rx_ds4 (through rx_haptics) and the window

A stream: the first frame with sound is sent with SPK_START; digital silence sends nothing once SILENCE_END_S has passed (the frames
of that short tail go out as zeros, so the sender's stream stays continuous) and the last packet carries SPK_END. The sender wants
no packet at all while nothing plays: a running stream makes the pad send fewer input reports. Frames go out at the audio clock
(one per 10 ms), not in the bursts HIDMaestro delivers; the sender's jitter buffer and its stream timeout do the rest. No packet
is repeated. All ring and frame index math is in whole frames (the channel count of the endpoint is not always 4).

The virtual pad of slot i is fed by the physical pad rx_swap.route(i): the setting, the sender's capability and the packets
follow that physical pad, like the game feedback and the haptics do.
"""
from __future__ import annotations

import functools
import math
import sys
import threading
import time
from array import array
from collections import deque
from collections.abc import Callable, Sequence
from typing import Final, TypeAlias

import rx_events as ev
import rx_haptics as H
import rx_settings
import rx_state as S
import rx_swap
from proto import CAP_SPEAKER, SPK_BYTES, SPK_END, SPK_FRAME, SPK_RATE, SPK_START
from rx_config import C
from rx_i18n import _

SPEAKER_SENDER: Final = 14                  # from this sender version the T_SPK packet exists (with the CAP_SPEAKER bit)
BYTES_PER_SAMPLE: Final = 2
FRAME_S: Final = SPK_FRAME / SPK_RATE       # 10 ms of sound per packet
MAX_QUEUE: Final = 4                        # frames waiting for their turn (40 ms); older ones are dropped (a gap = loss)
SILENCE_END_S: Final = 0.15                 # digital silence this long ends the stream (the sender's own timeout is 200 ms)
STALL_S: Final = 0.12                       # no audio from HIDMaestro this long while streaming = the game stopped rendering
IDLE_WAIT_S: Final = 1.0                    # the thread's longest sleep (a write wakes it at once)
WAKE_BYTES: Final = 1536                    # an idle link is woken once this much audio (4 ms) waits: windows of silence stay cheap
KNEE: Final = 24576                         # the soft clip is linear up to 75 % of the int16 full scale ...
S16_MAX: Final = 32767
SPAN: Final = S16_MAX - KNEE                # ... then bends towards 32767 (tanh)
# "Test speaker": a short jingle, (note Hz, seconds) - a rising C major arpeggio that lands on a long high C. The notes sit at
# 520-1050 Hz, where the pad's tiny speaker is loudest.
JINGLE: Final[tuple[tuple[float, float], ...]] = (
    (523.25, 0.14), (659.25, 0.14), (783.99, 0.14), (1046.50, 0.28), (783.99, 0.14), (1046.50, 0.50),
)
TEST_SAMPLES: Final = sum(round(secs * SPK_RATE) for _hz, secs in JINGLE)
TEST_FRAMES: Final = -(-TEST_SAMPLES // SPK_FRAME)      # whole 10 ms frames (the last one is padded with zeros)
TEST_S: Final = TEST_SAMPLES / SPK_RATE
TEST_LEVEL: Final = 0.5                     # of the full scale, before the pad's volume
OVERTONE: Final = 0.3                       # the 2nd harmonic: a plain sine sounds thin on a small speaker
DECAY: Final = 4.0                          # a note fades by e**-4 over its length (a soft bell, not an organ)
EDGE: Final = round(0.005 * SPK_RATE)       # 5 ms linear attack and release: no clicks at the note edges
BIG_ENDIAN: Final = sys.byteorder == "big"

ZERO_FRAME: Final = bytes(SPK_BYTES)
SendFn: TypeAlias = Callable[[int, int, int, bytes], None]


# ---------------- DSP ----------------
def _bend(v: float) -> int:
    """A sample above the knee: tanh towards the full scale, the sign kept."""
    a = abs(v)
    return int(math.copysign(min(S16_MAX, KNEE + SPAN * math.tanh((a - KNEE) / SPAN)), v))


def mix(left: Sequence[int], right: Sequence[int], gain: float) -> array[int]:
    """Left and right channel (int16) -> mono int16: the average times `gain` (1.0 = 100 %), a soft clip instead of wrap-around."""
    k = gain / 2
    return array("h", [int(v) if -KNEE <= (v := (a + b) * k) <= KNEE else _bend(v) for a, b in zip(left, right, strict=True)])


def pcm_bytes(samples: array[int]) -> bytes:
    """int16 samples -> little-endian bytes (what the spk packet carries)."""
    if BIG_ENDIAN:
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


@functools.cache
def jingle() -> tuple[int, ...]:
    """The test jingle as mono int16 at SPK_RATE, zero-padded to whole frames (computed once, only when the test button is used)."""
    out: list[int] = []
    amp = TEST_LEVEL * S16_MAX / (1 + OVERTONE)         # the peak of fundamental + overtone stays at TEST_LEVEL
    for hz, secs in JINGLE:
        n = round(secs * SPK_RATE)
        w = 2 * math.pi * hz / SPK_RATE
        for k in range(n):                               # every note starts at phase 0, so it starts at zero amplitude
            env = min(1.0, k / EDGE, (n - k) / EDGE) * math.exp(-DECAY * k / n)
            out.append(round(amp * env * (math.sin(w * k) + OVERTONE * math.sin(2 * w * k))))
    out.extend([0] * (TEST_FRAMES * SPK_FRAME - len(out)))
    return tuple(out)


# ---------------- the per-slot link ----------------
class SpeakerLink:
    """The speaker of one virtual pad slot: audio in (from the slot's rx_haptics.Link), spk packets out. See the module docstring.

    The audio thread of HIDMaestro never touches this object beyond poke(); everything else runs on the link's own thread (step() is
    that thread's whole body, so tests drive it with a fake clock).
    """

    def __init__(self, idx: int, send: SendFn | None = None, clock: Callable[[], float] = time.perf_counter) -> None:
        """Create the idle speaker of virtual slot idx; `send(slot, frame, flags, pcm)` defaults to rx_net.send_speaker."""
        self.idx = idx
        self._send = send
        self._clock = clock
        self.link: H.Link | None = None             # the audio source (set by attach)
        self._r = 0                                 # the read position in the link's ring
        self._wake = threading.Event()
        self._lock = threading.Lock()               # the link's thread vs detach() / stop() from other threads
        self._inject: deque[bytes] = deque()        # frames from test(): they take the same way out as the game's
        self._pend = bytearray()                    # mixed mono bytes that do not make a whole frame yet
        self._queue: deque[bytes | None] = deque()  # frames to send; None = "end the stream here"
        self._pacer = H.Pacer(FRAME_S)
        self._counter = 0                           # the number of the next frame (wraps at 2**32)
        self._active = False                        # frames are being produced (a stream is on)
        self._sent_started = False                  # an SPK_START went out and no SPK_END since
        self._from_pcm = False                      # the running stream comes from the game's audio (not from test())
        self._quiet = 0                             # consecutive silent frames while active
        self._noted: set[str] = set()               # event log notes written since the pad was created
        self._thread: threading.Thread | None = None
        self.sent = self.dropped = self.overruns = 0

    # ----- the audio thread -----
    def poke(self, written: int) -> None:
        """Called by rx_haptics.Link.write on HIDMaestro's audio thread after every write: wake the thread when there is sound to take."""
        if (self._active or written - self._r >= WAKE_BYTES) and not self._wake.is_set():
            self._wake.set()

    # ----- setup -----
    def attach(self, link: H.Link) -> str | None:
        """Take the audio of `link` (its endpoint is configured). -> None, or why the speaker cannot use this audio format."""
        if link.rate != SPK_RATE:
            return f"{link.rate} Hz audio ({SPK_RATE} Hz expected)"
        with self._lock:
            self.link = link
            self._r = link.written
            self._pend.clear()
        link.on_write = self.poke
        return None

    def wake(self) -> None:
        """Make the thread look at the settings and the audio now."""
        self._wake.set()

    def ensure_thread(self) -> None:
        """Start the link's thread (once; the thread sleeps while nothing plays)."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = S.start_thread(f"speaker{self.idx}", self.run)

    def run(self) -> None:
        """The link's thread: step until the receiver stops."""
        while not S.stop.is_set():
            self._wake.clear()                      # before the step: a write during it keeps the event set - nothing is lost
            wait = self.step(self._clock())
            self._wake.wait(wait)
        self.stop()

    # ----- the link's thread -----
    def step(self, now: float) -> float:
        """One pass: take new audio, make frames, send what is due. -> how long the thread may sleep (a write wakes it)."""
        with self._lock:
            gain = self._gain()
            data = self._read() if self.link is not None else None
            if gain is None:                        # off, master only, no capable sender: the audio is dropped, any stream ends
                self._inject.clear()                # a test tone must not wait for the pad to be allowed later and play then
                self._pend.clear()
                self._end()
            else:
                if data:
                    self._feed(data, gain)
                self._take_injected()
                if self._active and self._from_pcm and self.link is not None and now - self.link.pcm_at > STALL_S:
                    self._end()                     # the game stopped rendering: Windows parks the endpoint, no more callbacks
            return self._pace(now)

    def _gain(self) -> float | None:
        """The volume (1.0 = 100 %) when this link may play, None when it must stay quiet."""
        t = rx_swap.route(self.idx)
        cfg = rx_settings.cur.speaker.get(S.profile_key(t))
        if not cfg.enabled and not self._inject:
            return None
        if not sender_speaker(t):
            self._note("sender", _("{pad}: gra daje dźwięk - nadajnik w starszej wersji nie przekaże go na głośnik (kliknij Aktualizuj)"))
            return None
        import rx_master            # rx_master imports rx_feedback, which imports rx_settings - lazily, like rx_haptics does
        if not rx_master.allowed(t, "speaker"):
            self._note("master", _("{pad}: gra daje dźwięk - pominięty (głośnik pada tylko dla pada master)"))
            return None
        return cfg.gain / 100

    def _note(self, why: str, text: str) -> None:
        """One event log line per reason and pad connection (the audio keeps arriving; the line must not repeat)."""
        if why not in self._noted:
            self._noted.add(why)
            t = rx_swap.route(self.idx)
            print(f"Controller {t}: game sound for the speaker not used ({why})")
            ev.info(text.format(pad=ev.pad_name(t)))

    def _read(self) -> bytes | None:
        """Take the unread audio out of the link's ring (whole frames); None when there is none."""
        assert self.link is not None
        data, r = self.link.read_from(self._r)
        if r - self._r != (len(data) if data else 0):   # the writer lapped us: what was pending belongs to a past that is gone
            self.overruns += 1
            self._pend.clear()
        self._r = r
        return data

    def _feed(self, data: bytes, gain: float) -> None:
        """Audio -> mono -> frames."""
        assert self.link is not None
        stride = self.link.channels
        left_at, right_at = self.link.speaker_channels
        samples = memoryview(data).cast("h")
        left, right = samples[left_at::stride].tolist(), samples[right_at::stride].tolist()
        if any(left) or any(right):
            self._pend += pcm_bytes(mix(left, right, gain))
        else:
            self._pend += bytes(len(left) * BYTES_PER_SAMPLE)       # digital silence: no work
        while len(self._pend) >= SPK_BYTES:
            frame = bytes(self._pend[:SPK_BYTES])
            del self._pend[:SPK_BYTES]
            self._frame(frame, from_pcm=True)

    def _take_injected(self) -> None:
        """Frames from test() go through the same path as the game's - as fast as the queue drains (it would drop the rest)."""
        while self._inject and len(self._queue) < MAX_QUEUE - 1:
            self._frame(self._inject.popleft(), from_pcm=False)

    def _frame(self, frame: bytes, from_pcm: bool) -> None:
        """One finished frame: sound opens / keeps a stream, silence is dropped (idle) or counted towards the end (active)."""
        if frame == ZERO_FRAME:
            if not self._active:
                return
            self._quiet += 1
            if self._quiet * FRAME_S >= SILENCE_END_S:
                self._end()
                return
        else:
            if not self._active:
                self._active, self._from_pcm = True, from_pcm
            self._quiet = 0
        self._enqueue(frame)

    def _enqueue(self, item: bytes | None) -> None:
        """Queue a frame (or the end mark); a full queue drops its oldest frame - the gap in the numbering tells the sender."""
        if len(self._queue) >= MAX_QUEUE:
            for k, queued in enumerate(self._queue):
                if queued is not None:
                    del self._queue[k]
                    self._counter = (self._counter + 1) & 0xFFFFFFFF
                    self.dropped += 1
                    break
        self._queue.append(item)

    def _end(self) -> None:
        """Close the running stream: an SPK_END goes out after the frames still waiting."""
        if self._active:
            self._active = False
            self._quiet = 0
            self._enqueue(None)

    def _pace(self, now: float) -> float:
        """Send one frame when it is due. -> how long to sleep."""
        if self._queue and self._pacer.ready(now):
            item = self._queue.popleft()
            self._emit(item)
            self._pacer.sent(now, len(self._queue))
        if self._queue:
            return max(0.0005, self._pacer.due - now)
        return STALL_S / 2 if self._active else IDLE_WAIT_S

    def _emit(self, item: bytes | None) -> None:
        """Send a frame (START on the first of a stream) or, for the end mark, a silent frame with SPK_END."""
        if item is None:
            if not self._sent_started:
                return
            flags, item, self._sent_started = SPK_END, ZERO_FRAME, False
            if self.dropped or self.overruns:
                print(f"Controller {rx_swap.route(self.idx)}: speaker stream ended ({self.sent} frames, {self.dropped} dropped, "
                      f"{self.overruns} ring overruns)")
        else:
            flags, self._sent_started = (0 if self._sent_started else SPK_START), True
        send = self._send
        if send is None:
            import rx_net
            send = rx_net.send_speaker
        send(rx_swap.route(self.idx), self._counter, flags, item)
        self._counter = (self._counter + 1) & 0xFFFFFFFF
        self.sent += 1

    # ----- from other threads -----
    def test(self, gain: float) -> None:
        """The jingle through the same path ("Test speaker"): its frames plus the silence that ends the stream."""
        tune = jingle()
        mono = pcm_bytes(mix(tune, tune, gain))
        frames = [mono[k * SPK_BYTES:(k + 1) * SPK_BYTES] for k in range(TEST_FRAMES)]
        frames += [ZERO_FRAME] * (math.ceil(SILENCE_END_S / FRAME_S) + 1)
        self._inject.extend(frames)
        self.ensure_thread()
        self.wake()

    def detach(self) -> None:
        """The virtual pad is gone: drop the audio, forget the state and send the SPK_END at once (the sender releases the speaker)."""
        with self._lock:
            if self.link is not None:
                self.link.on_write = None
                self._r = self.link.written
            self.link = None
            self._pend.clear()
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
speakers: list[SpeakerLink] = []


def init() -> None:
    """Create the per-slot speakers (nothing runs until a composite pad attaches its audio or a test tone is asked for)."""
    speakers[:] = [SpeakerLink(i) for i in range(C.NUM_PADS)]


def speaker(i: int) -> SpeakerLink:
    """The speaker of slot i (created on demand without init(), as the slot thread tolerates)."""
    while len(speakers) <= i:
        speakers.append(SpeakerLink(len(speakers)))
    return speakers[i]


def attach(idx: int, link: H.Link) -> None:
    """The composite pad of slot idx has its audio: the speaker takes channels 1 / 2 of it (when it can use this audio format)."""
    sp = speaker(idx)
    why = sp.attach(link)
    if why:
        print(f"Controller {rx_swap.route(idx)}: speaker: {why} - the speaker is not used")
        return
    sp.ensure_thread()


def detach(idx: int) -> None:
    """The virtual pad of slot idx was closed: end its stream and forget the audio."""
    if idx < len(speakers):
        speakers[idx].detach()


def stop_all() -> None:
    """Receiver shutdown: end every running stream so the senders release their speakers."""
    for sp in speakers:
        sp.stop()


def wake_all() -> None:
    """Make every speaker thread look at the settings now (a pad turned off ends its stream)."""
    for sp in speakers:
        sp.wake()


def sender_speaker(i: int) -> bool:
    """Tell whether the sender of physical pad i plays the speaker (v14+ and the CAP_SPEAKER bit of its version packet)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= SPEAKER_SENDER and bool(info[1] & CAP_SPEAKER)


def wanted_somewhere() -> bool:
    """Tell whether any pad setting turns the speaker on (otherwise no pad needs a look at its identity)."""
    return any(p.enabled for p in rx_settings.cur.speaker.pads.values())


def wanted_now(t: int) -> bool:
    """Tell whether the speaker setting of physical pad t is on (no sender or master check)."""
    return rx_settings.cur.speaker.get(S.profile_key(t)).enabled


def wanted(idx: int, t: int) -> bool:
    """The speaker half of rx_haptics.composite_wanted for virtual slot idx / physical pad t: on, a capable sender, allowed (master)."""
    if not wanted_now(t):
        return False
    if not sender_speaker(t):
        if S.alive[t]:
            speaker(idx)._note("sender", _("{pad}: głośnik pada jest włączony, ale nadajnik w starszej wersji go nie obsługuje "
                                           "(kliknij Aktualizuj)"))
        return False
    import rx_master
    return rx_master.allowed(t, "speaker")


def test(i: int, gain: float | None = None) -> str | None:
    """The "Test speaker" button for physical pad i. -> None when the tone was sent, else why not (for the window).

    gain (1.0 = 100 %) defaults to the saved volume of the pad; the window passes the value it shows. The same gates as the real
    path: the pad must be connected, its sender must know the speaker and the pad may use it (master only).
    """
    if not (0 <= i < C.NUM_PADS) or not S.alive[i]:
        return _("Pad nie jest podłączony.")
    if not sender_speaker(i):
        return _("Nadajnik w starszej wersji (bez głośnika) - kliknij Aktualizuj.")
    import rx_master
    if not rx_master.allowed(i, "speaker"):     # the link would drop the tone: say so instead of "sent"
        return _("Głośnik pada jest tylko dla pada master (Ustawienia → Master).")
    if gain is None:
        gain = rx_settings.cur.speaker.get(S.profile_key(i)).gain / 100
    speaker(rx_swap.route(i)).test(gain)        # the virtual slot whose packets go to physical pad i
    return None
