"""Receiver: prawdziwe opóźnienie pad -> wirtualny DS4, z rozbiciem na odcinki (okno: karta pada).

Każda próbka to jedna zmiana stanu pada, mierzona od chwili, gdy jądro telefonu dostało raport z pada, do oddania
raportu wirtualnemu DS4 (ViGEm):

  bt     opóźnienie Bluetooth ponad najlepszy przypadek z ostatnich 2 s (zegar pada vs czas odbioru w jądrze) -
         kolejka i retransmisje radia; stała część radia (~1-2 ms) jest niemierzalna bez wspólnego zegaru z padem
  phone  jądro telefonu -> wysłanie pakietu (wybudzenie nadajnika, zlewanie zmian w oknie pad_min_dt)
  net    sieć: wysłanie -> odbiór na PC, w zegarze PC (nadajnik zna przesunięcie zegarów z pongów, jak NTP)
  pc     odbiór pakietu -> raport w ViGEm (remap, limit pad_hz, wywołanie sterownika)

Dwa liczniki per slot:
  wszystkie zmiany (przycisk, gałka, ruch) z ostatnich WINDOW_S - summary()
  tylko przyciski, D-pad i spusty (przejście przez próg TRIG_ON) - ostatnie BUTTON_KEEP naciśnięć / puszczeń bez
  limitu czasu (naciśnięcia są rzadkie, 5 s dałoby kilka próbek) - buttons(); reset z okna: reset_buttons()
Wynik: średnia, p95 i max sumy oraz średnie odcinków (pasek odcinków sumuje się do średniej).
"""
import collections
import math
import threading
import time
from dataclasses import dataclass
from statistics import fmean

from proto import LAT_NONE

WINDOW_S = 5.0
MIN_SAMPLES = 10
BUTTON_KEEP = 100
MAX_PC_US = 1_000_000
_lock = threading.Lock()
_samples = collections.defaultdict(lambda: collections.deque(maxlen=8000))   # slot -> (czas ns, bt, phone, net, pc)
_buttons = collections.defaultdict(lambda: collections.deque(maxlen=BUTTON_KEEP))   # slot -> (bt, phone, net, pc)


@dataclass(frozen=True, slots=True)
class Summary:
    avg: float            # średnia sumy [ms]
    p95: float            # 95. percentyl sumy [ms]
    max: float            # najgorsza próbka [ms]
    bt: float | None      # średnie odcinków [ms]; bt None = nadajnik bez znaczników czasu pada
    phone: float
    net: float
    pc: float
    n: int                # liczba próbek


def _row(lat, done_ns):
    """lat = (odbiór ns, phone µs, bt µs, net µs) z rx_net -> (bt | None, phone, net, pc) albo None."""
    arr, phone, bt, net = lat
    pc = (done_ns - arr) // 1000
    if not 0 <= pc < MAX_PC_US:       # zmiana czekała na wirtualnego DS4, którego nie było - to nie opóźnienie
        return None
    return None if bt == LAT_NONE else bt, phone, net, pc


def add(i, lat, done_ns):
    """Raport z tą zmianą trafił do ViGEm w chwili done_ns."""
    if (r := _row(lat, done_ns)) is not None:
        with _lock:
            _samples[i].append((done_ns, *r))


def add_button(i, lat, done_ns):
    """Raport ze zmianą przycisku / D-pada / spustu (próg) trafił do ViGEm w chwili done_ns."""
    if (r := _row(lat, done_ns)) is not None:
        with _lock:
            _buttons[i].append(r)


def clear(i):
    """Pomiar ciągły slotu od nowa (cisza, inny pad). Licznik przycisków zostaje - patrz reset_buttons."""
    with _lock:
        _samples.pop(i, None)


def reset_buttons(i):
    with _lock:
        _buttons.pop(i, None)


def _mean(xs):
    return fmean(xs) / 1000 if xs else None


def _summarize(rows):
    """[(bt | None, phone, net, pc) µs] -> Summary."""
    bt, phone, net, pc = zip(*rows, strict=True)
    totals = sorted((b or 0) + p + n + c for b, p, n, c in rows)
    n = len(totals)
    return Summary(avg=fmean(totals) / 1000, p95=totals[max(0, math.ceil(n * 0.95) - 1)] / 1000, max=totals[-1] / 1000,
                   bt=_mean([b for b in bt if b is not None]), phone=_mean(phone), net=_mean(net), pc=_mean(pc), n=n)


def summary(i, now_ns=None):
    """-> Summary wszystkich zmian z ostatnich WINDOW_S albo None (za mało próbek: pad leży, starszy nadajnik,
    brak zegara PC)."""
    start = (now_ns or time.perf_counter_ns()) - int(WINDOW_S * 1e9)
    with _lock:
        d = _samples.get(i)
        rows = [r[1:] for r in d if r[0] >= start] if d else []
    return _summarize(rows) if len(rows) >= MIN_SAMPLES else None


def buttons(i):
    """-> Summary ostatnich BUTTON_KEEP naciśnięć / puszczeń albo None (jeszcze żadnego)."""
    with _lock:
        rows = list(_buttons.get(i, ()))
    return _summarize(rows) if rows else None
