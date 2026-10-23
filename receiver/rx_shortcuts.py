"""Receiver: the list of all pad shortcuts built from the current settings - for the "Pad shortcuts" panel in the window (rx_qt_keys).

No Qt here: groups() assembles the groups from the configuration (C), the master settings (rx_settings) and the console
mode (rx_console), so the panel always shows what really works: the chosen console-mode button, the assigned master
actions, the long-PS action. Disabled shortcuts (ps_modifier = false, action "none", master claim off) are not shown.

Keys are a tuple of tokens: a pad icon name ("ps", "cross", "dpad_up"...), "+" or a short text ("3 s", "×2").
feature = a key from rx_settings.MASTER_FEATURES - when the feature is restricted to the master, the panel shows a crown.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import rx_console
import rx_master
import rx_settings
import rx_testmode
from rx_config import C
from rx_i18n import _

HOLD_ACTION: Final = {"gamebar": "Xbox Game Bar (Win+G)", "bigpicture": "Steam Big Picture"}
SHARE_ACTION: Final = {"screenshot": "Zrzut ekranu (Obrazy\\Zrzuty ekranu)", "capture": "Zrzut ekranu przez Game Bar"}


@dataclass(frozen=True, slots=True)
class Shortcut:
    """One row of the panel: the key tokens, the description and the master feature that can restrict it."""

    keys: tuple[str, ...]     # tokens: icon names, "+" or a short text
    text: str
    feature: str | None = None


@dataclass(frozen=True, slots=True)
class Group:
    """A titled group of shortcuts with an optional note under it."""

    title: str
    items: tuple[Shortcut, ...]
    note: str = ""


def _s(x: float) -> str:
    """Format seconds without a needless ",0": 1.0 -> "1 s", 1.2 -> "1,2 s"."""
    return f"{x:g} s".replace(".", ",")


def master_only(feature: str | None) -> bool:
    """Tell whether a shortcut is restricted to the master pad (a master is set and the feature is in the only_master list)."""
    m = rx_settings.cur.master
    return bool(feature and m.pad and feature in m.only_master)


def _ps() -> Group:
    """The PS-button group: short press, long-press action, PS + Share capture and the PS + D-pad combos.

    Collapses to a single row plus a note when ps_modifier is off.
    """
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


def _touchpad() -> Group:
    """The touchpad group: switching between mouse and DS4 touch, and the gestures of the mouse mode."""
    return Group(_("Touchpad"), (
        Shortcut(("ps", "+", "touchpad"), _("Przełącz: mysz ↔ dotyk DS4 (klik touchpada)"), "mouse"),
        Shortcut(("touchpad", _("stuknięcie")), _("Mysz: lewy przycisk")),
        Shortcut(("touchpad", _("2 palce")), _("Mysz: prawy przycisk (stuknięcie dwoma palcami)")),
        Shortcut(("touchpad", _("2 palce") + " ↕"), _("Mysz: przewijanie (ruch dwoma palcami)")),
        Shortcut(("touchpad", _("klik")), _("Mysz: lewy przycisk (fizyczny klik; 2 palce = prawy)")),
    ))


def _master() -> Group:
    """The master-pad group: the claim hold and every action that has a button assigned (a note appears when there are none)."""
    m = rx_settings.cur.master
    items: list[Shortcut] = []
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


def _console() -> Group:
    """The console-mode group: the toggle button pressed twice within double_click_ms."""
    cfg = rx_console.ctl.cfg if rx_console.ctl is not None else rx_console.ConsoleConfig()
    return Group(_("Tryb konsoli"), (
        Shortcut((cfg.toggle_button, "×2"),
                 _("Włącz / wyłącz: ekran i dźwięk na TV (dwa naciśnięcia w {ms} ms)").format(ms=cfg.double_click_ms),
                 "console"),
    ), _("Działa tylko na pulpicie, w Steam i launcherach (lista w Ustawieniach), nie w grze."))


def _tv() -> Group:
    """The TV-mode group: navigating the window with the pad."""
    return Group(_("Tryb TV (okno na pełnym ekranie i aktywne)"), (
        Shortcut(("dpad_up", "dpad_down", "dpad_left", "dpad_right"), _("Poruszanie się, suwaki, zakładki")),
        Shortcut(("cross",), _("Wybór / kliknięcie")),
        Shortcut(("circle",), _("Wstecz: zamknij panel, Ustawienia, wyjdź z pełnego ekranu")),
        Shortcut(("l1", "r1"), _("Poprzedni / następny pad")),
    ))


def _test() -> Group:
    """The Test-view group: the emergency exit combination and the TV-mode exit hold."""
    from rx_qt import EXIT_HOLD_S       # just a constant - rx_qt is already loaded whenever the panel exists
    return Group(_("Widok Test"), (
        Shortcut((rx_testmode.ESCAPE_KEYS[0], "+", rx_testmode.ESCAPE_KEYS[1]),
                 _("Wyjście awaryjne z testu (pad master; działa też przy schowanym oknie)")),
        Shortcut(("circle", _s(EXIT_HOLD_S)), _("Wyjście z testu w trybie TV (testowany pad)")),
    ), _("W widoku Test pozostałe skróty nie działają."))


def groups() -> list[Group]:
    """Return all groups in panel order; empty groups (no shortcuts and no note) are skipped."""
    return [g for g in (_ps(), _touchpad(), _master(), _console(), _tv(), _test()) if g.items or g.note]
