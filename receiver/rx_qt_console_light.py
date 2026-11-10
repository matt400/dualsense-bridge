"""Receiver window: the Lightbar and Wake-on-LAN tabs of the settings window."""
from __future__ import annotations

from typing import Any

from rx_i18n import _
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QHBoxLayout, QLayout, QLineEdit,
                               QWidget)

import rx_lightbar
import rx_settings
import rx_state as S
from rx_config import C
from rx_qt_icons import icon
from rx_qt_padscope import PadScope
from rx_qt_style import set_prop
from rx_qt_widgets import (Combo, button, field, group, icon_button, icon_label, input_items,
                           label, pair, spinbox)
from rx_settings import (DEFAULT_PAD, LIGHT_MODES, LOW_PCT, WOL_BUTTONS, WOL_HOLD_MS, LightbarConfig,
                         PadLight, WolConfig)
from rx_qt_console_util import TabBase, wrap


SCOPE_LIGHT = "scope_lightbar"       # receiver_ui.ini key: the entry the tab showed last
LUMA_DARK_TEXT = 380      # R + G + B above this (of 765) is a light colour: the button text turns black


class LightWolTabs(TabBase):
    """The Lightbar and Wake-on-LAN tabs of the settings window."""

    def _light_tab(self) -> list[QWidget | QLayout]:
        """The Lightbar tab: the low battery threshold and the per-pad colour / mode / blinking / game override."""
        self._lights: dict[str, PadLight] = {}
        self._color, self._light_key, self._loading = "", self._saved_scope(SCOPE_LIGHT), False
        self.low_pct = spinbox(*LOW_PCT, 1, " %")
        low = group(_("NISKA BATERIA"),
                    field(_("Próg niskiej baterii"), self.low_pct),
                    label(_("Ten sam próg ma ostrzeżenie na karcie pada i powiadomienie z zasobnika. Ładujący się pad "
                            "nie miga."), "dim", wrap=True))
        self.light_scope = PadScope()
        self.light_scope.changed.connect(self._light_scope_changed)
        self.light_scope.reset.connect(self._reset_light_pad)
        self.light_mode =Combo([(k, _(v), "lightbar") for k, v in LIGHT_MODES.items()])
        self.light_mode.changed.connect(self._light_edited)
        self.light_color = button("", small=True)
        self.light_color.setToolTip(_("Wybierz kolor"))
        self.light_color.clicked.connect(self._pick_color)
        reset_color = icon_button("reset", _("Kolor domyślny (idle_lightbar z receiver.toml)"), 16)
        reset_color.clicked.connect(lambda: self._set_color(""))
        colors = QHBoxLayout()
        colors.setSpacing(8)
        colors.addWidget(self.light_color, 1)
        colors.addWidget(reset_color)
        self.light_blink = QCheckBox(_("Miganie na czerwono przy niskiej baterii (także w grze)"))
        self.light_blink.toggled.connect(self._light_edited)
        self.light_game = QCheckBox(_("Gra może zmienić kolor (tylko w trakcie gry; wyłączone = zawsze ten kolor)"))
        self.light_game.toggled.connect(self._light_edited)
        pad = group(_("KOLOR LIGHTBARA"),
                    label(_("Poza grą (pulpit, Steam, launchery - lista w Trybie konsoli) zawsze świeci ten kolor. W grze "
                            "może go zastąpić kolor gry, chyba że wyłączysz „Gra może zmienić kolor”. Miganie przy niskiej "
                            "baterii działa zawsze, a potwierdzenia (np. złoty błysk mastera) są na wierzchu."), "dim", wrap=True),
                    pair(field(_("Tryb"), self.light_mode), field(_("Kolor"), wrap(colors))),
                    self.light_game, self.light_blink)
        return [group("", self.light_scope), pad, low]

    def _light_scope_changed(self, key: str) -> None:
        """The same for the Lightbar tab."""
        self._remember_scope(SCOPE_LIGHT, key)
        self._pick_light_pad(key)

    def _load_light(self, cfg: LightbarConfig) -> None:
        """Fill the Lightbar tab from a LightbarConfig.

        The pad list (connected pads, remembered ones) and the values of the entry that was shown last.
        """
        self.low_pct.setValue(cfg.low_pct)
        self._lights = dict(cfg.pads)
        # reopening the window keeps the entry that was picked last (it would look like its setting was lost otherwise)
        self.light_scope.set_pads(self._scope_items(self._lights), self._light_key)
        self._pick_light_pad(self.light_scope.key())

    def _pick_light_pad(self, key: str) -> None:
        """Show the lightbar settings of the chosen entry (a pad without its own one shows the defaults); loading is not an edit."""
        self._light_key = key
        light = self._lights.get(key) or self._lights.get(DEFAULT_PAD) or PadLight()
        self._loading = True
        self.light_mode.set_data(light.mode)
        self.light_blink.setChecked(light.blink)
        self.light_game.setChecked(light.game)
        self._set_color(light.color, store=False)
        self._loading = False
        self.light_scope.show_state(key in self._lights)
        self._light_enable()

    def _reset_light_pad(self) -> None:
        """Drop the chosen pad's own lightbar entry: it uses the defaults again."""
        key = self._light_key
        if key != DEFAULT_PAD:
            self._lights.pop(key, None)
            self._pick_light_pad(key)

    def _light_enable(self) -> None:
        """The colour button works only in the solid mode."""
        self.light_color.setEnabled(self.light_mode.currentData() == "solid")

    def _set_color(self, hex_color: str, store: bool = True) -> None:
        """Set the chosen colour (empty = the default) and paint the button with it; store=False skips recording the edit."""
        self._color = hex_color
        rgb = rx_lightbar.hex_rgb(hex_color) if hex_color else C.IDLE_RGB
        text = rx_lightbar.rgb_hex(rgb) + ("" if hex_color else _("  (domyślny)"))
        fg = "#000000" if sum(rgb) > LUMA_DARK_TEXT else "#ffffff"
        self.light_color.setText(text)
        self.light_color.setStyleSheet(f"background: {rx_lightbar.rgb_hex(rgb)}; color: {fg};")
        if store:
            self._light_edited()

    def _pick_color(self) -> None:
        """Open the colour dialog and apply the picked colour."""
        start = QColor(*(rx_lightbar.hex_rgb(self._color) if self._color else C.IDLE_RGB))
        c = QColorDialog.getColor(start, self, _("Kolor lightbara"))
        if c.isValid():
            self._set_color(c.name())

    def _light_edited(self, *_args: Any) -> None:
        """Record the edited fields under the current entry: for a pad without one this is the copy of the defaults plus the change."""
        if self._loading:
            return
        key = self._light_key
        self._lights[key] = PadLight(mode=self.light_mode.currentData(), color=self._color,
                                     blink=self.light_blink.isChecked(), game=self.light_game.isChecked())
        self.light_scope.show_state(True)
        self._light_enable()

    def _wol_tab(self) -> list[QWidget | QLayout]:
        """The Wake-on-LAN tab: enable, the pad combination and hold time, and the MAC / broadcast addresses."""
        self.wol_on = QCheckBox(_("Budzenie komputera z pada"))
        self.wol_on.setIcon(icon("wake"))
        self.wol_btn = Combo(input_items(WOL_BUTTONS, text=True))
        self.wol_hold = spinbox(*WOL_HOLD_MS, 100, " ms")
        combo = QHBoxLayout()
        combo.setSpacing(8)
        combo.addWidget(icon_label("ps"))
        combo.addWidget(label("+"))
        combo.addWidget(self.wol_btn, 1)
        self.wol_mac, self.wol_bcast = QLineEdit(), QLineEdit()
        self.wol_mac.setPlaceholderText("auto")
        self.wol_bcast.setPlaceholderText("auto")
        self.wol_seen = label("", "dim", wrap=True)
        how = group(_("WAKE-ON-LAN"),
                    label(_("Gdy komputer nie odpowiada (wyłączony / uśpiony), przytrzymanie tej kombinacji na padzie wysyła "
                            "z telefonu magic packet. Przy działającym komputerze kombinacja idzie do receivera jak zwykle "
                            "(np. pauza mastera), więc nic nie koliduje. Pad potwierdza wysłanie białym błyskiem, błąd - czerwonym."),
                          "dim", wrap=True),
                    self.wol_on,
                    pair(field(_("Kombinacja na padzie"), wrap(combo)), field(_("Przytrzymanie"), self.wol_hold)))
        addr = group(_("ADRESY"),
                     pair(field(_("MAC karty sieciowej komputera"), self.wol_mac), field(_("Adres broadcast"), self.wol_bcast)),
                     self.wol_seen,
                     label(_("Puste = automatycznie: nadajnik zapamiętuje MAC komputera z tablicy ARP telefonu, póki komputer "
                             "działa, i wysyła na broadcast swojej sieci. Komputer musi mieć włączony Wake-on-LAN w BIOS / UEFI "
                             "i we właściwościach karty sieciowej (najlepiej przewodowej)."), "dim", wrap=True))
        return [how, addr]

    def _load_wol(self, w: WolConfig) -> None:
        """Fill the Wake-on-LAN tab from a WolConfig."""
        self.wol_on.setChecked(w.enabled)
        self.wol_btn.set_data(w.button)
        self.wol_hold.setValue(w.hold_ms)
        self.wol_mac.setText(w.mac)
        self.wol_bcast.setText(w.bcast)
        self._wol_seen()

    def _wol_seen(self) -> None:
        """What the sender detected by itself (the build2 packet) - a hint in the empty fields."""
        seen = S.sender_wol
        mac, bcast = seen if seen else ("", "")
        self.wol_mac.setPlaceholderText(_("auto: {v}").format(v=mac) if mac else "auto")
        self.wol_bcast.setPlaceholderText(_("auto: {v}").format(v=bcast) if bcast else "auto")
        if seen is None:
            self.wol_seen.setText(_("Nadajnik nie podał jeszcze adresów (offline albo starsza wersja - Aktualizuj)."))
        else:
            self.wol_seen.setText(_("Nadajnik użyje: MAC {mac}, broadcast {bcast}.").format(
                mac=mac or _("nieznany - wpisz go albo uruchom receiver, gdy nadajnik działa"), bcast=bcast or "255.255.255.255"))
        set_prop(self.wol_seen, "level", "warn" if seen is not None and not mac else "")

    def _collect_wol(self) -> WolConfig:
        """-> WolConfig, or a ValueError naming the field."""
        try:
            mac = rx_settings.WolConfig.V["mac"](self.wol_mac.text().strip())
        except ValueError:
            raise ValueError(_("Zły adres MAC - wpisz np. aa:bb:cc:dd:ee:ff albo zostaw puste.")) from None
        try:
            bcast = rx_settings.WolConfig.V["bcast"](self.wol_bcast.text().strip())
        except ValueError:
            raise ValueError(_("Zły adres broadcast - wpisz np. 192.168.1.255 albo zostaw puste.")) from None
        return WolConfig(enabled=self.wol_on.isChecked(), mac=mac, bcast=bcast, button=self.wol_btn.currentData(),
                         hold_ms=self.wol_hold.value())
