"""Receiver: the Test view (the window's right panel) - pad motion on charts, stick drift, gyro calibration.

Works on the selected pad (the card on the left). While the view is shown the game may get a neutral pad state (a lock - on by
default, remembered in receiver_ui.ini), so that testing buttons does not click in the game; the pad drawing shows the physical
state. Refresh: tick() from the window clock (30 Hz) only while the view is visible.
  Motion:    gyro [°/s] and accelerometer [g], 3 axes, the last CHART_S seconds, the scale adapts to the motion.
  Sticks:    position (square-root scale - small deviations are visible), the dead zone from the drift correction and the last measurement;
             "Measure drift" (rx_drift) -> a dead zone proposal -> "Apply" saves the pad's correction (rx_padcal).
  Gyroscope: "Calibrate" - the sender computes the offset of the pad lying still and stores it per pad (proto CMD_GYRO_CAL).
"""
import functools
import math
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

import rx_drift as D
import rx_profiles as P
import rx_state as S
from proto import CAL_FEW, CAL_MOVED, CAL_OK, CAL_RUNNING, CAL_STATUS, CALF_STORED
from rx_config import C
from rx_i18n import _
from rx_qt_icons import icon
from rx_qt_panels import _vbox, scrolled
from rx_qt_style import ACCENT, BORDER, DIM, FG, LINE, MUTED, OK, TRACK, WARN, px, set_prop
from rx_qt_widgets import ConfirmButton, SegmentBar, button, icon_label, label

CHART_S = 5.0
AXES = (("X", "#ff6b6b"), ("Y", OK), ("Z", "#5b9bff"))
CAL_S = 3                       # gyro calibration from the window [s]
CAL_WAIT_S = 3.0                # on top of the calibration time: how long we wait for the sender's answer
QUERY_S = 2.0                   # no calibration state (a lost packet) - ask the sender again every this many seconds
TRAIL = 24                      # this many last stick positions are shown as a trail
MIN_POINTS = 2                  # a polyline needs at least two samples


def _nice(v: float) -> float:
    """Round a chart scale up to 1 / 2 / 5 × 10^k."""
    if v <= 0:
        return 1.0
    k = 10 ** math.floor(math.log10(v))
    return next(m * k for m in (1, 2, 5, 10) if m * k >= v)


@functools.cache
def _font_px(pixels: int, bold: bool) -> QFont:
    """The chart font (Segoe UI) at a pixel size; cached - the charts ask for it on every repaint."""
    f = QFont("Segoe UI")
    f.setPixelSize(pixels)
    f.setBold(bold)
    return f


def _font(size: int, bold: bool = False) -> QFont:
    """The chart font at a DPI-scaled size (the cache is keyed by the scaled pixels, so a scale change builds a new font)."""
    return _font_px(px(size), bold)


def pct(v: float, digits: int = 0) -> str:
    """A fraction 0..1 -> "6%" / "4.2%"."""
    return f"{v * 100:.{digits}f}%"


class MotionChart(QWidget):
    """A scrolling chart of 3 axes (the last CHART_S s); a symmetric scale fitted to the largest value in the window."""

    def __init__(self, title: str, unit: str, min_range: float, digits: int) -> None:
        """Set the title, unit, minimum scale range and number of digits; the buffer holds CHART_S seconds at ~30 samples/s."""
        super().__init__()
        self.title, self.unit, self.min_range, self.digits = title, unit, min_range, digits
        self.data: deque[tuple[float, ...]] = deque(maxlen=round(CHART_S * 1000 / 33))
        self.setMinimumHeight(px(96))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        """The default size of the chart."""
        return QSize(px(300), px(96))

    def rescale(self) -> None:
        """Re-apply the DPI-scaled size after a scale change."""
        self.setMinimumHeight(px(96))
        self.updateGeometry()

    def push(self, values: Iterable[float]) -> None:
        """Append one sample (x, y, z) and repaint."""
        self.data.append(tuple(values))
        self.update()

    def clear(self) -> None:
        """Drop all samples."""
        self.data.clear()
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        """Paint the frame, the zero line, the three axis traces (scale = the largest value rounded by _nice).

        The title, the latest values and the scale.
        """
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(TRACK))
        p.drawRoundedRect(r, px(8), px(8))
        top = px(20)
        plot = r.adjusted(px(6), top, -px(6), -px(6))
        rng = _nice(max([self.min_range, *(abs(v) for row in self.data for v in row)]) * 1.1)
        mid = plot.center().y()
        p.setPen(QPen(QColor(LINE), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), mid), QPointF(plot.right(), mid))
        n = self.data.maxlen or 1       # always set (see __init__); `or 1` only keeps the type an int
        if len(self.data) >= MIN_POINTS:
            step = plot.width() / (n - 1)
            x0 = plot.right() - (len(self.data) - 1) * step
            for k, (_name, color) in enumerate(AXES):
                ky = plot.height() / 2 / rng
                # one polyline instead of a path built point by point (the chart refreshes dozens of times a second)
                pts = QPolygonF([QPointF(x0 + j * step, mid - row[k] * ky) for j, row in enumerate(self.data)])
                p.setPen(QPen(QColor(color), 1.6))
                p.drawPolyline(pts)
        p.setFont(_font(11, True))
        p.setPen(QColor(DIM))
        p.drawText(QPointF(r.left() + px(8), r.top() + px(14)), self.title)
        x = r.left() + px(8) + QFontMetricsF(p.font()).horizontalAdvance(self.title) + px(10)
        p.setFont(_font(11))
        fm = QFontMetricsF(p.font())
        last = self.data[-1] if self.data else (0, 0, 0)
        for (name, color), v in zip(AXES, last, strict=True):
            text = f"{name} {v:+.{self.digits}f}"
            p.setPen(QColor(color))
            p.drawText(QPointF(x, r.top() + px(14)), text)
            x += fm.horizontalAdvance(text) + px(8)
        scale = f"±{rng:g} {self.unit}"
        p.setPen(QColor(MUTED))
        p.drawText(QPointF(r.right() - px(8) - fm.horizontalAdvance(scale), r.top() + px(14)), scale)


class StickScope(QWidget):
    """Both sticks live.

    A square-root scale (r -> √r): a 1 % deviation takes 10 % of the radius, so drift is visible, and a full deflection
    still reaches the edge. A dashed circle = the dead zone from the drift correction, yellow = the peak of the last
    measurement.
    """

    RINGS = (0.05, 0.25)

    def __init__(self) -> None:
        """Start with both sticks centred, no trail, no dead zone circles and no peaks."""
        super().__init__()
        self.stick_pos = {"left": (0.0, 0.0), "right": (0.0, 0.0)}      # not `pos`: that would shadow QWidget.pos()
        self.trail = {"left": deque(maxlen=TRAIL), "right": deque(maxlen=TRAIL)}
        self.dz: dict[str, float] = {}         # drift correction {stick: dz}
        self.peak: dict[str, float] = {}       # the last measurement {stick: peak}
        self.setMinimumHeight(px(132))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        """The default size of the scope."""
        return QSize(px(300), px(132))

    def rescale(self) -> None:
        """Re-apply the DPI-scaled size after a scale change."""
        self.setMinimumHeight(px(132))
        self.updateGeometry()

    def set_pos(self, ls: Sequence[int], rs: Sequence[int]) -> None:
        """Set both stick positions (raw values -> unit range); the trail grows only when a position changed."""
        pos = {"left": (D.unit(ls[0]), D.unit(ls[1])), "right": (D.unit(rs[0]), D.unit(rs[1]))}
        if pos != self.stick_pos:
            for k, v in pos.items():
                self.trail[k].append(v)
            self.stick_pos = pos
            self.update()

    def set_marks(self, dz: Mapping[str, float], peak: Mapping[str, float]) -> None:
        """Set the dead zone circles and the measurement peaks; repaint on change."""
        if (dz, peak) != (self.dz, self.peak):
            self.dz, self.peak = dict(dz), dict(peak)
            self.update()

    def clear(self) -> None:
        """Drop the trails and the last peaks."""
        for t in self.trail.values():
            t.clear()
        self.peak = {}
        self.update()

    @staticmethod
    def _r(v: float) -> float:
        """The square-root scale of a radius clamped to 0..1."""
        return math.sqrt(min(1.0, max(0.0, v)))

    def paintEvent(self, _e: QPaintEvent) -> None:
        """Paint each stick.

        The plate, reference rings, the dead zone and peak circles, the fading trail, the current position and its value.
        """
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        rad = min(h / 2 - px(14), w / 4 - px(8))
        for k, name in enumerate(("left", "right")):
            c = QPointF(w * (0.25 + 0.5 * k), h / 2 - px(6))
            p.setPen(QPen(QColor(BORDER), 1))
            p.setBrush(QColor(TRACK))
            p.drawEllipse(c, rad, rad)
            p.setPen(QPen(QColor(LINE), 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for ring in self.RINGS:
                p.drawEllipse(c, rad * self._r(ring), rad * self._r(ring))
            p.drawLine(QPointF(c.x() - rad, c.y()), QPointF(c.x() + rad, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - rad), QPointF(c.x(), c.y() + rad))
            if d := self.dz.get(name):
                p.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(QColor(76, 141, 255, 40))
                p.drawEllipse(c, rad * self._r(d), rad * self._r(d))
            if pk := self.peak.get(name):
                p.setPen(QPen(QColor(WARN), 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, rad * self._r(pk), rad * self._r(pk))
            p.setPen(Qt.PenStyle.NoPen)
            trail = list(self.trail[name])
            for j, (x, y) in enumerate(trail[:-1]):
                p.setBrush(QColor(255, 255, 255, 25 + 120 * j // TRAIL))
                p.drawEllipse(self._pt(c, rad, x, y), px(2), px(2))
            x, y = self.stick_pos[name]
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.setBrush(QColor(ACCENT))
            p.drawEllipse(self._pt(c, rad, x, y), px(5), px(5))
            p.setFont(_font(11, True))
            p.setPen(QColor(DIM))
            text = "L" if name == "left" else "R"
            p.drawText(QPointF(c.x() - rad, c.y() - rad + px(10)), text)
            p.setFont(_font(11))
            p.setPen(QColor(FG))
            val = f"{x:+.3f}  {y:+.3f}"
            fm = QFontMetricsF(p.font())
            p.drawText(QPointF(c.x() - fm.horizontalAdvance(val) / 2, c.y() + rad + px(14)), val)

    def _pt(self, c: QPointF, rad: float, x: float, y: float) -> QPointF:
        """A unit-range position -> a point on the scaled plate (the direction kept, the radius square-rooted)."""
        r = math.hypot(x, y)
        k = rad * self._r(r) / r if r else 0.0
        return QPointF(c.x() + x * k, c.y() + y * k)


def _head(icon_name: str, text: str) -> QHBoxLayout:
    """A section heading row: a small icon and an h2 label."""
    row = QHBoxLayout()
    row.setSpacing(px(6))
    row.addWidget(icon_label(icon_name, px(16)))
    row.addWidget(label(text, "h2"), 1)
    return row


class TestPanel(QWidget):
    """The right panel in the Test view. block_changed(bool) - the user toggled the lock of output to the game."""

    block_changed = Signal(bool)

    def __init__(self, block_on: bool = True) -> None:
        """Build the panel: the output lock, motion charts, the stick scope with drift measurement, and the gyro calibration."""
        super().__init__()
        self.slot = 0
        self.run: D.DriftRun | None = None            # in progress
        self.result: D.DriftResult | None = None      # the last drift measurement result
        self.cal_t0: float | None = None              # gyro calibration: when the command was sent (None = nothing running)
        self.cal_note: tuple[str, str] | None = None  # (text, level) of the calibration result that should stay visible
        self.query_t = 0.0               # when we last asked the sender for the calibration state
        self._shown: tuple[Any, ...] | None = None    # the state refresh() last drew (tick() redraws only when it differs)
        body = QWidget()
        lay = _vbox(body)
        _vbox(self).addWidget(scrolled(body))

        self._build_lock(lay, block_on)
        self._build_motion(lay)
        self._build_drift(lay)
        self._build_gyro(lay)
        lay.addStretch(1)
        self._buttons = (self.measure, self.apply, self.cal_btn)
        self.reset_view()

    def _build_lock(self, lay: QVBoxLayout, block_on: bool) -> None:
        """The checkbox that blocks the output to the game while testing."""
        row = QHBoxLayout()
        row.setSpacing(px(6))
        row.addWidget(icon_label("shield", px(18)))
        self.block = QCheckBox(_("Blokuj wyjście do gry"))
        self.block.setToolTip(_("Na czas testu gra dostaje puszczone przyciski i gałki w środku tego pada; skróty pada, "
                                "mysz touchpada i tryb konsoli też nie reagują. Działa tylko przy aktywnym oknie - po "
                                "przejściu do gry albo wyjściu z testu pad od razu do niej wraca."))
        self.block.setChecked(block_on)
        self.block.toggled.connect(self.block_changed.emit)
        row.addWidget(self.block, 1)
        lay.addLayout(row)

    def _build_motion(self, lay: QVBoxLayout) -> None:
        """The gyro and accelerometer charts."""
        lay.addLayout(_head("pulse", _("RUCH")))
        self.gyro_chart = MotionChart(_("Gyro"), "°/s", 10.0, 1)
        self.acc_chart = MotionChart(_("Akcelerometr"), "g", 1.2, 2)
        lay.addWidget(self.gyro_chart)
        lay.addWidget(self.acc_chart)

    def _build_drift(self, lay: QVBoxLayout) -> None:
        """The stick scope, the drift measurement with its result and the stored correction."""
        lay.addLayout(_head("target", _("GAŁKI - DRYF")))
        self.scope = StickScope()
        self.scope.setToolTip(_("Skala pierwiastkowa: małe odchyłki są powiększone. Kropkowane okręgi: 5% i 25% "
                                "wychylenia; niebieski: strefa martwa z korekcji dryfu; żółty: szczyt ostatniego pomiaru."))
        lay.addWidget(self.scope)
        self.drift_hint = label(_("Puść gałki i odłóż pad - pomiar trwa {s} s.").format(s=f"{D.MEASURE_S:g}"), "dim", wrap=True)
        lay.addWidget(self.drift_hint)
        self.drift_bar = SegmentBar(px(5))
        lay.addWidget(self.drift_bar)
        self.drift_res = label("", wrap=True)
        lay.addWidget(self.drift_res)
        row = QHBoxLayout()
        row.setSpacing(px(8))
        self.measure = button(_("Zmierz dryf"))
        self.measure.setIcon(icon("target"))
        self.measure.clicked.connect(self.start_drift)
        self.apply = button(_("Zastosuj"), primary=True)
        self.apply.setToolTip(_("Zapisuje proponowaną strefę martwą jako korekcję dryfu tego pada - działa od razu "
                                "we wszystkich jego profilach (także profilach gier)."))
        self.apply.clicked.connect(self.apply_drift)
        row.addWidget(self.measure, 1)
        row.addWidget(self.apply, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(px(6))
        self.corr = label("", "dim", wrap=True)
        self.clear_btn = ConfirmButton(_("Wyczyść"), _("Wyczyścić?"), iconic="trash",
                                       tip=_("Wyczyść") + " - " + _("Usuwa korekcję dryfu tego pada"))
        self.clear_btn.confirmed.connect(self.clear_drift)
        row.addWidget(self.corr, 1)
        row.addWidget(self.clear_btn)
        lay.addLayout(row)

    def _build_gyro(self, lay: QVBoxLayout) -> None:
        """The gyro calibration: a hint, a progress bar, the state text and the button."""
        lay.addLayout(_head("gyro", _("ŻYROSKOP - KALIBRACJA")))
        lay.addWidget(label(_("Połóż pad na płaskiej, stabilnej powierzchni i nie dotykaj go przez {s} s.")
                            .format(s=CAL_S), "dim", wrap=True))
        self.cal_bar = SegmentBar(px(5))
        lay.addWidget(self.cal_bar)
        self.cal_text = label("", wrap=True)
        lay.addWidget(self.cal_text)
        self.cal_btn = button(_("Kalibruj żyroskop"))
        self.cal_btn.setIcon(icon("gyro"))
        self.cal_btn.setToolTip(_("Nadajnik liczy offset żyroskopu i zapisuje go na telefonie dla tego pada - "
                                  "po restarcie nie trzeba już kalibrować przy starcie."))
        self.cal_btn.clicked.connect(self.start_cal)
        lay.addWidget(self.cal_btn)

    # ---------- pad choice / view ----------
    def set_slot(self, slot: int) -> None:
        """Switch the panel to another pad and reset the view."""
        if slot != self.slot:
            self.slot = slot
            self.reset_view()

    def activate(self, on: bool) -> None:
        """Entering the Test view (ask for the calibration state from the sender) / leaving it (cancel the measurement)."""
        if on:
            self._query(time.monotonic())
        else:
            self.cancel()

    def _query(self, now: float) -> None:
        """Ask the sender for the pad's gyro calibration state."""
        self.query_t = now
        P.query_gyro_cal(self.slot)

    def cancel(self) -> None:
        """Abort a running drift measurement."""
        if self.run is not None:
            self.run.cancel()
            self.run = None

    def reset_view(self) -> None:
        """Clear charts, results and bars after a pad change, and ask for the calibration state."""
        self.cancel()
        self.result = None
        self.cal_t0 = self.cal_note = None
        self.gyro_chart.clear()
        self.acc_chart.clear()
        self.scope.clear()
        self._bar(self.drift_bar, None)
        self._bar(self.cal_bar, None)
        self._result("", "")
        self._query(time.monotonic())
        self.refresh()

    @staticmethod
    def _bar(bar: SegmentBar, p: float | None) -> None:
        """A progress bar: p = 0..1, None = hidden (nothing running)."""
        bar.setVisible(p is not None)
        if p is not None:
            bar.set([(p, ACCENT), (1 - p, LINE)])

    def _result(self, text: str, level: str) -> None:
        """Show a drift message (hidden when empty) with its level style."""
        self.drift_res.setText(text)
        self.drift_res.setVisible(bool(text))
        set_prop(self.drift_res, "level", level)

    def rescale(self) -> None:
        """Apply the new DPI scale to the charts, icons and bars."""
        for w in (self.gyro_chart, self.acc_chart, self.scope):
            w.rescale()
        for b in self._buttons:
            b.setIconSize(QSize(px(18), px(18)))
        self.drift_bar.setFixedHeight(px(5))
        self.cal_bar.setFixedHeight(px(5))

    # ---------- drift ----------
    def start_drift(self) -> None:
        """Start a drift measurement of the selected pad (needs it connected)."""
        if not S.alive[self.slot]:
            return
        self.cancel()
        self.result = None
        self.scope.clear()
        self.run = D.DriftRun(self.slot, time.monotonic(), P.raw_sticks if P.sender_calib(self.slot) else None)
        self.refresh()

    def apply_drift(self) -> None:
        """Save the proposed dead zones as the pad's drift correction (not after a measurement with movement)."""
        if self.result is None or self.result.moved:
            return
        vals = {n: self.result.stick(n).dz for n in ("left", "right")}
        ok, msg = P.set_drift(self.slot, vals)
        self._result(("✓ " + _("Zastosowano: L {l}, R {r}").format(l=pct(vals["left"]), r=pct(vals["right"])))
                     if ok else msg, "ok" if ok else "bad")
        self.result = None
        self.refresh()

    def clear_drift(self) -> None:
        """Remove the pad's drift correction."""
        ok, msg = P.set_drift(self.slot, {"left": 0, "right": 0})
        self._result(("✓ " + _("Korekcja dryfu usunięta")) if ok else msg, "ok" if ok else "bad")
        self.refresh()

    def _drift_tick(self, now: float) -> None:
        """Advance a running measurement: show progress, and on completion keep and show the result (a disconnected pad aborts it)."""
        if self.run is None:
            return
        if not S.alive[self.slot]:
            self.cancel()
            self._bar(self.drift_bar, None)
            self.refresh()
            return
        res = self.run.tick(now)
        self._bar(self.drift_bar, self.run.progress(now))
        if res is None and self.run.phase != "done":
            return
        self.run = None
        self._bar(self.drift_bar, None)
        self.result = res
        self._show_result(res)
        self.refresh()

    def _show_result(self, res: D.DriftResult | None) -> None:
        """Describe a measurement: no samples, stick moved, or the drift and the proposed dead zone per stick."""
        if res is None:
            self._result(_("Brak próbek - pad niepodłączony?"), "bad")
            return
        if res.moved:
            self._result(_("Gałka się ruszała - puść obie gałki i zmierz jeszcze raz."), "bad")
            return
        lines = [_("{s}: dryf {peak} → strefa martwa {dz}").format(
                     s=_("Lewa") if n == "left" else _("Prawa"), peak=pct(res.stick(n).peak, 1), dz=pct(res.stick(n).dz))
                 for n in ("left", "right")]
        if not P.sender_calib(self.slot):
            lines.append(_("Nadajnik w starszej wersji przycina odchyłki do ~2% - kliknij Aktualizuj, żeby mierzyć dokładnie."))
        self._result("\n".join(lines), "")

    # ---------- gyroscope ----------
    def start_cal(self) -> None:
        """Ask the sender to calibrate the gyro of the selected pad."""
        if P.calibrate_gyro(self.slot, CAL_S):
            self.cal_t0, self.cal_note = time.monotonic(), None
        self.refresh()

    def _cal_tick(self, now: float) -> None:
        """Follow a calibration.

        Progress bar, the sender's answer (or a timeout) and its result note; without a calibration, re-ask for a lost
        state.
        """
        if self.cal_t0 is None:
            if (S.gyro_cal[self.slot] is None and now - self.query_t >= QUERY_S and S.alive[self.slot]
                    and P.sender_calib(self.slot)):
                self._query(now)             # the answer to the state query was lost (UDP) - once more
            return
        r = S.gyro_cal[self.slot]
        done = r is not None and r[5] >= self.cal_t0 and r[0] not in (CAL_RUNNING, CAL_STATUS)
        if done or now - self.cal_t0 > CAL_S + CAL_WAIT_S:
            self.cal_t0 = None
            self._bar(self.cal_bar, None)
            if done and r is not None:       # done implies an answer; the check also narrows the type
                saved = bool(r[1] & CALF_STORED)
                self.cal_note = {CAL_OK: ("✓ " + (_("Skalibrowano i zapisano na telefonie.") if saved else
                                                  _("Skalibrowano (pad bez MAC - bez zapisu, do restartu nadajnika).")),
                                          "ok"),
                                 CAL_MOVED: (_("Pad się ruszał - połóż go nieruchomo i spróbuj jeszcze raz."), "bad"),
                                 CAL_FEW: (_("Brak danych z czujnika ruchu pada."), "bad")}.get(r[0])
            else:
                self.cal_note = (_("Brak odpowiedzi nadajnika."), "bad")
            return
        self._bar(self.cal_bar, min(1.0, (now - self.cal_t0) / CAL_S))

    def _cal_status(self) -> tuple[str, str]:
        """(text, level) of the pad's calibration state."""
        i = self.slot
        if not S.alive[i]:
            return _("Pad niepodłączony."), ""
        if not P.sender_calib(i):
            return _("Kalibracja z okna wymaga nowszego nadajnika - kliknij Aktualizuj."), "warn"
        if self.cal_t0 is not None:
            return _("Kalibruję - nie ruszaj pada…"), ""
        r = S.gyro_cal[i]
        head = self.cal_note
        if r is None:
            return (head[0], head[1]) if head else (_("Sprawdzam stan kalibracji…"), "")
        _status, flags, _stored, cur, t, _rx = r
        if flags & CALF_STORED and t:
            base = _("Zapisana kalibracja z {date}.").format(date=time.strftime("%Y-%m-%d %H:%M", time.localtime(t)))
        else:
            base = _("Brak zapisanej kalibracji - nadajnik kalibruje pad przy starcie (pad musi wtedy leżeć).")
        offs = "  ".join(f"{n} {v / C.GYRO_RES:+.2f}" for (n, _c), v in zip(AXES, cur, strict=True))
        text = base + "\n" + _("Offset teraz [°/s]: {v}").format(v=offs)
        if head:
            return head[0] + "\n" + text, head[1]
        return text, ""

    # ---------- refreshing ----------
    def _state(self) -> tuple[Any, ...]:
        """Everything refresh() shows, as a comparable tuple: when it is unchanged there is nothing to redraw."""
        i = self.slot
        return (i, bool(S.alive[i]), self.run is not None, id(self.result), self.cal_t0, self.cal_note, S.gyro_cal[i],
                P.sender_calib(i), tuple(sorted(P.drift(i).items())))

    def refresh(self) -> None:
        """Buttons and descriptions (on a state change, not every frame)."""
        self._shown = self._state()
        i = self.slot
        alive = bool(S.alive[i])
        busy = self.run is not None
        self.measure.setEnabled(alive and not busy)
        self.measure.setText(_("Mierzę…") if busy else _("Zmierz dryf"))
        self.apply.setEnabled(self.result is not None and not self.result.moved and not busy)
        d = P.drift(i)
        self.corr.setText(_("Korekcja dryfu: L {l} · R {r}").format(l=pct(d["left"]) if d.get("left") else "—",
                                                                   r=pct(d["right"]) if d.get("right") else "—")
                          if d else _("Bez korekcji dryfu - strefa martwa tylko z profilu."))
        self.clear_btn.setVisible(bool(d))
        peak = {} if self.result is None or self.result.moved else {n: self.result.stick(n).peak for n in ("left", "right")}
        self.scope.set_marks(d, peak)
        self.cal_btn.setEnabled(alive and P.sender_calib(i) and self.cal_t0 is None)
        text, level = self._cal_status()
        if text != self.cal_text.text():
            self.cal_text.setText(text)
        set_prop(self.cal_text, "level", level)

    def tick(self, now: float, live: Mapping[str, Any]) -> dict[str, Any]:
        """From the window clock (30 Hz).

        Charts, sticks, the progress of the measurement and calibration. live = rx_profiles.live() of the pad.
        """
        t = P.test_live(self.slot)
        self.gyro_chart.push(v / C.GYRO_RES for v in t["gyro"])
        self.acc_chart.push(v / C.ACC_RES for v in t["acc"])
        self.scope.set_pos(live["ls"], live["rs"])
        self._drift_tick(now)
        self._cal_tick(now)
        if self._state() != self._shown:
            self.refresh()
        return t

