"""Receiver: measure stick drift at rest and propose a deadzone (the Test view in the window).

Flow (DriftRun, stepped by the window clock): the sender gets CMD_RAW_STICKS (sticks without its own deadzone, otherwise
deviations of up to ~2 % would be clipped to the centre), after SETTLE_S every state sample from rx_net is collected
(S.probe) for MEASURE_S, then the sender's deadzone comes back. Result per stick: the largest deflection at rest r (0..1)
and a proposal dz = r * MARGIN rounded up to 0.01, at least MIN_DZ. A deflection above MOVE_R means somebody moved
the stick - the measurement has to be repeated.
"""
import math
from dataclasses import dataclass

import rx_state as S
from rx_shape import STICK_LIMITS

SETTLE_S, MEASURE_S = 0.3, 3.0
MARGIN, MIN_DZ, MOVE_R = 1.25, 0.03, 0.30
RAW_S = round(SETTLE_S + MEASURE_S + 3)   # how long the sender keeps the sticks without a deadzone (with a margin)
EPS = 1e-6                      # (t0 + 0.3) - t0 can be 0.29999999999995 (depending on the monotonic value) - without it
                                # "0.3 s elapsed" came out true or false at random (a flaky Test view test)


def unit(v):
    """Axis byte 0..255 (centre 128) -> -1..1 (same as rx_shape._unit)."""
    return (v - 128) / (128 if v < 128 else 127)


def propose(r):
    """Largest deflection at rest -> proposed deadzone (a fraction of the travel).

    Adds the MARGIN, rounds up to a whole 0.01 (the inner round() removes float noise so that e.g. 0.12 is not bumped to
    0.13) and clamps to [MIN_DZ, the maximum the shaping settings allow].
    """
    dz = math.ceil(round(r * MARGIN * 100, 6)) / 100
    return min(STICK_LIMITS["dz"][1], max(MIN_DZ, dz))


@dataclass(frozen=True, slots=True)
class StickDrift:
    peak: float          # largest deflection at rest (0..1)
    center: tuple        # mean position (x, y), -1..1
    dz: float            # proposed deadzone


@dataclass(frozen=True, slots=True)
class DriftResult:
    left: StickDrift
    right: StickDrift
    moved: bool          # a stick was deflected above MOVE_R during the measurement
    samples: int

    def stick(self, name):
        """The result for "left" or "right"."""
        return self.left if name == "left" else self.right


def _stick(points):
    """Drift of one stick from raw (x, y) byte samples: the peak is the largest distance from the centre, the centre is the mean."""
    xs, ys = [unit(x) for x, _y in points], [unit(y) for _x, y in points]
    peak = max(math.hypot(x, y) for x, y in zip(xs, ys, strict=True))
    return StickDrift(round(peak, 4), (round(sum(xs) / len(xs), 4), round(sum(ys) / len(ys), 4)), propose(peak))


def analyze(samples):
    """Samples (lx, ly, rx, ry) -> DriftResult (at least one sample required)."""
    left = _stick([(s[0], s[1]) for s in samples])
    right = _stick([(s[2], s[3]) for s in samples])
    return DriftResult(left, right, max(left.peak, right.peak) > MOVE_R, len(samples))


class DriftRun:
    """One measurement for a slot, a two-phase state machine: "settle" (let the raw-stick mode reach the pad), "measure"
    (collect samples), then "done". tick(now) -> None (still running) or DriftResult; cancel() aborts (the pad left, another
    pad was picked). raw(slot, seconds) is the command to the sender (rx_profiles.raw_sticks); None = the sender does not know it."""

    def __init__(self, slot, now, raw=None):
        self.slot, self.raw = slot, raw
        self.t0 = now
        self.phase = "settle"
        if raw is not None:
            raw(slot, RAW_S)

    def progress(self, now):
        """Fraction of the whole run (settle + measure) that has elapsed, 0..1 - drives the progress bar."""
        return min(1.0, max(0.0, (now - self.t0) / (SETTLE_S + MEASURE_S)))

    def tick(self, now):
        """Advance the run to time `now`; returns DriftResult once the measurement window ends, otherwise None."""
        if self.phase == "settle":
            if now - self.t0 < SETTLE_S - EPS:
                return None
            with S.lock:
                S.probe[self.slot] = [S.states[self.slot]["rawst"]]     # the state that is already there counts too
            self.phase = "measure"
            return None
        if self.phase != "measure" or now - self.t0 < SETTLE_S + MEASURE_S - EPS:
            return None
        samples = self._stop()
        return analyze(samples) if samples else None

    def _stop(self):
        """Stop collecting: take the gathered samples, switch the sender back to its normal deadzone, mark the run done."""
        with S.lock:
            samples, S.probe[self.slot] = S.probe[self.slot], None
        if self.phase != "done" and self.raw is not None:
            self.raw(self.slot, 0)
        self.phase = "done"
        return samples

    def cancel(self):
        """Abort a running measurement (no result); safe to call repeatedly."""
        if self.phase != "done":
            self._stop()
