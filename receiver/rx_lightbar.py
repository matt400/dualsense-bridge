"""Receiver: lightbar colour from the settings (settings.json -> lightbar) - pure functions, no threads.

Layers, from weakest to strongest: the pad's own colour from the settings (fixed or battery-based) -> the colour set by
the game (the game wins as soon as it has set anything) -> red blinking on low battery (also over the game colour) ->
shortcut confirmations (rx_feedback.PULSES, e.g. the golden flash of the master action). Charging has no effect of its
own; a charging pad does not blink.
"""
from rx_settings import PadLight

BLINK_PERIOD_S, BLINK_ON_S = 1.0, 0.4      # low battery: 0.4 s of red every second
LOW_RGB = (255, 0, 0)
FULL_PCT = 80                              # from this battery % up the battery colour is fully green
_RED, _YELLOW, _GREEN = (255, 0, 0), (255, 170, 0), (0, 220, 60)


def hex_rgb(text):
    """"#rrggbb" -> (r, g, b)."""
    return tuple(int(text[k:k + 2], 16) for k in (1, 3, 5))


def rgb_hex(rgb) -> str:
    """(r, g, b) -> "#rrggbb"."""
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix(a, b, t):
    """Linear interpolation between colours a and b at position t (0..1)."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))


def battery_rgb(pct, low_pct):
    """Battery colour: red up to the low-battery threshold, then a red -> yellow -> green gradient reaching full green at FULL_PCT.

    The gradient is split at the midpoint between the threshold and FULL_PCT, where the colour is pure yellow.
    """
    if pct <= low_pct:
        return _RED
    if pct >= FULL_PCT:
        return _GREEN
    mid = (low_pct + FULL_PCT) / 2
    return _mix(_RED, _YELLOW, (pct - low_pct) / (mid - low_pct)) if pct < mid else _mix(_YELLOW, _GREEN, (pct - mid) / (FULL_PCT - mid))


def own_color(light: PadLight, pct, low_pct, idle_rgb):
    """The pad's own colour from the settings (used while the game has set nothing).

    (rgb, whether it differs from the default idle_lightbar). Unknown battery (pct > 100) in battery mode falls back to
    the fixed colour.
    """
    if light.mode == "battery" and pct <= 100:
        return battery_rgb(pct, low_pct), True
    if light.color:
        rgb = hex_rgb(light.color)
        return rgb, rgb != tuple(idle_rgb)
    return tuple(idle_rgb), False


def blinking(light: PadLight, low):
    """Whether the lightbar should blink right now: blinking enabled for this pad and the battery is low."""
    return light.blink and low


def blink_on(now):
    """Whether `now` falls into the "on" part of the blink period."""
    return now % BLINK_PERIOD_S < BLINK_ON_S


def blink_rgb(base, now):
    """Colour for the current blink phase: red or the base colour.

    When the base is itself red (e.g. the battery colour at a low level) the phases are red and lightbar off, so that
    the blinking stays visible.
    """
    if blink_on(now):
        return LOW_RGB
    return (0, 0, 0) if tuple(base) == LOW_RGB else tuple(base)


def next_edge(now):
    """Seconds until the blink changes phase."""
    ph = now % BLINK_PERIOD_S
    return (BLINK_ON_S - ph) if ph < BLINK_ON_S else (BLINK_PERIOD_S - ph)
