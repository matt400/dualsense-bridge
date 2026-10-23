"""Receiver: the event log shown in the window (bottom panel) - in memory only, no Qt.

Entries are added by receiver modules (pad connected / disconnected, sender online / offline, master change, console
mode, errors, updates) and by the sender (the event packet, when enabled in the panel). The text is already in the
window language; the console log stays separate (print, in English). On every refresh the window fetches the new
entries by sequence number (since).
"""
import collections
import threading
import time
from dataclasses import dataclass

from proto import (BODY, E_ERROR, E_NET_DOWN, E_NO_RECEIVER, E_NO_SLOT, E_PAD_OFF, E_PAD_ON, E_POWER_OFF, E_START, E_WOL, E_WOL_FAIL,
                   EV_ERROR, EV_INFO, EV_WARN, T_EVENT, cstr)
from rx_i18n import _

KEEP = 5000                       # entries kept in memory (the panel's line limit is a separate setting)
LEVELS = ("info", "warn", "error")
WIRE_LEVEL = {EV_INFO: "info", EV_WARN: "warn", EV_ERROR: "error"}
# sender event texts (the E_* code from the event packet); {n} = pad number, {arg} = the packet argument
# (these are i18n keys - they stay in Polish and are translated through rx_i18n)
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
    t: float                      # time (time.time())
    level: str                    # info | warn | error
    text: str
    sender: bool = False          # came from the sender


_lock = threading.Lock()
_events = collections.deque(maxlen=KEEP)
_seq = 0
_seen = collections.deque(maxlen=128)      # sender event numbers (UDP repeats)


def add(level, text, sender=False, t=None):
    """Append an entry with the next sequence number (an unknown level becomes "info") and return it. Thread-safe."""
    global _seq
    with _lock:
        _seq += 1
        e = Event(_seq, time.time() if t is None else t, level if level in LEVELS else "info", text, sender)
        _events.append(e)
    return e


def info(text):
    """Add an info entry."""
    return add("info", text)


def warn(text):
    """Add a warning entry."""
    return add("warn", text)


def error(text):
    """Add an error entry."""
    return add("error", text)


def since(seq):
    """Entries newer than seq (oldest first); the window polls with the last number it has seen."""
    with _lock:
        if not _events or _events[-1].seq <= seq:
            return []
        return [e for e in _events if e.seq > seq]


def clear():
    """Drop all entries and the remembered sender event numbers."""
    with _lock:
        _events.clear()
        _seen.clear()


def visible(e, level):
    """Whether entry e passes the minimum-level filter."""
    return LEVELS.index(e.level) >= LEVELS.index(level)


def fmt(e):
    """'12:03:44  ⚠  text' (sender entries get a phone icon before the text) - the line used for copying."""
    mark = {"info": "·", "warn": "⚠", "error": "✖"}[e.level]
    return f"{time.strftime('%H:%M:%S', time.localtime(e.t))}  {mark}  " + ("📱 " if e.sender else "") + e.text


def pad_name(i):
    """Display name of pad slot i (slots are numbered from 0, names from 1)."""
    return _("Pad {n}").format(n=i + 1)


def on_sender_event(i, data, now=None):
    """Turn a sender event packet into an entry; a repeat (same event number) is dropped -> Event or None.

    The sender sends events with repeats because UDP can lose them, so the number is the dedup key. Known codes are
    rendered from SENDER_TEXT, unknown ones get a generic text. The packet carries the event's age, so the timestamp is
    moved back by it (events from before the receiver connected still land at the right time).
    """
    num, level, code, age, arg = BODY[T_EVENT].unpack_from(data, 4)
    if num in _seen:
        return None
    _seen.append(num)
    tpl = SENDER_TEXT.get(code)
    text = _(tpl).format(n=i + 1 if i != 255 else "?", arg=cstr(arg)) if tpl else _("Zdarzenie nadajnika {code}: {arg}").format(
        code=code, arg=cstr(arg))
    return add(WIRE_LEVEL.get(level, "info"), text, sender=True, t=(time.time() if now is None else now) - age)
