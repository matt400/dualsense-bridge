"""Receiver: dziennik zdarzeń dla okna (panel na dole) - tylko w pamięci, bez Qt.

Wpisy dodają moduły receivera (połączenie / rozłączenie pada, nadajnik online / offline, zmiana mastera, tryb
konsoli, błędy, aktualizacje) i nadajnik (pakiet event, gdy włączone w panelu). Tekst jest już w języku okna;
log w konsoli zostaje osobno (print, po angielsku). Okno pobiera nowe wpisy po numerze (since) co odświeżenie.
"""
import collections
import threading
import time
from dataclasses import dataclass

from proto import (BODY, E_ERROR, E_NET_DOWN, E_NO_RECEIVER, E_NO_SLOT, E_PAD_OFF, E_PAD_ON, E_POWER_OFF, E_START, E_WOL, E_WOL_FAIL,
                   EV_ERROR, EV_INFO, EV_WARN, T_EVENT, cstr)
from rx_i18n import _

KEEP = 5000                       # tyle wpisów w pamięci (limit linii panelu jest osobno, w ustawieniach)
LEVELS = ("info", "warn", "error")
WIRE_LEVEL = {EV_INFO: "info", EV_WARN: "warn", EV_ERROR: "error"}
# teksty zdarzeń nadajnika (kod E_* z pakietu event); {n} = numer pada, {arg} = argument z pakietu
SENDER_TEXT = {
    E_START: "Nadajnik uruchomiony (wersja {arg})",
    E_PAD_ON: "Pad {n} połączony z telefonem ({arg})",
    E_PAD_OFF: "Pad {n} rozłączony od telefonu",
    E_POWER_OFF: "Pad {n} wyłączony przez nadajnik ({arg})",
    E_WOL: "Wysłano Wake-on-LAN do {arg} (pad {n})",
    E_WOL_FAIL: "Wake-on-LAN nieudany: {arg}",
    E_NO_RECEIVER: "Nadajnik stracił kontakt z receiverem",
    E_NET_DOWN: "Sieć na telefonie niedostępna",
    E_ERROR: "Błąd nadajnika: {arg}",
    E_NO_SLOT: "Pad {arg} pominięty - wszystkie sloty nadajnika zajęte (max_pads w sender.toml)",
}


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    t: float                      # czas (time.time())
    level: str                    # info | warn | error
    text: str
    sender: bool = False          # przyszło z nadajnika


_lock = threading.Lock()
_events = collections.deque(maxlen=KEEP)
_seq = 0
_seen = collections.deque(maxlen=128)      # numery zdarzeń nadajnika (powtórki UDP)


def add(level, text, sender=False, t=None):
    global _seq
    with _lock:
        _seq += 1
        e = Event(_seq, time.time() if t is None else t, level if level in LEVELS else "info", text, sender)
        _events.append(e)
    return e


def info(text):
    return add("info", text)


def warn(text):
    return add("warn", text)


def error(text):
    return add("error", text)


def since(seq):
    """Wpisy nowsze niż seq (od najstarszego)."""
    with _lock:
        if not _events or _events[-1].seq <= seq:
            return []
        return [e for e in _events if e.seq > seq]


def clear():
    with _lock:
        _events.clear()
        _seen.clear()


def visible(e, level):
    return LEVELS.index(e.level) >= LEVELS.index(level)


def fmt(e):
    """'12:03:44  ⚠  tekst' (nadajnik: ikona telefonu przed tekstem) - linia do skopiowania."""
    mark = {"info": "·", "warn": "⚠", "error": "✖"}[e.level]
    return f"{time.strftime('%H:%M:%S', time.localtime(e.t))}  {mark}  " + ("📱 " if e.sender else "") + e.text


def pad_name(i):
    return _("Pad {n}").format(n=i + 1)


def on_sender_event(i, data, now=None):
    """Pakiet event z nadajnika -> wpis (powtórka odrzucona po numerze). -> Event albo None."""
    num, level, code, age, arg = BODY[T_EVENT].unpack_from(data, 4)
    if num in _seen:
        return None
    _seen.append(num)
    tpl = SENDER_TEXT.get(code)
    text = _(tpl).format(n=i + 1 if i != 255 else "?", arg=cstr(arg)) if tpl else _("Zdarzenie nadajnika {code}: {arg}").format(
        code=code, arg=cstr(arg))
    return add(WIRE_LEVEL.get(level, "info"), text, sender=True, t=(time.time() if now is None else now) - age)
