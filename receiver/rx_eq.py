"""Receiver: the equalizer of the pad's speaker - five bands on the mono game sound, no Qt (rx_speaker, settings, window).

Five bands (RBJ "audio EQ cookbook" biquads at 48 kHz): a low shelf at 100 Hz, peaking filters at 300 Hz, 1 kHz and 3 kHz, a high shelf
at 8 kHz, each -12 .. +12 dB in whole dB. A setting is a tuple of five ints (one per band); all zeros is "flat" and costs nothing
(rx_speaker takes its old path). The presets are only shortcuts that fill the five numbers: nothing but the numbers is stored, and the
window shows the preset whose numbers match (else "custom").

The filters run in series over a block of mono samples, one band after another (a band at 0 dB is skipped). The state (two delay values
per band) survives from block to block, so a block boundary is invisible; it is cleared by silence (a boosted filter would ring a few
ms into the pause and a stale state would click when the sound resumes) and by a band that turns on. The code is plain Python (no numpy
in the receiver): one multiply-add loop per active band, roughly 0.1-0.3 us per sample and band, so five active bands
cost a few percent of one core (tests/test_eq.py prints the figure).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

from proto import SPK_RATE

BANDS: Final[tuple[tuple[int, str], ...]] = ((100, "low"), (300, "peak"), (1000, "peak"), (3000, "peak"), (8000, "high"))
NUM_BANDS: Final = len(BANDS)
KHZ: Final = 1000
GAIN_DB: Final = (-12, 12)                  # per band, whole dB
Q: Final = 1.0                              # the peaking bands' width (about one octave)
FLAT: Final[tuple[int, ...]] = (0,) * NUM_BANDS

# The pad's speaker is tiny: it plays almost nothing under 300 Hz and is loudest at 0.5-3 kHz, so "voice" lifts the upper mids and
# "bass" pushes the lows (it can only make the little that the speaker has more audible).
PRESETS: Final[dict[str, tuple[int, ...]]] = {
    "flat": FLAT,
    "voice": (-6, -2, 3, 5, 1),
    "bass": (8, 5, 0, -2, -3),
}


def band_name(hz: int) -> str:
    """A band's label for the window: "100 Hz", "1 kHz"."""
    return f"{hz // KHZ} kHz" if hz >= KHZ else f"{hz} Hz"


def db_text(v: float) -> str:
    """A gain for the window: "+3 dB", "-6 dB", "0 dB"."""
    return f"{v:+.0f} dB" if round(v) else "0 dB"


def clamp(gains: Sequence[int]) -> tuple[int, ...]:
    """Five gains (any ints) -> a valid setting: each clamped to GAIN_DB. Raises ValueError for the wrong count or a non-number."""
    if len(gains) != NUM_BANDS:
        raise ValueError(f"expected {NUM_BANDS} gains, got {len(gains)}")
    out = []
    for g in gains:
        if isinstance(g, bool) or not isinstance(g, (int, float)):
            raise ValueError(f"bad gain {g!r}")
        out.append(max(GAIN_DB[0], min(GAIN_DB[1], round(g))))
    return tuple(out)


def preset_of(gains: Sequence[int]) -> str:
    """The name of the preset these gains equal ("custom" when none does)."""
    g = tuple(gains)
    return next((name for name, vals in PRESETS.items() if vals == g), "custom")


def coefficients(kind: str, hz: float, db: float, rate: int = SPK_RATE) -> tuple[float, float, float, float, float]:
    """One band as normalised biquad coefficients (b0, b1, b2, a1, a2), a0 = 1 (RBJ cookbook; the shelves use slope S = 1)."""
    a = 10 ** (db / 40)
    w = 2 * math.pi * hz / rate
    cos, sin = math.cos(w), math.sin(w)
    if kind == "peak":
        alpha = sin / (2 * Q)
        b0, b1, b2 = 1 + alpha * a, -2 * cos, 1 - alpha * a
        a0, a1, a2 = 1 + alpha / a, -2 * cos, 1 - alpha / a
    else:
        beta = 2 * math.sqrt(a) * (sin / 2 * math.sqrt(2))         # 2 sqrt(A) alpha, alpha = sin/2 sqrt(2) for S = 1
        if kind == "low":
            b0, b1, b2 = a * ((a + 1) - (a - 1) * cos + beta), 2 * a * ((a - 1) - (a + 1) * cos), a * ((a + 1) - (a - 1) * cos - beta)
            a0, a1, a2 = (a + 1) + (a - 1) * cos + beta, -2 * ((a - 1) + (a + 1) * cos), (a + 1) + (a - 1) * cos - beta
        else:
            b0, b1, b2 = a * ((a + 1) + (a - 1) * cos + beta), -2 * a * ((a - 1) + (a + 1) * cos), a * ((a + 1) + (a - 1) * cos - beta)
            a0, a1, a2 = (a + 1) - (a - 1) * cos + beta, 2 * ((a - 1) - (a + 1) * cos), (a + 1) - (a - 1) * cos - beta
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


class Equalizer:
    """The five filters of one pad's speaker; used from one thread (the speaker link's), so it takes no lock."""

    def __init__(self, rate: int = SPK_RATE) -> None:
        """Start flat."""
        self.rate = rate
        self.gains: tuple[int, ...] = FLAT
        self._stages: list[tuple[int, tuple[float, float, float, float, float]]] = []     # (band, coefficients) of the active bands
        self._z: list[list[float]] = [[0.0, 0.0] for _ in BANDS]                           # transposed direct form II state per band

    @property
    def flat(self) -> bool:
        """True when no band is active (nothing to process)."""
        return not self._stages

    def set_gains(self, gains: Sequence[int]) -> None:
        """Use these gains (dB per band). Unchanged gains do nothing; a band that was off starts with a clean state."""
        g = tuple(gains)
        if g == self.gains:
            return
        for band, (old, new) in enumerate(zip(self.gains, g, strict=True)):
            if old == 0 and new != 0:
                self._z[band] = [0.0, 0.0]
        self.gains = g
        self._stages = [(band, coefficients(BANDS[band][1], BANDS[band][0], db, self.rate)) for band, db in enumerate(g) if db]

    def reset(self) -> None:
        """Forget the filters' memory (after silence or a gap in the audio)."""
        for z in self._z:
            z[0] = z[1] = 0.0

    def process(self, x: list[float]) -> list[float]:
        """Filter a block of mono samples in place through every active band; returns the same list."""
        for band, (b0, b1, b2, a1, a2) in self._stages:
            z = self._z[band]
            z1, z2 = z
            for i, v in enumerate(x):
                y = b0 * v + z1
                z1 = b1 * v - a1 * y + z2
                z2 = b2 * v - a2 * y
                x[i] = y
            z[0], z[1] = z1, z2
        return x
