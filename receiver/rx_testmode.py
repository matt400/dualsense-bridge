"""Receiver: widok Test otwarty w oknie - skróty wszystkich padów wyłączone i awaryjne wyjście z pada.

Okno (wątek GUI) ustawia set_active(), gdy widok Test jest otwarty w aktywnym oknie. Wtedy żaden pad nie uruchamia
skrótów: PS + przycisk (rx_hotkeys), akcje i przejęcie mastera (rx_master), tryb konsoli (rx_console), PS + klik
touchpada. Po zmianie stanu sloty skrótów zaczynają od czystego stanu (sync() w wątku sieci), więc przycisk trzymany
przez granicę testu nie odpali niczego.

Jedyny wyjątek: PS + △ trzymane ESCAPE_S na padzie master (bez mastera w ustawieniach - na dowolnym padzie; master
ustawiony, ale nie podłączony - wg when_absent) od razu kończy test, nawet przy schowanym albo zablokowanym oknie, bez
myszy: zdejmuje blokadę wyjścia do gry, a przyciski i D-pad trzymane w tej chwili (PS, △ i reszta na testowanym
padzie) gra dostaje jako puszczone do ich puszczenia - pierwszy raport po teście nie niesie kombinacji; gałki i spusty
idą analogowo na żywo. Okno dostaje on_escape().

Pomiar czasu bez wątków i timerów: początek przytrzymania zapamiętany przy zmianie przycisków (on_state), sprawdzany
przy każdym stanie pada i co 100 ms w pętli sieci (tick) - przytrzymanie bez ruchu pada też się liczy.
"""
from collections.abc import Callable

import rx_events as ev
import rx_feedback as F
import rx_hotkeys as hotkeys
import rx_master as master
import rx_settings
import rx_state as S
from rx_buttons import BIT, BUTTON_CODES, PS_BIT, TRIGGERS, pressed_inputs
from rx_i18n import _

ESCAPE_S = 3.0
ESCAPE_MASK = PS_BIT | BIT[BUTTON_CODES["triangle"]]

active = False                                  # widok Test otwarty - skróty padów nie działają
escaped = False                                 # test zakończony z pada; rearm() przy następnym wejściu w Test
on_escape: Callable[[], None] | None = None     # okno: wyjdź z widoku Test (wołane z wątku sieci)
_gen = 0                                        # zmiana `active` - sloty skrótów od nowa (sync)
_seen: list[int] = []
_since: list[float | None] = []                 # od kiedy slot trzyma PS + △ (None = nie trzyma)


def init(n: int) -> None:
    global active, escaped
    active = escaped = False
    _seen[:] = [_gen] * n
    _since[:] = [None] * n


def _slot(i: int) -> int:
    """Indeks slotu; bez init() (np. moduł użyty przed startem okna) listy rosną do liczby slotów."""
    if i >= len(_since):
        n = max(i + 1, len(S.states or ()))
        _seen.extend([_gen] * (n - len(_seen)))
        _since.extend([None] * (n - len(_since)))
    return i


def set_active(on: bool) -> bool:
    """Wątek okna: widok Test otwarty / zamknięty. -> True, gdy się zmieniło."""
    global active, _gen
    on = bool(on)
    if on == active:
        return False
    active = on
    _gen += 1
    for i in range(len(_since)):
        _since[i] = None
    return True


def rearm() -> None:
    """Wątek okna: ponowne wejście w widok Test po wyjściu z pada."""
    global escaped
    escaped = False


def sync(i: int) -> None:
    """Wątek sieci, przed skrótami: po zmianie stanu testu sloty skrótów pada zaczynają od czystego stanu
    (bez trwających kombinacji, timerów przytrzymania i PS czekającego na impuls)."""
    if _seen[_slot(i)] != _gen:
        _seen[i] = _gen
        hotkeys.reset(i)
        master.reset(i)


def shortcuts_on() -> bool:
    return not active


def may_escape(i: int) -> bool:
    """Pad master; bez mastera w ustawieniach każdy pad; master niepodłączony - wg when_absent (jak rx_master)."""
    m = rx_settings.cur.master
    if not m.pad:
        return True
    ms = master.master_slot()
    return m.when_absent == "all" if ms is None else i == ms


def on_state(i: int, mask: int, now: float) -> bool:
    """Zmiana surowych przycisków slotu i (wątek sieci). -> True, gdy wyjście właśnie się wykonało."""
    if not active:
        return False
    _slot(i)
    if mask & ESCAPE_MASK != ESCAPE_MASK or not may_escape(i):
        _since[i] = None
        return False
    if _since[i] is None:
        _since[i] = now
    return _check(i, now)


def tick(now: float) -> bool:
    """Pętla sieci co 100 ms: przytrzymanie bez żadnej zmiany na padzie też się kończy po ESCAPE_S."""
    return active and any(_check(i, now) for i in range(len(_since)))


def held_s(i: int, now: float) -> float:
    """Ile sekund slot trzyma kombinację wyjścia (0 = nie trzyma) - podpowiedź w oknie."""
    t = _since[i] if active and i < len(_since) else None
    return 0.0 if t is None else max(0.0, now - t)


def _check(i: int, now: float) -> bool:
    t = _since[i]
    if t is None or now - t < ESCAPE_S:
        return False
    escape(i)
    return True


def escape(i: int) -> None:
    """Wymuszone wyjście z widoku Test przez pad i: koniec blokady, neutralne wejścia do puszczenia, okno wychodzi."""
    global active, escaped, _gen
    active, escaped = False, True
    _gen += 1
    for k in range(len(_since)):
        _since[k] = None
    for s in range(len(S.states)):
        if s == i or S.states[s]["blocked"]:
            _release(s)
    _seen[i] = _gen                     # slot i już wyczyszczony w _release - sync() nie zgubi połykania
    print(f"Controller {i}: PS + Triangle held {ESCAPE_S:g}s - test view closed, output to games restored")
    ev.info(_("{pad}: wyjście z widoku Test (PS + △)").format(pad=ev.pad_name(i)))
    F.pulse(i, "action")
    if (cb := on_escape) is not None:
        cb()


def _release(s: int) -> None:
    """Slot wraca do gry z neutralnym raportem: trzymane teraz wejścia są zużyte (gra dostaje je jako puszczone do
    puszczenia), dopiero potem zdjęta blokada - żaden raport do wirtualnego DS4 nie niesie PS + △."""
    hotkeys.reset(s)
    master.reset(s)
    with S.lock:
        st = S.states[s]
        master.slots[s].sup = frozenset(pressed_inputs(*st["raw"])) - set(TRIGGERS)   # spusty: analogowo na żywo
        st["fwd"] = hotkeys.forward(s, *st["raw"][:3])
    _seen[s] = _gen
    if not S.set_blocked(s, False):     # nie był zablokowany (master obok testowanego pada) - sam przelicz wyjście
        with S.lock:
            st = S.states[s]
            S.recompute(st, S.remap_maps[s])
            st["lat"] = st["blat"] = None
            st["ver"] += 1
        S.notify(s)
