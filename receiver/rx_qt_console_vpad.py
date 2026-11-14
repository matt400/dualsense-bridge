"""Receiver window: the Pads tab of the settings window: virtual pad backend, HIDMaestro, fixed player order."""
from __future__ import annotations

import threading

from rx_i18n import _
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QLayout, QLineEdit,
                               QWidget)

import rx_ds4
import rx_hm
import rx_hm_update
import rx_winapi
from rx_config import C
from rx_qt_icons import icon
from rx_qt_style import set_prop
from rx_qt_widgets import (Combo, button, field, group, label, spinbox)
from rx_settings import (PADS_COUNT, SLOT_GRACE_MS, SLOT_MODES, VPAD_BACKENDS, UiConfig, VpadConfig)
from rx_qt_console_util import row_of, TabBase


class VpadTab(TabBase):
    """The Pads tab of the settings window: virtual pad backend, HIDMaestro, fixed player order."""

    def _vpad_tab(self) -> list[QWidget | QLayout]:
        """The Pads tab: the backend, HIDMaestro download / path, the current state and the fixed player order."""
        self.vpad_backend = Combo([(k, _(v)) for k, v in VPAD_BACKENDS.items()])
        self.vpad_backend.changed.connect(lambda _v: self._vpad_state())
        self.vpad_dll = QLineEdit()
        self.vpad_dll.setPlaceholderText(_("automatycznie"))
        self.vpad_now = label("", "dim", wrap=True)
        self.vpad_admin = button(_("Uruchom ponownie jako administrator"), small=True)
        self.vpad_admin.setIcon(icon("shield"))
        self.vpad_admin.clicked.connect(self.elevate.emit)
        self.vpad_fixed = QCheckBox(_("Stała kolejność graczy"))
        self.vpad_mode = Combo([(k, _(v)) for k, v in SLOT_MODES.items()])
        self.vpad_grace = spinbox(*SLOT_GRACE_MS, 100, " ms")
        self.vpad_mode.changed.connect(lambda _v: self._vpad_state())
        self.vpad_fixed.toggled.connect(lambda _on: self._vpad_state())
        self.vpad_mirror = QCheckBox(_("Wirtualny pad taki jak fizyczny (DualShock 4 → DS4, Xbox → Xbox 360)"))
        self.vpad_auto = QCheckBox(_("Pobieraj HIDMaestro automatycznie (GitHub, przypięta wersja)"))
        self.vpad_auto.toggled.connect(lambda _on: self._vpad_state())
        self.vpad_check = button(_("Sprawdź teraz"), small=True)
        self.vpad_check.setIcon(icon("update"))
        self.vpad_check.clicked.connect(lambda: self._hm_check())
        self.vpad_net = button(_("Zainstaluj .NET 10"), small=True)
        self.vpad_net.setIcon(icon("update"))
        self.vpad_net.clicked.connect(self._net_install)
        self._net_busy = False
        self._net_msg = ""
        self._audio_gen = 0         # the newest audio listing; an older one that finishes late is dropped
        self.outputs_listed.connect(self._outputs_ready)
        self._hm_timer = QTimer(self, interval=500)
        self._hm_timer.timeout.connect(self._vpad_state)
        backend = group(_("WIRTUALNE PADY"),
                        label(_("HIDMaestro tworzy natywny DualSense (gry widzą pada PS5) sterownikiem w trybie użytkownika - "
                                "jest rozwijany. Receiver pobiera go sam i uruchamia się jako administrator (tego wymaga "
                                "tworzenie padów). ViGEmBus (DualShock 4) nie jest już rozwijany, zostaje jako zapas."),
                              "dim", wrap=True),
                        field(_("Backend"), self.vpad_backend),
                        self.vpad_auto,
                        field(_("Własna HIDMaestro.Core.dll (plik albo folder; puste = pobrana automatycznie)"), self.vpad_dll),
                        self.vpad_now, row_of(self.vpad_check, self.vpad_net, self.vpad_admin))
        self.pads_mode = Combo([("dynamic", _("Dynamiczna: tylko podłączone pady")), ("fixed", _("Stała: zawsze te same sloty"))])
        self.pads_mode.changed.connect(lambda _v: self._pads_view_state())
        self.pads_count = spinbox(PADS_COUNT[0], min(PADS_COUNT[1], C.NUM_PADS))
        self.pads_count_row = field(_("Liczba padów, które są zawsze widoczne"), self.pads_count)
        shown = group(_("PADY W OKNIE"),
                      label(_("Dynamiczna: kolumna po lewej pokazuje tylko podłączone pady. Stała: zawsze widać pierwsze pady "
                              "z listy (także rozłączone); podłączony pad spoza tej liczby też się pojawia. Działa od razu po "
                              "zapisaniu."), "dim", wrap=True),
                      field(_("Liczba padów"), self.pads_mode),
                      self.pads_count_row)
        if PADS_COUNT[1] > C.NUM_PADS:
            shown.layout().addWidget(label(_("Liczba slotów w receiverze: {n} (opcja pads w receiver.toml albo --pads) - "
                                             "więcej wymaga zmiany tej wartości i restartu.").format(n=C.NUM_PADS), "dim", wrap=True))
        kind = group(_("RODZAJ PADA W GRZE"),
                     self.vpad_mirror,
                     label(_("Włączone: pad DualSense widać w grze jako DualSense, DualShock 4 jako DS4, a pady Xbox (i inne, np. "
                             "Switch Pro) jako Xbox 360 - gra pokazuje właściwe ikony przycisków. Xbox 360 i DS4 działają przez "
                             "ViGEmBus; bez niego taki pad jest widoczny jako DualSense. Wyłączone: zawsze DualSense."),
                           "dim", wrap=True))
        order = group(_("KOLEJNOŚĆ GRACZY"),
                      self.vpad_fixed,
                      field(_("Sposób"), self.vpad_mode),
                      field(_("Czekanie na pad 1 przed padem zastępczym"), self.vpad_grace),
                      label(_("Gry i Steam numerują pady wg kolejności podłączenia. Gdy pad 2 włączy się pierwszy, "
                              "czeka ~1 s na pad 1: pady włączone razem powstają po kolei bez żadnego dodatkowego urządzenia "
                              "(Steam Input nic nie widzi). Dopiero gdy pad 1 jest spóźniony, slot 1 dostaje pad zastępczy, "
                              "który pad 1 potem przejmuje. Tryb „czekaj” nie tworzy zastępczego, ale pad 2 może wtedy "
                              "zostać graczem 1. Który fizyczny pad to pad 1 / 2, pamięta nadajnik (slots.json, wg MAC)."),
                            "dim", wrap=True))
        return [shown, backend, kind, order]

    def _load_vpad(self, v: VpadConfig) -> None:
        """Fill the Pads tab from a VpadConfig."""
        self.vpad_backend.set_data(v.backend)
        self.vpad_dll.setText(v.hm_dll)
        self.vpad_fixed.setChecked(v.fixed_slots)
        self.vpad_mode.set_data(v.slot_mode)
        self.vpad_grace.setValue(v.slot_grace_ms)
        self.vpad_mirror.setChecked(v.mirror)
        self.vpad_auto.setChecked(v.hm_auto)
        self._vpad_state()

    def _load_pads_view(self, ui: UiConfig) -> None:
        """Fill the "pads in the window" group from the UI settings (the count field only matters for the fixed mode)."""
        self.pads_mode.set_data(ui.pads_mode)
        self.pads_count.setValue(ui.pads_count)
        self._pads_view_state()

    def _pads_view_state(self) -> None:
        """The number of pads is asked for only in the fixed mode."""
        self.pads_count_row.setVisible(self.pads_mode.currentData() == "fixed")

    def _hm_check(self) -> None:
        """Download the pinned HIDMaestro in the background if it is not on disk (minutes); the state is refreshed every 0.5 s."""
        threading.Thread(target=rx_hm_update.check, name="hidmaestro check", daemon=True).start()
        self._hm_timer.start()
        self._vpad_state(busy=True)

    def _net_install(self) -> None:
        """.NET 10 through winget in the background (several minutes); after the install HIDMaestro works from the next pad connection."""

        def run() -> None:
            ok, msg = rx_hm.install_dotnet()
            self._net_msg = _("✓ .NET 10 zainstalowany - HIDMaestro zadziała przy następnym połączeniu pada") if ok \
                else _("Instalacja .NET 10 nie udała się: {err}").format(err=msg)
            self._net_busy = False
        self._net_busy, self._net_msg = True, ""
        threading.Thread(target=run, name="dotnet install", daemon=True).start()
        self._hm_timer.start()
        self._vpad_state()

    def _vpad_state(self, busy: bool = False) -> None:
        """What works now: the kind of plugged pads.

        The HIDMaestro version (downloaded / newest / downloading / error), the reason for working without HIDMaestro, the
        restart-as-administrator button.
        """
        backend = self.vpad_backend.currentData()
        fixed = self.vpad_fixed.isChecked()
        self.vpad_mode.setEnabled(fixed)
        self.vpad_grace.setEnabled(fixed and self.vpad_mode.currentData() == "placeholder")
        used = sorted({rx_ds4.LABELS[k] for k in rx_ds4.kinds if k})
        lines = [_("Teraz: {kinds}").format(kinds=", ".join(used)) if used else _("Teraz: żaden wirtualny pad nie jest podłączony")]
        st = rx_hm_update.refresh_status() if not rx_hm_update.status.busy else rx_hm_update.status
        busy = busy or st.busy
        lines += self._hm_download_lines(st, busy)
        need_net = backend != "vigem" and rx_hm.need_dotnet()
        lines += self._net_lines(need_net)
        self.vpad_net.setVisible(need_net or self._net_busy)
        self.vpad_net.setEnabled(not self._net_busy)
        if not busy and not self._net_busy:
            self._hm_timer.stop()
        self.sender_cfg.stop()
        self.vpad_check.setEnabled(not busy and backend != "vigem")
        if backend != "vigem" and rx_ds4.hm_note:
            lines.append(_("HIDMaestro nieużywany: {why}").format(why=rx_ds4.hm_note))
        need_admin = backend != "vigem" and not rx_winapi.is_admin()
        if need_admin:
            lines.append(_("Receiver działa bez uprawnień administratora - HIDMaestro nie utworzy padów."))
        self.vpad_now.setText("\n".join(lines))
        set_prop(self.vpad_now, "level", "warn" if need_admin or (backend == "hidmaestro" and rx_ds4.hm_note) else "")
        self.vpad_admin.setVisible(need_admin)

    @staticmethod
    def _hm_download_lines(st: rx_hm_update.Status, busy: bool) -> list[str]:
        """The HIDMaestro download state as status lines: checking / downloading, the downloaded version, the last error."""
        lines = []
        if busy:
            lines.append(_("HIDMaestro: sprawdzanie / pobieranie…"))
        elif st.version:
            newer = st.latest and rx_hm_update.version_tuple(st.latest) > rx_hm_update.version_tuple(st.version)
            lines.append(_("HIDMaestro {v} pobrany").format(v=st.version)
                         + (_(" (przypięta wersja: {v})").format(v=st.latest) if newer else ""))
        if st.error and not busy:
            lines.append(_("Pobieranie HIDMaestro: {err}").format(err=st.error))
        return lines

    def _net_lines(self, need_net: bool) -> list[str]:
        """The .NET 10 line: installing now, the result of the last install, or that it is missing."""
        if self._net_busy:
            return [_("Instalowanie .NET 10 (winget)…")]
        if self._net_msg:
            return [self._net_msg]
        return [_("Brakuje .NET 10 - HIDMaestro go wymaga.")] if need_net else []

    def _collect_vpad(self) -> VpadConfig:
        """-> the VpadConfig described by the Pads tab."""
        return VpadConfig(backend=self.vpad_backend.currentData(), fixed_slots=self.vpad_fixed.isChecked(),
                          slot_mode=self.vpad_mode.currentData(), slot_grace_ms=self.vpad_grace.value(),
                          hm_dll=self.vpad_dll.text().strip(), hm_auto=self.vpad_auto.isChecked(), mirror=self.vpad_mirror.isChecked())
