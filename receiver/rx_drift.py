"""Receiver: measure stick drift at rest and propose a deadzone (the Test view in the window).

Flow (DriftRun, stepped by the window clock): the sender gets CMD_RAW_STICKS (sticks without its own deadzone, otherwise
deviations of up to ~2 % would be clipped to the centre), after SETTLE_S every state sample from rx_net is collected
(S.probe) for MEASURE_S, then the sender's deadzone comes back. Result per stick: the largest deflection at rest r (0..1)
and a proposal dz = r * MARGIN rounded up to 0.01, at least MIN_DZ. A deflection above MOVE_R means somebody moved
the stick - the measurement has to be repeated.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

import rx_state as S
from rx_shape import STICK_LIMITS

SETTLE_S: Final = 0.3
MEASURE_S: Final = 3.0
MARGIN: Final = 1.25
MIN_DZ: Final = 0.03
MOVE_R: Final = 0.30
AXIS_CENTRE: Final = 128
AXIS_NEG_SPAN: Final = 128           # the axis byte has 128 steps below the centre and 127 above it
AXIS_POS_SPAN: Final = 127
RAW_MARGIN_S: Final = 3
RAW_S: Final = round(SETTLE_S + MEASURE_S + RAW_MARGIN_S)   # how long the sender keeps the sticks without a deadzone (with a margin)
EPS: Final = 1e-6                     # (t0 + 0.3) - t0 can be 0.29999999999995 (depending on the monotonic value) - without it
                                # "0.3 s elapsed" came out true or false at random (a flaky Test view test)


def unit(v: int) -> float:
    """Convert an axis byte 0..255 (centre 128) to -1..1 (same as rx_shape._unit)."""
    return (v - AXIS_CENTRE) / (AXIS_NEG_SPAN if v < AXIS_CENTRE else AXIS_POS_SPAN)


def propose(r: float) -> float:
    """Turn the largest deflection at rest into a proposed deadzone (a fraction of the travel).

    Adds the MARGIN, rounds up to a whole 0.01 (the inner round() removes float noise so that e.g. 0.12 is not bumped to
    0.13) and clamps to [MIN_DZ, the maximum the shaping settings allow].
    """
    dz = math.ceil(round(r * MARGIN * 100, 6)) / 100
    return min(STICK_LIMITS["dz"][1], max(MIN_DZ, dz))


@dataclass(frozen=True, slots=True)
class StickDrift:
    """The drift of one stick."""

    peak: float          # largest deflection at rest (0..1)
    center: tuple[float, float]   # mean position (x, y), -1..1
    dz: float            # proposed deadzone


@dataclass(frozen=True, slots=True)
class DriftResult:
    """The result of a measurement: both sticks."""

    left: StickDrift
    right: StickDrift
    moved: bool          # a stick was deflected above MOVE_R during the measurement
    samples: int

    def stick(self, name: str) -> StickDrift:
        """Return the result for "left" or "right"."""
        return self.left if name == "left" else self.right


def _stick(points: Sequence[tuple[int, int]]) -> StickDrift:
    """Compute the drift of one stick from raw (x, y) byte samples (peak = largest distance from the centre, centre = mean)."""
    xs, ys = [unit(x) for x, _y in points], [unit(y) for _x, y in points]
    peak = max(math.hypot(x, y) for x, y in zip(xs, ys, strict=True))
    return StickDrift(round(peak, 4), (round(sum(xs) / len(xs), 4), round(sum(ys) / len(ys), 4)), propose(peak))


def analyze(samples: Sequence[Sequence[int]]) -> DriftResult:
    """Turn samples (lx, ly, rx, ry) into a DriftResult (at least one sample required)."""
    left = _stick([(s[0], s[1]) for s in samples])
    right = _stick([(s[2], s[3]) for s in samples])
    return DriftResult(left, right, max(left.peak, right.peak) > MOVE_R, len(samples))


class DriftRun:
    """One measurement for a slot, a two-phase state machine.

    "settle" (let the raw-stick mode reach the pad), "measure" (collect samples), then "done". tick(now) -> None (still
    running) or DriftResult; cancel() aborts (the pad left, another pad was picked). raw(slot, seconds) is the command
    to the sender (rx_profiles.raw_sticks); None = the sender does not know it.
    """

    def __init__(self, slot: int, now: float, raw: Callable[[int, int], None] | None = None) -> None:
        """Start a run for `slot` at time `now`; `raw` is the command to the sender (None = it does not know it)."""
        self.slot, self.raw = slot, raw
        self.t0 = now
        self.phase = "settle"
        if raw is not None:
            raw(slot, RAW_S)

    def progress(self, now: float) -> float:
        """Fraction of the whole run (settle + measure) that has elapsed, 0..1 - drives the progress bar."""
        return min(1.0, max(0.0, (now - self.t0) / (SETTLE_S + MEASURE_S)))

    def tick(self, now: float) -> DriftResult | None:
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

    def _stop(self) -> list[tuple[int, ...]] | None:
        """Stop collecting: take the gathered samples, switch the sender back to its normal deadzone, mark the run done."""
        with S.lock:
            samples, S.probe[self.slot] = S.probe[self.slot], None
        if self.phase != "done" and self.raw is not None:
            self.raw(self.slot, 0)
        self.phase = "done"
        return samples

    def cancel(self) -> None:
        """Abort a running measurement (no result); safe to call repeatedly."""
        if self.phase != "done":
            self._stop()
