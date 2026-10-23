"""Receiver: small window widgets.

A slider with a value, a button with confirmation, a battery bar, a switch, a collapsible "Advanced" section, a
latency segment bar, the sender pill that shows the sender device's battery.
"""
import math
import time
from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPaintEvent, QWheelEvent
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from rx_qt_style import CHARGE, LINE, OK, TRACK, px, set_prop


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    """A label with plain text.

    Part of the content comes from the network (the sender, Bluetooth names, the pad id) - the default AutoText would
    render HTML tags in it.
    """
    lb = QLabel(text)
    lb.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    return lb


def button(text: str, primary: bool = False, small: bool = False) -> QPushButton:
    """A push button with the `primary` / `small` style properties and a pointing-hand cursor."""
    b = QPushButton(text)
    b.setProperty("primary", primary)
    b.setProperty("small", small)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)    # keyboard / pad focus (TV mode); a mouse click does not leave it behind
    return b


def icon_button(name: str, tip_text: str, size: int = 20) -> QPushButton:
    """A button with only an icon (rx_qt_icons) and a tooltip - the window header, the pad card."""
    from rx_qt_icons import icon
    b = button("")
    b.setProperty("iconic", True)
    b.setIcon(icon(name))
    b.setIconSize(QSize(px(size), px(size)))
    b.setToolTip(tip_text)
    return b


def spinbox(lo: int, hi: int, step: int = 1, suffix: str = "") -> QSpinBox:
    """A number field.

    The style sheet's padding does not move the QSpinBox text field (the text sticks to the left edge, unlike QLineEdit
    / QComboBox) - the left gap is made through the inner field's text margins.
    """
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.lineEdit().setTextMargins(px(10), 0, 0, 0)
    return box


def icon_label(name: str, size: int = 22) -> QLabel:
    """A pad button icon as a QLabel (the full name in the tooltip)."""
    from rx_qt_icons import pixmap, tip
    lb = QLabel()
    pm = pixmap(name, size * 2)
    pm.setDevicePixelRatio(2)          # sharp also with 150-200 % screen scaling
    lb.setPixmap(pm)
    lb.setFixedSize(size, size)
    lb.setToolTip(tip(name))
    return lb


def group(title: str, *items: QWidget | QLayout) -> QFrame:
    """A settings window section: a frame with a title and its content (widgets or layouts) one under another."""
    box = QFrame()
    box.setObjectName("group")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(14, 12, 14, 14)
    lay.setSpacing(10)
    if title:
        lay.addWidget(label(title, "h2"))
    for it in items:
        if isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return box


def pair(a: QWidget, b: QWidget) -> QHBoxLayout:
    """Two fields side by side, equal width."""
    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(a, 1)
    row.addWidget(b, 1)
    return row


def left(widget: QWidget) -> QHBoxLayout:
    """A widget (e.g. a small button) at its natural width, at the left edge - not stretched over the whole row."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(widget)
    row.addStretch(1)
    return row


def field(caption: str, widget: QWidget) -> QWidget:
    """A small caption above a widget (as in the browser editor)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(label(caption, "dim", wrap=True))
    lay.addWidget(widget)
    return w


def input_items(names: Iterable[str], text: bool = False) -> list[tuple[str, str, str]]:
    """Pad inputs as list items: the button icon (the full name in the tooltip); text=True adds the name next to it."""
    from rx_qt_icons import tip
    return [(n, tip(n) if text else "", n) for n in names]


class Combo(QComboBox):
    """A list with values (userData) - selecting by value, not by index; set_data emits no signals.

    An item: (value, text) or (value, text, icon) - the icon is a name from rx_qt_icons (a pad button etc.).
    """

    changed = Signal(object)     # the new value (userData)

    def __init__(self, items: Iterable[tuple[Any, ...]] = ()) -> None:
        """Build the list from `items`; `changed` fires only on user selection (activated), the tooltip follows the current item."""
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(22, 22))
        # a long item does not stretch the window - the list takes the width from the layout, the text is elided
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        self.set_items(items)
        self.activated.connect(lambda i: self.changed.emit(self.itemData(i)))
        self.currentIndexChanged.connect(lambda i: self.setToolTip(self.itemData(i, Qt.ItemDataRole.ToolTipRole) or ""))

    def set_items(self, items: Iterable[tuple[Any, ...]]) -> None:
        """Replace all items without emitting signals; an item with an icon also gets that icon's tooltip."""
        from rx_qt_icons import icon, tip
        self.blockSignals(True)
        self.clear()
        for value, text, *ic in items:
            if ic and ic[0]:
                self.addItem(icon(ic[0]), text, value)
                self.setItemData(self.count() - 1, tip(ic[0]), Qt.ItemDataRole.ToolTipRole)
            else:
                self.addItem(text, value)
        self.blockSignals(False)
        self.setToolTip(self.itemData(self.currentIndex(), Qt.ItemDataRole.ToolTipRole) or "")

    def set_data(self, value: Any) -> None:
        """Select the item with this value (the first one if absent) without emitting signals."""
        i = self.findData(value)
        self.blockSignals(True)
        self.setCurrentIndex(i if i >= 0 else 0)
        self.blockSignals(False)
        self.setToolTip(self.itemData(self.currentIndex(), Qt.ItemDataRole.ToolTipRole) or "")

    def wheelEvent(self, e: QWheelEvent) -> None:
        """Ignore the wheel: it should scroll the panel, not change the list under the cursor."""
        e.ignore()


class _Slider(QSlider):
    """A slider that reacts to the wheel only when focused, so scrolling a panel does not change values."""
    def wheelEvent(self, e: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class SliderRow(QWidget):
    """A caption + value above a slider. Values are in display units; unit: "%", "x" (e.g. 1.25×) or ""."""

    changed = Signal(float)      # only after a user move, not after set()

    def __init__(self, caption: str, lo: float, hi: float, step: float, unit: str = "", fmt: Callable[[float], str] | None = None) -> None:
        """A slider over lo..hi (display units) in steps of `step`; `fmt` replaces the unit in the value text."""
        super().__init__()
        self.lo, self.step, self.unit, self.fmt = lo, step, unit, fmt   # fmt(v) -> the value text (instead of unit)
        self.title, self.val = label(caption, "dim", wrap=True), label("", "val")
        self.slider = _Slider(Qt.Orientation.Horizontal)
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.slider.setRange(0, round((hi - lo) / step))
        self.slider.valueChanged.connect(self._moved)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.val)
        lay.addLayout(top)
        lay.addWidget(self.slider)

    def _text(self, v: float) -> str:
        """The displayed value text: from fmt, or by unit."""
        if self.fmt is not None:
            return self.fmt(v)
        return f"{round(v)}%" if self.unit == "%" else f"{v:.2f}×" if self.unit == "x" else str(round(v))

    def value(self) -> float:
        """The current value in display units (lo + position * step)."""
        return self.lo + self.slider.value() * self.step

    def set(self, v: float) -> None:
        """Set the value without emitting `changed` and refresh the label."""
        self.slider.blockSignals(True)
        self.slider.setValue(round((v - self.lo) / self.step))
        self.slider.blockSignals(False)
        self.val.setText(self._text(self.value()))

    def _moved(self, _pos: int) -> None:
        """User moved the slider: refresh the label and emit `changed`."""
        v = self.value()
        self.val.setText(self._text(v))
        self.changed.emit(v)


class ConfirmButton(QPushButton):
    """A button that needs a second click within a few seconds (instead of a dialog).

    The first click changes the label and turns it red, the second emits `confirmed`. iconic: icon only, the label
    appears once armed.
    """

    confirmed = Signal()

    def __init__(self, text: str, armed: str = "Sure?", timeout_ms: int = 3000, small: bool = False,
                 iconic: str | None = None, tip: str = "") -> None:
        """`iconic` = an icon name: an icon-only button whose text becomes the tooltip (`tip` overrides it)."""
        super().__init__("" if iconic else text)
        self._text, self._armed = ("" if iconic else text), armed
        self.setProperty("small", small)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if iconic:
            from rx_qt_icons import icon
            self.setProperty("iconic", True)
            self.setIcon(icon(iconic))
            self.setIconSize(QSize(px(20), px(20)))
            self.setToolTip(tip or text)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self.disarm)
        self.clicked.connect(self._click)

    def _click(self) -> None:
        """First click arms the button and starts the timer; a click while armed confirms."""
        if self._timer.isActive():
            self.disarm()
            self.confirmed.emit()
        else:
            self.setText(self._armed)
            set_prop(self, "armed", True)
            self._timer.start()

    def disarm(self) -> None:
        """Back to the normal label and style (also called by the timeout)."""
        self._timer.stop()
        self.setText(self._text)
        set_prop(self, "armed", False)


class Collapsible(QWidget):
    """A collapsible section ("▸ ADVANCED"): rarely changed settings tucked away so the panel does not overwhelm.

    set_open(True) from code when something inside has been changed (so it is visible that the setting is in effect).
    """

    toggled = Signal(bool)

    def __init__(self, title: str, *items: QWidget | QLayout, opened: bool = False) -> None:
        """Wrap `items` (widgets or layouts) under a clickable header; `opened` = initially expanded."""
        super().__init__()
        self._title = title
        self.head = QPushButton()
        self.head.setObjectName("fold")
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.head.clicked.connect(lambda: self.set_open(not self.body.isVisibleTo(self)))
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 2, 0, 0)
        bl.setSpacing(10)
        for it in items:
            if isinstance(it, QWidget):
                bl.addWidget(it)
            else:
                bl.addLayout(it)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.head)
        lay.addWidget(self.body)
        self.set_open(opened)

    def is_open(self) -> bool:
        """Whether the body is shown."""
        return self.body.isVisibleTo(self)

    def set_open(self, on: bool) -> None:
        """Show / hide the body, update the arrow in the header and emit `toggled`."""
        on = bool(on)
        self.body.setVisible(on)
        self.head.setText(("▾  " if on else "▸  ") + self._title)
        self.toggled.emit(on)


class SegmentBar(QWidget):
    """A thin bar of coloured segments (latency components): a segment's width = its share of the total."""

    def __init__(self, height: int = 6) -> None:
        """An empty bar `height` px high."""
        super().__init__()
        self.setFixedHeight(height)
        self.parts: tuple[tuple[float, str], ...] = ()

    def sizeHint(self) -> QSize:
        """A wide, flat default size."""
        return QSize(120, self.height())

    def set(self, parts: Iterable[tuple[float, str]]) -> None:
        """Parts = [(value, colour)]; an empty list = the track only."""
        parts = tuple((max(0.0, v), c) for v, c in parts)
        if parts != self.parts:
            self.parts = parts
            self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        """Clip to a rounded track, then paint each segment proportionally (a 1.5 px gap between segments)."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.setClipPath(track)
        p.fillRect(QRectF(0, 0, w, h), QColor(LINE))
        total = sum(v for v, _ in self.parts)
        x = 0.0
        for k, (v, color) in enumerate(self.parts):
            seg = w * v / total if total > 0 else 0
            last = k == len(self.parts) - 1
            p.fillRect(QRectF(x, 0, seg if last else max(0.0, seg - 1.5), h), QColor(color))
            x += seg


class BatteryBar(QWidget):
    """A rounded battery bar; pct = None -> an empty track (unknown state)."""

    def __init__(self, height: int = 10) -> None:
        """A bar `height` px high showing an unknown level."""
        super().__init__()
        self.setFixedHeight(height)
        self.pct: float | None = None
        self.color = QColor(OK)

    def sizeHint(self) -> QSize:
        """A wide, flat default size."""
        return QSize(120, self.height())

    def set(self, pct: float | None, color: Any) -> None:
        """Update the percentage and colour; repaint only when something changed."""
        color = QColor(color)
        if (pct, color) != (self.pct, self.color):
            self.pct, self.color = pct, color
            self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        """Paint the track, then the filled part (never narrower than its height)."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TRACK))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        if self.pct:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(0, 0, max(w * self.pct / 100, h), h), h / 2, h / 2)   # min. width = a dot


class BatteryPill(QLabel):
    """The sender pill ("● Sender online (87%)"); with a battery its charge is the background, filled from the left.

    The fill colour follows the level (the caller picks it). While the device charges, a wave runs along the top edge of
    the pill (inside the top padding, over the filled part only) - the background itself stays still. Without a battery
    (pct = None) it is a plain QSS pill. The wave is a repaint timer that runs only while charging, only for a visible
    window, and not while `animate()` says no (a game in the foreground: the window is not worth any CPU then).
    """

    FILL_ALPHA = 90            # the text on top must stay readable
    WAVE_MS = 50               # 20 frames a second is smooth enough for a slow wave
    WAVE_PERIOD_S = 1.6        # the wave moves one wavelength in this time

    def __init__(self, animate: Callable[[], bool] = lambda: True) -> None:
        """A pill without a battery; `animate` is asked before every wave frame."""
        super().__init__("")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setObjectName("pill")
        self._animate = animate
        self._pct: int | None = None
        self._color = QColor(OK)
        self._charging = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.WAVE_MS)
        self._timer.timeout.connect(self._tick)

    def set_battery(self, pct: int | None, color: Any = OK, charging: bool = False) -> None:
        """Show the charge level (None = no battery: a plain pill); repaints only when something changed."""
        color = QColor(color)
        if (pct, color, charging) == (self._pct, self._color, self._charging):
            return
        self._pct, self._color, self._charging = pct, color, charging
        set_prop(self, "battery", pct is not None)      # the QSS drops the opaque background, so the fill can be painted
        if charging and pct and not self._timer.isActive():
            self._timer.start()
        elif not (charging and pct):
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        """One wave frame - skipped while the window is hidden / minimised or a game is in the foreground."""
        if self.isVisible() and not self.window().isMinimized() and self._animate():
            self.update()

    def _wave(self, width: float) -> QPainterPath:
        """The band along the top edge, `width` px wide, with a wavy lower edge that moves to the right."""
        wavelength, amp, base = px(26), px(1.5), px(3)
        phase = 2 * math.pi * ((time.monotonic() % self.WAVE_PERIOD_S) / self.WAVE_PERIOD_S)
        path = QPainterPath()
        path.moveTo(0, 0)
        step = max(px(3), 2)
        x = 0.0
        while x < width:
            path.lineTo(x, base + amp * (1 + math.sin(2 * math.pi * x / wavelength - phase)))
            x += step
        path.lineTo(width, base + amp * (1 + math.sin(2 * math.pi * width / wavelength - phase)))
        path.lineTo(width, 0)
        path.closeSubpath()
        return path

    def paintEvent(self, e: QPaintEvent) -> None:
        """Paint the track, the charge fill and (charging) the wave; then the QLabel draws the border and the text."""
        if self._pct is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), px(11), px(11))     # the radius of QLabel#pill in the QSS
            p.setClipPath(shape)
            p.fillPath(shape, QColor(TRACK))
            fill_w = w * self._pct / 100
            fill = QColor(self._color)
            fill.setAlpha(self.FILL_ALPHA)
            p.fillRect(QRectF(0, 0, fill_w, h), fill)
            if self._charging and fill_w > 0:
                wave = QColor(CHARGE)
                wave.setAlpha(210)
                p.fillPath(self._wave(fill_w), wave)
            p.end()                       # the QLabel paints on the same device right after this
        super().paintEvent(e)


class Segmented(QFrame):
    """An option switch in one "rail" ([ Result ][ Physical ]).

    The selected option is a raised pill, the options have equal width and spacing, so they do not merge into one
    button.
    """

    changed = Signal(str)
    GAP = 4

    def __init__(self, options: Iterable[tuple[str, str]]) -> None:
        """`options` = [(key, text)], laid out in one rail."""
        super().__init__()
        self.setObjectName("segment")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(self.GAP)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("seg", "true")
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _c=False, k=key: self.changed.emit(k))
        self._equalize()

    def _equalize(self) -> None:
        """Equal option width (the width of the longest text, measured for the bold - selected - version)."""
        widths = []
        for b in self.buttons.values():
            f = b.font()
            f.setBold(True)
            widths.append(QFontMetrics(f).horizontalAdvance(b.text()))
        w = max(widths, default=0) + 36
        for b in self.buttons.values():
            b.setMinimumWidth(w)

    def set(self, key: str) -> None:
        """Check the option with this key."""
        self.buttons[key].setChecked(True)

    def value(self) -> str:
        """The key of the checked option."""
        return next(k for k, b in self.buttons.items() if b.isChecked())
