"""Receiver: lista wszystkich skrótów pada z bieżących ustawień - dla panelu „Skróty pada” w oknie (rx_qt_keys).

Bez Qt: groups() składa grupy z konfiguracji (C), ustawień mastera (rx_settings) i trybu konsoli (rx_console), więc
panel zawsze pokazuje to, co naprawdę działa: wybrany przycisk trybu konsoli, przypisane akcje mastera, akcję
długiego PS. Wyłączone skróty (ps_modifier = false, akcja "none", przejęcie mastera wyłączone) nie są pokazywane.

Klawisze to krotka tokenów: nazwa ikony pada ("ps", "cross", "dpad_up"...), "+" albo krótki tekst ("3 s", "×2").
feature = klucz z rx_settings.MASTER_FEATURES - przy zastrzeżeniu dla mastera panel pokazuje koronę.
"""
from dataclasses import dataclass

import rx_console
import rx_master
import rx_settings
import rx_testmode
from rx_config import C
from rx_i18n import _

HOLD_ACTION = {"gamebar": "Xbox Game Bar (Win+G)", "bigpicture": "Steam Big Picture"}
SHARE_ACTION = {"screenshot": "Zrzut ekranu (Obrazy\\Zrzuty ekranu)", "capture": "Zrzut ekranu przez Game Bar"}


@dataclass(frozen=True, slots=True)
class Shortcut:
    keys: tuple[str, ...]
    text: str
    feature: str | None = None


@dataclass(frozen=True, slots=True)
class Group:
    title: str
    items: tuple[Shortcut, ...]
    note: str = ""


def _s(x):
    """Sekundy bez zbędnego ",0": 1.0 -> "1 s", 1.2 -> "1,2 s"."""
    return f"{x:g} s".replace(".", ",")


def master_only(feature):
    """Skrót zastrzeżony dla pada master (master ustawiony i funkcja na liście only_master)."""
    m = rx_settings.cur.master
    return bool(feature and m.pad and feature in m.only_master)


def _ps():
    if not C.PS_MODIFIER:
        return Group(_("Skróty z PS"), (Shortcut(("ps",), _("PS dla gry i Steam (od razu)"), "ps_game"),),
                     _("Skróty z PS są wyłączone (ps_modifier = false)."))
    items = [Shortcut(("ps",), _("Krótko: PS dla gry i Steam (po puszczeniu)"), "ps_game")]
    if hold := HOLD_ACTION.get(C.PS_HOLD_ACTION):
        items.append(Shortcut(("ps", _s(C.PS_HOLD_S)), _(hold), "ps_hold"))
    if share := SHARE_ACTION.get(C.PS_SHARE_ACTION):
        items.append(Shortcut(("ps", "+", "share"), _(share), "screenshot"))
    items += [
        Shortcut(("ps", "+", "dpad_up"), _("Czułość myszy touchpada +15 %"), "sens"),
        Shortcut(("ps", "+", "dpad_down"), _("Czułość myszy touchpada −15 %"), "sens"),
        Shortcut(("ps", "+", "dpad_left"), _("Poprzedni profil pada"), "profile"),
        Shortcut(("ps", "+", "dpad_right"), _("Następny profil pada"), "profile"),
    ]
    return Group(_("Skróty z PS"), tuple(items))


def _touchpad():
    return Group(_("Touchpad"), (
        Shortcut(("ps", "+", "touchpad"), _("Przełącz: mysz ↔ dotyk DS4 (klik touchpada)"), "mouse"),
        Shortcut(("touchpad", _("stuknięcie")), _("Mysz: lewy przycisk")),
        Shortcut(("touchpad", _("2 palce")), _("Mysz: prawy przycisk (stuknięcie dwoma palcami)")),
        Shortcut(("touchpad", _("2 palce") + " ↕"), _("Mysz: przewijanie (ruch dwoma palcami)")),
        Shortcut(("touchpad", _("klik")), _("Mysz: lewy przycisk (fizyczny klik; 2 palce = prawy)")),
    ))


def _master():
    m = rx_settings.cur.master
    items = []
    if m.claim_hold:
        items.append(Shortcut(("ps", "+", rx_master.CLAIM_BUTTON, _s(rx_master.CLAIM_S)),
                              _("Ten pad zostaje masterem (mignie na złoto)")))
    for action, button in m.actions.items():
        if button:
            text, hold = rx_settings.MASTER_ACTIONS[action]
            keys = ("ps", "+", button, _s(rx_master.HOLD_S)) if hold else ("ps", "+", button)
            items.append(Shortcut(keys, _(text), "actions"))
    return Group(_("Pad master"), tuple(items),
                 "" if items else _("Brak przypisanych akcji (Ustawienia → Master)."))


def _console():
    cfg = rx_console.ctl.cfg if rx_console.ctl is not None else rx_console.ConsoleConfig()
    return Group(_("Tryb konsoli"), (
        Shortcut((cfg.toggle_button, "×2"),
                 _("Włącz / wyłącz: ekran i dźwięk na TV (dwa naciśnięcia w {ms} ms)").format(ms=cfg.double_click_ms),
                 "console"),
    ), _("Działa tylko na pulpicie, w Steam i launcherach (lista w Ustawieniach), nie w grze."))


def _tv():
    return Group(_("Tryb TV (okno na pełnym ekranie i aktywne)"), (
        Shortcut(("dpad_up", "dpad_down", "dpad_left", "dpad_right"), _("Poruszanie się, suwaki, zakładki")),
        Shortcut(("cross",), _("Wybór / kliknięcie")),
        Shortcut(("circle",), _("Wstecz: zamknij panel, Ustawienia, wyjdź z pełnego ekranu")),
        Shortcut(("l1", "r1"), _("Poprzedni / następny pad")),
    ))


def _test():
    from rx_qt import EXIT_HOLD_S       # tylko stała - rx_qt jest już załadowany, gdy panel istnieje
    return Group(_("Widok Test"), (
        Shortcut((rx_testmode.ESCAPE_KEYS[0], "+", rx_testmode.ESCAPE_KEYS[1]),
                 _("Wyjście awaryjne z testu (pad master; działa też przy schowanym oknie)")),
        Shortcut(("circle", _s(EXIT_HOLD_S)), _("Wyjście z testu w trybie TV (testowany pad)")),
    ), _("W widoku Test pozostałe skróty nie działają."))


def groups():
    """Wszystkie grupy w kolejności panelu; puste grupy (bez skrótów i bez uwagi) pominięte."""
    return [g for g in (_ps(), _touchpad(), _master(), _console(), _tv(), _test()) if g.items or g.note]
