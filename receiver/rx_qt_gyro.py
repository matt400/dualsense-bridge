"""Receiver: the live preview of aiming with the gyro.

It floats over the top-right corner of the pad drawing (the same place as the tilt preview, rx_qt_gstick) while the "Gyro
aiming" section of the Gyro tab is expanded:

  GyroView     drawing: the range circle (the edge = full deflection, GYRO_DPS_FULL / sensitivity deg/s), the anti-deadzone ring
               (stick mode), the rotation of the pad (blue dot) and, in stick mode, the right stick the game gets (crosshair)
  GyroPreview  the frame: the view, the numbers and the state; the window timer calls tick() at 30 Hz while it is shown

The rotation is computed with the same function as the real output (rx_shape.gyro_rates), so the preview also shows what the
game would get while the gyro is not active (a hold button not pressed) or the mode is off. It reads the pad state only; nothing
here changes the output.
"""
from collections.abc import Sequence
from typing import Any, NamedTuple

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from rx_config import C
from rx_i18n import _
from rx_qt_style import ACCENT, CARD, LINE, OK
from rx_qt_widgets import CornerFrame, label
from rx_shape import GYRO_DPS_FULL, _unit, gyro_mouse_velocity, gyro_rates

PREVIEW_W, PREVIEW_GAP, VIEW_PX = 360, 12, 110      # the same size and distance to the corner as the tilt preview [px]
IDLE_COLOR = "#8e96ab"


class Sample(NamedTuple):
    """What the view draws; compared as a whole, so an unchanged state does not repaint."""

    stick_mode: bool
    anti: float                       # anti-deadzone ring radius (0..1 of the range)
    rate: tuple[float, float]         # rotation / full deflection; x right, y down (screen space), may exceed 1
    out: tuple[float, float] | None   # the right stick the game gets (-1..1, y down as in the pad bytes); None outside the stick mode
    active: bool


IDLE = Sample(False, 0.0, (0.0, 0.0), None, False)


class GyroView(QWidget):
    """2D view of the rotation: X = turning left / right (yaw), Y = pitch (down = down). The edge of the circle = full deflection."""

    def __init__(self) -> None:
        """A fixed-size square view, initially at rest."""
        super().__init__()
        self.setFixedSize(VIEW_PX, VIEW_PX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.sample = IDLE

    def set_data(self, sample: Sample) -> None:
        """Show a new sample; repaint only when something changed."""
        if sample != self.sample:
            self.sample = sample
            self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        """Draw the range circle, the anti-deadzone ring, the rotation dot and the stick output crosshair."""
        s = self.sample
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = (min(self.width(), self.height()) - 8) / 2
        p.setBrush(QColor(CARD))
        p.setPen(QPen(QColor(LINE), 1))
        p.drawEllipse(c, r, r)
        if s.stick_mode and s.anti > 0:
            p.setBrush(QColor(LINE))
            p.drawEllipse(c, r * s.anti, r * s.anti)                 # the anti-deadzone: the first push of the stick
        p.setPen(QPen(QColor(LINE), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        x, y = s.rate[0] * r, s.rate[1] * r
        k = max(1.0, (x * x + y * y) ** 0.5 / r)                     # a faster turn stays on the edge of the circle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QPointF(c.x() + x / k, c.y() + y / k), 4, 4)
        if s.out is not None:
            out = QPointF(c.x() + s.out[0] * r, c.y() + s.out[1] * r)
            p.setPen(QPen(QColor(OK if s.active else IDLE_COLOR), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(out, 7, 7)
            p.drawLine(QPointF(out.x() - 11, out.y()), QPointF(out.x() + 11, out.y()))
            p.drawLine(QPointF(out.x(), out.y() - 11), QPointF(out.x(), out.y() + 11))
        p.end()


class GyroPreview(CornerFrame):
    """The frame over the pad drawing: the view, the rotation / output numbers and whether the gyro steers now."""

    def __init__(self, pad: QWidget) -> None:
        """Create the (hidden) preview floating over the pad drawing `pad`."""
        super().__init__(pad, PREVIEW_GAP)
        self.setObjectName("group")
        self._texts: tuple[str, str] | None = None     # the texts last laid out (the size is recomputed only when they change)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(10)
        self.view = GyroView()
        self.numbers = label("", "dim", wrap=True)
        self.status = label("", "dim", wrap=True)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(label(_("CELOWANIE - PODGLĄD"), "h2"))
        texts.addWidget(self.numbers)
        texts.addWidget(self.status)
        texts.addStretch(1)
        lay.addWidget(self.view)
        lay.addLayout(texts, 1)
        self.setFixedWidth(PREVIEW_W)
        self.hide()

    def tick(self, show: bool, g: dict[str, Any], gyro: Sequence[float], active: bool, rs_out: Sequence[int]) -> None:
        """30 Hz from the window timer: show / hide, refresh the view and the texts.

        Args:
            show: whether the section is expanded (and the Gyro tab visible).
            g: the gyro settings of the edited profile (mode, sens, inversion, anti).
            gyro: the pad's raw gyro (pitch, yaw, roll).
            active: the gyro steers now (the profile is in use and the "hold" condition is met).
            rs_out: the right stick the game gets (bytes) - in the stick mode it includes the gyro.
        """
        if not show:
            self.hide()
            return
        fresh = not self.isVisible()
        if fresh:
            self.show()
            self.raise_()
        rates = gyro_rates(gyro, g, C.GYRO_RES)
        full = GYRO_DPS_FULL / g["sens"]
        stick = g["mode"] == "stick"
        out = (_unit(rs_out[0]), _unit(rs_out[1])) if stick else None
        self.view.set_data(Sample(stick, g["anti"], (rates[0] / full, rates[1] / full), out, active))
        numbers = _("Obrót: poziomo {x:+.0f}°/s  pionowo {y:+.0f}°/s").format(x=rates[0], y=rates[1])
        if out is not None:
            numbers += "\n" + _("Prawa gałka (wyjście): X {x:+.0f}%  Y {y:+.0f}%").format(x=out[0] * 100, y=out[1] * 100)
        elif g["mode"] == "mouse":
            vx, vy = gyro_mouse_velocity(rates, g)
            numbers += "\n" + _("Kursor: {x:+.0f} / {y:+.0f} px/s").format(x=vx, y=vy)
        status = (_("Podgląd - gyro jest wyłączone") if g["mode"] == "off"
                  else _("● Steruje") if active else _("Gyro nieaktywne"))
        if (numbers, status) != self._texts:
            self._texts = (numbers, status)
            self.numbers.setText(numbers)
            self.status.setText(status)
            fresh = True
        if fresh:             # a pad resize is handled by the event filter; only new texts / a fresh show need a re-layout
            self.place()
