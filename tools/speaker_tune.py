#!/usr/bin/env python3
"""Auto-tuner for the pad's speaker (throwaway tool, same family as speaker_probe.py).

It closes the loop that was done by hand so far: play a tone on the pad with ``speaker_probe.py tone``, record the pad's speaker with
a microphone on THIS machine, count the dropouts in the recording and search for the settings that give the fewest.

    python3 speaker_tune.py tune              # stop dualsense-sender first; put the pad's speaker near this machine's microphone
    python3 speaker_tune.py tune --quick      # a shorter search
    python3 speaker_tune.py analyze rec.wav   # count the dropouts in a recording you made yourself
    python3 speaker_tune.py selftest          # checks the analysis on a synthetic recording

How a run is measured: the tone is a pure sine, so the recording is demodulated at the tone's frequency (quadrature, 2 ms blocks) to get
its envelope. A dropout is a stretch of at least 8 ms where the envelope is below 40 % of the median. The score is dropouts per second
of tone; ``lost`` is the share of the time spent in dropouts. Only the standard library is used, so it runs on the phone as well.

The search (each step keeps the best so far): the baseline twice (period 10.667 ms, 80 B, hybrid Opus), a coarse sweep of the report
period, a fine sweep around the best period, a sweep of the Opus frame size, and a final comparison of the winner against the baseline.
A result is recommended only when it beats the baseline by a clear margin; otherwise the tuner says that nothing helps.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REC_RATE = 16000                    # Hz; the tone is below 1.3 kHz, so 16 kHz is plenty
BLOCK_MS = 2.0                      # envelope resolution
DIP_FRACTION = 0.4                  # a block below this share of the median envelope is "silent"
DIP_MIN_MS = 8.0                    # a dropout lasts at least this long
TRIM_S = 0.5                        # ignored at both ends of the tone (fade in / out, the start of the stream)
MIN_LEVEL = 40.0                    # median envelope (int16 units) below which nothing was heard
MIN_BLOCKS_HEARD = 0.5              # the tone must be present in at least this share of the active region
BASE_PERIOD, BASE_SIZE, BASE_CODEC = 10.667, 80, "hybrid"
BITS = 16                           # bits per sample of the recordings
MIN_ENV_BLOCKS = 100                # fewer envelope blocks than this (0.2 s) is not a recording
NEEDED_GAIN = 0.25                  # the winner must have at least this much fewer dropouts than the baseline
SETTLE_S = 2.0                      # pause between two runs
TONE_FIND_LO, TONE_FIND_HI = 600, 1300   # Hz; where the played tone can appear (1000 Hz in -> 937.5 Hz out)
RESULTS_FILE = "speaker_tune_results.json"


# ---------------------------------------------------------------- reading a recording
def read_wav(path: str | Path) -> tuple[int, array.array[int]]:
    """Read a 16-bit WAV, tolerating the broken length fields of a recorder that was killed; stereo is reduced to the first channel.

    Args:
        path: The file.

    Returns:
        (sample rate, mono samples).

    Raises:
        ValueError: Not a 16-bit PCM WAV with a data chunk.
    """
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a WAV file")
    pos, rate, channels, bits = 12, 0, 1, BITS
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if cid == b"fmt ":
            channels = int.from_bytes(data[body + 2:body + 4], "little")
            rate = int.from_bytes(data[body + 4:body + 8], "little")
            bits = int.from_bytes(data[body + 14:body + 16], "little")
        elif cid == b"data":
            if bits != BITS:
                raise ValueError(f"{path}: {bits}-bit samples ({BITS} expected)")
            end = len(data) if size in (0, 0xFFFFFFFF) or body + size > len(data) else body + size
            raw = data[body:end]
            samples = array.array("h")
            samples.frombytes(raw[:len(raw) // 2 * 2])
            if sys.byteorder == "big":
                samples.byteswap()
            return rate, (samples[::channels] if channels > 1 else samples)
        pos = body + size + (size & 1)
    raise ValueError(f"{path}: no data chunk")


# ---------------------------------------------------------------- analysis
def goertzel(samples: array.array[int], rate: int, freq: float) -> float:
    """Power of ``samples`` at ``freq`` (Goertzel's algorithm)."""
    coeff = 2.0 * math.cos(2.0 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for v in samples:
        s1, s2 = v + coeff * s1 - s2, s1
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def find_tone(samples: array.array[int], rate: int) -> float:
    """Frequency (Hz) of the loudest steady tone between TONE_FIND_LO and TONE_FIND_HI, looked for in the loudest second."""
    win, hop = rate, rate // 4
    best_at, best_energy = 0, -1.0
    for at in range(0, max(1, len(samples) - win + 1), hop):
        chunk = samples[at:at + win:8]
        energy = float(sum(v * v for v in chunk))
        if energy > best_energy:
            best_at, best_energy = at, energy
    seg = samples[best_at:best_at + win]
    coarse = max(range(TONE_FIND_LO, TONE_FIND_HI + 1, 10), key=lambda f: goertzel(seg, rate, f))
    return float(max(range(coarse - 10, coarse + 11), key=lambda f: goertzel(seg, rate, f)))


def envelope(samples: array.array[int], rate: int, freq: float) -> list[float]:
    """Amplitude of the tone at ``freq`` in blocks of BLOCK_MS (quadrature demodulation, int16 units)."""
    n = max(4, round(rate * BLOCK_MS / 1000))
    w = 2.0 * math.pi * freq / rate
    cos, sin, hyp = math.cos, math.sin, math.hypot
    out: list[float] = []
    for start in range(0, len(samples) - n + 1, n):
        i = q = 0.0
        for k in range(n):
            v = samples[start + k]
            ph = w * (start + k)
            i += v * cos(ph)
            q += v * sin(ph)
        out.append(2.0 * hyp(i, q) / n)
    return out


@dataclass
class Score:
    """What one recording says about the speaker."""

    ok: bool
    note: str = ""
    freq: float = 0.0
    seconds: float = 0.0
    dips: int = 0
    dips_per_s: float = 0.0
    lost: float = 0.0           # share of the time spent in dropouts, 0..1
    level: float = 0.0          # median envelope


def score_envelope(env: list[float]) -> Score:
    """Count the dropouts in the steady part of an envelope."""
    if len(env) < MIN_ENV_BLOCKS:
        return Score(False, "recording too short")
    ordered = sorted(env)
    top = ordered[int(len(ordered) * 0.95)]
    if top < MIN_LEVEL:
        return Score(False, "no tone heard (microphone too far or too quiet)")
    heard = [i for i, v in enumerate(env) if v > 0.2 * top]
    trim = round(TRIM_S * 1000 / BLOCK_MS)
    a, b = heard[0] + trim, heard[-1] + 1 - trim
    if b - a < 1000 / BLOCK_MS * 2:
        return Score(False, "the tone is shorter than 2 s after trimming")
    seg = env[a:b]
    med = sorted(seg)[len(seg) // 2]
    if med < MIN_LEVEL:
        return Score(False, "no tone heard (microphone too far or too quiet)")
    low = [v < DIP_FRACTION * med for v in seg]
    if sum(low) > (1 - MIN_BLOCKS_HEARD) * len(seg):
        return Score(False, "the tone is absent for more than half of the time")
    need = math.ceil(DIP_MIN_MS / BLOCK_MS)
    dips = run = 0
    for flag in [*low, False]:
        if flag:
            run += 1
        else:
            dips += run >= need
            run = 0
    seconds = len(seg) * BLOCK_MS / 1000
    return Score(True, "", 0.0, seconds, dips, dips / seconds, sum(low) / len(seg), med)


def analyze(samples: array.array[int], rate: int) -> Score:
    """Find the tone in a recording and count its dropouts."""
    freq = find_tone(samples, rate)
    result = score_envelope(envelope(samples, rate, freq))
    result.freq = freq
    return result


# ---------------------------------------------------------------- recording and playing
def recorder_command(path: Path) -> tuple[list[str], str]:
    """The command that records the default microphone to ``path`` until it is interrupted, and the tool's name."""
    rate = str(REC_RATE)
    options: list[tuple[str, list[str]]] = [
        ("pw-record", ["pw-record", "--rate", rate, "--channels", "1", "--format", "s16", str(path)]),
        ("parecord", ["parecord", f"--rate={rate}", "--channels=1", "--format=s16le", "--file-format=wav", str(path)]),
        ("arecord", ["arecord", "-q", "-t", "wav", "-f", "S16_LE", "-r", rate, "-c", "1", str(path)]),
        ("ffmpeg", ["ffmpeg", "-loglevel", "error", "-y", "-f", "pulse", "-i", "default", "-ac", "1", "-ar", rate, str(path)]),
    ]
    for name, cmd in options:
        if shutil.which(name):
            return cmd, name
    raise SystemExit("no recorder found (install pipewire-utils, pulseaudio-utils, alsa-utils or ffmpeg), "
                     "or record by hand and use `analyze`")


@dataclass(frozen=True)
class Config:
    """One setting of the speaker path under test."""

    period: float = BASE_PERIOD
    size: int = BASE_SIZE
    codec: str = BASE_CODEC

    def label(self) -> str:
        """Short text for the tables."""
        return f"period {self.period:.3f} ms, {self.size} B, {self.codec}"


class Bench:
    """Plays a tone with the probe and records it; remembers every run."""

    def __init__(self, probe: Path, hz: float, secs: float, extra: list[str]) -> None:
        """Remember how to run the probe."""
        self.probe, self.hz, self.secs, self.extra = probe, hz, secs, extra
        self.runs: list[dict[str, Any]] = []
        self.work = Path(tempfile.mkdtemp(prefix="speaker_tune_"))

    def measure(self, cfg: Config) -> Score:
        """Run one configuration once: record while the probe plays, analyse the recording."""
        wav = self.work / f"run{len(self.runs):03d}.wav"
        cmd, _ = recorder_command(wav)
        rec = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        play = [sys.executable, str(self.probe), "tone", str(self.hz), str(self.secs), "--period-ms", f"{cfg.period:.4f}",
                "--size", str(cfg.size), "--codec", cfg.codec, *self.extra]
        try:
            done = subprocess.run(play, capture_output=True, text=True, timeout=self.secs + 60, check=False)
        finally:
            time.sleep(1.0)
            rec.send_signal(signal.SIGINT)
            try:
                rec.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rec.kill()
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
            raise SystemExit("the probe failed (is dualsense-sender stopped and the pad connected?):\n  " + "\n  ".join(tail))
        rate, samples = read_wav(wav)
        score = analyze(samples, rate)
        self.runs.append({"config": asdict(cfg), "score": asdict(score), "wav": str(wav)})
        time.sleep(SETTLE_S)
        return score


# ---------------------------------------------------------------- the search
class Search:
    """Coordinate search over period and size; every measurement is kept and averaged per configuration."""

    def __init__(self, bench: Bench) -> None:
        """Start with nothing measured."""
        self.bench = bench
        self.seen: dict[Config, list[float]] = {}

    def mean(self, cfg: Config) -> float:
        """Average dropouts per second of a configuration measured so far."""
        v = self.seen[cfg]
        return sum(v) / len(v)

    def run(self, cfg: Config, why: str) -> float:
        """Measure ``cfg`` once more (a failed recording is retried once) and print the line."""
        for attempt in (1, 2):
            score = self.bench.measure(cfg)
            if score.ok:
                break
            print(f"   ! {score.note}" + (" - trying again" if attempt == 1 else ""))
        else:
            raise SystemExit(f"cannot measure: {score.note}")
        self.seen.setdefault(cfg, []).append(score.dips_per_s)
        print(f"  {why:<9} {cfg.label():<44} {score.dips_per_s:5.2f} dropouts/s  "
              f"(lost {score.lost * 100:4.1f} %, tone {score.freq:.0f} Hz)")
        return score.dips_per_s

    def best(self) -> Config:
        """The configuration with the fewest dropouts so far (ties: the first measured)."""
        return min(self.seen, key=self.mean)

    def sweep(self, configs: list[Config], why: str) -> None:
        """Measure every configuration that has not been measured yet."""
        for cfg in configs:
            if cfg not in self.seen:
                self.run(cfg, why)

    def tune(self, lo: float, hi: float, step: float, sizes: list[int]) -> dict[str, Any]:
        """The whole search; returns the summary that goes into the results file."""
        base = Config()
        print("1/5 baseline (twice, the spread tells how much to trust a difference)")
        self.run(base, "baseline")
        self.run(base, "baseline")
        print("2/5 coarse sweep of the report period")
        n = round((hi - lo) / step)
        self.sweep([Config(round(lo + i * step, 4)) for i in range(n + 1)], "period")
        print("3/5 fine sweep around the best period")
        centre = self.best().period
        fine = [Config(round(centre + k * step / 4, 4)) for k in range(-4, 5)]
        self.sweep(fine, "fine")
        print("4/5 Opus frame size at the best period")
        centre = self.best().period
        self.sweep([Config(centre, s) for s in sizes], "size")
        winner = self.best()
        print("5/5 the winner against the baseline, once more each")
        if winner != base:
            self.run(winner, "winner")
            self.run(base, "baseline")
            self.run(winner, "winner")
        w, b = self.mean(winner), self.mean(base)
        gain = 1 - w / b if b > 0 else 0.0
        good = winner != base and gain >= NEEDED_GAIN
        return {"baseline": asdict(base), "baseline_dips_per_s": b, "winner": asdict(winner), "winner_dips_per_s": w, "gain": gain,
                "recommend": good, "table": {c.label(): self.mean(c) for c in sorted(self.seen, key=lambda c: (c.period, c.size))}}


def cmd_tune(args: argparse.Namespace) -> None:
    """Run the search and print what to do with the result."""
    probe = Path(args.probe)
    if not probe.exists():
        raise SystemExit(f"{probe} not found: fetch it with "
                         "`git show origin/feat/speaker-gap-probe:tools/speaker_probe.py > /tmp/speaker_probe.py`")
    recorder_command(Path("x.wav"))
    bench = Bench(probe, args.hz, args.secs, args.probe_args)
    lo, hi, step = (10.62, 10.78, 0.04) if args.quick else (args.lo, args.hi, args.step)
    sizes = [80, 120] if args.quick else [60, 80, 100, 120, 160]
    t0 = time.monotonic()
    summary = Search(bench).tune(lo, hi, step, sizes)
    summary["minutes"] = round((time.monotonic() - t0) / 60, 1)
    out = Path(args.out)
    out.write_text(json.dumps({"summary": summary, "runs": bench.runs}, indent=1))
    print(f"\nbaseline  {summary['baseline_dips_per_s']:.2f} dropouts/s")
    print(f"winner    {summary['winner_dips_per_s']:.2f} dropouts/s  ({Config(**summary['winner']).label()})")
    if summary["recommend"]:
        print(f"RESULT: {summary['gain'] * 100:.0f} % fewer dropouts. Send {out}; the winning settings become the sender's defaults.")
    else:
        print(f"RESULT: no setting is clearly better than the default (needed {NEEDED_GAIN * 100:.0f} % fewer). Send {out} anyway.")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Analyse one or more recordings made by hand."""
    for path in args.files:
        rate, samples = read_wav(path)
        s = analyze(samples, rate)
        if s.ok:
            print(f"{path}: tone {s.freq:.0f} Hz, {s.seconds:.1f} s, {s.dips} dropouts = {s.dips_per_s:.2f}/s, lost {s.lost * 100:.1f} %")
        else:
            print(f"{path}: {s.note}")


# ---------------------------------------------------------------- self test
def synth(secs: float, freq: float, gaps: list[tuple[float, float]], rate: int = REC_RATE) -> array.array[int]:
    """A sine with silent gaps (start s, length s), for the self test."""
    out = array.array("h")
    for n in range(round(secs * rate)):
        t = n / rate
        quiet = any(a <= t < a + length for a, length in gaps)
        out.append(0 if quiet else round(8000 * math.sin(2 * math.pi * freq * t)))
    return out


def cmd_selftest(_args: argparse.Namespace) -> None:
    """Check the analysis on synthetic recordings: no gaps, then 12 gaps of 32 ms and 6 of 10 ms."""
    clean = analyze(synth(8, 937.5, []), REC_RATE)
    assert clean.ok and clean.dips == 0 and abs(clean.freq - 937) <= 1, clean
    gaps = [(1.0 + 0.5 * i, 0.032) for i in range(12)] + [(1.25 + 0.9 * i, 0.014) for i in range(6)]
    rec = analyze(synth(8, 937.5, gaps), REC_RATE)
    assert rec.ok and rec.dips == len(gaps), rec
    short = analyze(synth(8, 937.5, [(2.0, 0.004), (3.0, 0.005)]), REC_RATE)
    assert short.ok and short.dips == 0, short
    silent = analyze(array.array("h", [0] * (REC_RATE * 6)), REC_RATE)
    assert not silent.ok, silent
    print(f"selftest OK: clean {clean.dips} dropouts, {len(gaps)} gaps found {rec.dips}, 4-5 ms gaps ignored, silence rejected")


def main() -> None:
    """Parse the command line."""
    here = Path(__file__).resolve().parent
    default_probe = here / "speaker_probe.py"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tune", help="search for the settings with the fewest dropouts")
    t.add_argument("--probe", default=str(default_probe if default_probe.exists() else "/tmp/speaker_probe.py"), help="speaker_probe.py")
    t.add_argument("--hz", type=float, default=1000.0, help="tone played (the pad outputs it 6.25 %% lower)")
    t.add_argument("--secs", type=float, default=10.0, help="length of the tone of one run")
    t.add_argument("--lo", type=float, default=10.60, help="coarse sweep: lowest report period [ms]")
    t.add_argument("--hi", type=float, default=10.84, help="coarse sweep: highest report period [ms]")
    t.add_argument("--step", type=float, default=0.02, help="coarse sweep: step [ms]")
    t.add_argument("--quick", action="store_true", help="a short search (about 8 runs)")
    t.add_argument("--out", default=RESULTS_FILE)
    t.add_argument("probe_args", nargs="*", help="extra arguments for the probe, after --")
    t.set_defaults(fn=cmd_tune)
    a = sub.add_parser("analyze", help="count the dropouts in recordings (16-bit WAV)")
    a.add_argument("files", nargs="+")
    a.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("selftest", help="check the analysis on a synthetic recording")
    s.set_defaults(fn=cmd_selftest)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
