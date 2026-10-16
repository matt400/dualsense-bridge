"""Receiver: widok Test otwarty w oknie - skróty wszystkich padów wyłączone i awaryjne wyjście z pada.

Okno (wątek GUI) ustawia set_active(), gdy widok Test jest otwarty w aktywnym oknie. Wtedy żaden pad nie uruchamia
skrótów: PS + przycisk (rx_hotkeys), akcje i przejęcie mastera (rx_master), tryb konsoli (rx_console), PS + klik
touchpada. Po zmianie stanu sloty skrótów zaczynają od czystego stanu (sync() w wątku sieci), więc przycisk trzymany
przez granicę testu nie odpali niczego.

Jedyny wyjątek: Options + Create naciśnięte razem na padzie master (bez mastera w ustawieniach - na dowolnym padzie; master
ustawiony, ale nie podłączony - wg when_absent) od razu kończy test, nawet przy schowanym albo zablokowanym oknie, bez
myszy: zdejmuje blokadę wyjścia do gry, a przyciski i D-pad trzymane w tej chwili (Options, Create i reszta na testowanym
padzie) gra dostaje jako puszczone do ich puszczenia - pierwszy raport po teście nie niesie kombinacji; gałki i spusty
idą analogowo na żywo. Okno dostaje on_escape().

Bez PS i bez przytrzymania: długie PS + △ wyłączało pada (Bluetooth). Liczy się zbocze - kombinacja trzymana już przy
wejściu w Test albo przez granicę testu musi zostać puszczona i naciśnięta od nowa.
"""
from collections.abc import Callable

import rx_events as ev
import rx_feedback as F
import rx_hotkeys as hotkeys
import rx_master as master
import rx_settings
import rx_state as S
from rx_buttons import BIT, BUTTON_CODES, TRIGGERS, pressed_inputs
from rx_i18n import _

ESCAPE_KEYS = ("options", "share")              # Options + Create (Share)
ESCAPE_MASK = BIT[BUTTON_CODES["options"]] | BIT[BUTTON_CODES["share"]]

active = False                                  # widok Test otwarty - skróty padów nie działają
escaped = False                                 # test zakończony z pada; rearm() przy następnym wejściu w Test
on_escape: Callable[[], None] | None = None     # okno: wyjdź z widoku Test (wołane z wątku sieci)
_gen = 0                                        # zmiana `active` - sloty skrótów od nowa (sync)
_seen: list[int] = []
_held: list[bool] = []                          # slot trzyma kombinację w ostatnim znanym stanie


def init(n: int) -> None:
    global active, escaped
    active = escaped = False
    _seen[:] = [_gen] * n
    _held[:] = [False] * n


def _slot(i: int) -> int:
    """Indeks slotu; bez init() (np. moduł użyty przed startem okna) listy rosną do liczby slotów."""
    if i >= len(_held):
        n = max(i + 1, len(S.states or ()))
        _seen.extend([_gen] * (n - len(_seen)))
        _held.extend(_now_held(k) for k in range(len(_held), n))
    return i


def _now_held(i: int) -> bool:
    """Kombinacja wciśnięta w ostatnim stanie slotu i (przed pierwszym pakietem: nie)."""
    states = S.states or ()
    return i < len(states) and states[i]["raw"][0] & ESCAPE_MASK == ESCAPE_MASK


def set_active(on: bool) -> bool:
    """Wątek okna: widok Test otwarty / zamknięty. -> True, gdy się zmieniło."""
    global active, _gen
    on = bool(on)
    if on == active:
        return False
    active = on
    _gen += 1
    _held[:] = map(_now_held, range(len(_held)))   # trzymane przez granicę testu - wychodzi dopiero nowe naciśnięcie
    return True


def rearm() -> None:
    """Wątek okna: ponowne wejście w widok Test po wyjściu z pada."""
    global escaped
    escaped = False


def sync(i: int) -> None:
    """Wątek sieci, przed skrótami: po zmianie stanu testu sloty skrótów pada zaczynają od czystego stanu
    (bez trwających kombinacji, timerów przytrzymania i PS czekającego na impuls) - liczonego od wejść trzymanych
    przed tą zmianą (S.states[i]["raw"] jest jeszcze poprzednim stanem), więc nowe naciśnięcie działa od razu."""
    if _seen[_slot(i)] != _gen:
        _seen[i] = _gen
        hotkeys.reset(i, keep=True)
        master.reset(i, keep=True)
        import rx_console                 # rx_console importuje rx_master - import tutaj
        rx_console.resync(i)


def shortcuts_on() -> bool:
    return not active


def may_escape(i: int) -> bool:
    """Pad master; bez mastera w ustawieniach każdy pad; master niepodłączony - wg when_absent (jak rx_master)."""
    m = rx_settings.cur.master
    if not m.pad:
        return True
    ms = master.master_slot()
    return m.when_absent == "all" if ms is None else i == ms


def on_state(i: int, raw: tuple) -> bool:
    """Zmiana surowych przycisków slotu i (wątek sieci), raw = (mask, hx, hy, lt, rt) jeszcze przed zapisem w stanie.
    -> True, gdy wyjście właśnie się wykonało."""
    if not active:
        return False
    _slot(i)
    held = raw[0] & ESCAPE_MASK == ESCAPE_MASK
    pressed, _held[i] = held and not _held[i], held
    if not pressed or not may_escape(i):
        return False
    escape(i, raw)
    return True


def escape(i: int, raw: tuple | None = None) -> None:
    """Wymuszone wyjście z widoku Test przez pad i: koniec blokady, neutralne wejścia do puszczenia, okno wychodzi.
    raw: stan pada i z pakietu, który odpalił wyjście - zapisany od razu, bo kombinacja jest w nim pierwszy raz
    (wcześniej stan w S.states był sprzed naciśnięcia i Options + Create nie byłyby połknięte)."""
    global active, escaped, _gen
    if raw is not None:
        with S.lock:
            S.states[i]["raw"] = raw
    active, escaped = False, True
    _gen += 1                           # _held: od nowa w set_active() przy następnym wejściu w Test
    for s in range(len(S.states)):
        if s == i or S.states[s]["blocked"]:
            _release(s)
    _seen[i] = _gen                     # slot i już wyczyszczony w _release - sync() nie zgubi połykania
    print(f"Controller {i}: Options + Create - test view closed, output to games restored")
    ev.info(_("{pad}: wyjście z widoku Test (Options + Create)").format(pad=ev.pad_name(i)))
    F.pulse(i, "action")
    if (cb := on_escape) is not None:
        cb()


def _release(s: int) -> None:
    """Slot wraca do gry z neutralnym raportem: trzymane teraz wejścia są zużyte (gra dostaje je jako puszczone do
    puszczenia), dopiero potem zdjęta blokada - żaden raport do wirtualnego DS4 nie niesie Options + Create."""
    import rx_console                   # rx_console importuje rx_master - import tutaj
    hotkeys.reset(s, keep=True)
    master.reset(s, keep=True)
    rx_console.resync(s)                # przycisk trybu trzymany przy wyjściu to nie nowe kliknięcie
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
