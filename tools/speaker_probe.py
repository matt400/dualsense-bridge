#!/usr/bin/env python3
"""Throwaway probe: speaker audio (Opus) over Bluetooth on a real DualSense, HID output report 0x36. Run on the sender (phone / Pi).

It imports nothing from the repo, so it survives refactors. Needs the stdlib, libopus (`doas apk add opus`), python-evdev for the
pad lookup and the `jitter` command, and root for /dev/hidraw*:

    python3 speaker_probe.py bench                  # encoder CPU on this machine, no pad needed (run it with the sender running)
    python3 speaker_probe.py bench --paced          # the same in the real rhythm (one frame per 10.667 ms), split into its parts
    doas systemctl stop dualsense-sender            # the sender must not write to the pad meanwhile
    doas python3 speaker_probe.py info              # pad, hidraw, output report sizes from the HID descriptor, libopus
    doas python3 speaker_probe.py layout            # 0x31 setups, 0x36 layouts, Opus sizes; you answer "did you hear it"
    doas python3 speaker_probe.py tone 500 5        # a speaker tone with the chosen layout, prints the first report
    doas python3 speaker_probe.py timing            # 10.667 vs 10.0 ms pacing, an underrun (silence frame vs skipped report)
    doas python3 speaker_probe.py clock             # report period sweep + a frame dropped every 16, with a 440 Hz tone
    doas python3 speaker_probe.py combo             # haptics + speaker in one 0x36, haptics only, speaker only, 0x32 <-> 0x36
    doas python3 speaker_probe.py modes             # does a 0x31 refresh during the speaker cut it? (--only NAME,.. --repeat N)
    doas python3 speaker_probe.py jitter            # input interval jitter with and without the stream (A-B-A, 60 s each)
    doas python3 speaker_probe.py stop              # pop at the start / end, silence tail, how long the audio path stays alive
    doas python3 speaker_probe.py edge              # what removes the pop at the end of a stream (tails, fade, volume 0, audio off)
    doas python3 speaker_probe.py gap               # clean 1 kHz tone in 6 variants, you RECORD each (analysed offline: 94 Hz buzz)
    doas python3 speaker_probe.py gap --only var2.5,var5   # only the variable-length packets (4-5 x 2.5 ms, 2-3 x 5 ms per report)
    doas python3 speaker_probe.py gap --hz 1234 --continuous --only nodrop,p10.5,p10.4,p10.3,lowdelay,silk10,hybrid10   # ONE recording
    doas python3 speaker_probe.py gap --wav speech.wav   # the same variants with your own speech (48 kHz mono WAV)

Every command that measures something merges its result into speaker_probe_results.json (paste that file back). `layout` stores the
setup / layout / Opus size that worked best under "layout" -> "best", and the later commands use it (override: --setup --layout --size).

Report 0x36 as tried here (398 B, UNVERIFIED - this script exists to check it). The HID descriptor gives 0x32..0x36 data sizes of
141 / 205 / 269 / 333 / 397 B (64 B more per id). The bytes of a working 0x32 read as a list of sub-packets, each
`pid | 0x80 ("sized"), length, payload`:
    0     0x36
    1     sequence number in the high nibble
    2..   0x91 0x07 <0xFE, 0x10 x5, report counter>     control sub-packet (0x11)
          0x92 0x40 <64 B>                              haptics sub-packet (0x12): 32 stereo frames of s8 at 3 kHz
          0x93 <len> <Opus frame>                       speaker sub-packet (0x13): 48 kHz mono, 10 ms, CBR  <- the hypothesis
          (the community notes also name a state sub-packet 0x10 between control and haptics: variant `notes-order`)
    -4    CRC32 little endian over 0xA2 + everything before it
The speaker is enabled by a 0x31 report: speaker volume (common byte 5, valid_flag0 0x20), audio_control 0x30 (byte 7, valid_flag0
0x80), audio_control2 preamp 0x02 (byte 37). Offsets are from the kernel's report struct, the values from the notes.
"""
import argparse
import array
import contextlib
import ctypes
import ctypes.util
import dataclasses
import glob
import itertools
import json
import math
import os
import platform
import random
import re
import select
import sys
import threading
import time
import wave
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

HAPT_RATE = 3000                         # haptics sample rate over Bluetooth, Hz
HAPT_FRAMES = 32                         # stereo frames per haptics sub-packet
HAPT_CHUNK = 2 * HAPT_FRAMES             # 64 bytes of s8 samples
REPORT_PERIOD = HAPT_FRAMES / HAPT_RATE  # 10.667 ms: one haptics chunk per report
OPUS_PERIOD = 0.010                      # 10 ms of speaker audio per Opus frame
OPUS_RATE = 48000                        # speaker sample rate, Hz
OPUS_FRAME = 480                         # samples per 10 ms Opus frame
BYTES_PER_SAMPLE = 2                     # signed 16-bit PCM
OPUS_MAX_PACKET = 1275                   # the largest Opus packet
OPUS_APPLICATION_AUDIO, REQ_BITRATE, REQ_VBR, REQ_COMPLEXITY = 2049, 4002, 4006, 4010
OPUS_APPLICATION_VOIP, OPUS_APPLICATION_LOWDELAY = 2048, 2051    # VOIP prefers SILK, RESTRICTED_LOWDELAY is CELT only (no look-ahead)
REQ_MAX_BANDWIDTH, REQ_SIGNAL, REQ_FORCE_MODE = 4004, 4024, 11002       # FORCE_MODE: libopus' internal switch (1000 SILK, 1001 hybrid)
BANDWIDTH_WIDE, SIGNAL_VOICE, MODE_SILK, MODE_HYBRID = 1103, 3001, 1000, 1001
# Opus modes tried by `gap` (name -> Encoder keywords). Default = what the sender uses: AUDIO application, CELT fullband.
CODECS: dict[str, dict[str, int]] = {
    "": {},
    "lowdelay": {"application": OPUS_APPLICATION_LOWDELAY},
    "silk": {"application": OPUS_APPLICATION_VOIP, "force_mode": MODE_SILK, "max_bandwidth": BANDWIDTH_WIDE, "signal": SIGNAL_VOICE},
    "hybrid": {"application": OPUS_APPLICATION_VOIP, "force_mode": MODE_HYBRID, "signal": SIGNAL_VOICE},
}
R36_LEN, R32_LEN = 398, 142              # report id + data bytes (checked against the HID descriptor in `info`)
R32_ID, R36_ID = 0x32, 0x36
CRC_SEED = b"\xa2"
RESYNC_PERIODS = 3                       # a stream that falls this many periods behind restarts its clock
MAX_FAILS = 5                            # consecutive write errors before a stream gives up
SPK_HZ, SPK_AMP = 500.0, 0.5             # the speaker test tone
HAPT_HZ, HAPT_AMP = 80.0, 0.8            # the haptics test tone
CLOCK_HZ = 440.0                         # `clock` tone: 4.4 cycles per 10 ms, so a dropped frame is a phase jump (500 Hz = 5.0: invisible)
DEFAULT_SIZE = 80                        # Opus bytes per 10 ms frame (64 kbps CBR)
SIZES = (40, 80, 120, 160, 200)          # Opus frame sizes tried by `layout` (32 / 64 / 96 / 128 / 160 kbps CBR)
STATE_LEN = 47                           # the common part of a 0x31 report
CTL_HEAD = bytes([0xFE]) + bytes([0x10]) * 5    # control sub-packet payload before the report counter
POWER_AT, SPK_VOL_AT, AUDIO_CTL_AT, AUDIO_CTL2_AT = 36, 5, 7, 37     # offsets in the common part of a 0x31 report
FLAG1_P0 = 0x02 | 0x20 | 0x40            # valid_flag1 of the haptics setup report measured in P0
TIME_SCALE = 1.0                         # observation waits are multiplied by this (dry-run shortens them)
AUTO = False                             # dry-run: answer every question with its first choice
BT_BUS = 0x0005
LONG_ITEM = 0xFE                         # HID descriptor: long item prefix
TAG_SIZE, TAG_ID, TAG_COUNT, TAG_OUTPUT = 7, 8, 9, 9    # HID descriptor: report size / id / count (global), Output (main)
GAP_WARN_MS = (20.0, 50.0)               # input intervals counted as "late"
OFF_TRIGGER = bytes([0x05]) + bytes(10)  # trigger effect "off"
BENCH_FRAMES = 1000                      # frames encoded per `bench` row
Q_SPK = "speaker: y(es) / n(o) / w(eak) / d(istorted)"
Q_HAPT = "haptics: y(es) / n(o) / w(eak)"
Q_GLITCH = "glitches: n(one) / t(icking, steady) / p(ops now and then) / d(ropouts)"
_HID_DIR = re.compile(r"^([0-9A-Fa-f]{4}):[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")


# ---------------------------------------------------------------- Opus (libopus through ctypes)
_LIB: ctypes.CDLL | None = None


def _opus_lib() -> ctypes.CDLL:
    """Load libopus once and declare the few functions used; exits with a hint when it is missing."""
    global _LIB
    if _LIB is not None:
        return _LIB
    for name in (ctypes.util.find_library("opus"), "libopus.so.0", "libopus.so", "libopus-0.dll"):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int32]
        lib.opus_encode.restype = ctypes.c_int32
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_encoder_destroy.restype = None
        lib.opus_repacketizer_create.argtypes = []
        lib.opus_repacketizer_create.restype = ctypes.c_void_p
        lib.opus_repacketizer_init.argtypes = [ctypes.c_void_p]
        lib.opus_repacketizer_init.restype = ctypes.c_void_p
        lib.opus_repacketizer_cat.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32]
        lib.opus_repacketizer_cat.restype = ctypes.c_int
        lib.opus_repacketizer_out.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32]
        lib.opus_repacketizer_out.restype = ctypes.c_int32
        lib.opus_repacketizer_destroy.argtypes = [ctypes.c_void_p]
        lib.opus_repacketizer_destroy.restype = None
        lib.opus_encoder_ctl.restype = ctypes.c_int
        lib.opus_get_version_string.restype = ctypes.c_char_p
        _LIB = lib
        return lib
    raise SystemExit("libopus not found: doas apk add opus (postmarketOS) / apt install libopus0 (Debian)")


def opus_version() -> str:
    """The libopus version string, or a note when the library is missing."""
    try:
        return _opus_lib().opus_get_version_string().decode()
    except SystemExit:
        return "(libopus not found)"


class Encoder:
    """A mono 48 kHz Opus encoder for 10 ms frames, CBR, so every frame has the same size (``size`` bytes including the TOC byte)."""

    def __init__(self, size: int, complexity: int | None = None, ms: float = 10, application: int = OPUS_APPLICATION_AUDIO,
                 force_mode: int = 0, max_bandwidth: int = 0, signal: int = 0) -> None:
        """Create the encoder; the bitrate is chosen so that a CBR frame of ``ms`` milliseconds is ``size`` bytes.

        ``application`` / ``force_mode`` / ``max_bandwidth`` / ``signal`` pick another Opus mode (see CODECS); 0 = libopus decides.
        """
        self._lib = _opus_lib()
        self.size, self.mismatch = size, 0
        err = ctypes.c_int(0)
        self._state = ctypes.c_void_p(self._lib.opus_encoder_create(OPUS_RATE, 1, application, ctypes.byref(err)))
        if err.value != 0 or not self._state:
            raise SystemExit(f"opus_encoder_create failed ({err.value})")
        self._ctl(REQ_BITRATE, round(size * 8 * 1000 / ms))
        self._ctl(REQ_VBR, 0)
        for request, value in ((REQ_MAX_BANDWIDTH, max_bandwidth), (REQ_SIGNAL, signal), (REQ_FORCE_MODE, force_mode)):
            if value:
                self._ctl(request, value)
        if complexity is not None:
            self._ctl(REQ_COMPLEXITY, complexity)
        self._out = ctypes.create_string_buffer(OPUS_MAX_PACKET)

    def _ctl(self, request: int, value: int) -> None:
        if self._lib.opus_encoder_ctl(self._state, ctypes.c_int(request), ctypes.c_int32(value)) != 0:
            raise SystemExit(f"opus_encoder_ctl({request}, {value}) failed")

    def encode(self, pcm: array.array) -> bytes:  # type: ignore[type-arg]
        """Encode one frame of signed 16-bit samples (480 = 10 ms, 960 = 20 ms) into one Opus packet."""
        n = self._lib.opus_encode(self._state, pcm.buffer_info()[0], len(pcm), ctypes.addressof(self._out), OPUS_MAX_PACKET)
        if n < 0:
            raise OSError(f"opus_encode failed ({n})")
        if n != self.size:
            self.mismatch += 1
        return ctypes.string_at(ctypes.addressof(self._out), n)

    def close(self) -> None:
        """Free the encoder."""
        if self._state:
            self._lib.opus_encoder_destroy(self._state)
            self._state = ctypes.c_void_p(0)


class FakeEncoder:
    """Dry-run stand-in when libopus is missing: ``size`` bytes of deterministic filler, NOT valid Opus."""

    def __init__(self, size: int, complexity: int | None = None, ms: float = 10, **_mode: int) -> None:
        """Remember the frame size."""
        self.size, self.mismatch = size, 0

    def encode(self, pcm: array.array) -> bytes:  # type: ignore[type-arg]
        """Return ``size`` bytes derived from the first samples."""
        return bytes((pcm[i % len(pcm)] + i) & 0xFF for i in range(self.size))

    def close(self) -> None:
        """Nothing to free."""


class Repacker:
    """Joins several Opus packets of the same frame length into one packet (libopus' repacketizer), e.g. 5 x 2.5 ms = 12.5 ms."""

    def __init__(self) -> None:
        """Create the repacketizer (needs the real libopus: a dry run without it just concatenates the filler)."""
        try:
            self._lib: ctypes.CDLL | None = _opus_lib()
        except SystemExit:
            self._lib = None
        self._state = ctypes.c_void_p(self._lib.opus_repacketizer_create()) if self._lib else None
        self._out = ctypes.create_string_buffer(OPUS_MAX_PACKET)

    def join(self, packets: list[bytes]) -> bytes:
        """One packet carrying all ``packets`` (in order); a single packet is returned as is."""
        if len(packets) == 1:
            return packets[0]
        if self._lib is None or self._state is None:
            return b"".join(packets)
        self._lib.opus_repacketizer_init(self._state)
        for pkt in packets:
            if self._lib.opus_repacketizer_cat(self._state, pkt, len(pkt)) != 0:
                raise OSError("opus_repacketizer_cat refused a packet (different frame lengths?)")
        n = self._lib.opus_repacketizer_out(self._state, ctypes.addressof(self._out), OPUS_MAX_PACKET)
        if n < 0:
            raise OSError(f"opus_repacketizer_out failed ({n})")
        return ctypes.string_at(ctypes.addressof(self._out), n)

    def close(self) -> None:
        """Free the repacketizer."""
        if self._lib is not None and self._state:
            self._lib.opus_repacketizer_destroy(self._state)
            self._state = ctypes.c_void_p(0)


def make_encoder(size: int, dry: bool, complexity: int | None = None, ms: float = 10, **mode: int) -> Encoder | FakeEncoder:
    """A real encoder; in a dry run without libopus a fake one. ``mode``: Encoder keywords (CODECS)."""
    try:
        return Encoder(size, complexity, ms, **mode)
    except SystemExit:
        if not dry:
            raise
        return FakeEncoder(size, complexity, ms, **mode)


# ---------------------------------------------------------------- report building
def _crc_tail(report: bytearray) -> bytes:
    """Return the report with its CRC32 (seed 0xA2, little endian) written into the last four bytes."""
    report[-4:] = (zlib.crc32(CRC_SEED + bytes(report[:-4])) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(report)


def feedback_trigger(level: int = 4) -> bytes:
    """Build a "feedback" trigger effect (0x21) with the same resistance in all ten zones."""
    effect = bytearray(11)
    effect[0] = 0x21
    effect[1:3] = (0x03FF).to_bytes(2, "little")
    effect[3:7] = sum(level << (3 * zone) for zone in range(10)).to_bytes(4, "little")
    return bytes(effect)


def build_0x31(seq: int, *, flag0: int = 0, flag1: int = 0, flag2: int = 0, motors: tuple[int, int] = (0, 0),
               rgb: tuple[int, int, int] | None = None, trig: tuple[bytes, bytes] | None = None,
               extra: tuple[tuple[int, int], ...] = ()) -> bytes:
    """Build a Bluetooth output report 0x31 (78 B): the common part of the USB report 0x02 wrapped like the kernel does.

    Args:
        seq: Sequence number (low 4 bits are used).
        flag0: valid_flag0 (0x01 compat rumble, 0x02 haptics select, 0x04 / 0x08 right / left trigger, 0x20 speaker volume,
            0x80 audio control).
        flag1: valid_flag1 (0x04 = lightbar colour from this report).
        flag2: valid_flag2 (0x04 = vibration v2).
        motors: (right weak, left strong) motor, 0..255.
        rgb: Lightbar colour (needs flag1 0x04).
        trig: (L2 effect, R2 effect), 11 bytes each (needs flag0 0x0C).
        extra: (offset in the common part, value) pairs written last (motor power, speaker volume, audio control).

    Returns:
        The report bytes.
    """
    common = bytearray(STATE_LEN)
    common[0], common[1], common[38] = flag0, flag1, flag2
    common[2], common[3] = motors
    if trig is not None:
        common[21:32], common[10:21] = trig
    if rgb is not None:
        common[44:47] = bytes(rgb)
    for offset, value in extra:
        common[offset] = value
    report = bytearray([0x31, (seq & 0x0F) << 4, 0x10]) + common + bytes(24) + bytes(4)
    return _crc_tail(report)


@dataclasses.dataclass(frozen=True)
class Setup:
    """A 0x31 report that prepares the pad for a stream; the guesses differ in the flags that guard the audio bytes."""

    name: str
    note: str
    flag0: int
    flag1: int
    extra: tuple[tuple[int, int], ...]

    def kwargs(self, volume: int) -> dict[str, Any]:
        """Arguments of build_0x31 with the speaker volume filled in."""
        return {"flag0": self.flag0, "flag1": self.flag1,
                "extra": tuple((o, volume if o == SPK_VOL_AT else v) for o, v in self.extra)}


_SPEAKER_BYTES = ((POWER_AT, 0), (SPK_VOL_AT, 0), (AUDIO_CTL_AT, 0x30), (AUDIO_CTL2_AT, 0x02))
SETUPS = [
    Setup("p0", "only the haptics setup measured in P0 (the speaker is not enabled: the control)", 0x00, FLAG1_P0, ((POWER_AT, 0),)),
    Setup("notes", "P0 setup + speaker volume (flag0 0x20), audio_control 0x30 (flag0 0x80), audio_control2 0x02", 0xA0, FLAG1_P0,
          _SPEAKER_BYTES),
    Setup("notes-f1", "as `notes`, plus valid_flag1 0x80 (a guess for the flag of audio_control2)", 0xA0, FLAG1_P0 | 0x80, _SPEAKER_BYTES),
]
BY_SETUP = {s.name: s for s in SETUPS}


def normal_report(seq: int) -> bytes:
    """What the sender sends today (flag0 = haptics select + compat rumble, v2 flag), motors and triggers off."""
    return build_0x31(seq, flag0=0x03 | 0x0C, flag2=0x04, trig=(OFF_TRIGGER, OFF_TRIGGER))


@dataclasses.dataclass(frozen=True)
class Layout:
    """The order of the sub-packets in a haptics / speaker report."""

    name: str
    note: str
    order: tuple[str, ...]


LAYOUTS = [
    Layout("hyp", "control, haptics, speaker 0x93 (the hypothesis)", ("ctl", "hapt", "spk")),
    Layout("notes-order", "control, state 0x10 (47 B, valid flags 0), haptics, speaker (the order in the community notes)",
           ("ctl", "state", "hapt", "spk")),
    Layout("spk-first", "control, speaker, haptics", ("ctl", "spk", "hapt")),
    Layout("state-last", "control, haptics, speaker, state", ("ctl", "hapt", "spk", "state")),
]
BY_LAYOUT = {lay.name: lay for lay in LAYOUTS}
L32 = Layout("0x32", "what the sender sends for haptics today", ("ctl", "hapt"))


@dataclasses.dataclass
class Tick:
    """What one report period sends: a haptics chunk, an Opus frame (None = no speaker sub-packet) and the report kind."""

    hapt: bytes
    spk: bytes | None
    kind: str = "36"


def _sub(pid: int, payload: bytes) -> bytes:
    """One "sized" sub-packet: pid | 0x80, length, payload."""
    return bytes([pid | 0x80, len(payload)]) + payload


def build_report(rid: int, layout: Layout, seq: int, counter: int, tick: Tick, length: int) -> bytes:
    """Build a 0x32 / 0x36 report from the layout's sub-packets, zero padded, with the CRC."""
    parts = {"ctl": _sub(0x11, CTL_HEAD + bytes([counter & 0xFF])), "state": _sub(0x10, bytes(STATE_LEN)), "hapt": _sub(0x12, tick.hapt)}
    if tick.spk is not None:
        parts["spk"] = _sub(0x13, tick.spk)
    body = b"".join(parts[name] for name in layout.order if name in parts)
    if 2 + len(body) > length - 4:
        raise ValueError(f"the sub-packets need {2 + len(body) + 4} B, the report has {length}")
    report = bytearray(length)
    report[0], report[1] = rid, (seq & 0x0F) << 4
    report[2:2 + len(body)] = body
    return _crc_tail(report)


# ---------------------------------------------------------------- sample sources
class Sine:
    """A phase-continuous sine with a 30 ms fade in / out; ``secs=None`` = endless, zeros after the end."""

    def __init__(self, hz: float, amp: float, rate: int, secs: float | None, ramp_s: float = 0.03) -> None:
        """Set up the oscillator (``ramp_s``: the fade in / out)."""
        self.hz, self.amp, self.rate, self.n = hz, amp, rate, 0
        self.total = None if secs is None else round(secs * rate)
        self.ramp = ramp_s * rate

    def take(self, count: int) -> list[float]:
        """The next ``count`` samples, -1..1."""
        out = []
        for _ in range(count):
            gain = min(1.0, self.n / self.ramp)
            if self.total is not None:
                gain = max(0.0, min(gain, (self.total - self.n) / self.ramp))
            out.append(self.amp * gain * math.sin(2 * math.pi * self.hz * self.n / self.rate))
            self.n += 1
        return out


class Wav:
    """The samples of a 48 kHz WAV file (any channel count, mixed to mono), looped, with the same ``take`` as Sine."""

    def __init__(self, path: str, amp: float, secs: float | None) -> None:
        """Read the whole file; refuses another sample rate (convert: ffmpeg -i in.mp3 -ac 1 -ar 48000 speech.wav)."""
        with wave.open(path, "rb") as w:
            if w.getsampwidth() != BYTES_PER_SAMPLE or w.getframerate() != OPUS_RATE:
                raise SystemExit(f"{path}: need 16-bit PCM at {OPUS_RATE} Hz (ffmpeg -i in.mp3 -ac 1 -ar 48000 speech.wav)")
            raw = array.array("h")
            raw.frombytes(w.readframes(w.getnframes()))
            ch = w.getnchannels()
        if sys.byteorder == "big":
            raw.byteswap()
        self.data = [sum(raw[i:i + ch]) / ch / 32768 for i in range(0, len(raw) - ch + 1, ch)]
        if not self.data:
            raise SystemExit(f"{path}: no samples")
        self.amp, self.n = amp, 0
        self.total = None if secs is None else round(secs * OPUS_RATE)

    def take(self, count: int) -> list[float]:
        """The next ``count`` samples, -1..1 (zeros after ``secs``)."""
        out = []
        for _ in range(count):
            live = self.total is None or self.n < self.total
            out.append(self.amp * self.data[self.n % len(self.data)] if live else 0.0)
            self.n += 1
        return out


def to_pcm(samples: list[float]) -> array.array:  # type: ignore[type-arg]
    """Float -1..1 -> signed 16-bit PCM."""
    return array.array("h", [int(max(-1.0, min(1.0, x)) * 32767) for x in samples])


def to_s8_stereo(samples: list[float]) -> bytes:
    """Float -1..1 -> interleaved s8 stereo (both grips the same), clipped to +-127."""
    return bytes((max(-127, min(127, round(x * 127))) & 0xFF) for x in samples for _ in (0, 1))


@dataclasses.dataclass
class Plan:
    """What a stream sends. ``spk``: tone | silence | off (no speaker sub-packet); ``hapt``: tone | zero."""

    secs: float | None = 3.0
    spk: str = "tone"
    hapt: str = "zero"
    spk_hz: float = SPK_HZ
    hapt_hz: float = HAPT_HZ
    drop_every: int = 0                              # skip one source frame every N periods (keeps a tone real-time at 10.667 ms)
    gap_at: float = 0.0                              # an underrun: from this second ...
    gap_len: float = 0.0                             # ... for this long ...
    gap_mode: str = ""                               # ... silence frames ("silence") or no report at all ("skip")
    script: tuple[tuple[float, str], ...] = ()       # (until second, report kind "32" | "36"); empty = always 0x36
    tail_s: float = 0.0                              # digital silence after the tone (0x36 reports)
    fade_s: float = 0.03                             # fade in / out of the speaker tone
    preencode: bool = False                          # endless tone: encode one second up front and loop it (a quiet probe thread)
    spk_ms: float = 10                               # length of one Opus frame in ms (10, 20; 2.5 / 5 with ``mean_ms``)
    spk_every: int = 1                               # a speaker sub-packet only in every Nth report (the others carry none)
    wav: str = ""                                    # play this 48 kHz WAV instead of the sine
    mean_ms: float = 0.0                             # > 0: reports carry whole numbers of ``spk_ms`` frames, this long on average
    codec: str = ""                                  # an Opus mode from CODECS ("" = the sender's)


class Source:
    """Yields one Tick per report period according to a Plan; ``None`` = send nothing this period (the clock still runs)."""

    def __init__(self, plan: Plan, period: float, enc: Encoder | FakeEncoder | None) -> None:
        """Prepare the oscillators; ``enc_ms`` collects the encoder time of every frame."""
        self.plan, self.period, self.enc = plan, period, enc
        self.enc_ms: list[float] = []
        self.spk_osc: Sine | Wav = (Wav(plan.wav, SPK_AMP, plan.secs) if plan.wav
                                    else Sine(plan.spk_hz, SPK_AMP, OPUS_RATE, plan.secs, plan.fade_s))
        self.frame = round(OPUS_RATE * plan.spk_ms / 1000)
        self.repack = Repacker() if plan.mean_ms else None
        self.hapt_osc = Sine(plan.hapt_hz, HAPT_AMP, HAPT_RATE, plan.secs)
        self._cache: list[bytes] = []
        if plan.preencode and enc is not None and plan.secs is None and plan.spk == "tone":
            self.spk_osc.take(round(plan.fade_s * OPUS_RATE))        # past the fade in: the loop must not dip every second
            self._cache = [enc.encode(to_pcm(self.spk_osc.take(OPUS_FRAME))) for _ in range(round(1 / OPUS_PERIOD))]

    def __iter__(self) -> Iterator[Tick | None]:
        """One item per period until the plan (and its tail) ends."""
        limit = None if self.plan.secs is None else round(self.plan.secs / self.period)
        total = None if limit is None else limit + round(self.plan.tail_s / self.period)
        for i in itertools.count():
            if total is not None and i >= total:
                return
            yield self._tick(i, tail=limit is not None and i >= limit)

    def _kind(self, t: float) -> str:
        return next((kind for until, kind in self.plan.script if t < until), "36")

    def _speaker(self, i: int, silent: bool) -> bytes | None:
        if self.plan.spk == "off" or self.enc is None or i % self.plan.spk_every:
            return None
        if self._cache:
            return self._cache[i % len(self._cache)]
        p = self.plan
        if self.repack is not None:
            return self._variable(i, silent)
        if p.drop_every and i % p.drop_every == p.drop_every - 1:
            self.spk_osc.take(self.frame)                       # one source frame is dropped: the tone stays real-time
        quiet = silent or p.spk == "silence"
        samples = [0.0] * self.frame if quiet else self.spk_osc.take(self.frame)
        t0 = time.perf_counter()
        frame = self.enc.encode(to_pcm(samples))
        self.enc_ms.append((time.perf_counter() - t0) * 1000)
        return frame

    def _frames_in(self, i: int) -> int:
        """How many short frames report ``i`` carries: the mean length is spread evenly (4 or 5 x 2.5 ms, 2 or 3 x 5 ms)."""
        per = self.plan.mean_ms / self.plan.spk_ms                 # frames per report on average (4.27 / 2.13)
        return int((i + 1) * per) - int(i * per)

    def _variable(self, i: int, silent: bool) -> bytes:
        """One packet of ``_frames_in(i)`` short frames joined by the repacketizer: the average audio per report is the report period."""
        assert self.enc is not None and self.repack is not None
        quiet = silent or self.plan.spk == "silence"
        t0 = time.perf_counter()
        packets = [self.enc.encode(to_pcm([0.0] * self.frame if quiet else self.spk_osc.take(self.frame)))
                   for _ in range(self._frames_in(i))]
        out = self.repack.join(packets)
        self.enc_ms.append((time.perf_counter() - t0) * 1000)
        return out

    def _haptics(self, tail: bool) -> bytes:
        if self.plan.hapt == "tone" and not tail:
            return to_s8_stereo(self.hapt_osc.take(HAPT_FRAMES))
        return bytes(HAPT_CHUNK)

    def _tick(self, i: int, tail: bool) -> Tick | None:
        p, t = self.plan, i * self.period
        in_gap = bool(p.gap_mode) and p.gap_at <= t < p.gap_at + p.gap_len
        speaker = self._speaker(i, silent=tail or (in_gap and p.gap_mode == "silence"))
        hapt = self._haptics(tail)
        if in_gap and p.gap_mode == "skip":
            return None
        return Tick(hapt, speaker, self._kind(t))


# ---------------------------------------------------------------- pad access
class Pad:
    """The pad's hidraw: one fd, one sequence counter shared by every report (like Rumble in the sender)."""

    def __init__(self, path: str | None, len36: int, len32: int) -> None:
        """Open ``path`` for writing (None = /dev/null, dry run)."""
        self.fd = os.open(path or os.devnull, os.O_WRONLY)
        self.seq, self.counter, self.len36, self.len32 = 0, 0, len36, len32
        self.lock = threading.Lock()
        self.first: dict[str, bytes] = {}      # the first report of each kind, for the log

    def _send(self, key: str, build: Callable[[int], bytes]) -> None:
        with self.lock:
            self.seq = (self.seq + 1) & 0x0F
            data = build(self.seq)
            self.first.setdefault(key, data)
            os.write(self.fd, data)

    def send31(self, key: str = "0x31", **kw: Any) -> None:
        """Send a 0x31 report built with build_0x31(seq, **kw)."""
        self._send(key, lambda seq: build_0x31(seq, **kw))

    def send_setup(self, setup: Setup, volume: int) -> None:
        """Send a setup report."""
        self.send31(f"setup-{setup.name}", **setup.kwargs(volume))

    def send_normal(self) -> None:
        """Send the sender's current-style report with motors and triggers off."""
        self._send("normal", normal_report)

    def send_tick(self, layout: Layout, tick: Tick) -> None:
        """Send one 0x32 / 0x36 report for a tick."""
        def build(seq: int) -> bytes:
            self.counter += 1
            if tick.kind == "32":
                return build_report(R32_ID, L32, seq, self.counter, Tick(tick.hapt, None, "32"), self.len32)
            return build_report(R36_ID, layout, seq, self.counter, tick, self.len36)
        self._send("0x" + tick.kind, build)

    def close(self) -> None:
        """Close the fd."""
        with contextlib.suppress(OSError):
            os.close(self.fd)


@dataclasses.dataclass
class PadInfo:
    """A found pad."""

    name: str
    uniq: str
    hidraw: str | None
    bt: bool
    event: str | None


def _hid_dir(node: str) -> tuple[str, int] | None:
    """Walk up a sysfs path to the HID device directory (0005:054C:0CE6.0001); returns (dir, bus)."""
    path = node
    while path not in ("/", ""):
        match = _HID_DIR.match(os.path.basename(path))
        if match:
            return path, int(match.group(1), 16)
        path = os.path.dirname(path)
    return None


def _uevent(path: str) -> dict[str, str]:
    try:
        with open(path) as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return {}


def find_hidraw(event_path: str, uniq: str) -> tuple[str | None, bool]:
    """Find the hidraw node of the same HID device as an evdev node (sysfs walk, then HID_UNIQ), like the sender does."""
    node = os.path.realpath(f"/sys/class/input/{os.path.basename(event_path)}")
    hid = _hid_dir(node)
    if hid is not None:
        found = sorted(glob.glob(os.path.join(hid[0], "hidraw", "hidraw*")))
        if found:
            return f"/dev/{os.path.basename(found[0])}", hid[1] == BT_BUS
    for raw in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        ev = _uevent(os.path.join(raw, "device", "uevent"))
        if uniq and ev.get("HID_UNIQ", "").lower() == uniq.lower():
            return f"/dev/{os.path.basename(raw)}", int(ev.get("HID_ID", "0:0:0").split(":")[0], 16) == BT_BUS
    return None, False


def find_pads() -> list[PadInfo]:
    """List the connected DualSense pads (needs python-evdev)."""
    try:
        import evdev
    except ImportError:
        raise SystemExit("python-evdev is missing: doas pip install --break-system-packages evdev, or pass --hidraw /dev/hidrawN") from None
    pads = []
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if "Wireless Controller" in dev.name and "Motion" not in dev.name and "Touchpad" not in dev.name:
            hidraw, bt = find_hidraw(path, dev.uniq or "")
            pads.append(PadInfo(dev.name, dev.uniq or "", hidraw, bt, path))
        dev.close()
    return pads


def output_sizes(descriptor: bytes) -> dict[int, int]:
    """Parse a HID report descriptor: {output report id: data bytes without the id byte}."""
    sizes: dict[int, int] = {}
    report_id = size = count = 0
    i = 0
    while i < len(descriptor):
        prefix = descriptor[i]
        if prefix == LONG_ITEM:                              # long item: skip it
            i += 3 + descriptor[i + 1]
            continue
        length = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(descriptor[i + 1:i + 1 + length], "little")
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:                                   # global items
            if tag == TAG_SIZE:
                size = value
            elif tag == TAG_ID:
                report_id = value
            elif tag == TAG_COUNT:
                count = value
        elif kind == 0 and tag == TAG_OUTPUT:                    # main item "Output"
            sizes[report_id] = sizes.get(report_id, 0) + size * count
        i += 1 + length
    return {rid: (bits + 7) // 8 for rid, bits in sizes.items()}


def descriptor_sizes(hidraw: str) -> dict[int, int]:
    """Output report sizes of a hidraw node, from sysfs ({} when unreadable)."""
    try:
        return output_sizes(Path(f"/sys/class/hidraw/{os.path.basename(hidraw)}/device/report_descriptor").read_bytes())
    except OSError:
        return {}


def sender_pids() -> list[int]:
    """PIDs of a running dualsense sender (it would write to the pad at the same time)."""
    pids = []
    for entry in glob.glob("/proc/[0-9]*/cmdline"):
        with contextlib.suppress(OSError):
            if b"sender_controller" in Path(entry).read_bytes():
                pids.append(int(entry.split("/")[2]))
    return pids


# ---------------------------------------------------------------- results and statistics
def pct(values: list[float], p: float) -> float:
    """Percentile of a list (nearest rank)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))]


def describe(values_ms: list[float]) -> dict[str, float]:
    """Count, mean, p50 / p99 / p99.9 / max of a list of milliseconds."""
    if not values_ms:
        return {"n": 0}
    return {"n": len(values_ms), "mean": round(sum(values_ms) / len(values_ms), 3), "p50": round(pct(values_ms, 50), 3),
            "p99": round(pct(values_ms, 99), 3), "p999": round(pct(values_ms, 99.9), 3), "max": round(max(values_ms), 3)}


def load_results(path: Path) -> dict[str, Any]:
    """The results file as a dict ({} when missing or broken)."""
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    return {}


def save_result(path: Path, key: str, value: object) -> None:
    """Merge ``{key: value}`` into the results JSON file."""
    data = load_results(path)
    data[key] = value
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"  -> saved '{key}' in {path}")


def ask(prompt: str, choices: str) -> str:
    """Ask a question answered by one of the letters in ``choices`` (anything after the letter is kept as a comment)."""
    if AUTO:
        print(f"{prompt} [{'/'.join(choices)}] -> {choices[0]} (auto)")
        return choices[0]
    while True:
        answer = input(f"{prompt} [{'/'.join(choices)}]: ").strip().lower()
        if answer[:1] in choices and answer:
            return answer


def wait(seconds: float) -> None:
    """Sleep for an observation window."""
    time.sleep(seconds * TIME_SCALE)


# ---------------------------------------------------------------- streaming
@dataclasses.dataclass
class Stats:
    """What a stream did."""

    reports: int = 0
    skipped: int = 0
    errors: int = 0
    resyncs: int = 0
    started: float = 0.0
    ended: float = 0.0
    intervals_ms: list[float] = dataclasses.field(default_factory=list)
    write_ms: list[float] = dataclasses.field(default_factory=list)
    enc_ms: list[float] = dataclasses.field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """A JSON-friendly summary."""
        span = max(self.ended - self.started, 1e-9)
        return {"reports": self.reports, "skipped": self.skipped, "errors": self.errors, "resyncs": self.resyncs,
                "reports_per_s": round(self.reports / span, 2), "interval_ms": describe(self.intervals_ms),
                "write_ms": describe(self.write_ms), "encode_ms": describe(self.enc_ms)}


def run_stream(pad: Pad, layout: Layout, source: Source, stop: threading.Event | None = None) -> Stats:
    """Write one report per period (absolute deadlines: no drift, a resync after a stall) until the source ends or ``stop`` is set."""
    st = Stats()
    st.started = deadline = time.monotonic()
    last, fails = 0.0, 0
    for tick in source:
        if stop is not None and stop.is_set():
            break
        t0 = time.monotonic()
        if tick is None:
            st.skipped += 1
        else:
            if last:
                st.intervals_ms.append((t0 - last) * 1000)
            last = t0
            try:
                pad.send_tick(layout, tick)
                fails = 0
            except OSError as e:
                st.errors += 1
                fails += 1
                if st.errors == 1:
                    print(f"  write error: {e!r}")
                if fails >= MAX_FAILS:
                    break
            st.write_ms.append((time.monotonic() - t0) * 1000)
            st.reports += 1
        deadline += source.period
        lag = time.monotonic() - deadline
        if lag > RESYNC_PERIODS * source.period:
            deadline, st.resyncs = time.monotonic(), st.resyncs + 1
        elif lag < 0:
            time.sleep(-lag)
    st.ended = time.monotonic()
    st.enc_ms = source.enc_ms
    return st


class Background:
    """A stream running in a thread, so the main thread can send 0x31 reports or read the input meanwhile."""

    def __init__(self, pad: Pad, layout: Layout, source: Source) -> None:
        """Start streaming ``source`` right away."""
        self.stop = threading.Event()
        self.stats = Stats()
        self.thread = threading.Thread(target=self._run, args=(pad, layout, source), daemon=True)
        self.thread.start()

    def _run(self, pad: Pad, layout: Layout, source: Source) -> None:
        self.stats = run_stream(pad, layout, source, self.stop)

    def finish(self) -> Stats:
        """Stop the stream and return what it did."""
        self.stop.set()
        self.thread.join()
        return self.stats


def report_stats(label: str, st: Stats) -> dict[str, object]:
    """Print and return a stream summary."""
    s = st.summary()
    print(f"  {label}: {s['reports']} reports, {s['reports_per_s']}/s, skipped {s['skipped']}, "
          f"errors {s['errors']}, resyncs {s['resyncs']}")
    print(f"    interval ms {s['interval_ms']}")
    print(f"    write ms    {s['write_ms']}")
    print(f"    encode ms   {s['encode_ms']}")
    return s


# ---------------------------------------------------------------- session
@dataclasses.dataclass
class Session:
    """Everything a command needs: the pad, the opened hidraw, the chosen setup / layout / Opus size and the options."""

    pad: Pad
    info: PadInfo
    args: argparse.Namespace
    sizes: dict[int, int]
    setup: Setup
    layout: Layout
    size: int

    @property
    def out(self) -> Path:
        """The results file."""
        return Path(self.args.out)

    @property
    def period(self) -> float:
        """The report period in seconds."""
        return float(self.args.period_ms) / 1000

    def prepare(self, setup: Setup | None = None) -> None:
        """Send the 0x31 setup report and give the pad a moment."""
        self.pad.send_setup(setup or self.setup, self.args.volume)
        time.sleep(0.05)

    def source(self, plan: Plan, size: int | None = None, period: float | None = None) -> Source:
        """A source for a plan with a fresh encoder (CBR state starts clean)."""
        ms = plan.spk_ms                                        # the same bitrate whatever the frame length
        enc = (make_encoder(round((size or self.size) * ms / 10), bool(self.args.dry_run), ms=ms, **CODECS[plan.codec])
               if plan.spk != "off" else None)
        return Source(plan, period or self.period, enc)

    def play(self, plan: Plan, *, setup: Setup | None = None, layout: Layout | None = None, size: int | None = None,
             period: float | None = None, prepare: bool = True) -> Stats:
        """Prepare the pad and stream a plan to its end."""
        if prepare:
            self.prepare(setup)
        return run_stream(self.pad, layout or self.layout, self.source(plan, size, period))

    def background(self, plan: Plan, period: float | None = None) -> Background:
        """Prepare the pad and stream a plan in a thread."""
        self.prepare()
        return Background(self.pad, self.layout, self.source(plan, None, period))


def _pick_pad(args: argparse.Namespace) -> PadInfo:
    """The pad to probe: the one given, the one with --mac, or the first found (refuses a live sender and a USB pad)."""
    if sender_pids() and not args.force:
        raise SystemExit(f"the sender is running (pid {sender_pids()}): doas systemctl stop dualsense-sender (or --force)")
    if args.hidraw:
        info = PadInfo("(given)", "", args.hidraw, True, None)
    else:
        pads = find_pads()
        if args.mac:
            pads = [p for p in pads if p.uniq.lower() == args.mac.lower()]
        if not pads:
            raise SystemExit("no DualSense found (connected? root?)")
        info = pads[0]
        for p in pads:
            print(f"pad: {p.name} {p.uniq} hidraw={p.hidraw} {'Bluetooth' if p.bt else 'USB'}{'  <- using' if p is info else ''}")
    if info.hidraw is None or not info.bt:
        raise SystemExit("need a Bluetooth pad with a hidraw node (report 0x36 does not exist on USB)")
    return info


def _choice(args: argparse.Namespace) -> tuple[Setup, Layout, int]:
    """Setup / layout / size: the command line, else `layout`'s best (from the results file), else the defaults."""
    best = load_results(Path(args.out)).get("layout", {}).get("best", {})
    setup = BY_SETUP[args.setup or best.get("setup") or "notes"]
    layout = BY_LAYOUT[args.layout or best.get("layout") or "hyp"]
    return setup, layout, int(args.size or best.get("size") or DEFAULT_SIZE)


@contextlib.contextmanager
def session(args: argparse.Namespace) -> Iterator[Session]:
    """Find the pad, refuse to run next to a live sender, open hidraw; restore the pad's normal state on exit."""
    global AUTO, TIME_SCALE
    if args.dry_run:
        AUTO, TIME_SCALE = True, 0.05
        info = PadInfo("(dry run)", "", None, True, None)
    else:
        info = _pick_pad(args)
    sizes = descriptor_sizes(info.hidraw) if info.hidraw else {}
    len36 = args.len36 or (sizes[R36_ID] + 1 if R36_ID in sizes else R36_LEN)
    len32 = sizes[R32_ID] + 1 if R32_ID in sizes else R32_LEN
    pad = Pad(info.hidraw, len36, len32)
    setup, layout, size = _choice(args)
    try:
        yield Session(pad, info, args, sizes, setup, layout, size)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        with contextlib.suppress(OSError):
            pad.send_normal()
        pad.close()


def hexdump(data: bytes) -> str:
    """Hex bytes, 32 per line."""
    return "\n".join("    " + data[i:i + 32].hex(" ") for i in range(0, len(data), 32))


def show_first(s: Session, keys: tuple[str, ...] = ("0x36",)) -> None:
    """Print the exact first reports that went out."""
    for key in keys:
        if key in s.pad.first:
            print(f"  first {key} report ({len(s.pad.first[key])} B):\n{hexdump(s.pad.first[key])}")


# ---------------------------------------------------------------- commands: bench, info, tone
def _bench_frames() -> list[array.array]:  # type: ignore[type-arg]
    """100 PCM frames: a few sines plus noise, so the encoder has something to do (a pure tone is cheaper than music)."""
    rng = random.Random(1)
    frames = []
    for k in range(100):
        base = k * OPUS_FRAME
        samples = [0.25 * math.sin(2 * math.pi * 220 * (base + i) / OPUS_RATE)
                   + 0.15 * math.sin(2 * math.pi * 1800 * (base + i) / OPUS_RATE)
                   + 0.1 * rng.uniform(-1, 1) for i in range(OPUS_FRAME)]
        frames.append(to_pcm(samples))
    return frames


def _bench_paced(args: argparse.Namespace) -> None:
    """Encoder cost in the real rhythm: one frame per report period, so the core idles between frames like in the sender.

    The three parts of a frame are timed apart (the sine and the PCM conversion are probe code, only ``opus_encode`` is what the
    sender would pay), to see why the streams of the first round showed ~2 ms per frame against 0.09 ms back to back.
    """
    secs = 0.5 if args.dry_run else args.secs
    rows = []
    for complexity in (0, 5, 10):
        enc = make_encoder(DEFAULT_SIZE, bool(args.dry_run), complexity)
        osc = Sine(SPK_HZ, SPK_AMP, OPUS_RATE, None)
        parts: dict[str, list[float]] = {"sine": [], "to_pcm": [], "opus_encode": []}
        deadline = time.monotonic()
        for _ in range(max(1, round(secs / REPORT_PERIOD))):
            t0 = time.perf_counter()
            samples = osc.take(OPUS_FRAME)
            t1 = time.perf_counter()
            pcm = to_pcm(samples)
            t2 = time.perf_counter()
            enc.encode(pcm)
            t3 = time.perf_counter()
            for name, ms in zip(parts, ((t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000), strict=True):
                parts[name].append(ms)
            deadline += REPORT_PERIOD
            time.sleep(max(0.0, deadline - time.monotonic()))
        enc.close()
        row: dict[str, object] = {"complexity": complexity, "bytes_per_frame": DEFAULT_SIZE}
        row.update({name: describe(values) for name, values in parts.items()})
        rows.append(row)
        print(f"  paced, complexity {complexity:2d}: " + "; ".join(
            f"{name} mean {describe(values)['mean']} p99 {describe(values)['p99']} max {describe(values)['max']} ms"
            for name, values in parts.items()))
    save_result(Path(args.out), "bench_paced", {"secs": secs, "period_ms": round(REPORT_PERIOD * 1000, 3), "rows": rows})


def cmd_bench(args: argparse.Namespace) -> None:
    """Encoder CPU per 10 ms frame for the bitrates / complexities that matter, and what that means as a share of one core."""
    frames = _bench_frames()
    rows = []
    print(f"libopus {opus_version()}, {platform.machine()}, python {platform.python_version()}")
    for size in SIZES:
        for complexity in (0, 5, 10):
            enc = make_encoder(size, bool(args.dry_run), complexity)
            times = []
            for k in range(BENCH_FRAMES):
                t0 = time.perf_counter()
                enc.encode(frames[k % len(frames)])
                times.append((time.perf_counter() - t0) * 1000)
            enc.close()
            d = describe(times)
            core = round(d["mean"] / (OPUS_PERIOD * 1000) * 100, 2)
            rows.append({"bytes_per_frame": size, "kbps": size * 8 / 10, "complexity": complexity, "encode_ms": d,
                         "core_percent_at_100fps": core, "frame_size_mismatches": enc.mismatch})
            print(f"  {size:3d} B ({size * 0.8:5.1f} kbps) complexity {complexity:2d}: mean {d['mean']} ms, p99 {d['p99']} ms, "
                  f"max {d['max']} ms -> {core} % of one core per pad; size mismatches {enc.mismatch}")
    meta = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "machine": platform.machine(), "kernel": platform.release(),
            "libopus": opus_version(), "cpus": os.cpu_count()}
    save_result(Path(args.out), "bench", {"meta": meta, "rows": rows})
    if args.paced:
        _bench_paced(args)


def cmd_info(s: Session) -> None:
    """Pad, hidraw, descriptor sizes, libopus and the chosen setup / layout / size."""
    meta = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "kernel": platform.release(), "python": platform.python_version(),
            "libopus": opus_version(), "pad": dataclasses.asdict(s.info),
            "output_report_data_bytes": {hex(k): v for k, v in sorted(s.sizes.items())}, "len36_used": s.pad.len36,
            "chosen": {"setup": s.setup.name, "layout": s.layout.name, "size": s.size}}
    print(json.dumps(meta, indent=2))
    if R36_ID in s.sizes and s.sizes[R36_ID] + 1 != R36_LEN:
        print(f"!! the descriptor says report 0x36 has {s.sizes[R36_ID]} data bytes, not {R36_LEN - 1}: using {s.pad.len36} B")
    elif R36_ID not in s.sizes:
        print("!! report 0x36 is not in the HID descriptor (or sysfs is unreadable): the pad may ignore it")
    save_result(s.out, "meta", meta)


def cmd_tone(s: Session) -> None:
    """Stream a speaker tone (and an 80 Hz haptics tone with --hapt) with the chosen layout and print the bytes and pacing."""
    a = s.args
    plan = Plan(secs=a.seconds, spk_hz=a.hz, hapt="tone" if a.hapt else "zero", codec="" if a.codec == "celt" else a.codec)
    print(f"streaming {a.hz} Hz for {a.seconds} s: setup '{s.setup.name}', layout '{s.layout.name}', {s.size} B frames, "
          f"codec {a.codec}, period {a.period_ms} ms ...")
    st = s.play(plan)
    show_first(s, (f"setup-{s.setup.name}", "0x36"))
    save_result(s.out, f"tone_{s.setup.name}_{s.layout.name}_{s.size}", report_stats("tone", st))


# ---------------------------------------------------------------- commands: layout
def _best(rows: list[dict[str, Any]], key: str) -> Any:
    """The value of ``key`` in the first row that sounded clean, else weak, else distorted (rows are in preference order)."""
    for want in ("y", "w", "d"):
        for row in rows:
            if row["answer"].startswith(want):
                return row[key]
    return None


def _layout_try(s: Session, setup: Setup, layout: Layout, size: int, label: str) -> dict[str, Any]:
    """Play 2 s of speaker tone with one combination and ask what you heard."""
    print(f"\n== {label}: setup {setup.name} ({setup.note}); layout {layout.name} ({layout.note}); {size} B frames")
    st = s.play(Plan(secs=2.0), setup=setup, layout=layout, size=size)
    answer = ask(Q_SPK, "ynwd")
    first = s.pad.first.pop("0x36", b"")
    s.pad.first.pop(f"setup-{setup.name}", None)
    row = {"setup": setup.name, "layout": layout.name, "size": size, "answer": answer, "first_0x36": first.hex(), "reports": st.reports,
           "errors": st.errors, "encode_ms": describe(st.enc_ms)}
    s.pad.send_normal()
    wait(3.0)
    return row


def cmd_layout(s: Session) -> None:
    """Find a setup, then a layout, then an Opus size that make the speaker play; the best of each goes to the results file."""
    base_layout, base_size = BY_LAYOUT["hyp"], DEFAULT_SIZE
    setups = [_layout_try(s, st, base_layout, base_size, f"setup {i + 1}/{len(SETUPS)}") for i, st in enumerate(SETUPS)]
    best_setup = _best(setups, "setup") or "notes"
    if _best(setups, "setup") is None:
        print("\n!! no setup made the speaker play; going on with 'notes' (the layouts and sizes below are probably all 'n')")
    layouts = [_layout_try(s, BY_SETUP[best_setup], lay, base_size, f"layout {i + 1}/{len(LAYOUTS)}") for i, lay in enumerate(LAYOUTS)]
    best_layout = _best(layouts, "layout") or "hyp"
    sizes = [_layout_try(s, BY_SETUP[best_setup], BY_LAYOUT[best_layout], n, f"size {i + 1}/{len(SIZES)}") for i, n in enumerate(SIZES)]
    best = {"setup": best_setup, "layout": best_layout, "size": _best(sizes, "size") or DEFAULT_SIZE,
            "worked": any(r["answer"].startswith(("y", "w", "d")) for r in setups + layouts + sizes)}
    print(f"\nbest: {best}")
    save_result(s.out, "layout", {"setups": setups, "layouts": layouts, "sizes": sizes, "best": best})


# ---------------------------------------------------------------- commands: timing, combo
TIMINGS: list[tuple[str, str, float, Plan]] = [
    ("p10.667", "one frame per 10.667 ms report (the haptics clock)", REPORT_PERIOD, Plan(secs=5.0)),
    ("p10.0", "one frame per 10.0 ms report (the Opus clock)", OPUS_PERIOD, Plan(secs=5.0)),
    ("gap-silence", "10.667 ms, a 60 ms underrun at 2 s filled with silence frames", REPORT_PERIOD,
     Plan(secs=5.0, gap_at=2.0, gap_len=0.06, gap_mode="silence")),
    ("gap-skip", "10.667 ms, a 60 ms underrun at 2 s: no reports at all", REPORT_PERIOD,
     Plan(secs=5.0, gap_at=2.0, gap_len=0.06, gap_mode="skip")),
]


def cmd_timing(s: Session) -> None:
    """Pacing by ear: which report period gives a clean speaker tone, and what an underrun should look like."""
    results = []
    for name, note, period, plan in TIMINGS:
        print(f"\n== {name}: {note} (a 500 Hz tone; note the pitch too: add it as a comment, e.g. `n lower pitch`)")
        st = s.play(plan, period=period)
        answer = ask(Q_GLITCH, "ntpd")
        results.append({"variant": name, "period_ms": round(period * 1000, 3), "answer": answer, **report_stats(name, st)})
        s.pad.send_normal()
        wait(3.0)
    save_result(s.out, "timing", results)


# (name, report period, drop one source frame every N reports); a 440 Hz tone, 6 s each
CLOCKS: list[tuple[str, float, int]] = [
    ("p10.667", REPORT_PERIOD, 0), ("p10.4", 0.0104, 0), ("p10.2", 0.0102, 0), ("p11.0", 0.011, 0), ("p11.5", 0.0115, 0),
    ("p10.667-drop16", REPORT_PERIOD, 16),
]


def cmd_clock(s: Session) -> None:
    """How the pad's speaker follows the report clock: a period sweep (glitches and pitch) and a frame dropped every 16 reports.

    The first round's drop test used 500 Hz, which is exactly 5 cycles per 10 ms, so a dropped frame changed nothing. 440 Hz is
    4.4 cycles: a dropped frame is a phase jump (a click if the pad plays the frames back to back). The pitch against the first
    run tells whether the pad stretches the audio to the report rate (lower pitch with a longer period) or plays frames as they come.
    """
    results = []
    for k, (name, period, drop) in enumerate(CLOCKS):
        print(f"\n== {name}: report every {period * 1000:.1f} ms" + (f", a source frame dropped every {drop} reports" if drop else ""))
        st = s.play(Plan(secs=6.0, spk_hz=CLOCK_HZ, drop_every=drop), period=period)
        row: dict[str, object] = {"variant": name, "period_ms": round(period * 1000, 3), "drop_every": drop,
                                  "glitch": ask(Q_GLITCH, "ntpd")}
        if k:
            row["pitch_vs_first"] = ask("pitch compared to the first run: l(ower) / s(ame) / h(igher)", "lsh")
        row.update(report_stats(name, st))
        results.append(row)
        s.pad.send_normal()
        wait(3.0)
    save_result(s.out, "clock", results)


GAPS: list[tuple[str, str, float, dict[str, Any]]] = [
    ("today", "what the sender does now: 10 ms frames every 10.667 ms, a source frame dropped every 16", REPORT_PERIOD, {"drop_every": 16}),
    ("nodrop", "10 ms frames every 10.667 ms, nothing dropped: shows the pad's own artefact (a gap per report?)", REPORT_PERIOD, {}),
    ("p10.0", "10 ms frames every 10.0 ms (the pad overflows; for comparison)", 0.010, {}),
    ("opus20-alt", "20 ms frames in every second report (a gap every 21.3 ms instead of every 10.7 ms)", REPORT_PERIOD,
     {"spk_ms": 20, "spk_every": 2}),
    ("p10.5", "10 ms frames every 10.5 ms (the pad is fed 1.6% too fast: does it stop fading the frame edges?)", 0.0105, {}),
    ("p10.4", "10 ms frames every 10.4 ms (2.6% too fast)", 0.0104, {}),
    ("p10.3", "10 ms frames every 10.3 ms (3.5% too fast)", 0.0103, {}),
    ("lowdelay", "10.667 ms period, Opus RESTRICTED_LOWDELAY (CELT without look-ahead)", REPORT_PERIOD, {"codec": "lowdelay"}),
    ("silk10", "10.667 ms period, Opus SILK-only 10 ms (wideband, no MDCT overlap)", REPORT_PERIOD, {"codec": "silk"}),
    ("hybrid10", "10.667 ms period, Opus hybrid 10 ms", REPORT_PERIOD, {"codec": "hybrid"}),
    ("var2.5", "every report carries 4 or 5 frames of 2.5 ms (10 / 12.5 ms, 10.667 ms on average): no gap if the pad takes it",
     REPORT_PERIOD, {"spk_ms": 2.5, "mean_ms": REPORT_PERIOD * 1000}),
    ("var5", "every report carries 2 or 3 frames of 5 ms (10 / 15 ms, 10.667 ms on average)",
     REPORT_PERIOD, {"spk_ms": 5, "mean_ms": REPORT_PERIOD * 1000}),
]


def cmd_gap(s: Session) -> None:
    """Record-and-measure: the same clean tone (or your speech) in four ways, to see which one has no 94 Hz buzz.

    The pad plays one Opus frame per 10.667 ms report; with 10 ms frames that leaves 0.667 ms per report that nothing fills, or
    the pad stretches / hides it somehow. A steady tone shows it as sidebands 93.75 Hz either side of the tone. Nothing is asked
    here: for every variant the script says when to press record, plays 1 s of nothing + the tone, and you save one file per variant
    (name them after the variant). Analysis is offline.
    """
    only = set(s.args.only.split(",")) if s.args.only else None
    results = []
    for name, note, period, extra in GAPS:
        if only and name not in only:
            continue
        print(f"\n== {name}: {note}")
        if not (AUTO or s.args.continuous):
            input("  start the recording, then press Enter (the tone starts after 1 s): ")
        wait(1.0)
        plan = Plan(secs=s.args.secs, spk_hz=s.args.hz, wav=s.args.wav or "", **extra)
        st = s.play(plan, period=period)
        results.append({"variant": name, **report_stats(name, st)})
        s.pad.send_normal()
        wait(2.0)
        if s.args.continuous:
            wait(3.0)                                           # silence between the variants: the analysis cuts the file there
        elif not AUTO:
            input(f"  stop the recording and save it as gap_{name}.mp3 (Enter for the next variant): ")
    save_result(s.out, "gap", results)


COMBOS: list[tuple[str, str, Plan, tuple[tuple[str, str, str], ...]]] = [
    ("both", "speaker 500 Hz + haptics 80 Hz in every 0x36", Plan(secs=4.0, hapt="tone"),
     (("speaker", Q_SPK, "ynwd"), ("haptics", Q_HAPT, "ynw"))),
    ("hapt-only", "0x36 without a speaker sub-packet, haptics 80 Hz", Plan(secs=4.0, spk="off", hapt="tone"),
     (("haptics", Q_HAPT, "ynw"),)),
    ("silent-speaker", "the speaker sub-packet carries digital silence, haptics 80 Hz", Plan(secs=4.0, spk="silence", hapt="tone"),
     (("haptics", Q_HAPT, "ynw"),)),
    ("spk-only", "speaker only, zero haptics chunks (0x36 alone drives the 10.667 ms tick)", Plan(secs=4.0), (("speaker", Q_SPK, "ynwd"),)),
    ("switch", "0x32 haptics (2 s) -> 0x36 speaker + haptics (3 s) -> 0x32 (2 s)",
     Plan(secs=7.0, hapt="tone", script=((2.0, "32"), (5.0, "36"), (7.0, "32"))),
     (("haptics through both switches (no cut)", Q_HAPT, "ynw"), ("speaker during the middle part", Q_SPK, "ynwd"))),
]


def cmd_combo(s: Session) -> None:
    """Haptics and speaker in one 0x36, each alone, and a live switch between 0x32 and 0x36."""
    results = []
    for name, note, plan, questions in COMBOS:
        print(f"\n== {name}: {note}")
        st = s.play(plan)
        row: dict[str, Any] = {"variant": name, **report_stats(name, st)}
        for label, prompt, choices in questions:
            row[label] = ask(f"{label} - {prompt}", choices)
        results.append(row)
        s.pad.send_normal()
        wait(3.0)
    save_result(s.out, "combo", results)


# ---------------------------------------------------------------- commands: modes
# 0x31 reports tried while the speaker plays: (name, kwargs of build_0x31; "use_setup" = re-send a setup report instead)
STEPS: list[tuple[str, dict[str, Any]]] = [
    ("repo-flags", {"flag0": 0x03, "flag2": 0x04}),
    ("lightbar-flag0-zero", {"flag1": 0x04, "rgb": (255, 0, 0)}),
    ("trigger-only", {"flag0": 0x0C, "trig": (feedback_trigger(), feedback_trigger())}),
    ("trigger-with-repo-flags", {"flag0": 0x0F, "flag2": 0x04, "trig": (feedback_trigger(), feedback_trigger())}),
    ("motors-set", {"flag0": 0x03, "flag2": 0x04, "motors": (120, 120)}),
    ("speaker-setup-resend", {"use_setup": "chosen"}),
    ("p0-setup-resend", {"use_setup": "p0"}),
]


def _send_step(s: Session, name: str, kw: dict[str, Any]) -> None:
    """Send one step's 0x31 report."""
    if "use_setup" in kw:
        setup = s.setup if kw["use_setup"] == "chosen" else BY_SETUP[kw["use_setup"]]
        s.pad.send_setup(setup, s.args.volume)
    else:
        s.pad.send31(name, **kw)


def _observe(s: Session, name: str, phase: str, repeat: bool, kw: dict[str, Any]) -> dict[str, object]:
    """Send the step's report once (or every 0.4 s for ~3 s), then ask what happened to the speaker."""
    print(f"  {name}: sending {'every 0.4 s for 3 s' if repeat else 'once'} ...")
    for _ in range(8 if repeat else 1):
        _send_step(s, f"{name}-{phase}", kw)
        if repeat:
            time.sleep(0.4 * TIME_SCALE if AUTO else 0.4)
    wait(3.0)
    answer = ask("speaker tone now: k(ept) / c(ut) / w(eaker)", "kcw")
    row: dict[str, object] = {"step": name, "phase": phase, "answer": answer}
    if answer.startswith("c"):
        s.pad.send_setup(s.setup, s.args.volume)
        wait(1.5)
        row["back_after_speaker_setup"] = ask("after re-sending the speaker setup: is it back? y(es) / n(o)", "yn")
    return row


def cmd_modes(s: Session) -> None:
    """Does a 0x31 report during the speaker stream cut it? The answer decides the flags of the refreshes in P3b."""
    only = set(s.args.only.split(",")) if s.args.only else set()
    unknown = only - {name for name, _ in STEPS}
    if unknown:
        raise SystemExit(f"unknown step(s) {sorted(unknown)}; known: {', '.join(name for name, _ in STEPS)}")
    results = []
    for run in range(1, s.args.repeat + 1):
        for name, kw in STEPS:
            if only and name not in only:
                continue
            print(f"\n== {name} (run {run}/{s.args.repeat})")
            s.pad.send_normal()
            wait(1.0)
            bg = s.background(Plan(secs=None, preencode=True))
            wait(2.0)
            for phase, repeat in (("once", False), ("repeat", True)):
                results.append({**_observe(s, name, phase, repeat, kw), "run": run})
            bg.finish()
    save_result(s.out, "modes_repeat" if only or s.args.repeat > 1 else "modes", results)


# ---------------------------------------------------------------- commands: jitter
def find_motion(info: PadInfo) -> str | None:
    """The evdev node of the pad's motion sensors (same HID_UNIQ): the gyro noise makes it emit on every input report."""
    import evdev
    found = None
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if "Motion Sensors" in dev.name and (dev.uniq or "").lower() == info.uniq.lower():
            found = path
        dev.close()
    return found


def _describe_stamps(stamps: list[int], secs: float) -> dict[str, object]:
    """Describe the intervals between event timestamps (ns): rate, percentiles and how many gaps were long."""
    gaps = [(b - a) / 1e6 for a, b in itertools.pairwise(stamps)]
    return {"events": len(stamps), "rate_hz": round(len(stamps) / secs, 1), **describe(gaps),
            "gaps_over_20ms": sum(g > GAP_WARN_MS[0] for g in gaps), "gaps_over_50ms": sum(g > GAP_WARN_MS[1] for g in gaps)}


def measure_input(devs: dict[str, Any], secs: float) -> dict[str, dict[str, object]]:
    """Read several evdev nodes at once for ``secs``; per node, describe the SYN_REPORT intervals (kernel timestamps)."""
    from evdev import ecodes
    stamps: dict[str, list[int]] = {name: [] for name in devs}
    by_fd = {dev.fd: name for name, dev in devs.items()}
    end = time.monotonic() + secs
    while time.monotonic() < end:
        for fd in select.select(list(by_fd), [], [], 0.2)[0]:
            name = by_fd[fd]
            stamps[name] += [e.sec * 1_000_000_000 + e.usec * 1000 for e in devs[name].read()
                             if e.type == ecodes.EV_SYN and e.code == ecodes.SYN_REPORT]
    return {name: _describe_stamps(st, secs) for name, st in stamps.items()}


def cmd_jitter(s: Session) -> None:
    """Input interval jitter without / with a speaker stream (A-B-A like P0; --combo adds speaker + haptics)."""
    import evdev
    if s.info.event is None:
        raise SystemExit("jitter needs the pad's evdev node (not available with --hidraw / --dry-run)")
    devs: dict[str, Any] = {"pad": evdev.InputDevice(s.info.event)}
    motion = find_motion(s.info)
    if motion:
        devs["motion"] = evdev.InputDevice(motion)
    else:
        print("note: no motion sensors node found, measuring the gamepad node only (it emits only when something changes)")
    pre = not s.args.live_encode             # a pre-encoded loop keeps the probe thread quiet (no Python stalls next to the evdev reader)
    phases: list[tuple[str, str, Plan | None]] = [("no_stream", "A: no stream", None),
                                                  ("speaker", "B: speaker stream (0x36)", Plan(secs=None, preencode=pre))]
    if s.args.combo:
        phases.append(("speaker_haptics", "C: speaker + haptics in 0x36", Plan(secs=None, hapt="tone", preencode=pre)))
    phases.append(("no_stream_again", "A again: no stream", None))
    print("leave the pad on the table and do not touch it.")
    results: dict[str, object] = {"period_ms": s.args.period_ms, "pre_encoded": pre}
    for key, label, plan in phases:
        print(f"phase {label}, {s.args.secs:.0f} s ...")
        bg = s.background(plan) if plan is not None else None
        result = measure_input(devs, s.args.secs)
        if bg is not None:
            results[f"{key}_stream"] = report_stats(key, bg.finish())
            s.pad.send_normal()
        results[key] = result
        for node, numbers in result.items():
            print(f"  {key} [{node}]: {numbers}")
        wait(3.0)
    for dev in devs.values():
        dev.close()
    save_result(s.out, "jitter", results)


# ---------------------------------------------------------------- commands: stop
def _edge_questions(label: str) -> dict[str, str]:
    """Ask about the start and the end of a tone."""
    return {"label": label, "start": ask("start of the tone: n(o pop) / p(op or click)", "np"),
            "end": ask("end of the tone: n(o pop) / p(op or click) / c(ut short, the last part is missing)", "npc")}


def _burst(s: Session, label: str) -> str:
    """Play a 0.4 s tone after silence and ask how it started."""
    s.play(Plan(secs=0.4), prepare=False)
    return ask(f"{label}: the 0.4 s tone was i(mmediate) / d(elayed or cut at the start) / n(ot heard)", "idn")


def _edges(s: Session) -> list[dict[str, str]]:
    """Pop at the start / end: abrupt end, an Opus silence tail, and the sender's normal 0x31 right after the last frame."""
    rows = []
    for name, note, plan, normal_after in (
        ("abrupt", "3 s tone, then no more reports and no 0x31", Plan(secs=3.0), False),
        ("silence-tail", "3 s tone, then 200 ms of Opus silence frames, then nothing", Plan(secs=3.0, tail_s=0.2), False),
        ("normal-after", "3 s tone, then the sender's normal 0x31 report at once (rumble back)", Plan(secs=3.0), True),
    ):
        print(f"\n== {name}: {note}")
        s.play(plan)
        if normal_after:
            s.pad.send_normal()
        rows.append({"variant": name, **_edge_questions(name)})
        s.pad.send_normal()
        wait(3.0)
    return rows


def _alive(s: Session) -> dict[str, Any]:
    """How long the audio path stays up after a stream, and whether re-sending the setup fixes a dead one."""
    gaps = [float(x) for x in s.args.gaps.split(",")]
    result: dict[str, Any] = {"gaps": [], "after_setup_resend": None}
    for gap in gaps:
        print(f"\n== tone 1 s, then nothing for {gap:g} s")
        s.play(Plan(secs=1.0))
        wait(gap)
        answer = _burst(s, f"after {gap:g} s of nothing")
        result["gaps"].append({"gap_s": gap, "answer": answer})
        if not answer.startswith("i"):
            s.prepare()
            result["after_setup_resend"] = {"gap_s": gap, "answer": _burst(s, "after re-sending the speaker setup")}
            break
    return result


def cmd_stop(s: Session) -> None:
    """Pop at the start / end, whether a silence tail is needed, and how long the audio path stays alive."""
    save_result(s.out, "stop", {"edges": _edges(s), "alive": _alive(s)})


# ---------------------------------------------------------------- commands: edge
# (name, what it does, plan, a 0x31 sent right after the last report)
EDGES: list[tuple[str, str, Plan, str]] = [
    ("abrupt", "3 s tone, nothing after (the control: it popped at the end in round 1)", Plan(secs=3.0), ""),
    ("fade200", "a 200 ms fade out before the last frame", Plan(secs=3.0, fade_s=0.2), ""),
    ("tail1s", "1 s of Opus silence frames after the tone", Plan(secs=3.0, tail_s=1.0), ""),
    ("tail3s", "3 s of Opus silence frames after the tone", Plan(secs=3.0, tail_s=3.0), ""),
    ("vol0", "tone + 100 ms silence, then a 0x31 with the speaker volume 0", Plan(secs=3.0, tail_s=0.1), "vol0"),
    ("audio-off", "tone + 100 ms silence, then a 0x31 with audio_control 0", Plan(secs=3.0, tail_s=0.1), "audio-off"),
]
POSTS: dict[str, dict[str, Any]] = {"vol0": {"flag0": 0x20, "extra": ((SPK_VOL_AT, 0),)},
                                    "audio-off": {"flag0": 0x80, "extra": ((AUDIO_CTL_AT, 0x00),)}}


def cmd_edge(s: Session) -> None:
    """What removes the pop at the end of a stream. Every variant uses the `notes` setup so volume and audio control are restored."""
    notes = BY_SETUP["notes"]
    rows = []
    for name, note, plan, post in EDGES:
        print(f"\n== {name}: {note}")
        s.play(plan, setup=notes)
        if post:
            s.pad.send31(f"post-{post}", **POSTS[post])
        rows.append({"variant": name, **_edge_questions(name)})
        s.pad.send_setup(notes, s.args.volume)
        s.pad.send_normal()
        wait(3.0)
    save_result(s.out, "edge", rows)


# ---------------------------------------------------------------- main
def parse_args(argv: list[str]) -> argparse.Namespace:
    """Command line."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--hidraw", help="use this /dev/hidrawN instead of looking for the pad")
    common.add_argument("--mac", help="pick the pad with this address when several are connected")
    common.add_argument("--setup", choices=sorted(BY_SETUP), help="0x31 setup (default: the best from `layout`, else notes)")
    common.add_argument("--layout", choices=sorted(BY_LAYOUT), help="0x36 layout (default: the best from `layout`, else hyp)")
    common.add_argument("--size", type=int, help=f"Opus bytes per 10 ms frame (default: the best from `layout`, else {DEFAULT_SIZE})")
    common.add_argument("--volume", type=int, default=100, help="speaker volume byte of the setup report (default 100)")
    common.add_argument("--period-ms", type=float, default=REPORT_PERIOD * 1000, help="report period in ms (default 10.667)")
    common.add_argument("--out", default="speaker_probe_results.json", help="results file (merged on every run)")
    common.add_argument("--len36", type=int, default=0, help="report 0x36 length including the id (default: from the HID descriptor)")
    common.add_argument("--force", action="store_true", help="run although the sender is running")
    common.add_argument("--dry-run", action="store_true", help="no pad: write to /dev/null, answer questions automatically")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    bench = sub.add_parser("bench", parents=[common], help="Opus encoder CPU (no pad needed)")
    bench.add_argument("--paced", action="store_true", help="also measure in the real rhythm, one frame per 10.667 ms")
    bench.add_argument("--secs", type=float, default=8.0, help="length of each paced run")
    sub.add_parser("info", parents=[common], help="pad, hidraw, report sizes, libopus")
    tone = sub.add_parser("tone", parents=[common], help="stream a speaker tone")
    tone.add_argument("hz", nargs="?", type=float, default=SPK_HZ)
    tone.add_argument("seconds", nargs="?", type=float, default=5.0)
    tone.add_argument("--hapt", action="store_true", help="add an 80 Hz haptics tone")
    tone.add_argument("--codec", choices=["hybrid", "celt", "silk", "lowdelay"], default="hybrid",
                      help="Opus mode (default hybrid = what the sender uses now; celt = the old default, it smears a tone)")
    sub.add_parser("layout", parents=[common], help="setups, layouts, Opus sizes")
    sub.add_parser("timing", parents=[common], help="10.667 vs 10.0 ms pacing, underruns")
    sub.add_parser("clock", parents=[common], help="report period sweep, dropped frames (440 Hz)")
    sub.add_parser("combo", parents=[common], help="haptics + speaker in one report")
    modes = sub.add_parser("modes", parents=[common], help="0x31 reports during the speaker stream")
    modes.add_argument("--only", help="comma separated step names (results go to 'modes_repeat')")
    modes.add_argument("--repeat", type=int, default=1, help="run each step this many times (results go to 'modes_repeat')")
    jitter = sub.add_parser("jitter", parents=[common], help="input jitter with / without the stream")
    jitter.add_argument("--secs", type=float, default=60.0)
    jitter.add_argument("--combo", action="store_true", help="add a phase with speaker + haptics")
    jitter.add_argument("--live-encode", action="store_true", help="encode every frame live (as in round 1), not a pre-encoded loop")
    stop = sub.add_parser("stop", parents=[common], help="pop at start / end, audio path timeout")
    stop.add_argument("--gaps", default="1,3,10,30", help="silence gaps to try, seconds")
    sub.add_parser("edge", parents=[common], help="what removes the pop at the end of a stream")
    gap = sub.add_parser("gap", parents=[common], help="a tone / speech in 6 variants to RECORD and measure (94 Hz buzz)")
    gap.add_argument("--hz", type=float, default=1000.0, help="tone frequency (default 1000: where the pad's speaker is loudest)")
    gap.add_argument("--secs", type=float, default=6.0, help="length of each variant")
    gap.add_argument("--wav", help="play this 48 kHz WAV (speech) instead of the tone")
    gap.add_argument("--continuous", action="store_true", help="no questions: all variants one after another (3 s of silence between), "
                     "record ONE file; the order is the order of --only (or the list)")
    gap.add_argument("--only", help="comma separated variant names (today, nodrop, p10.0, p10.5, p10.4, p10.3, lowdelay, silk10, hybrid10, "
                     "opus20-alt, var2.5, var5); they run in the list's order, not the order given")
    return parser.parse_args(argv)


COMMANDS: dict[str, Callable[[Session], None]] = {"info": cmd_info, "tone": cmd_tone, "layout": cmd_layout, "timing": cmd_timing,
                                                  "clock": cmd_clock, "combo": cmd_combo, "modes": cmd_modes, "jitter": cmd_jitter,
                                                  "stop": cmd_stop, "edge": cmd_edge, "gap": cmd_gap}


def main(argv: list[str] | None = None) -> None:
    """Run one command."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "bench":
        cmd_bench(args)
        return
    if args.cmd != "info" and not args.dry_run and os.geteuid() != 0:
        print("note: not root - opening /dev/hidraw will probably fail (use doas)")
    with session(args) as s:
        COMMANDS[args.cmd](s)


if __name__ == "__main__":
    main()
