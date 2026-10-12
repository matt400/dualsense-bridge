"""Receiver: prawdziwe opóźnienie pad -> wirtualny DS4, z rozbiciem na odcinki (okno: karta pada).

Każda próbka to jedna zmiana stanu pada (przycisk, gałka, ruch), mierzona od chwili, gdy jądro telefonu dostało
raport z pada, do oddania raportu wirtualnemu DS4 (ViGEm):

  bt     opóźnienie Bluetooth ponad najlepszy przypadek z ostatnich 2 s (zegar pada vs czas odbioru w jądrze) -
         kolejka i retransmisje radia; stała część radia (~1-2 ms) jest niemierzalna bez wspólnego zegaru z padem
  phone  jądro telefonu -> wysłanie pakietu (wybudzenie nadajnika, zlewanie zmian w oknie pad_min_dt)
  net    sieć: wysłanie -> odbiór na PC, w zegarze PC (nadajnik zna przesunięcie zegarów z pongów, jak NTP)
  pc     odbiór pakietu -> raport w ViGEm (remap, limit pad_hz, wywołanie sterownika)

Próbki z ostatnich WINDOW_S; okno co 400 ms liczy medianę każdego odcinka i p50 / p99 sumy.
"""
import collections
import threading
import time
from dataclasses import dataclass

from proto import LAT_NONE

WINDOW_S = 5.0
MIN_SAMPLES = 10
MAX_PC_US = 1_000_000
_lock = threading.Lock()
_samples = collections.defaultdict(lambda: collections.deque(maxlen=8000))   # slot -> (czas ns, bt, phone, net, pc)


@dataclass(frozen=True, slots=True)
class Summary:
    total: float          # mediana sumy [ms]
    p99: float            # 99. percentyl sumy [ms]
    bt: float | None      # mediany odcinków [ms]; bt None = nadajnik bez znaczników czasu pada
    phone: float
    net: float
    pc: float
    n: int                # liczba próbek w oknie


def add(i, lat, done_ns):
    """Raport z tą zmianą trafił do ViGEm w chwili done_ns. lat = (odbiór ns, phone µs, bt µs, net µs) z rx_net."""
    arr, phone, bt, net = lat
    pc = (done_ns - arr) // 1000
    if not 0 <= pc < MAX_PC_US:       # zmiana czekała na wirtualnego DS4, którego nie było - to nie opóźnienie
        return
    with _lock:
        _samples[i].append((done_ns, None if bt == LAT_NONE else bt, phone, net, pc))


def clear(i):
    with _lock:
        _samples.pop(i, None)


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] / 1000 if xs else None


def summary(i, now_ns=None):
    """-> Summary z ostatnich WINDOW_S albo None (za mało próbek: pad leży, starszy nadajnik, brak zegara PC)."""
    start = (now_ns or time.perf_counter_ns()) - int(WINDOW_S * 1e9)
    with _lock:
        d = _samples.get(i)
        rows = [r for r in d if r[0] >= start] if d else []
    if len(rows) < MIN_SAMPLES:
        return None
    _t, bt, phone, net, pc = zip(*rows, strict=True)
    totals = sorted((b or 0) + p + n + c for b, p, n, c in zip(bt, phone, net, pc, strict=True))
    bts = [b for b in bt if b is not None]
    return Summary(total=totals[len(totals) // 2] / 1000, p99=totals[min(len(totals) - 1, int(len(totals) * 0.99))] / 1000,
                   bt=_median(bts), phone=_median(phone), net=_median(net), pc=_median(pc), n=len(rows))
