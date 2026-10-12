"""Receiver: pomiar dryfu gałek w spoczynku i propozycja strefy martwej (widok Test w oknie).

Przebieg (DriftRun, krok z zegara okna): nadajnik dostaje CMD_RAW_STICKS (gałki bez jego strefy martwej, inaczej
odchyłki do ~2 % byłyby przycięte do środka), po SETTLE_S zbieramy każdą próbkę stanu z rx_net (S.probe) przez
MEASURE_S, potem strefa martwa nadajnika wraca. Wynik dla gałki: największe wychylenie w spoczynku r (0..1) i
propozycja dz = r * MARGIN zaokrąglone w górę do 0,01, nie mniej niż MIN_DZ. Wychylenie ponad MOVE_R = ktoś ruszał
gałką - pomiar do powtórzenia.
"""
import math
from dataclasses import dataclass

import rx_state as S
from rx_shape import STICK_LIMITS

SETTLE_S, MEASURE_S = 0.3, 3.0
MARGIN, MIN_DZ, MOVE_R = 1.25, 0.03, 0.30
MAX_SAMPLES = 20_000            # bezpiecznik: 3 s przy 1000 Hz to 3000 próbek
RAW_S = round(SETTLE_S + MEASURE_S + 3)   # tyle nadajnik trzyma gałki bez strefy martwej (z zapasem)


def unit(v):
    """Bajt osi 0..255 (środek 128) -> -1..1 (jak rx_shape._unit)."""
    return (v - 128) / (128 if v < 128 else 127)


def propose(r):
    """Największe wychylenie w spoczynku -> proponowana strefa martwa (ułamek wychylenia)."""
    dz = math.ceil(round(r * MARGIN * 100, 6)) / 100
    return min(STICK_LIMITS["dz"][1], max(MIN_DZ, dz))


@dataclass(frozen=True, slots=True)
class StickDrift:
    peak: float          # największe wychylenie w spoczynku (0..1)
    center: tuple        # średnie położenie (x, y), -1..1
    dz: float            # proponowana strefa martwa


@dataclass(frozen=True, slots=True)
class DriftResult:
    left: StickDrift
    right: StickDrift
    moved: bool          # w trakcie pomiaru gałka była wychylona ponad MOVE_R
    samples: int

    def stick(self, name):
        return self.left if name == "left" else self.right


def _stick(points):
    xs, ys = [unit(x) for x, _y in points], [unit(y) for _x, y in points]
    peak = max(math.hypot(x, y) for x, y in zip(xs, ys, strict=True))
    return StickDrift(round(peak, 4), (round(sum(xs) / len(xs), 4), round(sum(ys) / len(ys), 4)), propose(peak))


def analyze(samples):
    """Próbki (lx, ly, rx, ry) -> DriftResult (co najmniej jedna próbka)."""
    left = _stick([(s[0], s[1]) for s in samples])
    right = _stick([(s[2], s[3]) for s in samples])
    return DriftResult(left, right, max(left.peak, right.peak) > MOVE_R, len(samples))


class DriftRun:
    """Jeden pomiar dla slotu. tick(now) -> None (trwa) albo DriftResult; cancel() przerywa (pad odszedł, inny pad).
    raw(slot, sekundy) - polecenie dla nadajnika (rx_profiles.raw_sticks); None = nadajnik go nie zna."""

    def __init__(self, slot, now, raw=None):
        self.slot, self.raw = slot, raw
        self.t0 = now
        self.phase = "settle"
        if raw is not None:
            raw(slot, RAW_S)

    def progress(self, now):
        return min(1.0, max(0.0, (now - self.t0) / (SETTLE_S + MEASURE_S)))

    def tick(self, now):
        if self.phase == "settle":
            if now - self.t0 < SETTLE_S:
                return None
            with S.lock:
                S.probe[self.slot] = [S.states[self.slot]["rawst"]]     # stan, który już jest, też się liczy
            self.phase = "measure"
            return None
        if self.phase != "measure" or now - self.t0 < SETTLE_S + MEASURE_S:
            return None
        samples = self._stop()
        return analyze(samples) if samples else None

    def _stop(self):
        with S.lock:
            samples, S.probe[self.slot] = S.probe[self.slot], None
        if self.phase != "done" and self.raw is not None:
            self.raw(self.slot, 0)
        self.phase = "done"
        return samples

    def cancel(self):
        if self.phase != "done":
            self._stop()
