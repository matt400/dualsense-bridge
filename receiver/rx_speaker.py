"""Receiver: the game's sound on the speaker of a Bluetooth DualSense - channels 1 / 2 of the pad's USB audio go to the sender.

The composite virtual pad (rx_haptics creates it when haptics OR the speaker is wanted) gives the game a "Speakers (Wireless
Controller)" output: 4 channels, 48 kHz, 16-bit; channels 1 / 2 are the speaker (speakerLeft / speakerRight), 3 / 4 the haptics.
rx_haptics.Link already copies the whole window into a ring on HIDMaestro's audio thread; this module is a second consumer of that
ring (its own read position, no second copy of the audio and no second .NET callback):

  mix         left + right -> mono, the pad's gain, the equalizer (rx_eq, skipped when flat), a soft clip above 75 % of the full scale
              (never wrap-around), 48 kHz s16
  resample    48 kHz -> 45 kHz (16 : 15, cubic): 512 source samples (10.667 ms) -> one frame of 480 samples
  SpeakerLink one per slot: ring -> mix -> resample -> 480-sample frames -> a short paced queue -> spk packets (one per 10.667 ms).
              One thread per link, started on first use, asleep while nothing plays (rx_net.send_speaker)
  glue        wanted / attach / detach / test(), used by rx_haptics (composite pad), rx_ds4 (through rx_haptics) and the window

Why 48 -> 45 kHz. The pad's Bluetooth speaker plays every 480-sample Opus frame over one report period, 10.667 ms (measured on a
real pad: a 1234 Hz tone comes out at 1157 Hz, exactly x 15/16), i.e. at an effective 45 kHz. Sound sent as it is would play 6.25 %
too low and too slow, and the sender would have to drop a frame in 16 to stay in real time. So the receiver resamples 16 : 15: each
frame stands for 512 source samples, and the pad plays them over the 10.667 ms they last. The audio keeps its pitch and its tempo, and
the sender's buffer neither fills nor needs to drop anything (the two clocks only differ by the PC's drift). The packet format is
unchanged: an older sender just plays what it gets (it dropped frames because it expected 100 per second, now it gets 93.75).

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
import time
from array import array
from collections import deque
from collections.abc import Callable, Sequence
from typing import Final, TypeAlias

import rx_eq
import rx_haptics as H
import rx_settings
import rx_state as S
import rx_swap
from proto import CAP_SPEAKER, SPK_BYTES, SPK_END, SPK_FRAME, SPK_RATE, SPK_START
from rx_config import C
from rx_i18n import _

SPEAKER_SENDER: Final = 14                  # from this sender version the T_SPK packet exists (with the CAP_SPEAKER bit)
SRC_FRAME: Final = 512                      # source samples one frame stands for (10.667 ms of the game's 48 kHz sound) ...
FRAME_S: Final = SRC_FRAME / SPK_RATE       # ... = the pad's report period: one packet (SPK_FRAME = 480 samples at 45 kHz) per this time
BLOCK_IN, BLOCK_OUT = 16, 15                # the resampler's unit: 16 source samples -> 15 output samples (48 -> 45 kHz)
SRC_NEED: Final = SRC_FRAME + 2             # a frame needs the previous sample (history) and 2 after its last one for the 4-tap cubic
HOST_MIN_DB: Final = -100.0                 # the endpoint's volume range (profile dualsense-composite) is -100 .. 0 dB
HOST_RANGE_DB: Final = 40.0                 # the span the slider really covers: its quietest audible step plays 40 dB down (host_factor)
HOST_MUTE_POS: Final = 0.005                # a slider position this low or lower (the minimum, 0 %) is silence
KNEE: Final = 24576                         # the soft clip is linear up to 75 % of the int16 full scale ...
S16_MAX: Final = 32767
SPAN: Final = S16_MAX - KNEE                # ... then bends towards 32767 (tanh)
# "Test speaker": a short jingle, (note Hz, seconds) - a rising C major arpeggio that lands on a long high C. The notes sit at
# 520-1050 Hz, where the pad's tiny speaker is loudest.
JINGLE: Final[tuple[tuple[float, float], ...]] = (
    (523.25, 0.14), (659.25, 0.14), (783.99, 0.14), (1046.50, 0.28), (783.99, 0.14), (1046.50, 0.50),
)
TEST_SAMPLES: Final = sum(round(secs * SPK_RATE) for _hz, secs in JINGLE)
TEST_FRAMES: Final = -(-TEST_SAMPLES // SRC_FRAME)      # whole frames of source samples (the last one is padded with zeros)
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


def mix_eq(left: Sequence[int], right: Sequence[int], gain: float, eq: rx_eq.Equalizer) -> array[int]:
    """Like mix(), with the equalizer between the gain and the soft clip (so a boost bends into the clip instead of wrapping).

    The filters work on floats and may go far over the int16 range on a boost: the soft clip takes any size.
    """
    k = gain / 2
    x = eq.process([(a + b) * k for a, b in zip(left, right, strict=True)])
    return array("h", [int(v) if -KNEE <= v <= KNEE else _bend(v) for v in x])


def host_factor(volume_db: float) -> float:
    """The factor for a Windows volume write (the UAC value in dB, -100 .. 0): a curve, not the dB taken literally.

    The endpoint declares a 100 dB range and Windows seems to spread its slider linearly over all of it (8 % was silent when the dB
    were taken literally, which fits about -92 dB; the log line of set_host_volume shows what really arrives). Literally, 50 % is
    a signal at 0.3 % of the full scale and 8 % is below one LSB: only the top of the slider would be audible. The pad's tiny
    speaker needs the slider to cover the span that can be heard, so the position p (0 = the minimum, 1 = 0 dB) is mapped
    linearly in dB onto HOST_RANGE_DB: 100 % = full level, 50 % = -20 dB, 8 % = -37 dB, and only the very bottom
    (p <= HOST_MUTE_POS) is silent. The curve is monotonic and continuous, so a drag has no steps.
    """
    pos = min(1.0, (volume_db - HOST_MIN_DB) / -HOST_MIN_DB)
    return 0.0 if pos <= HOST_MUTE_POS else 10 ** (-HOST_RANGE_DB * (1.0 - pos) / 20)


def pcm_bytes(samples: array[int]) -> bytes:
    """int16 samples -> little-endian bytes (what the spk packet carries)."""
    if BIG_ENDIAN:
        samples = array("h", samples)
        samples.byteswap()
    return samples.tobytes()


@functools.cache
def jingle() -> tuple[int, ...]:
    """The test jingle as mono int16 at SPK_RATE, zero-padded to whole source frames (computed once, only when the test button is used)."""
    out: list[int] = []
    amp = TEST_LEVEL * S16_MAX / (1 + OVERTONE)         # the peak of fundamental + overtone stays at TEST_LEVEL
    for hz, secs in JINGLE:
        n = round(secs * SPK_RATE)
        w = 2 * math.pi * hz / SPK_RATE
        for k in range(n):                               # every note starts at phase 0, so it starts at zero amplitude
            env = min(1.0, k / EDGE, (n - k) / EDGE) * math.exp(-DECAY * k / n)
            out.append(round(amp * env * (math.sin(w * k) + OVERTONE * math.sin(2 * w * k))))
    out.extend([0] * (TEST_FRAMES * SRC_FRAME - len(out)))
    return tuple(out)


# ---------------- 48 -> 45 kHz ----------------
def _phases() -> tuple[tuple[int, float, float, float, float], ...]:
    """The 15 output positions of a 16-sample block as (first tap offset, 4 Catmull-Rom weights).

    Output j sits at source position j * 16 / 15 inside the block; its four taps are the samples around it (one before the integer
    part, two after) and the weights follow from the fraction t. The weights of a position sum to 1, so a constant stays constant.
    """
    out = []
    for j in range(BLOCK_OUT):
        pos = j * BLOCK_IN / BLOCK_OUT
        base = int(pos)
        t = pos - base
        out.append((base, (-t ** 3 + 2 * t ** 2 - t) / 2, (3 * t ** 3 - 5 * t ** 2 + 2) / 2, (-3 * t ** 3 + 4 * t ** 2 + t) / 2,
                    (t ** 3 - t ** 2) / 2))
    return tuple(out)


PHASES: Final = _phases()
ROUND: Final = 32768.5                      # + 0.5 to round, + 32768 to keep the value positive for int() (which truncates)


class Resampler:
    """48 kHz mono -> 45 kHz mono in frames of SPK_FRAME samples, 512 source samples per frame (cubic, 16 : 15).

    The 15 phases repeat every 16 source samples, so a frame is 32 identical blocks. Each phase is one list comprehension over four
    strided views of the source (no per-sample indexing in Python), written straight into its slots of the frame (``out[j::15]``).
    ``buf`` holds one history sample, then the samples not used yet: a frame needs 512 of them plus 1 after the last (the taps reach
    one sample ahead) and consumes 512, so the last sample it used becomes the history of the next one.
    """

    def __init__(self) -> None:
        """Start empty (a history sample of 0: the first output of a block does not use it)."""
        self.buf = array("h", [0])

    def clear(self) -> None:
        """Forget everything buffered."""
        self.buf = array("h", [0])

    def push(self, samples: array[int]) -> None:
        """Append mono int16 samples at 48 kHz."""
        self.buf.extend(samples)

    def push_zeros(self, count: int) -> None:
        """Append digital silence (cheaper than building the zeros as a list first)."""
        self.buf.frombytes(bytes(count * H.BYTES_PER_SAMPLE))

    def frame(self) -> bytes | None:
        """The next frame as SPK_BYTES of little endian int16, or None until enough source samples are buffered."""
        buf = self.buf
        if len(buf) < SRC_NEED:
            return None
        src = buf[:SRC_NEED]                                        # the strided views below must see exactly this frame
        del buf[:SRC_FRAME]
        if not any(src):                                            # silence in, silence out: no work
            return ZERO_FRAME
        out = [0] * SPK_FRAME
        for j, (base, w0, w1, w2, w3) in enumerate(PHASES):
            out[j::BLOCK_OUT] = [int(a * w0 + b * w1 + c * w2 + d * w3 + ROUND) - 32768
                                 for a, b, c, d in zip(src[base::BLOCK_IN], src[base + 1::BLOCK_IN], src[base + 2::BLOCK_IN],
                                                       src[base + 3::BLOCK_IN], strict=False)]
        if min(out) < -S16_MAX - 1 or max(out) > S16_MAX:           # a cubic can overshoot a full-scale edge: clip, never wrap
            out = [max(-S16_MAX - 1, min(S16_MAX, v)) for v in out]
        return pcm_bytes(array("h", out))


# ---------------- the per-slot link ----------------
class SpeakerLink(H.PacedStream):
    """The speaker of one virtual pad slot: audio in (from the slot's rx_haptics.Link), spk packets out. See the module docstring.

    The audio thread of HIDMaestro never touches this object beyond poke(); everything else runs on the link's own thread (step() is
    that thread's whole body, so tests drive it with a fake clock).
    """

    KIND, UNITS, START, END, ZERO = "speaker", "frames", SPK_START, SPK_END, ZERO_FRAME

    def __init__(self, idx: int, send: SendFn | None = None, clock: Callable[[], float] = time.perf_counter) -> None:
        """Create the idle speaker of virtual slot idx; `send(slot, frame, flags, pcm)` defaults to rx_net.send_speaker."""
        super().__init__(idx, send, clock, FRAME_S)
        self.link: H.Link | None = None             # the audio source (set by attach)
        self._r = 0                                 # the read position in the link's ring
        self._inject: deque[array[int]] = deque()   # source frames (512 samples) from test(): the same way out as the game's
        self._rs = Resampler()                      # mixed mono samples that do not make a whole frame yet
        self._eq = rx_eq.Equalizer()                # this pad's equalizer (only the link's thread touches it)
        self.host_gain = 1.0                        # the Windows volume / mute of the pad's speaker endpoint as a factor (set_host_volume)
        self._host_muted = False
        self._host_db = 0.0

    # ----- the audio thread -----
    def poke(self, written: int) -> None:
        """Called by rx_haptics.Link.write on HIDMaestro's audio thread after every write: wake the thread when there is sound to take."""
        if (self._active or written - self._r >= H.WAKE_BYTES) and not self._wake.is_set():
            self._wake.set()

    # ----- setup -----
    def attach(self, link: H.Link) -> str | None:
        """Take the audio of `link` (its endpoint is configured). -> None, or why the speaker cannot use this audio format."""
        if link.rate != SPK_RATE:
            return f"{link.rate} Hz audio ({SPK_RATE} Hz expected)"
        with self._lock:
            self.link = link
            self._r = link.written
            self._rs.clear()
        link.on_write = self.poke
        return None

    def set_host_volume(self, function: str, is_mute: bool, muted: bool, volume_db: float) -> None:
        """The host wrote a volume or mute control of the pad's audio endpoint (HIDMaestro's ControlChanged, its audio event thread).

        Windows leaves the PCM of an endpoint that has a hardware volume untouched and sends the slider as a UAC write instead, so
        the speaker applies it itself: the volume goes through host_factor (a curve over the audible span), mute is silence. Only
        the speaker unit counts, and only the speaker: the haptics are the game's own signal and keep their strength setting.
        Until the first write the level is full (the endpoint reports its minimum as the initial value, which must not make the
        speaker silent before anybody touched a slider). The next frame uses the new factor; the state is two plain attributes,
        so the event thread and the link's thread need no lock.
        """
        if function != "speaker":
            return
        if is_mute:
            self._host_muted = muted
        else:
            self._host_db = volume_db
        self.host_gain = 0.0 if self._host_muted else host_factor(self._host_db)
        if not is_mute:                                 # the numbers Windows really sends: what to look at when the curve needs tuning
            print(f"Controller {rx_swap.route(self.idx)}: speaker volume {volume_db:.0f} dB -> x{self.host_gain:.3f}")

    # ----- the link's thread -----
    def step(self, now: float) -> float:
        """One pass: take new audio, make frames, send what is due. -> how long the thread may sleep (a write wakes it)."""
        with self._lock:
            cfg = self._settings()
            data = self._read() if self.link is not None else None
            if cfg is None:                         # off, master only, no capable sender: the audio is dropped, any stream ends
                self._inject.clear()                # a test tone must not wait for the pad to be allowed later and play then
                self._rs.clear()
                self._end()
            else:
                self._eq.set_gains(cfg.eq)          # a change from the window applies at once (equal gains: nothing happens)
                if data:
                    self._feed(data, cfg.gain / 100 * self.host_gain)
                self._take_injected()
                if self._active and self._from_pcm and self.link is not None and now - self.link.pcm_at > H.STALL_S:
                    self._end()                     # the game stopped rendering: Windows parks the endpoint, no more callbacks
            return self._pace(now)

    def _settings(self) -> rx_settings.PadSpeaker | None:
        """The pad's speaker settings (volume, equalizer) when this link may play, None when it must stay quiet."""
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
        return cfg

    def _read(self) -> bytes | None:
        """Take the unread audio out of the link's ring (whole frames); None when there is none."""
        assert self.link is not None
        data, r = self.link.read_from(self._r)
        if r - self._r != (len(data) if data else 0):   # the writer lapped us: what was pending belongs to a past that is gone
            self.overruns += 1
            self._rs.clear()
        self._r = r
        return data

    def _feed(self, data: bytes, gain: float) -> None:
        """Audio -> mono -> frames."""
        assert self.link is not None
        stride = self.link.channels
        left_at, right_at = self.link.speaker_channels
        samples = memoryview(data).cast("h")
        left, right = samples[left_at::stride].tolist(), samples[right_at::stride].tolist()
        if gain > 0.0 and (any(left) or any(right)):
            self._rs.push(mix(left, right, gain) if self._eq.flat else mix_eq(left, right, gain, self._eq))
        else:
            self._rs.push_zeros(len(left))                          # silence or muted: no work, and the filters forget the old sound
            self._eq.reset()
        self._drain(from_pcm=True)

    def _drain(self, from_pcm: bool) -> None:
        """Turn the buffered source samples into frames."""
        while (frame := self._rs.frame()) is not None:
            self._push(frame, from_pcm)

    def _take_injected(self) -> None:
        """The jingle goes through the same path as the game's sound - as fast as the queue drains (it would drop the rest)."""
        while self._inject and len(self._queue) < H.MAX_QUEUE - 1:
            self._rs.push(self._inject.popleft())
            self._drain(from_pcm=False)

    # ----- from other threads -----
    def test(self, gain: float, eq: Sequence[int] = rx_eq.FLAT) -> None:
        """The jingle through the same path ("Test speaker"): its frames plus the silence that ends the stream.

        `eq` are the equalizer gains to use; the jingle goes through a filter of its own (not the link's, which belongs to its thread),
        so the test plays what the window shows even when the pad's real sound is not running.
        """
        tune = jingle()
        tester = rx_eq.Equalizer()
        tester.set_gains(eq)
        mono = mix(tune, tune, gain) if tester.flat else mix_eq(tune, tune, gain, tester)
        self._inject.extend(mono[k * SRC_FRAME:(k + 1) * SRC_FRAME] for k in range(TEST_FRAMES))
        silence = array("h", bytes(SRC_FRAME * H.BYTES_PER_SAMPLE))      # +2 frames: one waits for 1 sample ahead
        self.inject([], [silence] * (math.ceil(H.SILENCE_END_S / FRAME_S) + 2))

    def inject(self, frames: Sequence[array[int]], tail: Sequence[array[int]] = ()) -> None:
        """Queue ready source frames (SRC_FRAME mono samples at 48 kHz each) and ``tail`` after them for the game's way out.

        The pad self-test sends its own stream; the same path as the game's sound (resampler, frames, pacing) carries it.
        """
        self._inject.extend(frames)
        self._inject.extend(tail)
        self.ensure_thread()
        self.wake()

    def detach(self) -> None:
        """The virtual pad is gone: drop the audio, forget the state and send the SPK_END at once (the sender releases the speaker)."""
        with self._lock:
            if self.link is not None:
                self.link.on_write = None
                self._r = self.link.written
            self.link = None
            self._rs.clear()
            self._inject.clear()
            self.host_gain, self._host_muted, self._host_db = 1.0, False, 0.0     # the next pad starts at full level again
            self._drop_stream()

    def _default_send(self) -> SendFn:
        """rx_net.send_speaker."""
        import rx_net
        return rx_net.send_speaker


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


def unavailable(i: int) -> str | None:
    """Why a test stream cannot reach the speaker of physical pad i (for the window), None when it can.

    The same gates as the real path: the pad must be connected, its sender must know the speaker and the pad may use it (master only).
    """
    if not (0 <= i < C.NUM_PADS) or not S.alive[i]:
        return _("Pad nie jest podłączony.")
    if not sender_speaker(i):
        return _("Nadajnik w starszej wersji (bez głośnika) - kliknij Aktualizuj.")
    import rx_master
    if not rx_master.allowed(i, "speaker"):     # the link would drop the tone: say so instead of "sent"
        return _("Głośnik pada jest tylko dla pada master (Ustawienia → Master).")
    return None


def test(i: int, gain: float | None = None, eq: Sequence[int] | None = None) -> str | None:
    """The "Test speaker" button for physical pad i. -> None when the tone was sent, else why not (for the window).

    gain (1.0 = 100 %) and eq (dB per band) default to the saved ones of the pad; the window passes the values it shows.
    """
    if (why := unavailable(i)) is not None:
        return why
    saved = rx_settings.cur.speaker.get(S.profile_key(i))
    # the virtual slot whose packets go to physical pad i
    speaker(rx_swap.route(i)).test(saved.gain / 100 if gain is None else gain, saved.eq if eq is None else eq)
    return None
