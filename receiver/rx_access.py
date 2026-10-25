"""Receiver: accessibility mode for the sticks - range, deadzone, curve and tremor damping per physical stick.

For a player who cannot push a stick all the way (a hardware overlay, a weak hand): the settings work on the PHYSICAL stick,
before the profile (swap / inversion / shaping), so they follow the stick whatever the profile does with it. They belong to
the pad (MAC), not to a profile - see rx_padcal, which stores them next to the drift correction:
    {"on": True, "left": {"dz", "outer", "curve", "steady"}, "right": {...}}      (a missing stick = no change)
  dz      inner deadzone, fraction of the travel (0..0.3)
  outer   how far the stick has to go for the FULL output, fraction of the travel (0.4..1) - "range"
  curve   response exponent (0.5..2): below 1 the small movements get stronger, above 1 weaker
  steady  tremor damping (0..0.1): a movement smaller than this, measured from the last output, is ignored
The damping is a threshold, not a low-pass filter on purpose: the sender does not repeat an unchanged state, so a filter that
needs a steady stream of samples would stop short of the final position; a threshold has a bounded error and adds no latency.

The module also analyses the "reach" measurement of the calibration wizard: the player circles the stick as far as is
comfortable and the proposal for `outer` is the smallest of the per-direction reaches, so every direction can reach 100 %.
Pure logic (no rx_state import) - rx_state and rx_padcal import it.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeAlias

from rx_shape import AXIS_CENTRE, AXIS_NEG_SPAN, AXIS_POS_SPAN, shape_stick

Access: TypeAlias = dict[str, Any]                  # the stored settings of a pad, see the module docstring
Runtime: TypeAlias = dict[str, dict[str, float]]    # {stick: parameters for apply()} - what recompute() uses
Sticks: TypeAlias = tuple[int, int, int, int]       # (lx, ly, rx, ry) axis bytes

STICKS: Final = ("left", "right")
DEF: Final = {"dz": 0.0, "outer": 1.0, "curve": 1.0, "steady": 0.0}
LIMITS: Final = {"dz": (0.0, 0.30), "outer": (0.40, 1.0), "curve": (0.5, 2.0), "steady": (0.0, 0.10)}
MIN_GAP: Final = 0.05            # the range always ends at least this far beyond the deadzone
DECIMALS: Final = 3
SATURATED: Final = 0.999         # an output this close to the full deflection is never held back by the damping

# reach analysis
SECTORS: Final = 16              # the circle is split into this many directions
MIN_SECTORS: Final = 12          # at least this many directions must be covered, otherwise the circle was not completed
MIN_R: Final = 0.2               # samples closer to the centre say nothing about the reach
MIN_PEAK: Final = 0.40           # a stick that never went beyond this cannot be helped by the range setting
RIM: Final = 0.6                 # only samples this close to the largest deflection (fraction of it) count as the rim
SECTOR_MIN: Final = 3            # rim samples a direction needs to count as covered
PERCENTILE: Final = 0.9          # the reach of a direction: robust against a single jerk
REACH_MARGIN: Final = 0.97       # the proposal sits a little below the reach, so that full output is easy to hit
FULL_AT: Final = 0.95            # a reach of at least this much of the travel needs no range scaling


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    """Convert to float and clamp to lo..hi; the default for anything that is not a number (or NaN)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else min(hi, max(lo, v))


def normalize_stick(e: Any) -> dict[str, float]:
    """Return the full parameters of one stick within their ranges (`outer` always beyond `dz`); defaults for a bad entry."""
    e = e if isinstance(e, dict) else {}
    out = {k: round(_clamp(e.get(k, d), *LIMITS[k], d), DECIMALS) for k, d in DEF.items()}
    out["outer"] = max(out["outer"], round(out["dz"] + MIN_GAP, DECIMALS))
    return out


def normalize(raw: Any) -> Access:
    """Clean stored settings: {"on", "left", "right"} with known keys, values in range, default sticks left out.

    Returns {} when the mode is off and nothing is set (nothing to store).
    """
    raw = raw if isinstance(raw, dict) else {}
    out: Access = {}
    for name in STICKS:
        if isinstance(raw.get(name), dict) and (p := normalize_stick(raw[name])) != DEF:
            out[name] = p
    if raw.get("on") is True:
        out["on"] = True
    return out


def runtime(cfg: Access) -> Runtime:
    """Convert normalised settings to what apply() needs: {} when the mode is off or changes nothing."""
    if not cfg.get("on"):
        return {}
    return {name: {**cfg[name], "anti": 0.0} for name in STICKS if name in cfg}


def _unit(v: int) -> float:
    """Convert an axis byte 0..255 (centre 128) to -1..1."""
    return (v - AXIS_CENTRE) / (AXIS_NEG_SPAN if v < AXIS_CENTRE else AXIS_POS_SPAN)


def apply(rt: Runtime, st: Sticks, last: Sticks) -> Sticks:
    """Apply the settings to the physical sticks `st`; `last` is the previous result (for the tremor damping).

    Range, deadzone and curve are the ordinary radial shaping of rx_shape; the damping then keeps the previous output while
    the new one is within `steady` of it. A stick back in the deadzone and a full deflection always get through at once.
    """
    out = list(st)
    for k, name in enumerate(STICKS):
        p = rt.get(name)
        if p is None:
            continue
        i = 2 * k
        x, y = shape_stick(st[i], st[i + 1], p)
        thr = p["steady"]
        if thr and (x, y) != (AXIS_CENTRE, AXIS_CENTRE):
            ux, uy = _unit(x), _unit(y)
            if math.hypot(ux, uy) < SATURATED and math.hypot(ux - _unit(last[i]), uy - _unit(last[i + 1])) <= thr:
                x, y = last[i], last[i + 1]
        out[i], out[i + 1] = x, y
    return (out[0], out[1], out[2], out[3])


def shaped_radius(p: Mapping[str, float], r: float) -> float:
    """The output radius (0..1) that the range / deadzone / curve of `p` give a physical deflection r (same formula as shape_stick)."""
    if r <= p["dz"]:
        return 0.0
    return min(1.0, (r - p["dz"]) / (p["outer"] - p["dz"])) ** p["curve"]


def drift_after(rt: Runtime, drift: Mapping[str, float]) -> dict[str, float]:
    """Express the drift deadzones of the physical sticks as they look AFTER the accessibility stage.

    The range scaling enlarges a small drift (5 % of the travel becomes 7 % with a 70 % range), so the profile's drift
    deadzone has to be run through the same transformation or the drift would slip past it.
    """
    return {n: round(shaped_radius(rt[n], v), DECIMALS) if n in rt else v for n, v in drift.items()}


@dataclass(frozen=True, slots=True)
class Reach:
    """The result of a reach measurement of one stick."""

    status: str          # "ok" | "few" (the circle was not completed) | "small" (the stick hardly moved)
    outer: float         # proposed range (1.0 = no scaling needed); meaningful for "ok" only
    peak: float          # the largest deflection seen (0..1)
    sectors: int         # how many of the SECTORS directions were covered


def _percentile(values: list[float]) -> float:
    """The PERCENTILE of a non-empty list."""
    values = sorted(values)
    return values[int(PERCENTILE * (len(values) - 1))]


def analyze_reach(samples: Sequence[Sequence[int]], stick: str) -> Reach:
    """Turn samples (lx, ly, rx, ry) of a circling stick into the proposed range.

    The reach of a direction is a high percentile of the rim samples in its sector; the proposal is the SMALLEST reach
    (with a margin), so that the whole circle can be pushed to full output. Fewer than MIN_SECTORS covered directions or a
    peak below MIN_PEAK is reported instead of guessing.
    """
    i = 0 if stick == "left" else 2
    pts = [(_unit(s[i]), _unit(s[i + 1])) for s in samples]
    radii = [math.hypot(x, y) for x, y in pts]
    peak = max(radii, default=0.0)
    if peak < MIN_PEAK:
        return Reach("small", 1.0, round(peak, DECIMALS), 0)
    rim = max(MIN_R, RIM * peak)
    sectors: dict[int, list[float]] = {}
    for (x, y), r in zip(pts, radii, strict=True):
        if r >= rim:
            k = int((math.atan2(y, x) + math.pi) / (2 * math.pi) * SECTORS) % SECTORS
            sectors.setdefault(k, []).append(r)
    reach = [_percentile(v) for v in sectors.values() if len(v) >= SECTOR_MIN]
    if len(reach) < MIN_SECTORS:
        return Reach("few", 1.0, round(peak, DECIMALS), len(reach))
    worst = min(reach)
    outer = 1.0 if worst >= FULL_AT else max(LIMITS["outer"][0], math.floor(round(worst * REACH_MARGIN * 100, 6)) / 100)
    return Reach("ok", outer, round(peak, DECIMALS), len(reach))


def merge_proposal(cfg: Access, stick: str, **values: float) -> Access:
    """Return normalised settings with `values` set on one stick (the mode switch and the other stick are kept)."""
    cur = normalize_stick(cfg.get(stick))
    return normalize({**cfg, stick: {**cur, **values}})


def describe(cfg: Mapping[str, Any], stick: str) -> dict[str, float]:
    """The parameters of a stick for the UI: the stored ones, or the defaults."""
    return normalize_stick(cfg.get(stick))
