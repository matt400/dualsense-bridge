"""Receiver: lightbar colour from the settings (settings.json -> lightbar) - pure functions, no threads.

Layers, from weakest to strongest: the pad's own colour from the settings (fixed or battery-based) -> the colour set by
the game (the game wins as soon as it has set anything) -> red blinking on low battery (also over the game colour) ->
shortcut confirmations (rx_feedback.PULSES, e.g. the golden flash of the master action). Charging has no effect of its
own; a charging pad does not blink.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final, TypeAlias

from rx_settings import PadLight

RGB: TypeAlias = tuple[int, int, int]

BLINK_PERIOD_S: Final = 1.0              # low battery: 0.4 s of red every second
BLINK_ON_S: Final = 0.4
LOW_RGB: Final = (255, 0, 0)
FULL_PCT: Final = 80                     # from this battery % up the battery colour is fully green
UNKNOWN_PCT: Final = 100                 # a battery level above this means "unknown" (see own_color)
_RED: Final = (255, 0, 0)
_YELLOW: Final = (255, 170, 0)
_GREEN: Final = (0, 220, 60)


def hex_rgb(text: str) -> RGB:
    """Convert "#rrggbb" to (r, g, b)."""
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


def rgb_hex(rgb: Sequence[int]) -> str:
    """Convert (r, g, b) to "#rrggbb"."""
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    """Interpolate linearly between colours a and b at position t (0..1)."""
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


def battery_rgb(pct: float, low_pct: float) -> RGB:
    """Return the battery colour: red up to the low-battery threshold, then a red -> yellow -> green gradient to FULL_PCT.

    The gradient is split at the midpoint between the threshold and FULL_PCT, where the colour is pure yellow.
    """
    if pct <= low_pct:
        return _RED
    if pct >= FULL_PCT:
        return _GREEN
    mid = (low_pct + FULL_PCT) / 2
    if pct < mid:
        return _mix(_RED, _YELLOW, (pct - low_pct) / (mid - low_pct))
    return _mix(_YELLOW, _GREEN, (pct - mid) / (FULL_PCT - mid))


def own_color(light: PadLight, pct: float, low_pct: float, idle_rgb: Sequence[int]) -> tuple[RGB, bool]:
    """Return the pad's own colour from the settings (used while the game has set nothing).

    The result is (rgb, whether it differs from the default idle_lightbar). Unknown battery (pct > 100) in battery mode
    falls back to the fixed colour.
    """
    idle = (idle_rgb[0], idle_rgb[1], idle_rgb[2])
    if light.mode == "battery" and pct <= UNKNOWN_PCT:
        return battery_rgb(pct, low_pct), True
    if light.color:
        rgb = hex_rgb(light.color)
        return rgb, rgb != idle
    return idle, False


def blinking(light: PadLight, low: bool) -> bool:
    """Tell whether the lightbar should blink right now: blinking enabled for this pad and the battery is low."""
    return light.blink and low


def blink_on(now: float) -> bool:
    """Tell whether `now` falls into the "on" part of the blink period."""
    return now % BLINK_PERIOD_S < BLINK_ON_S


def blink_rgb(base: Sequence[int], now: float) -> RGB:
    """Colour for the current blink phase: red or the base colour.

    When the base is itself red (e.g. the battery colour at a low level) the phases are red and lightbar off, so that
    the blinking stays visible.
    """
    if blink_on(now):
        return LOW_RGB
    colour = (base[0], base[1], base[2])
    return (0, 0, 0) if colour == LOW_RGB else colour


def next_edge(now: float) -> float:
    """Return the seconds until the blink changes phase."""
    ph = now % BLINK_PERIOD_S
    return (BLINK_ON_S - ph) if ph < BLINK_ON_S else (BLINK_PERIOD_S - ph)
