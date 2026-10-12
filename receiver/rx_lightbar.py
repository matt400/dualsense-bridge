"""Receiver: kolor lightbara z ustawień (settings.json -> lightbar) - czyste funkcje, bez wątków.

Kolejność (od najsłabszego): kolor z ustawień pada (stały albo wg baterii) -> kolor z gry (gra ma pierwszeństwo, gdy
cokolwiek ustawiła) -> czerwone miganie przy niskiej baterii (także nad kolorem gry) -> potwierdzenia skrótów
(rx_feedback.PULSES, np. złoty błysk mastera). Ładowanie nie ma osobnego efektu; ładujący się pad nie miga.
"""
from rx_settings import PadLight

BLINK_PERIOD_S, BLINK_ON_S = 1.0, 0.4      # niska bateria: 0.4 s czerwieni co sekundę
LOW_RGB = (255, 0, 0)
FULL_PCT = 80                              # od tylu % kolor baterii jest w pełni zielony
_RED, _YELLOW, _GREEN = (255, 0, 0), (255, 170, 0), (0, 220, 60)


def hex_rgb(text):
    return tuple(int(text[k:k + 2], 16) for k in (1, 3, 5))


def rgb_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))


def battery_rgb(pct, low_pct):
    """Czerwony do progu niskiej baterii, przez żółty do zielonego przy FULL_PCT."""
    if pct <= low_pct:
        return _RED
    if pct >= FULL_PCT:
        return _GREEN
    mid = (low_pct + FULL_PCT) / 2
    return _mix(_RED, _YELLOW, (pct - low_pct) / (mid - low_pct)) if pct < mid else _mix(_YELLOW, _GREEN, (pct - mid) / (FULL_PCT - mid))


def own_color(light: PadLight, pct, low_pct, idle_rgb):
    """Kolor z ustawień pada (gdy gra nic nie ustawiła) -> (rgb, czy to coś innego niż domyślny idle_lightbar).
    Bateria nieznana (pct > 100) w trybie wg baterii = kolor stały."""
    if light.mode == "battery" and pct <= 100:
        return battery_rgb(pct, low_pct), True
    if light.color:
        rgb = hex_rgb(light.color)
        return rgb, rgb != tuple(idle_rgb)
    return tuple(idle_rgb), False


def blinking(light: PadLight, low):
    return light.blink and low


def blink_on(now):
    return now % BLINK_PERIOD_S < BLINK_ON_S


def blink_rgb(base, now):
    """Faza migania: czerwień albo kolor bazowy; gdy baza jest czerwona (np. kolor wg baterii przy niskim stanie) -
    czerwień i zgaszony lightbar, żeby miganie było widać."""
    if blink_on(now):
        return LOW_RGB
    return (0, 0, 0) if tuple(base) == LOW_RGB else tuple(base)


def next_edge(now):
    """Za ile sekund miganie zmieni fazę."""
    ph = now % BLINK_PERIOD_S
    return (BLINK_ON_S - ph) if ph < BLINK_ON_S else (BLINK_PERIOD_S - ph)
