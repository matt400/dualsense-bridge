"""Receiver: the window's right column panels - profile choice, the editor of the selected pad part, gyro and the list of changes.

On top only what changes most often (what a button does, where a stick comes from, trigger resistance); response tuning
(dead zones, curves) is in the collapsible "Advanced" section, opened by itself when something in it was changed.

The panels read and change the Editor (rx_editor) and tell the window with the `edited` signal that the profile changed
(the window refreshes the badges and the status, and sends the profile to the pad after a short delay).
"""
from rx_i18n import _
from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QCompleter, QFrame, QHBoxLayout, QLineEdit, QScrollArea, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

import rx_buttons as B
import rx_status as ST
from proto import RFX_GAIN_MAX, TRIG_AMP_MAX, TRIG_CLICK_MAX, TRIG_CLICK_MIN, TRIG_FORCE_MAX, TRIG_FREQ_MAX, TRIG_START_MAX
from rx_shape import RUMBLE_LIMITS, needs_fx
from rx_editor import GYRO_MODES, L3_OF, SHORT, STICK_NAME, STICK_OF
from rx_qt_style import set_prop
from rx_qt_icons import pixmap
from rx_qt_gstick import TiltSection
from rx_gstick_io import runner
from rx_qt_widgets import (Collapsible, Combo, ConfirmButton, SliderRow, button, field, icon_button, input_items, label,
                           left)

NONE = "__none"
# trigger effect mode -> (name, tooltip); "inherit" only in a game profile
FX_MODES = {
    "inherit": ("Jak w profilu pada", "W tej grze spust działa tak, jak ustawiono w profilu domyślnym pada."),
    "off": ("Wyłączony", "Spust bez efektu."),
    "res": ("Opór", "Stały opór od wybranego miejsca skoku, np. gaz i hamulec w wyścigach."),
    "click": ("Klik", "Opór narasta do wybranego punktu i puszcza z kliknięciem - jak spust broni."),
    "vib": ("Drgania", "Spust drga stale od wybranego miejsca skoku."),
}
FX_SLIDERS = {"res": ("start", "force"), "click": ("pos", "force"), "vib": ("start", "amp", "freq")}
RUMBLE_ON = {"inherit": "Jak w profilu pada", "on": "Włączona", "off": "Wyłączona"}
RUMBLE_KEY = {None: "inherit", True: "on", False: "off"}


def _of8(v) -> str:
    """Slider text for a value out of TRIG_FORCE_MAX, e.g. "5 / 8"."""
    return f"{round(v)} / {TRIG_FORCE_MAX}"
def targets():
    """Choices of what a button can act as: every pad input (icon + name) and \"disabled\"."""
    return [*input_items(B.INPUTS, text=True), (NONE, _("— wyłączony —"))]   # icon + name: the choice is visible at once


def r3(v):
    """Round to 3 decimals (profile values are stored that way)."""
    return round(v, 3)


def _vbox(parent=None, spacing=10):
    """A vertical layout without margins (on `parent` when given)."""
    lay = QVBoxLayout(parent)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


def scrolled(widget):
    """A panel in vertical scrolling (no frame) - the content may be taller than the window."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if widget.layout():
        widget.layout().setContentsMargins(0, 12, 6, 0)      # a gap under the tabs and room for the scroll bar
    sa.setWidget(widget)
    return sa


class ProfileBar(QWidget):
    """The pad's profile list (default + games), a new game profile and deleting."""

    selected = Signal(str)            # the key of the chosen profile
    create = Signal(str, bool)        # (program name, start from the current settings)
    delete = Signal()

    def __init__(self) -> None:
        """Build the profile combo with the add / delete buttons, an info line and the hidden new-game-profile form."""
        super().__init__()
        self._sig = None
        self.recent, self.existing = [], set()
        lay = _vbox(self, 6)
        row = QHBoxLayout()
        self.combo = Combo()
        self.combo.changed.connect(lambda key: self.selected.emit(key))
        self.combo.setToolTip(_("● = profil teraz w użyciu"))
        self.add = icon_button("plus", _("Nowy profil dla gry"), 18)
        self.add.clicked.connect(self._open_new)
        self.remove = ConfirmButton(_("Usuń"), _("Usunąć?"), small=True)
        self.remove.confirmed.connect(self.delete)
        row.addWidget(self.combo, 1)
        row.addWidget(self.add)
        row.addWidget(self.remove)
        lay.addLayout(row)
        self.info = label("", "dim", wrap=True)
        lay.addWidget(self.info)

        self.form = QFrame()
        self.form.setObjectName("row")
        fl = QVBoxLayout(self.form)
        fl.setContentsMargins(10, 8, 10, 10)
        fl.setSpacing(6)
        self.exe = QLineEdit()
        self.exe.setPlaceholderText("eldenring.exe")
        self.exe.setToolTip(_("Nazwa programu jak w Menedżerze zadań → Szczegóły"))
        self.completer_model = QStringListModel()
        completer = QCompleter(self.completer_model, self.exe)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.exe.setCompleter(completer)
        self.exe.returnPressed.connect(self._create)
        self.copy = QCheckBox(_("Zacznij od bieżących ustawień"))
        self.copy.setChecked(True)
        ok, cancel = button(_("Utwórz"), primary=True, small=True), button(_("Anuluj"), small=True)
        ok.clicked.connect(self._create)
        cancel.clicked.connect(self.close_new)
        btns = QHBoxLayout()
        btns.addWidget(ok)
        btns.addWidget(cancel)
        btns.addStretch(1)
        fl.addWidget(label(_("Program gry (jak w Menedżerze zadań → Szczegóły)"), "dim"))
        fl.addWidget(self.exe)
        fl.addWidget(self.copy)
        fl.addLayout(btns)
        self.form.hide()
        lay.addWidget(self.form)

    def reload(self, profiles, key, info, recent) -> None:
        """profiles = [(key, name, in use)]; the list is rebuilt only when it changed (an open one is not closed)."""
        sig = tuple(profiles)
        if sig != self._sig:
            self._sig = sig
            self.combo.set_items([(k, n + ("  ●" if use else "")) for k, n, use in profiles])
        self.combo.set_data(key)
        self.remove.setVisible(B.is_game_key(key))
        self.info.setText(info)
        self.existing = {n for k, n, _ in profiles if B.is_game_key(k)}
        if recent != self.recent:
            self.recent = recent
            self.completer_model.setStringList(recent)

    def _open_new(self) -> None:
        """Show the new profile form, pre-filled with the most recent program that has no profile yet."""
        self.exe.setText(next((x for x in self.recent if x not in self.existing), ""))
        self.form.show()
        self.exe.setFocus()
        self.exe.selectAll()

    def _create(self) -> None:
        """Emit `create` with the typed program name and the copy-current-settings flag."""
        self.create.emit(self.exe.text(), self.copy.isChecked())

    def close_new(self) -> None:
        """Hide the new profile form."""
        self.form.hide()


class PartPage(QWidget):
    """The editor of the selected pad part: a button / D-pad direction, a trigger (with a curve) or a stick (swap, inversion, curve)."""

    edited = Signal()

    def __init__(self, ed) -> None:
        """Stack three pages: a hint (nothing selected), a button / trigger page and a stick page."""
        super().__init__()
        self.ed, self.name = ed, None
        self.stack = QStackedWidget()
        _vbox(self).addWidget(self.stack)
        self.stack.addWidget(self._hint_page())
        self.stack.addWidget(self._button_page())
        self.stack.addWidget(self._stick_page())

    # ---------- building ----------
    @staticmethod
    def _hint_page():
        """The page shown while no part is selected."""
        w = QWidget()
        lay = _vbox(w)
        lay.addWidget(label(_("Kliknij przycisk, spust albo gałkę na rysunku pada, aby ją zremapować lub dostroić."), "dim", wrap=True))
        lay.addStretch(1)
        return scrolled(w)

    def _button_page(self):
        """The page for a button or trigger: the target combo and reset; for L2 / R2 also the trigger effect, game rumble and the response
        tuning."""
        w = QWidget()
        lay = _vbox(w)
        head = QHBoxLayout()
        self.title_icon = label("")
        self.title = label("", "h3")
        head.addWidget(self.title_icon)
        head.addWidget(self.title, 1)
        lay.addLayout(head)
        self.dst = Combo(targets())
        self.dst.changed.connect(lambda v: self._set_button(self.name, v))
        lay.addWidget(field(_("Działa jak"), self.dst))
        self.dflt = button(_("Domyślnie"), small=True)
        self.dflt.clicked.connect(lambda: self._reset_button(self.name))
        lay.addWidget(self.dflt, 0, Qt.AlignmentFlag.AlignLeft)

        self.trig = QWidget()                        # L2 / R2 only: the trigger effect, game rumble and the (collapsed) response
        tl = _vbox(self.trig)
        tl.addSpacing(4)
        tl.addWidget(label(_("EFEKT SPUSTU"), "h2"))
        self.fx_mode = Combo()
        self.fx_mode.changed.connect(lambda v: self._trigger_fx(mode=v))
        tl.addWidget(field(_("Efekt"), self.fx_mode))
        self.fx_hint = label("", "dim", wrap=True)
        tl.addWidget(self.fx_hint)
        self.fx = {
            "start": SliderRow(_("Od (skok spustu)"), 0, TRIG_START_MAX * 10, 10, "%"),
            "pos": SliderRow(_("Punkt kliku (skok spustu)"), TRIG_CLICK_MIN * 10, TRIG_CLICK_MAX * 10, 10, "%"),
            "force": SliderRow(_("Siła"), 1, TRIG_FORCE_MAX, 1, fmt=_of8),
            "amp": SliderRow(_("Siła drgań"), 1, TRIG_AMP_MAX, 1, fmt=_of8),
            "freq": SliderRow(_("Częstotliwość drgań"), 1, TRIG_FREQ_MAX, 1, fmt=lambda v: f"{round(v)} Hz"),
        }
        for k, row in self.fx.items():
            scale = 10 if k in ("start", "pos") else 1
            row.changed.connect(lambda v, k=k, sc=scale: self._trigger_fx(**{k: round(v / sc)}))
            tl.addWidget(row)
        tl.addWidget(label(_("Gra przez DS4 nie ustawi efektów DualSense, więc robi to nadajnik. W profilu gry "
                             "„Jak w profilu pada” bierze ustawienie z profilu domyślnego."), "dim", wrap=True))

        tl.addSpacing(4)
        tl.addWidget(label(_("WIBRACJA Z GRY NA SPUSTACH"), "h2"))
        self.rv_on = Combo()
        self.rv_on.changed.connect(lambda v: self._rumble_fx(on={"inherit": None, "on": True, "off": False}[v]))
        tl.addWidget(field(_("Wibracja z gry"), self.rv_on))
        gain_lo, _hi, _d = RUMBLE_LIMITS["gain"]
        self.rv_gain = SliderRow(_("Siła (względem wibracji pada)"), gain_lo, RFX_GAIN_MAX, 10, "%")
        self.rv_freq = SliderRow(_("Częstotliwość drgań"), 1, TRIG_FREQ_MAX, 1, fmt=lambda v: f"{round(v)} Hz")
        self.rv_gain.changed.connect(lambda v: self._rumble_fx(gain=round(v)))
        self.rv_freq.changed.connect(lambda v: self._rumble_fx(freq=round(v)))
        tl.addWidget(self.rv_gain)
        tl.addWidget(self.rv_freq)
        tl.addWidget(label(_("Namiastka wibracji spustów: L2 drga jak duży silnik pada, R2 jak mały. Na czas wibracji "
                             "zastępuje efekt spustu, po chwili ciszy efekt wraca. Wspólne dla L2 i R2."), "dim", wrap=True))
        self.fx_warn = label("", wrap=True)
        tl.addWidget(self.fx_warn)
        self.tdz = SliderRow(_("Strefa martwa (ignoruj lekkie dotknięcia do)"), 0, 200, 1)
        self.tmax = SliderRow(_("Pełne wciśnięcie od"), 55, 255, 1)
        self.tdef = button(_("Domyślna reakcja"), small=True)
        self.tdz.changed.connect(lambda v: self._tune(self.name, dz=round(v)))
        self.tmax.changed.connect(lambda v: self._tune(self.name, max=round(v)))
        self.tdef.clicked.connect(lambda: self._reset_tuning(self.name))
        self.trig_adv = Collapsible(_("ZAAWANSOWANE: REAKCJA SPUSTU"), self.tdz, self.tmax, left(self.tdef))
        tl.addWidget(self.trig_adv)
        lay.addWidget(self.trig)
        lay.addStretch(1)
        return scrolled(w)

    def _stick_page(self):
        """The page for a stick: the source stick, axis inversion, the stick-click target and the response tuning."""
        w = QWidget()
        lay = _vbox(w)
        self.stick_title = label("", "h3")
        lay.addWidget(self.stick_title)
        self.ssrc = Combo([(k, _(STICK_NAME[k])) for k in ("left", "right", "none")])
        self.ssrc.changed.connect(lambda v: self._stick(src=v))
        lay.addWidget(field(_("Gra widzi dane tej gałki z"), self.ssrc))
        inv = QHBoxLayout()
        self.invx, self.invy = QCheckBox(_("Odwróć X")), QCheckBox(_("Odwróć Y"))
        self.invx.toggled.connect(lambda on: self._stick(inv_x=on))
        self.invy.toggled.connect(lambda on: self._stick(inv_y=on))
        inv.addWidget(self.invx)
        inv.addWidget(self.invy)
        inv.addStretch(1)
        lay.addLayout(inv)
        self.l3_caption = label("", "dim")
        self.l3 = Combo(targets())
        self.l3.changed.connect(lambda v: self._set_button(L3_OF[self.name], v))
        lay.addWidget(self.l3_caption)
        lay.addWidget(self.l3)
        self.dz = SliderRow(_("Strefa martwa"), 0, 50, 1, "%")
        self.outer = SliderRow(_("Pełne wychylenie od"), 50, 100, 1, "%")
        self.curve = SliderRow(_("Krzywa (powyżej 1 = dokładniejsze małe ruchy)"), 0.3, 3, 0.05, "x")
        self.anti = SliderRow(_("Anty-strefa martwa (pokonuje strefę martwą gry)"), 0, 50, 1, "%")
        self.dz.changed.connect(lambda v: self._tune(STICK_OF[self.name], dz=r3(v / 100)))
        self.outer.changed.connect(lambda v: self._tune(STICK_OF[self.name], outer=r3(v / 100)))
        self.curve.changed.connect(lambda v: self._tune(STICK_OF[self.name], curve=r3(v)))
        self.anti.changed.connect(lambda v: self._tune(STICK_OF[self.name], anti=r3(v / 100)))
        self.sdef = button(_("Domyślna reakcja"), small=True)
        self.sdef.clicked.connect(lambda: self._reset_tuning(STICK_OF[self.name]))
        self.stick_adv = Collapsible(_("ZAAWANSOWANE: REAKCJA GAŁKI"), self.dz, self.outer, self.curve, self.anti,
                                     left(self.sdef))
        lay.addSpacing(4)
        lay.addWidget(self.stick_adv)
        lay.addStretch(1)
        return scrolled(w)

    # ---------- changes ----------
    def _set_button(self, src, value) -> None:
        """Remap `src` to `value` (NONE = disabled) and report the edit."""
        self.ed.set_button(src, None if value == NONE else value)
        self.edited.emit()

    def _reset_button(self, src) -> None:
        """Restore the default mapping of `src`."""
        self.ed.clear_button(src)
        self.refresh()
        self.edited.emit()

    def _tune(self, name, **patch) -> None:
        """Patch the response tuning of a part (dead zone, curve...)."""
        self.ed.set_tuning(name, **patch)
        self.edited.emit()

    def _reset_tuning(self, name) -> None:
        """Restore the default response of a part."""
        self.ed.clear_tuning(name)
        self.refresh()
        self.edited.emit()

    def _trigger_fx(self, **patch) -> None:
        """Patch the trigger effect of the selected trigger; a mode change also re-shows the matching sliders."""
        if self.name in B.TRIGGERS:
            self.ed.set_trigger_fx(self.name, **patch)
            if "mode" in patch:
                self._show_fx(self.ed.trigger_fx(self.name))
            self.edited.emit()

    def _rumble_fx(self, **patch) -> None:
        """Patch the game-rumble-on-triggers settings (shared by L2 and R2)."""
        self.ed.set_rumble_fx(**patch)
        if "on" in patch:
            self._show_rumble(self.ed.rumble_fx())
        self.edited.emit()

    def _show_fx(self, fx) -> None:
        """The sliders of the effect mode (the rest hidden) and the hint."""
        self.fx_hint.setText(_(FX_MODES[fx["mode"]][1]))
        shown = FX_SLIDERS.get(fx["mode"], ())
        for k, row in self.fx.items():
            row.setVisible(k in shown)
            row.set(fx[k] * (10 if k in ("start", "pos") else 1))
        self.update_caps()

    def _show_rumble(self, rv) -> None:
        """Show the game rumble settings; the sliders only when it is on."""
        self.rv_gain.set(rv["gain"])
        self.rv_freq.set(rv["freq"])
        self.rv_gain.setVisible(bool(rv["on"]))
        self.rv_freq.setVisible(bool(rv["on"]))
        self.update_caps()

    def _stick(self, **patch) -> None:
        """Patch the stick mapping (source, inversion) of the selected stick."""
        if self.name is not None and self.name in STICK_OF:
            self.ed.set_stick(STICK_OF[self.name], **patch)
            self.edited.emit()

    # ---------- reading ----------
    def show_part(self, name) -> None:
        """Select a part (a button, trigger or stick name) and load it."""
        self.name = name
        self.refresh()

    def _target(self, src):
        """What `src` currently acts as, NONE when disabled."""
        t = self.ed.target_of(src)
        return NONE if t is None else t

    def refresh(self) -> None:
        """Load the selected part's values from the profile into the widgets (without triggering changes)."""
        name, ed = self.name, self.ed
        if name is None:
            self.stack.setCurrentIndex(0)
            return
        if name in STICK_OF:
            out = STICK_OF[name]
            e, t = ed.stick(out), ed.tuning(out)
            self.stick_title.setText(_(STICK_NAME[out]))
            self.ssrc.set_data(e["src"])
            for cb, on in ((self.invx, e["inv_x"]), (self.invy, e["inv_y"])):
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)
            self.l3_caption.setText(_("Klik gałki ({b}) działa jak").format(b=SHORT[L3_OF[name]]))
            self.l3.set_data(self._target(L3_OF[name]))
            self.dz.set(t["dz"] * 100)
            self.outer.set(t["outer"] * 100)
            self.curve.set(t["curve"])
            self.anti.set(t["anti"] * 100)
            self.stick_adv.set_open(out in ed.prof["tuning"] or self.stick_adv.is_open())
            self.stack.setCurrentIndex(2)
            return
        trig = name in B.TRIGGERS
        self.title.setText(_(B.LABELS[name]))
        pm = pixmap(name, 56)
        pm.setDevicePixelRatio(2)
        self.title_icon.setPixmap(pm)
        self.dst.set_data(self._target(name))
        self.trig.setVisible(trig)
        if trig:
            t, fx = ed.tuning(name), ed.trigger_fx(name)
            self.tdz.set(t["dz"])
            self.tmax.set(t["max"])
            self.trig_adv.set_open(name in ed.prof["tuning"] or self.trig_adv.is_open())
            game = ed.is_game()
            self.fx_mode.set_items([(k, _(v[0])) for k, v in FX_MODES.items() if game or k != "inherit"])
            self.fx_mode.set_data(fx["mode"])
            self.rv_on.set_items([(k, _(v)) for k, v in RUMBLE_ON.items() if game or k != "inherit"])
            rv = ed.rumble_fx()
            self.rv_on.set_data(RUMBLE_KEY[rv["on"]])
            self._show_fx(fx)
            self._show_rumble(rv)
        self.stack.setCurrentIndex(1)

    def update_caps(self) -> None:
        """A warning under the trigger effects when this pad's sender will not apply them (every 400 ms from the window - a cheap read)."""
        text, level = (ST.triggers_status(self.ed.slot, needs_fx(self.ed.prof["triggers"]))
                       if self.name in B.TRIGGERS else ("", ""))
        if text != self.fx_warn.text():
            self.fx_warn.setText(("⚠  " + text) if text else "")
        self.fx_warn.setVisible(bool(text))
        set_prop(self.fx_warn, "level", level)


class GyroPage(QWidget):
    """Aiming with the gyro: as the right stick (any game) or the mouse; when it is active and with what sensitivity."""

    edited = Signal()

    def __init__(self, ed) -> None:
        """Build the mode combo and the settings (activation, sensitivity, inversion, anti dead zone)."""
        super().__init__()
        self.ed = ed
        w = QWidget()
        lay = _vbox(w)
        _vbox(self).addWidget(scrolled(w))
        aim = QWidget()                     # the first category: aiming with the gyro (a collapsed section, like the tilt ones)
        al = _vbox(aim)
        al.setContentsMargins(0, 0, 0, 0)
        self.mode = Combo([(k, _(v)) for k, v in GYRO_MODES.items()])
        self.mode.changed.connect(lambda v: self._set(mode=v, relayout=True))
        al.addWidget(field(_("Tryb"), self.mode))
        self.off_hint = label(_("Obracaj padem, aby celować: jako prawa gałka (każda gra) albo jako mysz."), "dim", wrap=True)
        al.addWidget(self.off_hint)
        self.body = QWidget()
        bl = _vbox(self.body)
        self.hold = Combo([("always", _("Zawsze")), *input_items(B.INPUTS, text=True)])
        self.hold.changed.connect(lambda v: self._set(hold=v))
        bl.addWidget(field(_("Aktywne (zawsze albo przy trzymanym przycisku)"), self.hold))
        self.sens = SliderRow(_("Czułość"), 0.1, 5, 0.05, "x")
        self.sens.changed.connect(lambda v: self._set(sens=round(v, 2)))
        bl.addWidget(self.sens)
        inv = QHBoxLayout()
        self.invx, self.invy = QCheckBox(_("Odwróć w poziomie")), QCheckBox(_("Odwróć w pionie"))
        self.invx.toggled.connect(lambda on: self._set(inv_x=on))
        self.invy.toggled.connect(lambda on: self._set(inv_y=on))
        inv.addWidget(self.invx)
        inv.addWidget(self.invy)
        inv.addStretch(1)
        bl.addLayout(inv)
        self.anti = SliderRow(_("Anty-strefa martwa (małe obroty też ruszają widokiem)"), 0, 50, 1, "%")
        self.anti.changed.connect(lambda v: self._set(anti=r3(v / 100)))
        bl.addWidget(self.anti)
        self.live = label("", "dim")
        bl.addWidget(self.live)
        al.addWidget(self.body)
        self.aim_cat = Collapsible(_("CELOWANIE ŻYROSKOPEM"), aim)
        lay.addWidget(self.aim_cat)
        self.tilt = TiltSection()           # per pad, not per profile: the tilt of the pad as a stick (rx_gstick_io)
        self.tilt.changed.connect(lambda cfg: runner.set_config(self.ed.slot, cfg))
        lay.addWidget(self.tilt)
        lay.addStretch(1)

    def _set(self, relayout=False, **patch) -> None:
        """Patch the gyro settings; relayout reloads the widgets (a mode change shows / hides parts)."""
        self.ed.set_gyro(**patch)
        if relayout:
            self.refresh()
        self.edited.emit()

    def refresh(self) -> None:
        """Load the gyro settings from the profile into the widgets (without triggering changes)."""
        g = self.ed.gyro()
        self.mode.set_data(g["mode"])
        on = g["mode"] != "off"
        self.off_hint.setVisible(not on)
        self.body.setVisible(on)
        self.hold.set_data(g["hold"])
        self.sens.set(g["sens"])
        for cb, v in ((self.invx, g["inv_x"]), (self.invy, g["inv_y"])):
            cb.blockSignals(True)
            cb.setChecked(v)
            cb.blockSignals(False)
        self.anti.set(g["anti"] * 100)
        self.anti.setVisible(g["mode"] == "stick")
        self.tilt.set_slot(self.ed.slot)

    def set_live(self, in_use, active) -> None:
        """The row under the settings: whether the gyro reacts now (only for the profile in use)."""
        g = self.ed.gyro()
        if g["mode"] == "off":
            return
        if not in_use:
            text = _("Ten profil nie jest teraz używany.")
        elif active:
            text = _("● Gyro aktywne")
        else:
            text = _("Gyro nieaktywne") if g["hold"] == "always" else _("Gyro nieaktywne — przytrzymaj {b}").format(
                b=_(B.LABELS[g["hold"]]))
        self.live.setText(text)
        set_prop(self.live, "level", "ok" if in_use and active else "")


class ChangesPage(QWidget):
    """The list of changes against the defaults; × undoes one item, "Delete all" (2 clicks) - the whole profile."""

    undone = Signal()      # an item was undone - the window must refresh all panels

    def __init__(self, ed) -> None:
        """Build the scrollable list and the Delete all button."""
        super().__init__()
        self.ed, self._sig = ed, None
        self.box = QWidget()
        self.lay = _vbox(self.box, 4)
        outer = _vbox(self, 8)
        outer.addWidget(scrolled(self.box), 1)
        self.reset = ConfirmButton(_("Usuń wszystkie"), _("Usunąć wszystkie?"), small=True)
        self.reset.setToolTip(_("Usuń wszystkie zmiany w tym profilu"))
        outer.addLayout(left(self.reset))

    def refresh(self):
        """-> the number of changes. Rows are rebuilt only when the list changed (a slider makes many changes a second)."""
        changes = self.ed.changes()
        sig = tuple(text for text, _ in changes)
        if sig != self._sig:
            self._sig = sig
            while self.lay.count():
                w = self.lay.takeAt(0).widget()
                if w:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
            for text, undo in changes:
                self.lay.addWidget(self._row(text, undo))
            if not changes:
                self.lay.addWidget(label(_("Brak zmian - wszystko działa domyślnie."), "dim", wrap=True))
            self.lay.addStretch(1)
            self.reset.setVisible(bool(changes))
        return len(changes)

    def _row(self, text, undo):
        """One change row: its text and a × button that undoes it."""
        row = QFrame()
        row.setObjectName("row")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(10, 5, 6, 5)
        hl.addWidget(label(text, wrap=True), 1)
        x = QToolButton()
        x.setText("×")
        x.setToolTip(_("Usuń tę zmianę"))
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.clicked.connect(lambda: self._undo(undo))
        hl.addWidget(x)
        return row

    def _undo(self, undo) -> None:
        """Run the undo of one item and tell the window to refresh everything."""
        undo()
        self.undone.emit()
