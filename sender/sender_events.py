"""Sender: zdarzenia do dziennika w oknie receivera (pakiet event) - połączenie / rozłączenie pada, Wake-on-LAN,
brak receivera, błędy.

Tylko gdy receiver je włączył (flaga SCFG_EVENTS w pakiecie scfg, zapamiętana w wol.json - działa też od startu,
zanim receiver się odezwie). Zdarzenie czeka w kolejce (najwyżej KEEP), aż receiver odpowiada na ping, i idzie
SENDS razy w kolejnych pingach (UDP); receiver odrzuca powtórki po numerze. Dzięki temu np. wysłany Wake-on-LAN
pojawia się w dzienniku, gdy komputer wstanie - z prawdziwym czasem (pole wieku).
"""
import collections
import random
import time

from proto import BODY, EV_ERROR, EV_INFO, EV_WARN, T_EVENT

KEEP = 32
SENDS = 2
ARG_MAX = BODY[T_EVENT].size - 6
enabled = False
_queue = collections.deque(maxlen=KEEP)      # [numer, czas, poziom, kod, slot, argument, ile razy jeszcze]
_next = random.getrandbits(16)


def add(level, code, idx=255, arg=""):
    global _next
    if not enabled:
        return
    _next = (_next + 1) & 0xFFFF
    _queue.append([_next, time.monotonic(), level, code, idx, arg.encode("utf-8", "replace")[:ARG_MAX], SENDS])


def info(code, idx=255, arg=""):
    add(EV_INFO, code, idx, arg)


def warn(code, idx=255, arg=""):
    add(EV_WARN, code, idx, arg)


def error(code, idx=255, arg=""):
    add(EV_ERROR, code, idx, arg)


def set_enabled(on):
    global enabled
    enabled = bool(on)
    if not enabled:
        _queue.clear()


def flush(emit, now=None):
    """Wołane z pętli pingu, gdy receiver odpowiada: wysyła czekające zdarzenia (każde SENDS razy)."""
    now = time.monotonic() if now is None else now
    for ev in list(_queue):
        num, t, level, code, idx, arg, left = ev
        emit(T_EVENT, idx, num, level, code, min(0xFFFF, int(now - t)), arg)
        ev[6] = left - 1
        if ev[6] <= 0:
            _queue.remove(ev)


def pending():
    return len(_queue)
