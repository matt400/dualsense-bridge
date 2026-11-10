"""Receiver window: the Haptics and speaker tab of the settings window (per pad, with the speaker equalizer)."""
from __future__ import annotations

from typing import Any

from rx_i18n import _
from PySide6.QtWidgets import (QCheckBox, QLayout, QWidget)

import rx_eq
import rx_haptics
import rx_speaker
import rx_state as S
from rx_config import C
from rx_qt_padscope import PadScope
from rx_qt_widgets import (Combo, SliderRow, button, field, group, label, pair, spinbox)
from rx_settings import (DEFAULT_PAD, HAPTICS_GAIN, SPEAKER_GAIN, HapticsConfig, PadHaptics, PadSpeaker, SpeakerConfig)

from rx_qt_console_util import TabBase

SCOPE_HAPTICS = "scope_haptics"     # receiver_ui.ini key: the entry the tab showed last


class HapticsTab(TabBase):
    """The Haptics and speaker tab of the settings window (per pad, with the speaker equalizer)."""

    def _haptics_tab(self) -> list[QWidget | QLayout]:
        """The Haptics tab: game HD haptics and the pad's speaker per pad (opt-in), the strength / volume and the test buttons."""
        self._haptics: dict[str, PadHaptics] = {}
        self._haptics_key, self._haptics_loading = self._saved_scope(SCOPE_HAPTICS), False
        self._speaker: dict[str, PadSpeaker] = {}
        self.hap_scope = PadScope()
        self.hap_scope.changed.connect(self._haptics_scope_changed)
        self.hap_scope.reset.connect(self._reset_haptics_pad)
        self.hap_on = QCheckBox(_("Haptyka gry (HD haptics) na tym padzie"))
        self.hap_on.toggled.connect(self._haptics_edited)
        self.hap_gain = spinbox(*HAPTICS_GAIN, 5, " %")
        self.hap_gain.valueChanged.connect(self._haptics_edited)
        self.hap_test = button(_("Test haptyki"))
        self.hap_test.clicked.connect(self._test_haptics)
        self.hap_note = label("", "dim", wrap=True)
        self.spk_on = QCheckBox(_("Głośnik pada (dźwięk gry) na tym padzie"))
        self.spk_on.toggled.connect(self._haptics_edited)
        self.spk_gain = spinbox(*SPEAKER_GAIN, 5, " %")
        self.spk_gain.valueChanged.connect(self._haptics_edited)
        self.spk_test = button(_("Test głośnika"))
        self.spk_test.clicked.connect(self._test_speaker)
        self.spk_note = label("", "dim", wrap=True)
        self.eq_preset = Combo([("flat", _("Płaski")), ("voice", _("Głos")), ("bass", _("Bas")), ("custom", _("Własny"))])
        self.eq_preset.changed.connect(self._eq_preset_picked)
        self.eq_rows = [SliderRow(rx_eq.band_name(hz), *rx_eq.GAIN_DB, 1, fmt=rx_eq.db_text) for hz, _kind in rx_eq.BANDS]
        for row in self.eq_rows:
            row.changed.connect(self._eq_moved)
        box = group(_("HAPTYKA GRY"),
                    label(_("Gry z haptyką DualSense (dźwięk z kanałów 3-4) wysyłają ją do pada przez nadajnik. Wymaga "
                            "wirtualnego pada USB (profil composite) i nadajnika w wersji z haptyką. Jest wyłączona domyślnie: "
                            "pierwsze włączenie instaluje sterownik jądra (usbip-win2), a Windows dodaje urządzenie dźwiękowe "
                            "„Wireless Controller” (domyślne wyjście dźwięku zostaje bez zmian)."), "dim", wrap=True),
                    self.hap_on,
                    pair(field(_("Siła"), self.hap_gain), field(_("Próba"), self.hap_test)),
                    self.hap_note,
                    label(_("Zmiana włączenia działa po ponownym połączeniu pada; siła - od razu."), "dim", wrap=True))
        speaker = group(_("GŁOŚNIK PADA"),
                        label(_("Dźwięk gry z wyjścia „Wireless Controller” (kanały 1-2) gra na głośniku pada przez nadajnik "
                                "(Bluetooth, kodek Opus). Potrzebuje tego samego wirtualnego pada USB co haptyka, nadajnika w wersji "
                                "z głośnikiem i biblioteki libopus na nadajniku. Jest wyłączony domyślnie. Ustawienia pada wyżej "
                                "(własne lub domyślne) dotyczą też głośnika."), "dim", wrap=True),
                        self.spk_on,
                        pair(field(_("Głośność"), self.spk_gain), field(_("Próba"), self.spk_test)),
                        self.spk_note,
                        field(_("Korektor (equalizer)"), self.eq_preset),
                        *self.eq_rows,
                        label(_("Korektor działa na dźwięk gry przed wysłaniem na głośnik pada i ma to samo ustawienie "
                                "własne / domyślne co pad. Test głośnika gra z pokazanymi ustawieniami."), "dim", wrap=True),
                        label(_("Zmiana włączenia działa po ponownym połączeniu pada; głośność - od razu."), "dim", wrap=True))
        return [group("", self.hap_scope), box, speaker]

    def _haptics_scope_changed(self, key: str) -> None:
        """The user flipped "Default settings" or picked another pad on the Haptics tab: remember it and show that entry."""
        self._remember_scope(SCOPE_HAPTICS, key)
        self._pick_haptics_pad(key)

    def _load_haptics(self, cfg: HapticsConfig, speaker: SpeakerConfig) -> None:
        """Fill the Haptics tab: the pad list (connected, remembered) and the values of the entry that was shown last."""
        self._haptics = dict(cfg.pads)
        self._speaker = dict(speaker.pads)
        # reopening the window keeps the entry that was picked last (it would look like its setting was lost otherwise)
        self.hap_scope.set_pads(self._scope_items({**self._haptics, **self._speaker}), self._haptics_key)
        self._pick_haptics_pad(self.hap_scope.key())

    def _pick_haptics_pad(self, key: str) -> None:
        """Show the haptics settings of the chosen entry (a pad without its own one shows the defaults); loading is not an edit."""
        self._haptics_key = key
        cfg = self._haptics.get(key) or self._haptics.get(DEFAULT_PAD) or PadHaptics()
        spk = self._speaker.get(key) or self._speaker.get(DEFAULT_PAD) or PadSpeaker()
        self._haptics_loading = True
        self.hap_on.setChecked(cfg.enabled)
        self.hap_gain.setValue(cfg.gain)
        self.spk_on.setChecked(spk.enabled)
        self.spk_gain.setValue(spk.gain)
        self._show_eq(spk.eq)
        self._haptics_loading = False
        self.hap_scope.show_state(key in self._haptics or key in self._speaker)
        self._haptics_enable()

    def _reset_haptics_pad(self) -> None:
        """Drop the chosen pad's own haptics / speaker entry: it uses the defaults again."""
        key = self._haptics_key
        if key != DEFAULT_PAD:
            self._haptics.pop(key, None)
            self._speaker.pop(key, None)
            self._pick_haptics_pad(key)

    def _haptics_pad_slot(self) -> int | None:
        """The slot of the pad chosen in the combo (None for the default entry or a pad that is not connected)."""
        for i in range(C.NUM_PADS):
            if S.pad_id[i] and S.alive[i] and S.profile_key(i) == self._haptics_key:
                return i
        return None

    def _haptics_enable(self) -> None:
        """Enable the test buttons only for a connected pad; say when its sender is too old."""
        slot = self._haptics_pad_slot()
        self.hap_test.setEnabled(slot is not None)
        self.spk_test.setEnabled(slot is not None)
        old = slot is not None and not rx_haptics.sender_haptics(slot)
        self.hap_note.setText(_("Nadajnik tego pada jest w starszej wersji i nie przekaże haptyki (kliknij Aktualizuj).") if old else "")
        old = slot is not None and not rx_speaker.sender_speaker(slot)
        self.spk_note.setText(_("Nadajnik tego pada jest w starszej wersji i nie przekaże dźwięku na głośnik (kliknij Aktualizuj).")
                              if old else "")

    def _haptics_edited(self, *_args: Any) -> None:
        """Record the edited fields under the current entry: for a pad without one this is the copy of the defaults plus the change."""
        if self._haptics_loading:
            return
        key = self._haptics_key
        self._haptics[key] = PadHaptics(enabled=self.hap_on.isChecked(), gain=self.hap_gain.value())
        self._speaker[key] = PadSpeaker(enabled=self.spk_on.isChecked(), gain=self.spk_gain.value(), eq=self._eq_values())
        self.hap_scope.show_state(True)
        self._haptics_enable()

    def _eq_values(self) -> tuple[int, ...]:
        """The equalizer gains (dB per band) the sliders show."""
        return tuple(round(row.value()) for row in self.eq_rows)

    def _show_eq(self, gains: tuple[int, ...]) -> None:
        """Put gains on the sliders and the matching preset name in the list (no signals: loading is not an edit)."""
        for row, db in zip(self.eq_rows, gains, strict=True):
            row.set(db)
        self.eq_preset.set_data(rx_eq.preset_of(gains))

    def _eq_preset_picked(self, name: str) -> None:
        """A preset was chosen: its gains go to the sliders ("custom" keeps what the sliders show)."""
        if name in rx_eq.PRESETS:
            self._show_eq(rx_eq.PRESETS[name])
        self._haptics_edited()

    def _eq_moved(self, _value: float) -> None:
        """A slider was moved: the preset list follows (a preset whose numbers match, else "custom")."""
        self.eq_preset.set_data(rx_eq.preset_of(self._eq_values()))
        self._haptics_edited()

    def _test_haptics(self) -> None:
        """Send a short tone through the haptics path to the chosen pad (with the strength shown, not the saved one)."""
        slot = self._haptics_pad_slot()
        if slot is None:
            return
        why = rx_haptics.test(slot, self.hap_gain.value() / 100)
        self._status(why or _("Ton próbny wysłany do pada."), "warn" if why else "ok")

    def _test_speaker(self) -> None:
        """Send the test jingle through the speaker path to the chosen pad (with the volume shown, not the saved one)."""
        slot = self._haptics_pad_slot()
        if slot is None:
            return
        why = rx_speaker.test(slot, self.spk_gain.value() / 100, self._eq_values())
        self._status(why or _("Jingiel wysłany do pada."), "warn" if why else "ok")
