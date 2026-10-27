#!/usr/bin/env python3
"""Throwaway probe: HD haptics over Bluetooth on a real DualSense (HID output report 0x32), run on the sender (phone / Pi).

It imports nothing from the repo, so it survives refactors. Needs only the stdlib (+ python-evdev for the pad lookup and
the `jitter` command) and root for /dev/hidraw*:

    doas systemctl stop dualsense-sender            # the sender must not write to the pad meanwhile
    doas python3 haptics_probe.py info              # pad, hidraw, output report sizes from the HID descriptor
    doas python3 haptics_probe.py tone 80 5         # 80 Hz for 5 s, prints the exact bytes of the first reports
    doas python3 haptics_probe.py wav clip.wav      # any 8/16-bit WAV, resampled to 3 kHz
    doas python3 haptics_probe.py layout            # report variants, you answer "did you feel it"
    doas python3 haptics_probe.py modes             # does a 0x31 report (lightbar / triggers / motors) cut the haptics?
    doas python3 haptics_probe.py jitter            # input interval jitter with and without the stream
    doas python3 haptics_probe.py idle              # how long the pad keeps the audio path alive, which keepalive helps

Every command that measures something merges its result into haptics_probe_results.json (paste that file back).

Report 0x32 as used here (142 B, from community notes, UNVERIFIED - this script exists to check it):
    0     0x32
    1     sequence number in the high nibble
    2-4   0x91 0x07 0xFE         control sub-packet (pid 0x11 | "sized" bit, length 7, flags)
    5-9   queue depth: 16 in all five bytes (16,0,0,0,0 gave no vibration on a real pad)
    10    report counter
    11-12 0x92 0x40              haptics sub-packet (pid 0x12 | "sized" bit, length 64)
    13-76 32 stereo frames of 8-bit samples at 3 kHz (10.667 ms)
    -4    CRC32 little endian over 0xA2 + everything before it
"""
import argparse
import array
import contextlib
import dataclasses
import glob
import itertools
import json
import math
import os
import platform
import re
import select
import sys
import threading
import time
import wave
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path

RATE = 3000                              # haptics sample rate over Bluetooth, Hz
FRAMES = 32                              # stereo frames per 0x32 report
CHUNK = 2 * FRAMES                       # 64 bytes of samples
PERIOD = FRAMES / RATE                   # 10.667 ms of audio per report
R32_LEN = 142                            # report id + 141 data bytes (checked against the HID descriptor in `info`)
CRC_SEED = b"\xa2"
RESYNC_PERIODS = 3                       # a stream that falls this many periods behind restarts its clock
MAX_FAILS = 5                            # consecutive write errors before a stream gives up
TONE_HZ = 80.0
TONE_AMP = 0.8
TIME_SCALE = 1.0                         # observation waits are multiplied by this (dry-run shortens them)
AUTO = False                             # dry-run: answer every question with its first choice
BT_BUS = 0x0005
R32_ID = 0x32                            # output report id of the haptics report
WIDTH_16 = 2                             # bytes per sample of a 16-bit WAV
LONG_ITEM = 0xFE                         # HID descriptor: long item prefix
TAG_SIZE, TAG_ID, TAG_COUNT, TAG_OUTPUT = 7, 8, 9, 9    # HID descriptor: report size / id / count (global), Output (main)
GAP_WARN_MS = (20.0, 50.0)               # input intervals counted as "late"
OFF_TRIGGER = bytes([0x05]) + bytes(10)  # trigger effect "off"
_HID_DIR = re.compile(r"^([0-9A-Fa-f]{4}):[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")


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
               power: int | None = None, rgb: tuple[int, int, int] | None = None,
               trig: tuple[bytes, bytes] | None = None) -> bytes:
    """Build a Bluetooth output report 0x31 (78 B): the common part of the USB report 0x02 wrapped like the kernel does.

    Args:
        seq: Sequence number (low 4 bits are used).
        flag0: valid_flag0 (0x01 compat rumble, 0x02 haptics select, 0x04 / 0x08 right / left trigger).
        flag1: valid_flag1 (0x04 = lightbar colour from this report).
        flag2: valid_flag2 (0x04 = vibration v2).
        motors: (right weak, left strong) motor, 0..255.
        power: The motor power level byte (common part, offset 36), or None to leave it 0.
        rgb: Lightbar colour (needs flag1 0x04).
        trig: (L2 effect, R2 effect), 11 bytes each (needs flag0 0x0C).

    Returns:
        The report bytes.
    """
    common = bytearray(47)
    common[0], common[1], common[38] = flag0, flag1, flag2
    common[2], common[3] = motors
    if power is not None:
        common[36] = power
    if trig is not None:
        common[21:32], common[10:21] = trig
    if rgb is not None:
        common[44:47] = bytes(rgb)
    report = bytearray([0x31, (seq & 0x0F) << 4, 0x10]) + common + bytes(24) + bytes(4)
    return _crc_tail(report)


def setup_report(seq: int) -> bytes:
    """The report sent once before streaming: motor power reduction 0, haptics filter bits, valid_flag0 left at 0."""
    return build_0x31(seq, flag1=0x02 | 0x20 | 0x40, power=0)


def normal_report(seq: int) -> bytes:
    """What the sender sends today (flag0 = haptics select + compat rumble, v2 flag), motors and triggers off."""
    return build_0x31(seq, flag0=0x03 | 0x0C, flag2=0x04, trig=(OFF_TRIGGER, OFF_TRIGGER))


@dataclasses.dataclass(frozen=True)
class Variant:
    """One way of laying out a 0x32 report; everything the references disagree on is a field here."""

    name: str
    note: str
    signed: bool = True                  # samples are signed 8-bit (False: unsigned, silence = 0x80)
    seq: str = "shared"                  # "shared": counter shared with 0x31 reports, "zero": nibble always 0
    depth: bytes = bytes([16] * 5)       # confirmed on a real pad: 16,0,0,0,0 is silent, five times 16 works
    sized: bool = True                   # sub-packet header has the "sized" bit (0x91 / 0x92 instead of 0x11 / 0x12)
    lr: str = "both"                     # "both" | "left" | "right": which channel carries the signal
    setup: bool = True                   # send the 0x31 setup report first


VARIANTS = [
    Variant("baseline", "signed samples, shared sequence, queue depth 16 x5 (the layout that worked on the real pad)"),
    Variant("unsigned", "unsigned 8-bit samples (silence = 0x80)", signed=False),
    Variant("seq-zero", "sequence nibble of 0x32 always 0", seq="zero"),
    Variant("depth-16-0", "queue depth 16,0,0,0,0 (silent on the real pad, kept as a control)", depth=bytes([16, 0, 0, 0, 0])),
    Variant("no-sized-bit", "sub-packet headers 0x11 / 0x12 without the sized bit", sized=False),
    Variant("left-only", "signal on the first sample of each frame only", lr="left"),
    Variant("right-only", "signal on the second sample of each frame only", lr="right"),
    Variant("no-setup", "baseline without the 0x31 setup report (only meaningful if an earlier variant worked)", setup=False),
]
BY_NAME = {v.name: v for v in VARIANTS}


def build_0x32(v: Variant, seq: int, counter: int, chunk: bytes, length: int = R32_LEN) -> bytes:
    """Build a Bluetooth haptics report 0x32 from 64 bytes of 8-bit stereo samples, laid out as `v` says."""
    report = bytearray(length)
    sized = 0x80 if v.sized else 0
    report[0] = 0x32
    report[1] = ((seq if v.seq == "shared" else 0) & 0x0F) << 4
    report[2:5] = bytes([0x11 | sized, 0x07, 0xFE])
    report[5:10] = v.depth
    report[10] = counter & 0xFF
    report[11:13] = bytes([0x12 | sized, CHUNK])
    report[13:13 + CHUNK] = chunk
    return _crc_tail(report)


def _quantize(x: float, signed: bool) -> int:
    """Float -1..1 -> one byte, clipped to +-127 (unsigned: 1..255 around 128)."""
    q = max(-127, min(127, round(x * 127)))
    return (q & 0xFF) if signed else q + 128


def encode(v: Variant, pairs: list[tuple[float, float]]) -> bytes:
    """Interleave 32 (left, right) float frames into the 64-byte sample block of a 0x32 report."""
    out = bytearray(CHUNK)
    for i, (left, right) in enumerate(pairs):
        if v.lr == "left":
            right = 0.0
        elif v.lr == "right":
            left = 0.0
        out[2 * i], out[2 * i + 1] = _quantize(left, v.signed), _quantize(right, v.signed)
    return bytes(out)


# ---------------------------------------------------------------- sample sources (32 stereo frames per item)
Frames = Iterator[list[tuple[float, float]]]


def tone_source(hz: float, secs: float | None, amp: float = TONE_AMP) -> Frames:
    """A phase-continuous sine with a 30 ms fade in / out; ``secs=None`` = endless."""
    total = None if secs is None else max(1, round(secs * RATE / FRAMES)) * FRAMES
    ramp, n = 0.03 * RATE, 0
    while total is None or n < total:
        pairs = []
        for _ in range(FRAMES):
            gain = min(1.0, n / ramp)
            if total is not None:
                gain = max(0.0, min(gain, (total - n) / ramp))
            sample = amp * gain * math.sin(2 * math.pi * hz * n / RATE)
            pairs.append((sample, sample))
            n += 1
        yield pairs


def silence_source(secs: float | None) -> Frames:
    """Digital silence for ``secs`` (None = endless)."""
    count = None if secs is None else max(1, round(secs / PERIOD))
    for _ in itertools.count() if count is None else range(count):
        yield [(0.0, 0.0)] * FRAMES


def resample(x: list[float], rate: int) -> list[float]:
    """Box-filter resample to 3 kHz (good enough for a probe: a moving average, then picking the window centres)."""
    if rate == RATE:
        return x
    prefix = list(itertools.accumulate(x, initial=0.0))
    ratio = rate / RATE
    out = []
    for k in range(int(len(x) / ratio)):
        lo = int(k * ratio)
        hi = min(len(x), max(lo + 1, int((k + 1) * ratio)))
        out.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def wav_source(path: Path, gain: float) -> Frames:
    """Read an 8 / 16-bit PCM WAV, resample it to 3 kHz stereo and yield it in 32-frame blocks."""
    with wave.open(str(path), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 1:
        values = [(b - 128) / 128 for b in raw]
    elif width == WIDTH_16:
        pcm = array.array("h")
        pcm.frombytes(raw)
        if sys.byteorder == "big":
            pcm.byteswap()
        values = [s / 32768 for s in pcm]
    else:
        raise SystemExit("only 8 / 16-bit PCM WAV files are supported")
    left = resample(values[0::channels], rate)
    right = resample(values[1::channels], rate) if channels > 1 else left
    print(f"{path.name}: {rate} Hz, {channels} ch, {width * 8} bit -> {len(left) / RATE:.2f} s at {RATE} Hz")
    for i in range(0, len(left), FRAMES):
        block = [(left[j] * gain, right[j] * gain) for j in range(i, min(i + FRAMES, len(left)))]
        yield block + [(0.0, 0.0)] * (FRAMES - len(block))


# ---------------------------------------------------------------- pad access
class Pad:
    """The pad's hidraw: one fd, one sequence counter shared by every report (like Rumble in the sender)."""

    def __init__(self, path: str | None, len32: int) -> None:
        """Open ``path`` for writing (None = /dev/null, dry run)."""
        self.fd = os.open(path or os.devnull, os.O_WRONLY)
        self.seq, self.counter, self.len32 = 0, 0, len32
        self.lock = threading.Lock()
        self.first: dict[str, bytes] = {}      # the first report of each kind, for the log

    def _send(self, key: str, build: Callable[[int], bytes]) -> None:
        with self.lock:
            self.seq = (self.seq + 1) & 0x0F
            data = build(self.seq)
            self.first.setdefault(key, data)
            os.write(self.fd, data)

    def send31(self, key: str = "0x31", **kw: object) -> None:
        """Send a 0x31 report built with build_0x31(seq, **kw)."""
        self._send(key, lambda seq: build_0x31(seq, **kw))  # type: ignore[arg-type]

    def send_setup(self) -> None:
        """Send the setup report."""
        self._send("setup", setup_report)

    def send_normal(self) -> None:
        """Send the sender's current-style report with motors and triggers off."""
        self._send("normal", normal_report)

    def send32(self, v: Variant, chunk: bytes) -> None:
        """Send one haptics report."""
        def build(seq: int) -> bytes:
            self.counter += 1
            return build_0x32(v, seq, self.counter, chunk, self.len32)
        self._send("0x32", build)

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


def save_result(path: Path, key: str, value: object) -> None:
    """Merge ``{key: value}`` into the results JSON file."""
    data: dict[str, object] = {}
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text())
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
    errors: int = 0
    resyncs: int = 0
    started: float = 0.0
    ended: float = 0.0
    intervals_ms: list[float] = dataclasses.field(default_factory=list)
    write_ms: list[float] = dataclasses.field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """A JSON-friendly summary."""
        span = max(self.ended - self.started, 1e-9)
        return {"reports": self.reports, "errors": self.errors, "resyncs": self.resyncs, "reports_per_s": round(self.reports / span, 2),
                "interval_ms": describe(self.intervals_ms), "write_ms": describe(self.write_ms)}


def run_stream(pad: Pad, v: Variant, source: Frames, stop: threading.Event | None = None, pace: str = "abs") -> Stats:
    """Write 0x32 reports until the source ends or ``stop`` is set.

    ``pace="abs"``: absolute deadlines every 10.667 ms (no drift, resyncs after a stall). ``pace="sleep"``: a plain sleep
    after each write (drifts; the references say such a stream drops out about once a second - this lets you check).
    """
    st = Stats()
    st.started = deadline = time.monotonic()
    last, fails = 0.0, 0
    for pairs in source:
        if stop is not None and stop.is_set():
            break
        chunk = encode(v, pairs)
        t0 = time.monotonic()
        if last:
            st.intervals_ms.append((t0 - last) * 1000)
        last = t0
        try:
            pad.send32(v, chunk)
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
        if pace == "sleep":
            time.sleep(PERIOD)
            continue
        deadline += PERIOD
        lag = time.monotonic() - deadline
        if lag > RESYNC_PERIODS * PERIOD:
            deadline, st.resyncs = time.monotonic(), st.resyncs + 1
        elif lag < 0:
            time.sleep(-lag)
    st.ended = time.monotonic()
    return st


class Background:
    """A stream running in a thread, so the main thread can send 0x31 reports or read the input meanwhile."""

    def __init__(self, pad: Pad, v: Variant, source: Frames, pace: str) -> None:
        """Start streaming ``source`` right away."""
        self.stop = threading.Event()
        self.stats = Stats()
        self.thread = threading.Thread(target=self._run, args=(pad, v, source, pace), daemon=True)
        self.thread.start()

    def _run(self, pad: Pad, v: Variant, source: Frames, pace: str) -> None:
        self.stats = run_stream(pad, v, source, self.stop, pace)

    def finish(self) -> Stats:
        """Stop the stream and return what it did."""
        self.stop.set()
        self.thread.join()
        return self.stats


def prepare(pad: Pad, v: Variant) -> None:
    """Send the 0x31 setup report if the variant uses one."""
    if v.setup:
        pad.send_setup()
        time.sleep(0.05)


def report_stats(label: str, st: Stats) -> dict[str, object]:
    """Print and return a stream summary."""
    s = st.summary()
    print(f"  {label}: {s['reports']} reports, {s['reports_per_s']}/s (ideal {1 / PERIOD:.1f}), "
          f"errors {s['errors']}, resyncs {s['resyncs']}")
    print(f"    interval ms {s['interval_ms']}")
    print(f"    write ms    {s['write_ms']}")
    return s


# ---------------------------------------------------------------- session
@dataclasses.dataclass
class Session:
    """Everything a command needs: the pad, the opened hidraw and the options."""

    pad: Pad
    info: PadInfo
    variant: Variant
    args: argparse.Namespace
    sizes: dict[int, int]

    @property
    def out(self) -> Path:
        """The results file."""
        return Path(self.args.out)


@contextlib.contextmanager
def session(args: argparse.Namespace) -> Iterator[Session]:
    """Find the pad, refuse to run next to a live sender, open hidraw; restore the pad's normal state on exit."""
    global AUTO, TIME_SCALE
    if args.dry_run:
        AUTO, TIME_SCALE = True, 0.05
        info = PadInfo("(dry run)", "", None, True, None)
    else:
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
            raise SystemExit("need a Bluetooth pad with a hidraw node (report 0x32 does not exist on USB)")
    sizes = descriptor_sizes(info.hidraw) if info.hidraw else {}
    len32 = args.len32 or (sizes[R32_ID] + 1 if R32_ID in sizes else R32_LEN)
    pad = Pad(info.hidraw, len32)
    try:
        yield Session(pad, info, BY_NAME[args.variant], args, sizes)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        with contextlib.suppress(OSError):
            pad.send_normal()
        pad.close()


def hexdump(data: bytes) -> str:
    """Hex bytes, 32 per line."""
    return "\n".join("    " + data[i:i + 32].hex(" ") for i in range(0, len(data), 32))


def show_first(s: Session) -> None:
    """Print the exact first reports that went out."""
    for key in ("setup", "0x32"):
        if key in s.pad.first:
            print(f"  first {key} report ({len(s.pad.first[key])} B):\n{hexdump(s.pad.first[key])}")


# ---------------------------------------------------------------- commands
def cmd_info(s: Session) -> None:
    """Pad, hidraw, descriptor sizes."""
    meta = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "kernel": platform.release(), "python": platform.python_version(),
            "pad": dataclasses.asdict(s.info), "output_report_data_bytes": {hex(k): v for k, v in sorted(s.sizes.items())},
            "len32_used": s.pad.len32}
    print(json.dumps(meta, indent=2))
    if R32_ID in s.sizes and s.sizes[R32_ID] + 1 != R32_LEN:
        print(f"!! the descriptor says report 0x32 has {s.sizes[R32_ID]} data bytes, the notes say {R32_LEN - 1}: using {s.pad.len32} B")
    elif R32_ID not in s.sizes:
        print("!! report 0x32 is not in the HID descriptor (or sysfs is unreadable): the pad may ignore it")
    save_result(s.out, "meta", meta)


def cmd_tone(s: Session) -> None:
    """Stream a sine and print the bytes, pacing and write times."""
    a = s.args
    prepare(s.pad, s.variant)
    print(f"streaming {a.hz} Hz for {a.seconds} s, variant '{s.variant.name}', pace '{a.pace}' ...")
    st = run_stream(s.pad, s.variant, tone_source(a.hz, a.seconds), pace=a.pace)
    show_first(s)
    save_result(s.out, f"tone_{s.variant.name}_{a.pace}", report_stats("tone", st))
    print("  felt it? -> note it. Other layouts: `layout`.")


def cmd_wav(s: Session) -> None:
    """Stream a WAV file."""
    prepare(s.pad, s.variant)
    st = run_stream(s.pad, s.variant, wav_source(Path(s.args.file), s.args.gain), pace=s.args.pace)
    show_first(s)
    report_stats("wav", st)


def cmd_layout(s: Session) -> None:
    """Play every layout variant and ask what you felt."""
    only = set(s.args.only.split(",")) if s.args.only else None
    results = []
    for v in VARIANTS:
        if only and v.name not in only:
            continue
        print(f"\n== {v.name}: {v.note}")
        prepare(s.pad, v)
        st = run_stream(s.pad, v, tone_source(TONE_HZ, 2.0), pace=s.args.pace)
        if v.lr in ("left", "right"):
            answer = ask("which side vibrated? l(eft grip) / r(ight grip) / b(oth) / n(one)", "lrbn")
        else:
            answer = ask("did you feel it? y(es) / n(o) / w(eak)", "ynw")
        results.append({"variant": v.name, "answer": answer, "first_0x32": s.pad.first["0x32"].hex(), "reports": st.reports,
                        "errors": st.errors})
        s.pad.first.pop("0x32", None)
        s.pad.send_normal()
        wait(3.0)
    save_result(s.out, "layout", results)


# 0x31 reports tried while a stream is playing: (name, kwargs of build_0x31)
STEPS: list[tuple[str, dict[str, object]]] = [
    ("repo-flags", {"flag0": 0x03, "flag2": 0x04}),
    ("lightbar-flag0-zero", {"flag1": 0x04, "rgb": (255, 0, 0)}),
    ("trigger-only", {"flag0": 0x0C, "trig": (feedback_trigger(), feedback_trigger())}),
    ("trigger-with-repo-flags", {"flag0": 0x0F, "flag2": 0x04, "trig": (feedback_trigger(), feedback_trigger())}),
    ("motors-set", {"flag0": 0x03, "flag2": 0x04, "motors": (120, 120)}),
]


def _observe(s: Session, name: str, phase: str, repeat: bool, kw: dict[str, object]) -> dict[str, object]:
    """Send the step's report once (or every 0.4 s for ~3 s), then ask what happened to the haptics."""
    print(f"  {name}: sending {'every 0.4 s for 3 s' if repeat else 'once'} ...")
    for _ in range(8 if repeat else 1):
        s.pad.send31(f"{name}-{phase}", **kw)
        if repeat:
            time.sleep(0.4 * TIME_SCALE if AUTO else 0.4)
    wait(3.0)
    answer = ask("haptics tone now: k(ept) / c(ut) / w(eaker)", "kcw")
    row: dict[str, object] = {"step": name, "phase": phase, "answer": answer}
    if answer.startswith("c"):
        s.pad.send_setup()
        wait(1.5)
        row["back_after_setup"] = ask("after re-sending the setup report: is it back? y(es) / n(o)", "yn")
    return row


def cmd_modes(s: Session) -> None:
    """Does a 0x31 report during a stream cut the haptics? The answer decides the flags in P1."""
    results = []
    for name, kw in STEPS:
        print(f"\n== {name}")
        s.pad.send_normal()
        wait(1.0)
        prepare(s.pad, s.variant)
        bg = Background(s.pad, s.variant, tone_source(TONE_HZ, None), s.args.pace)
        wait(2.0)
        results.append(_observe(s, name, "once", False, kw))
        results.append(_observe(s, name, "repeat", True, kw))
        bg.finish()
    save_result(s.out, "modes", results)


def measure_input(dev: object, secs: float) -> dict[str, object]:
    """Read the pad's evdev node for ``secs`` and describe the intervals between SYN_REPORT events (kernel timestamps)."""
    from evdev import ecodes
    stamps: list[int] = []
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if not select.select([dev.fd], [], [], 0.2)[0]:  # type: ignore[attr-defined]
            continue
        stamps += [e.sec * 1_000_000_000 + e.usec * 1000 for e in dev.read()  # type: ignore[attr-defined]
                   if e.type == ecodes.EV_SYN and e.code == ecodes.SYN_REPORT]
    gaps = [(b - a) / 1e6 for a, b in itertools.pairwise(stamps)]
    return {"events": len(stamps), "rate_hz": round(len(stamps) / secs, 1), **describe(gaps),
            "gaps_over_20ms": sum(g > GAP_WARN_MS[0] for g in gaps), "gaps_over_50ms": sum(g > GAP_WARN_MS[1] for g in gaps)}


def cmd_jitter(s: Session) -> None:
    """Input interval jitter without and with a haptics stream; this number decides how aggressive P1 may be."""
    import evdev
    if s.info.event is None:
        raise SystemExit("jitter needs the pad's evdev node (not available with --hidraw / --dry-run)")
    dev = evdev.InputDevice(s.info.event)
    secs = s.args.secs
    print("leave the pad on the table and do not touch it.")
    results: dict[str, object] = {}
    print(f"phase A: {secs:.0f} s without a stream ...")
    results["no_stream"] = measure_input(dev, secs)
    print(f"  {results['no_stream']}")
    wait(3.0)
    prepare(s.pad, s.variant)
    print(f"phase B: {secs:.0f} s with the 0x32 stream ({s.args.pace} pacing) ...")
    bg = Background(s.pad, s.variant, tone_source(TONE_HZ, None), s.args.pace)
    results["with_stream"] = measure_input(dev, secs)
    results["stream"] = report_stats("stream", bg.finish())
    print(f"  {results['with_stream']}")
    dev.close()
    save_result(s.out, "jitter", results)


def _burst_answer(s: Session, label: str) -> str:
    """Play a 0.4 s tone after silence and ask how it started."""
    run_stream(s.pad, s.variant, tone_source(TONE_HZ, 0.4), pace=s.args.pace)
    return ask(f"{label}: the 0.4 s tone was i(mmediate) / d(elayed or cut at the start) / n(ot felt)", "idn")


def _keepalive(s: Session, rate: float, secs: float) -> None:
    """Send digital silence at ``rate`` reports per second for ``secs`` (full rate = a normal silent stream)."""
    if rate >= 1 / PERIOD:
        run_stream(s.pad, s.variant, silence_source(secs), pace=s.args.pace)
        return
    for _ in range(max(1, int(secs * rate))):
        s.pad.send32(s.variant, encode(s.variant, [(0.0, 0.0)] * FRAMES))
        time.sleep(1 / rate)


def cmd_idle(s: Session) -> None:
    """How long the pad keeps its audio path alive after the stream stops, and which silence keepalive keeps it instant."""
    gaps = [float(x) for x in s.args.gaps.split(",")]
    results: dict[str, object] = {"gaps": [], "keepalive": []}
    bad_gap = 0.0
    for gap in gaps:
        print(f"\n== stream 1 s, then nothing for {gap:g} s")
        prepare(s.pad, s.variant)
        run_stream(s.pad, s.variant, tone_source(TONE_HZ, 1.0), pace=s.args.pace)
        wait(gap)
        answer = _burst_answer(s, f"after {gap:g} s of nothing")
        results["gaps"].append({"gap_s": gap, "answer": answer})  # type: ignore[attr-defined]
        if not answer.startswith("i"):
            bad_gap = gap
            break
    if bad_gap:
        for rate in (1 / PERIOD, 20.0, 5.0, 1.0):
            print(f"\n== stream 1 s, then silence reports at {rate:.0f}/s for {bad_gap:g} s")
            prepare(s.pad, s.variant)
            run_stream(s.pad, s.variant, tone_source(TONE_HZ, 1.0), pace=s.args.pace)
            _keepalive(s, rate, bad_gap)
            answer = _burst_answer(s, f"keepalive {rate:.0f}/s")
            results["keepalive"].append({"gap_s": bad_gap, "rate_per_s": round(rate, 1), "answer": answer})  # type: ignore[attr-defined]
    else:
        print("\nno failing gap: the audio path stayed up for the longest gap, no keepalive needed (up to that gap).")
    save_result(s.out, "idle", results)


# ---------------------------------------------------------------- main
def parse_args(argv: list[str]) -> argparse.Namespace:
    """Command line."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--hidraw", help="use this /dev/hidrawN instead of looking for the pad")
    common.add_argument("--mac", help="pick the pad with this address when several are connected")
    common.add_argument("--variant", default="baseline", choices=sorted(BY_NAME), help="report layout (see `layout`)")
    common.add_argument("--pace", default="abs", choices=("abs", "sleep"), help="abs = absolute 10.667 ms deadlines, sleep = naive")
    common.add_argument("--out", default="haptics_probe_results.json", help="results file (merged on every run)")
    common.add_argument("--len32", type=int, default=0, help="report 0x32 length including the id (default: from the HID descriptor)")
    common.add_argument("--force", action="store_true", help="run although the sender is running")
    common.add_argument("--dry-run", action="store_true", help="no pad: write to /dev/null, answer questions automatically")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", parents=[common], help="pad, hidraw, report sizes")
    tone = sub.add_parser("tone", parents=[common], help="stream a sine")
    tone.add_argument("hz", nargs="?", type=float, default=TONE_HZ)
    tone.add_argument("seconds", nargs="?", type=float, default=5.0)
    wav = sub.add_parser("wav", parents=[common], help="stream a WAV file")
    wav.add_argument("file")
    wav.add_argument("--gain", type=float, default=1.0)
    layout = sub.add_parser("layout", parents=[common], help="report layout variants")
    layout.add_argument("--only", help="comma separated variant names")
    sub.add_parser("modes", parents=[common], help="0x31 reports during a stream")
    jitter = sub.add_parser("jitter", parents=[common], help="input jitter with / without the stream")
    jitter.add_argument("--secs", type=float, default=60.0)
    idle = sub.add_parser("idle", parents=[common], help="audio path timeout and keepalive")
    idle.add_argument("--gaps", default="1,3,10,30", help="silence gaps to try, seconds")
    return parser.parse_args(argv)


COMMANDS: dict[str, Callable[[Session], None]] = {"info": cmd_info, "tone": cmd_tone, "wav": cmd_wav, "layout": cmd_layout,
                                                  "modes": cmd_modes, "jitter": cmd_jitter, "idle": cmd_idle}


def main(argv: list[str] | None = None) -> None:
    """Run one command."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd != "info" and not args.dry_run and os.geteuid() != 0:
        print("note: not root - opening /dev/hidraw will probably fail (use doas)")
    with session(args) as s:
        COMMANDS[args.cmd](s)


if __name__ == "__main__":
    main()
