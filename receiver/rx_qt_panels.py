"""Receiver: panele prawej kolumny okna - wybór profilu, edytor wybranej części pada, gyro i lista zmian.

Panele czytają i zmieniają Editor (rx_editor) i sygnałem `edited` mówią oknu, że profil się zmienił
(okno odświeża plakietki, status i wysyła profil na pad z krótkim opóźnieniem).
"""
from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QCompleter, QFrame, QHBoxLayout, QLineEdit, QScrollArea, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

import rx_buttons as B
from rx_editor import GYRO_MODES, L3_OF, SHORT, STICK_NAME, STICK_OF
from rx_qt_style import set_prop
from rx_qt_widgets import Combo, ConfirmButton, SliderRow, button, field, label

NONE = "__none"
TARGETS = [(n, B.LABELS[n]) for n in B.INPUTS] + [(NONE, "— disabled —")]


def r3(v):
    return round(v, 3)


def _vbox(parent=None, spacing=10):
    lay = QVBoxLayout(parent)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


def scrolled(widget):
    """Panel w pionowym przewijaniu (bez ramki) - zawartość może być wyższa niż okno."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if widget.layout():
        widget.layout().setContentsMargins(0, 12, 6, 0)      # odstęp pod zakładkami i miejsce na pasek przewijania
    sa.setWidget(widget)
    return sa


class ProfileBar(QWidget):
    """Lista profili pada (domyślny + gry), nowy profil gry i usuwanie."""

    selected = Signal(str)            # klucz wybranego profilu
    create = Signal(str, bool)        # (nazwa programu, zacznij od bieżących ustawień)
    delete = Signal()

    def __init__(self):
        super().__init__()
        self._sig = None
        self.recent, self.existing = [], set()
        lay = _vbox(self, 6)
        row = QHBoxLayout()
        self.combo = Combo()
        self.combo.changed.connect(lambda key: self.selected.emit(key))
        self.add = button("＋ Game", small=True)
        self.add.setToolTip("New profile for a game")
        self.add.clicked.connect(self._open_new)
        self.remove = ConfirmButton("Delete", small=True)
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
        self.exe.setToolTip("Program name as shown in Task Manager → Details")
        self.completer_model = QStringListModel()
        completer = QCompleter(self.completer_model, self.exe)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.exe.setCompleter(completer)
        self.exe.returnPressed.connect(self._create)
        self.copy = QCheckBox("Start from the current settings")
        self.copy.setChecked(True)
        ok, cancel = button("Create", primary=True, small=True), button("Cancel", small=True)
        ok.clicked.connect(self._create)
        cancel.clicked.connect(self.close_new)
        btns = QHBoxLayout()
        btns.addWidget(ok)
        btns.addWidget(cancel)
        btns.addStretch(1)
        fl.addWidget(label("Game program (as in Task Manager → Details)", "dim"))
        fl.addWidget(self.exe)
        fl.addWidget(self.copy)
        fl.addLayout(btns)
        self.form.hide()
        lay.addWidget(self.form)

    def reload(self, profiles, key, info, recent):
        """profiles = [(klucz, nazwa, w użyciu)]; lista przebudowywana tylko, gdy się zmieniła (nie zamyka otwartej)."""
        sig = tuple(profiles)
        if sig != self._sig:
            self._sig = sig
            self.combo.set_items([(k, n + ("   ● in use" if use else "")) for k, n, use in profiles])
        self.combo.set_data(key)
        self.remove.setVisible(B.is_game_key(key))
        self.info.setText(info)
        self.existing = {n for k, n, _ in profiles if B.is_game_key(k)}
        if recent != self.recent:
            self.recent = recent
            self.completer_model.setStringList(recent)

    def _open_new(self):
        self.exe.setText(next((x for x in self.recent if x not in self.existing), ""))
        self.form.show()
        self.exe.setFocus()
        self.exe.selectAll()

    def _create(self):
        self.create.emit(self.exe.text(), self.copy.isChecked())

    def close_new(self):
        self.form.hide()


class PartPage(QWidget):
    """Edytor wybranej części pada: przycisk / kierunek D-pada, spust (z krzywą) albo gałka (zamiana, odwrócenie, krzywa)."""

    edited = Signal()

    def __init__(self, ed):
        super().__init__()
        self.ed, self.name = ed, None
        self.stack = QStackedWidget()
        _vbox(self).addWidget(self.stack)
        self.stack.addWidget(self._hint_page())
        self.stack.addWidget(self._button_page())
        self.stack.addWidget(self._stick_page())

    # ---------- budowa ----------
    @staticmethod
    def _hint_page():
        w = QWidget()
        lay = _vbox(w)
        lay.addWidget(label("SELECTED", "h2"))
        lay.addWidget(label("Click a button, trigger or stick on the controller to remap or tune it.", "dim", wrap=True))
        lay.addStretch(1)
        return scrolled(w)

    def _button_page(self):
        w = QWidget()
        lay = _vbox(w)
        lay.addWidget(label("SELECTED", "h2"))
        self.title = label("", "h3")
        lay.addWidget(self.title)
        self.dst = Combo(TARGETS)
        self.dst.changed.connect(lambda v: self._set_button(self.name, v))
        lay.addWidget(field("Acts as", self.dst))
        self.dflt = button("Default", small=True)
        self.dflt.clicked.connect(lambda: self._reset_button(self.name))
        lay.addWidget(self.dflt, 0, Qt.AlignmentFlag.AlignLeft)
        self.trig = QWidget()
        tl = _vbox(self.trig)
        self.trig_title = label("", "h2")
        self.tdz = SliderRow("Deadzone (ignore light touches up to)", 0, 200, 1)
        self.tmax = SliderRow("Full press at", 55, 255, 1)
        self.tdef = button("Default response", small=True)
        self.tdz.changed.connect(lambda v: self._tune(self.name, dz=round(v)))
        self.tmax.changed.connect(lambda v: self._tune(self.name, max=round(v)))
        self.tdef.clicked.connect(lambda: self._reset_tuning(self.name))
        for wd in (self.trig_title, self.tdz, self.tmax):
            tl.addWidget(wd)
        tl.addWidget(self.tdef, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.trig)
        lay.addStretch(1)
        return scrolled(w)

    def _stick_page(self):
        w = QWidget()
        lay = _vbox(w)
        lay.addWidget(label("SELECTED", "h2"))
        self.stick_title = label("", "h3")
        lay.addWidget(self.stick_title)
        self.ssrc = Combo([(k, STICK_NAME[k]) for k in ("left", "right", "none")])
        self.ssrc.changed.connect(lambda v: self._stick(src=v))
        lay.addWidget(field("The game sees this stick's data from", self.ssrc))
        inv = QHBoxLayout()
        self.invx, self.invy = QCheckBox("Invert X"), QCheckBox("Invert Y")
        self.invx.toggled.connect(lambda on: self._stick(inv_x=on))
        self.invy.toggled.connect(lambda on: self._stick(inv_y=on))
        inv.addWidget(self.invx)
        inv.addWidget(self.invy)
        inv.addStretch(1)
        lay.addLayout(inv)
        self.l3_caption = label("", "dim")
        self.l3 = Combo(TARGETS)
        self.l3.changed.connect(lambda v: self._set_button(L3_OF[self.name], v))
        lay.addWidget(self.l3_caption)
        lay.addWidget(self.l3)
        lay.addWidget(label("RESPONSE", "h2"))
        self.dz = SliderRow("Deadzone", 0, 50, 1, "%")
        self.outer = SliderRow("Full tilt at", 50, 100, 1, "%")
        self.curve = SliderRow("Curve (above 1 = finer small moves)", 0.3, 3, 0.05, "x")
        self.anti = SliderRow("Anti-deadzone (beats the game's own deadzone)", 0, 50, 1, "%")
        self.dz.changed.connect(lambda v: self._tune(STICK_OF[self.name], dz=r3(v / 100)))
        self.outer.changed.connect(lambda v: self._tune(STICK_OF[self.name], outer=r3(v / 100)))
        self.curve.changed.connect(lambda v: self._tune(STICK_OF[self.name], curve=r3(v)))
        self.anti.changed.connect(lambda v: self._tune(STICK_OF[self.name], anti=r3(v / 100)))
        for wd in (self.dz, self.outer, self.curve, self.anti):
            lay.addWidget(wd)
        self.sdef = button("Default response", small=True)
        self.sdef.clicked.connect(lambda: self._reset_tuning(STICK_OF[self.name]))
        lay.addWidget(self.sdef, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        return scrolled(w)

    # ---------- zmiany ----------
    def _set_button(self, src, value):
        self.ed.set_button(src, None if value == NONE else value)
        self.edited.emit()

    def _reset_button(self, src):
        self.ed.clear_button(src)
        self.refresh()
        self.edited.emit()

    def _tune(self, name, **patch):
        self.ed.set_tuning(name, **patch)
        self.edited.emit()

    def _reset_tuning(self, name):
        self.ed.clear_tuning(name)
        self.refresh()
        self.edited.emit()

    def _stick(self, **patch):
        if self.name in STICK_OF:
            self.ed.set_stick(STICK_OF[self.name], **patch)
            self.edited.emit()

    # ---------- odczyt ----------
    def show_part(self, name):
        self.name = name
        self.refresh()

    def _target(self, src):
        t = self.ed.target_of(src)
        return NONE if t is None else t

    def refresh(self):
        """Wczytuje wartości wybranej części z profilu do widżetów (bez wywoływania zmian)."""
        name, ed = self.name, self.ed
        if name is None:
            self.stack.setCurrentIndex(0)
            return
        if name in STICK_OF:
            out = STICK_OF[name]
            e, t = ed.stick(out), ed.tuning(out)
            self.stick_title.setText(STICK_NAME[out])
            self.ssrc.set_data(e["src"])
            for cb, on in ((self.invx, e["inv_x"]), (self.invy, e["inv_y"])):
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)
            self.l3_caption.setText(f"Stick click ({SHORT[L3_OF[name]]}) acts as")
            self.l3.set_data(self._target(L3_OF[name]))
            self.dz.set(t["dz"] * 100)
            self.outer.set(t["outer"] * 100)
            self.curve.set(t["curve"])
            self.anti.set(t["anti"] * 100)
            self.stack.setCurrentIndex(2)
            return
        trig = name in B.TRIGGERS
        self.title.setText(B.LABELS[name])
        self.dst.set_data(self._target(name))
        self.trig.setVisible(trig)
        if trig:
            t = ed.tuning(name)
            self.trig_title.setText(f"{SHORT[name]} RESPONSE (WHAT THE GAME GETS)")
            self.tdz.set(t["dz"])
            self.tmax.set(t["max"])
        self.stack.setCurrentIndex(1)


class GyroPage(QWidget):
    """Celowanie żyroskopem: jako prawa gałka (każda gra) albo mysz; kiedy aktywne i z jaką czułością."""

    edited = Signal()

    def __init__(self, ed):
        super().__init__()
        self.ed = ed
        w = QWidget()
        lay = _vbox(w)
        _vbox(self).addWidget(scrolled(w))
        lay.addWidget(label("GYRO AIM", "h2"))
        self.mode = Combo(list(GYRO_MODES.items()))
        self.mode.changed.connect(lambda v: self._set(mode=v, relayout=True))
        lay.addWidget(field("Mode", self.mode))
        self.off_hint = label("Turn the pad to aim: as the right stick (any game) or as a mouse.", "dim", wrap=True)
        lay.addWidget(self.off_hint)
        self.body = QWidget()
        bl = _vbox(self.body)
        self.hold = Combo([("always", "Always")] + [(n, f"While holding {B.LABELS[n]}") for n in B.INPUTS])
        self.hold.changed.connect(lambda v: self._set(hold=v))
        bl.addWidget(field("Active", self.hold))
        self.sens = SliderRow("Sensitivity", 0.1, 5, 0.05, "x")
        self.sens.changed.connect(lambda v: self._set(sens=round(v, 2)))
        bl.addWidget(self.sens)
        inv = QHBoxLayout()
        self.invx, self.invy = QCheckBox("Invert horizontal"), QCheckBox("Invert vertical")
        self.invx.toggled.connect(lambda on: self._set(inv_x=on))
        self.invy.toggled.connect(lambda on: self._set(inv_y=on))
        inv.addWidget(self.invx)
        inv.addWidget(self.invy)
        inv.addStretch(1)
        bl.addLayout(inv)
        self.anti = SliderRow("Anti-deadzone (small turns still move the view)", 0, 50, 1, "%")
        self.anti.changed.connect(lambda v: self._set(anti=r3(v / 100)))
        bl.addWidget(self.anti)
        self.live = label("", "dim")
        bl.addWidget(self.live)
        lay.addWidget(self.body)
        lay.addStretch(1)

    def _set(self, relayout=False, **patch):
        self.ed.set_gyro(**patch)
        if relayout:
            self.refresh()
        self.edited.emit()

    def refresh(self):
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

    def set_live(self, in_use, active):
        """Wiersz pod ustawieniami: czy gyro teraz reaguje (tylko dla profilu w użyciu)."""
        g = self.ed.gyro()
        if g["mode"] == "off":
            return
        if not in_use:
            text = "This profile is not in use right now."
        elif active:
            text = "● Gyro active"
        else:
            text = "Gyro idle" if g["hold"] == "always" else f"Gyro idle — hold {B.LABELS[g['hold']]}"
        self.live.setText(text)
        set_prop(self.live, "level", "ok" if in_use and active else "")


class ChangesPage(QWidget):
    """Lista zmian względem ustawień domyślnych; × cofa jedną pozycję."""

    undone = Signal()      # cofnięto pozycję - okno musi odświeżyć wszystkie panele

    def __init__(self, ed):
        super().__init__()
        self.ed, self._sig = ed, None
        self.box = QWidget()
        self.lay = _vbox(self.box, 4)
        _vbox(self).addWidget(scrolled(self.box))

    def refresh(self):
        """-> liczba zmian. Wiersze są przebudowywane tylko, gdy lista się zmieniła (suwak daje wiele zmian na sekundę)."""
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
                self.lay.addWidget(label("No changes - everything works as default.", "dim", wrap=True))
            self.lay.addStretch(1)
        return len(changes)

    def _row(self, text, undo):
        row = QFrame()
        row.setObjectName("row")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(10, 5, 6, 5)
        hl.addWidget(label(text, wrap=True), 1)
        x = QToolButton()
        x.setText("×")
        x.setToolTip("Remove this change")
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.clicked.connect(lambda: self._undo(undo))
        hl.addWidget(x)
        return row

    def _undo(self, undo):
        undo()
        self.undone.emit()
