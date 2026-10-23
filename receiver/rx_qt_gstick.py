"""Receiver: pad tilt as an analog stick - the section at the bottom of the "Gyro" tab (per-pad settings, a live 2D preview).
It lives next to the pad drawing on purpose: while the tilt drives a stick, the drawing shows the stick that the game gets.

  TiltView         drawing: deadzone and range circles, raw tilt (grey), filtered (blue), output (crosshair)
  TiltSection      settings (sliders, lists, switches), center calibration, raw / filtered output numbers

Setting changes go through the `changed(Config)` signal to rx_gstick_io.runner (a slot in the window); the 250 Hz thread never
touches Qt, and the preview is refreshed by the window timer (tick) from an engine snapshot."""
import time
from dataclasses import replace

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

import rx_buttons as B
import rx_gstick as G
from rx_gstick_io import runner
from rx_i18n import _
from rx_qt_style import ACCENT, BAD, CARD, LINE, OK
from rx_qt_widgets import Collapsible, Combo, SliderRow, button, field, input_items, label

MSG_S = 4.0
STATE_TEXT = {G.ST_OFF: "Wyłączone", G.ST_HOLD: "Czeka na sprzęgło", G.ST_SAFE: "Ustaw pada blisko środka",
              G.ST_IDLE: "Uśpione (pad nieruchomy)", G.ST_CAL: "Kalibracja...", G.ST_RUN: "● Steruje"}
CAL_TEXT = {True: "Środek ustawiony.", False: "Kalibracja nie wyszła - trzymaj pada nieruchomo przez chwilę."}


class TiltView(QWidget):
    """2D preview: X axis = sideways tilt, Y = front down (up in the drawing). Scale: 1.0 = range (outer)."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.cfg = G.Config()
        self.snap = (G.ST_OFF, G.Z_DEAD, (0.0, 0.0), (0.0, 0.0), (0.0, 0.0), 0.0, False, False)

    def set_data(self, cfg, snap):
        if cfg != self.cfg or snap != self.snap:
            self.cfg, self.snap = cfg, snap
            self.update()

    def paintEvent(self, _e):
        cfg, (_state, zone, raw, filt, xy, _mag, _sprint, driving) = self.cfg, self.snap
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 8
        c = QPointF(self.width() / 2, self.height() / 2)
        r = side / 2
        p.setBrush(QColor(CARD))
        p.setPen(QPen(QColor(LINE), 1))
        p.drawEllipse(c, r, r)                                       # range (outer)
        dz = r * cfg.deadzone / cfg.outer
        p.setBrush(QColor(LINE))
        p.drawEllipse(c, dz, dz)                                     # deadzone
        p.setPen(QPen(QColor(LINE), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(BAD if zone == G.Z_OUTER else LINE), 2))
        p.drawEllipse(c, r, r)

        def at(v):                                                   # degrees -> pixels (y up), clipped to the frame
            x, y = v[0] / cfg.outer * r, -v[1] / cfg.outer * r
            k = max(1.0, (x * x + y * y) ** 0.5 / r)
            return QPointF(c.x() + x / k, c.y() + y / k)

        for pos, col, rad in ((at(raw), "#8e96ab", 3), (at(filt), ACCENT, 4)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(pos, rad, rad)
        out = QPointF(c.x() + xy[0] * r, c.y() - xy[1] * r)           # output: the full circle range
        p.setPen(QPen(QColor(OK if driving else "#8e96ab"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(out, 7, 7)
        p.drawLine(QPointF(out.x() - 11, out.y()), QPointF(out.x() + 11, out.y()))
        p.drawLine(QPointF(out.x(), out.y() - 11), QPointF(out.x(), out.y() + 11))
        p.end()


class TiltSection(QWidget):
    """The settings of the pad selected in the window. Changes leave through `changed(Config)`; tick() feeds the preview."""

    changed = Signal(object)         # the pad's new G.Config

    def __init__(self):
        super().__init__()
        self.slot = 0
        self._msg_until = 0.0
        self._preview = None             # the slot whose engine we asked to follow the pad (preview), None = none
        self.cfg = G.Config()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        # every category is a collapsed section; the live preview is its own one, so it only runs while it is open
        self.on = QCheckBox(_("Włącz (dla tego pada)"))
        self.on.toggled.connect(lambda v: self._set(enabled=v))
        self.target = Combo([(k, _(v)) for k, v in G.TARGETS.items()])
        self.target.changed.connect(lambda v: self._set(target=v))
        self.clutch = Combo([("always", _("Zawsze")), *input_items(B.INPUTS, text=True)])
        self.clutch.changed.connect(lambda v: self._set(clutch=v))
        inv = QHBoxLayout()
        self.inv_p, self.inv_r = QCheckBox(_("Odwróć pitch")), QCheckBox(_("Odwróć roll"))
        self.inv_p.toggled.connect(lambda v: self._set(invert_pitch=v))
        self.inv_r.toggled.connect(lambda v: self._set(invert_roll=v))
        inv.addWidget(self.inv_p)
        inv.addWidget(self.inv_r)
        inv.addStretch(1)
        self.cal = button(_("Ustaw środek"))
        self.cal.setToolTip(_("Bieżące położenie pada staje się środkiem (0, 0) - trzymaj pada nieruchomo"))
        self.cal.clicked.connect(self._calibrate)
        self.cal_reset = button(_("Zeruj środek"), small=True)
        self.cal_reset.clicked.connect(lambda: self._set(center=None))
        cal_row = QHBoxLayout()
        cal_row.addWidget(self.cal, 1)
        cal_row.addWidget(self.cal_reset)
        self.cal_note = label("", "dim", wrap=True)
        self.sl = {}
        self.curve = Combo([(k, _(v)) for k, v in G.CURVES.items()])
        self.curve.changed.connect(lambda v: self._set(curve=v))
        self.sprint = Combo([("none", _("Brak")), *input_items([n for n in G.SPRINTS if n != "none"], text=True)])
        self.sprint.changed.connect(lambda v: self._set(sprint=v))
        self.focus = QCheckBox(_("Zeruj po zmianie okna"))
        self.focus.setToolTip(_("Zeruje gałkę, gdy na pierwszym planie zmieni się program"))
        self.focus.toggled.connect(lambda v: self._set(focus_release=v))
        self.feedback = Combo([(k, _(v)) for k, v in G.FEEDBACKS.items()])
        self.feedback.changed.connect(lambda v: self._set(feedback=v))
        self.view = TiltView()
        self.numbers = label("", "dim", wrap=True)
        self.status = label("", "dim")
        self.cats = [
            Collapsible(_("PRZECHYŁ: PODSTAWOWE"), self.on, field(_("Zastępuje wejście pada"), self.target),
                        field(_("Aktywne (sprzęgło)"), self.clutch), inv, cal_row, self.cal_note),
            Collapsible(_("PRZECHYŁ: ZAKRES I FILTR"), self._slider("deadzone"), self._slider("outer"), self._slider("smoothing"),
                        self._slider("ratio"), field(_("Krzywa odpowiedzi"), self.curve)),
            Collapsible(_("PRZECHYŁ: AUTOMATYKA"), self._slider("idle_s"), self._slider("delta"),
                        field(_("Przycisk na skraju zakresu"), self.sprint), self._slider("sprint_at"), self.focus),
            Collapsible(_("PRZECHYŁ: SYGNAŁ ZWROTNY"),
                        field(_("Sygnał przy skraju zakresu i powrocie do środka"), self.feedback)),
            Collapsible(_("PRZECHYŁ: PODGLĄD"), label(_("Przechyl pada: przód w dół = gałka w górę, na bok = gałka w bok. Gałka na "
                                                      "rysunku pada porusza się razem z nim."), "dim", wrap=True),
                        self.view, self.numbers, self.status),
        ]
        self.preview_cat = self.cats[-1]
        for c in self.cats:
            lay.addWidget(c)
        self.refresh()

    def _slider(self, name):
        captions = {"deadzone": (_("Martwa strefa"), "°", 0.5), "outer": (_("Maksymalne wychylenie"), "°", 1),
                    "smoothing": (_("Wygładzanie"), "%", 1), "ratio": (_("Czułość pionu względem poziomu"), "x", 0.05),
                    "idle_s": (_("Uśpienie po bezruchu (0 = wyłączone)"), " s", 0.1),
                    "delta": (_("Najmniejsza wysyłana zmiana"), "%", 0.1), "sprint_at": (_("Przycisk od tylu % zasięgu"), "%", 1)}
        caption, unit, step = captions[name]
        lo, hi = G.LIMITS[name]
        row = SliderRow(caption, lo, hi, step, fmt=self._fmt(unit))
        row.changed.connect(lambda v, n=name: self._set(**{n: round(v, 3)}))
        self.sl[name] = row
        return row

    @staticmethod
    def _fmt(unit):
        if unit == "x":
            return lambda v: f"{v:.2f}×"
        return lambda v: f"{v:.1f}{unit}".replace(".0" + unit, unit)

    def set_slot(self, slot):
        if slot != self.slot:
            self.idle()
        self.slot = slot
        self.refresh()

    def _set(self, **patch):
        cfg = G.normalize(G.to_raw(replace(self.cfg, **patch)))      # the same limits as when the file is loaded (outer > deadzone)
        self.cfg = cfg
        self.changed.emit(cfg)
        self.refresh(cfg, reload=False)

    def _calibrate(self):
        runner.calibrate(self.slot)

    def refresh(self, cfg=None, reload=True):
        """Pad settings from the file (pad / tab change) or after an edit in this window (reload=False)."""
        if reload:
            self.cfg = cfg = runner.config(self.slot)
        cfg = cfg or self.cfg
        for cb, v in ((self.on, cfg.enabled), (self.inv_p, cfg.invert_pitch), (self.inv_r, cfg.invert_roll),
                      (self.focus, cfg.focus_release)):
            cb.blockSignals(True)
            cb.setChecked(v)
            cb.blockSignals(False)
        for combo, v in ((self.target, cfg.target), (self.clutch, cfg.clutch), (self.sprint, cfg.sprint), (self.curve, cfg.curve),
                         (self.feedback, cfg.feedback)):
            combo.set_data(v)
        for name, row in self.sl.items():
            row.set(getattr(cfg, name))
        self.sl["sprint_at"].setEnabled(cfg.sprint != "none")
        self.cal_reset.setEnabled(cfg.center is not None)

    def tick(self):
        """Live preview (30 Hz from the window timer, only while this section is visible). Also asks the engine to follow the
        pad even when the feature is off, so the tilt can be seen before it is switched on."""
        msg, now = runner.cal_msg[self.slot], time.monotonic()
        if msg is not None and not self._msg_until:
            self._msg_until = now + MSG_S                    # the calibration result shows for a moment
            self.refresh()                                   # the new center was saved by the thread
            self.cal_note.setText(_(CAL_TEXT[msg]))
        elif msg is not None and now > self._msg_until:
            runner.cal_msg[self.slot], self._msg_until, msg = None, 0.0, None
            self.cal_note.setText("")
        if not self.preview_cat.is_open():       # a collapsed preview costs nothing: the engine is not asked to follow the pad
            self.idle()
            return
        if self._preview != self.slot:
            self.idle()
            self._preview = self.slot
            runner.set_preview(self.slot, True)
        snap = runner.snapshot(self.slot)
        self.view.set_data(self.cfg, snap)
        state, _zone, raw, _filt, xy, _mag, sprint, _driving = snap
        self.numbers.setText(_("Raw: pitch {p:+.1f}°  roll {r:+.1f}°   |   Filtered output: X {x:+.0f}%  Y {y:+.0f}%{s}").format(
            p=raw[1], r=raw[0], x=xy[0] * 100, y=xy[1] * 100, s="  [sprint]" if sprint else ""))
        if not self.cfg.enabled and state != G.ST_OFF:
            self.status.setText(_("Podgląd - funkcja jest wyłączona"))
        else:
            self.status.setText(_(STATE_TEXT[state]))

    def idle(self):
        """The section is not visible any more: the engine stops following the pad when the feature is off."""
        if self._preview is not None:
            runner.set_preview(self._preview, False)
            self._preview = None
