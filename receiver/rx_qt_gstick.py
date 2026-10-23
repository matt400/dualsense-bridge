"""Receiver: karta "Przechył" - przechył pada jako gałka analogowa (ustawienia per pad, podgląd 2D na żywo).

  TiltView         rysunek: okręgi martwej strefy i zasięgu, surowy przechył (szary), po filtrze (niebieski), wyjście (kropka)
  GyroStickPage    ustawienia (suwaki, listy, przełączniki), kalibracja środka, liczby raw / filtered output

Zmiany ustawień idą sygnałem `changed(Config)` do rx_gstick_io.runner (slot w oknie); wątek 250 Hz nie dotyka Qt,
a podgląd odświeża timer okna (tick) z migawki silnika."""
import time
from dataclasses import replace

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

import rx_buttons as B
import rx_gstick as G
from rx_gstick_io import runner
from rx_i18n import _
from rx_qt_panels import scrolled
from rx_qt_style import ACCENT, BAD, CARD, LINE, OK
from rx_qt_widgets import Combo, SliderRow, button, field, group, input_items, label, pair

MSG_S = 4.0
STATE_TEXT = {G.ST_OFF: "Wyłączone", G.ST_HOLD: "Czeka na sprzęgło", G.ST_SAFE: "Ustaw pada blisko środka",
              G.ST_IDLE: "Uśpione (pad nieruchomy)", G.ST_CAL: "Kalibracja...", G.ST_RUN: "● Steruje"}
CAL_TEXT = {True: "Środek ustawiony.", False: "Kalibracja nie wyszła - trzymaj pada nieruchomo przez chwilę."}


class TiltView(QWidget):
    """Podgląd 2D: oś X = przechył w bok, Y = przód w dół (do góry na rysunku). Skala: 1.0 = zasięg (outer)."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(180, 180)
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
        p.drawEllipse(c, r, r)                                       # zasięg (outer)
        dz = r * cfg.deadzone / cfg.outer
        p.setBrush(QColor(LINE))
        p.drawEllipse(c, dz, dz)                                     # martwa strefa
        p.setPen(QPen(QColor(LINE), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(BAD if zone == G.Z_OUTER else LINE), 2))
        p.drawEllipse(c, r, r)

        def at(v):                                                   # stopnie -> piksele (y w górę), obcięte do ramki
            x, y = v[0] / cfg.outer * r, -v[1] / cfg.outer * r
            k = max(1.0, (x * x + y * y) ** 0.5 / r)
            return QPointF(c.x() + x / k, c.y() + y / k)

        for pos, col, rad in ((at(raw), "#8e96ab", 3), (at(filt), ACCENT, 4)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(pos, rad, rad)
        out = QPointF(c.x() + xy[0] * r, c.y() - xy[1] * r)           # wyjście: pełny zakres okręgu
        p.setPen(QPen(QColor(OK if driving else "#8e96ab"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(out, 7, 7)
        p.drawLine(QPointF(out.x() - 11, out.y()), QPointF(out.x() + 11, out.y()))
        p.drawLine(QPointF(out.x(), out.y() - 11), QPointF(out.x(), out.y() + 11))
        p.end()


class GyroStickPage(QWidget):
    changed = Signal(object)         # nowy G.Config pada

    def __init__(self):
        super().__init__()
        self.slot = 0
        self._msg_until = 0.0
        self.cfg = G.Config()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        QVBoxLayout(self).addWidget(scrolled(w))
        lay.addWidget(label(_("GAŁKA Z PRZECHYŁU PADA"), "h2"))
        self.on = QCheckBox(_("Włącz (dla tego pada)"))
        self.on.toggled.connect(lambda v: self._set(enabled=v))
        lay.addWidget(self.on)
        self.target = Combo([(k, _(v)) for k, v in G.TARGETS.items()])
        self.target.changed.connect(lambda v: self._set(target=v))
        self.clutch = Combo([("always", _("Zawsze")), *input_items(B.INPUTS, text=True)])
        self.clutch.changed.connect(lambda v: self._set(clutch=v))
        lay.addLayout(pair(field(_("Zastępuje wejście pada"), self.target), field(_("Aktywne (sprzęgło)"), self.clutch)))
        self.sprint = Combo([("none", _("Brak")), *input_items([n for n in G.SPRINTS if n != "none"], text=True)])
        self.sprint.changed.connect(lambda v: self._set(sprint=v))
        self.curve = Combo([(k, _(v)) for k, v in G.CURVES.items()])
        self.curve.changed.connect(lambda v: self._set(curve=v))
        lay.addLayout(pair(field(_("Przycisk na skraju zakresu"), self.sprint), field(_("Krzywa odpowiedzi"), self.curve)))
        inv = QHBoxLayout()
        self.inv_p, self.inv_r = QCheckBox(_("Odwróć pitch")), QCheckBox(_("Odwróć roll"))
        self.inv_p.toggled.connect(lambda v: self._set(invert_pitch=v))
        self.inv_r.toggled.connect(lambda v: self._set(invert_roll=v))
        inv.addWidget(self.inv_p)
        inv.addWidget(self.inv_r)
        inv.addStretch(1)
        lay.addLayout(inv)
        self.sl = {}
        for name, caption, unit, step in (("deadzone", _("Martwa strefa"), "°", 0.5), ("outer", _("Maksymalne wychylenie"), "°", 1),
                                          ("smoothing", _("Wygładzanie"), "%", 1),
                                          ("ratio", _("Czułość pionu względem poziomu"), "x", 0.05),
                                          ("idle_s", _("Uśpienie po bezruchu (0 = wyłączone)"), " s", 0.1),
                                          ("delta", _("Najmniejsza wysyłana zmiana"), "%", 0.1),
                                          ("sprint_at", _("Przycisk od tylu % zasięgu"), "%", 1)):
            lo, hi = G.LIMITS[name]
            row = SliderRow(caption, lo, hi, step, fmt=self._fmt(unit))
            row.changed.connect(lambda v, n=name: self._set(**{n: round(v, 3)}))
            self.sl[name] = row
            lay.addWidget(row)
        self.focus = QCheckBox(_("Zeruj gałkę po zmianie okna na pierwszym planie"))
        self.focus.toggled.connect(lambda v: self._set(focus_release=v))
        lay.addWidget(self.focus)
        self.cal = button(_("Ustaw środek (trzymaj pada nieruchomo)"))
        self.cal.clicked.connect(self._calibrate)
        self.cal_reset = button(_("Zeruj środek"), small=True)
        self.cal_reset.clicked.connect(lambda: self._set(center=None))
        row = QHBoxLayout()
        row.addWidget(self.cal, 1)
        row.addWidget(self.cal_reset)
        lay.addLayout(row)
        self.view = TiltView()
        self.view.setMinimumHeight(220)
        self.numbers = label("", "dim", wrap=True)
        self.status = label("", "dim")
        lay.addWidget(group(_("Podgląd"), self.view, self.numbers, self.status))
        lay.addStretch(1)
        self.refresh()

    @staticmethod
    def _fmt(unit):
        if unit == "x":
            return lambda v: f"{v:.2f}×"
        return lambda v: f"{v:.1f}{unit}".replace(".0" + unit, unit)

    def set_slot(self, slot):
        self.slot = slot
        self.refresh()

    def _set(self, **patch):
        cfg = replace(self.cfg, **patch)
        cfg = G.normalize(G.to_raw(cfg))                     # te same granice co przy wczytaniu pliku (outer > martwa strefa)
        self.cfg = cfg
        self.changed.emit(cfg)
        self.refresh(cfg, reload=False)

    def _calibrate(self):
        runner.calibrate(self.slot)

    def refresh(self, cfg=None, reload=True):
        """Ustawienia pada z pliku (zmiana pada / karty) albo po edycji w tym oknie (reload=False)."""
        if reload:
            self.cfg = cfg = runner.config(self.slot)
        cfg = cfg or self.cfg
        for cb, v in ((self.on, cfg.enabled), (self.inv_p, cfg.invert_pitch), (self.inv_r, cfg.invert_roll),
                      (self.focus, cfg.focus_release)):
            cb.blockSignals(True)
            cb.setChecked(v)
            cb.blockSignals(False)
        for combo, v in ((self.target, cfg.target), (self.clutch, cfg.clutch), (self.sprint, cfg.sprint), (self.curve, cfg.curve)):
            combo.set_data(v)
        for name, row in self.sl.items():
            row.set(getattr(cfg, name))
        self.sl["sprint_at"].setEnabled(cfg.sprint != "none")
        self.cal_reset.setEnabled(cfg.center is not None)

    def tick(self):
        """Podgląd na żywo (30 Hz z timera okna, tylko gdy karta widoczna)."""
        snap = runner.snapshot(self.slot)
        self.view.set_data(self.cfg, snap)
        state, _zone, raw, _filt, xy, _mag, sprint, _driving = snap
        self.numbers.setText(_("Raw: pitch {p:+.1f}°  roll {r:+.1f}°   |   Filtered output: X {x:+.0f}%  Y {y:+.0f}%{s}").format(
            p=raw[1], r=raw[0], x=xy[0] * 100, y=xy[1] * 100, s="  [sprint]" if sprint else ""))
        msg, now = runner.cal_msg[self.slot], time.monotonic()
        if msg is not None and not self._msg_until:
            self._msg_until = now + MSG_S                    # wynik kalibracji widać chwilę, potem znów stan
            self.refresh()                                   # nowy środek zapisany przez wątek
        elif msg is not None and now > self._msg_until:
            runner.cal_msg[self.slot], self._msg_until, msg = None, 0.0, None
        self.status.setText(_(STATE_TEXT[state]) if msg is None else _(CAL_TEXT[msg]))
