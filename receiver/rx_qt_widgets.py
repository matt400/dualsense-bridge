"""Receiver: małe widżety okna - suwak z wartością, przycisk z potwierdzeniem, pasek baterii, przełącznik."""
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
                               QWidget)

from rx_qt_style import OK, TRACK, set_prop


def label(text="", role=None, wrap=False):
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    return lb


def button(text, primary=False, small=False):
    b = QPushButton(text)
    b.setProperty("primary", primary)
    b.setProperty("small", small)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def icon_label(name, size=22):
    """Ikona przycisku pada jako QLabel (z pełną nazwą w podpowiedzi)."""
    from rx_qt_icons import pixmap, tip
    lb = QLabel()
    pm = pixmap(name, size * 2)
    pm.setDevicePixelRatio(2)          # ostra także przy skalowaniu ekranu 150-200 %
    lb.setPixmap(pm)
    lb.setFixedSize(size, size)
    lb.setToolTip(tip(name))
    return lb


def group(title, *items):
    """Sekcja okna ustawień: ramka z tytułem i zawartością (widżety albo układy) jedna pod drugą."""
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


def pair(a, b):
    """Dwa pola obok siebie, po równo."""
    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(a, 1)
    row.addWidget(b, 1)
    return row


def left(widget):
    """Widżet (np. mały przycisk) w naturalnej szerokości, przy lewej krawędzi - nie rozciągnięty na cały wiersz."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(widget)
    row.addStretch(1)
    return row


def field(caption, widget):
    """Mały podpis nad widżetem (jak w edytorze w przeglądarce)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(label(caption, "dim", wrap=True))
    lay.addWidget(widget)
    return w


def input_items(names, text=False):
    """Wejścia pada jako pozycje listy: ikona przycisku (pełna nazwa w podpowiedzi); text=True dodaje nazwę obok."""
    from rx_qt_icons import tip
    return [(n, tip(n) if text else "", n) for n in names]


class Combo(QComboBox):
    """Lista z wartościami (userData) - wybór po wartości, a nie po numerze; set_data nie wywołuje sygnałów.
    Pozycja: (wartość, tekst) albo (wartość, tekst, ikona) - ikona to nazwa z rx_qt_icons (przycisk pada itp.)."""

    changed = Signal(object)     # nowa wartość (userData)

    def __init__(self, items=()):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(22, 22))
        # długa pozycja nie rozpycha okna - lista zajmuje szerokość z układu, tekst jest skracany
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        self.set_items(items)
        self.activated.connect(lambda i: self.changed.emit(self.itemData(i)))
        self.currentIndexChanged.connect(lambda i: self.setToolTip(self.itemData(i, Qt.ItemDataRole.ToolTipRole) or ""))

    def set_items(self, items):
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

    def set_data(self, value):
        i = self.findData(value)
        self.blockSignals(True)
        self.setCurrentIndex(i if i >= 0 else 0)
        self.blockSignals(False)
        self.setToolTip(self.itemData(self.currentIndex(), Qt.ItemDataRole.ToolTipRole) or "")

    def wheelEvent(self, e):      # kółko ma przewijać panel, a nie przestawiać listę pod kursorem
        e.ignore()


class _Slider(QSlider):
    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class SliderRow(QWidget):
    """Podpis + wartość nad suwakiem. Wartości w jednostkach wyświetlanych; unit: "%", "x" (np. 1.25×) albo ""."""

    changed = Signal(float)      # tylko po ruchu użytkownika, nie po set()

    def __init__(self, caption, lo, hi, step, unit=""):
        super().__init__()
        self.lo, self.step, self.unit = lo, step, unit
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

    def _text(self, v):
        return f"{round(v)}%" if self.unit == "%" else f"{v:.2f}×" if self.unit == "x" else str(round(v))

    def value(self):
        return self.lo + self.slider.value() * self.step

    def set(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(round((v - self.lo) / self.step))
        self.slider.blockSignals(False)
        self.val.setText(self._text(self.value()))

    def _moved(self, _pos):
        v = self.value()
        self.val.setText(self._text(v))
        self.changed.emit(v)


class ConfirmButton(QPushButton):
    """Przycisk, który wymaga drugiego kliknięcia w ciągu kilku sekund (zamiast okna dialogowego): pierwsze
    kliknięcie zmienia napis i kolor na czerwony, drugie wysyła `confirmed`."""

    confirmed = Signal()

    def __init__(self, text, armed="Sure?", timeout_ms=3000, small=False):
        super().__init__(text)
        self._text, self._armed = text, armed
        self.setProperty("small", small)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self.disarm)
        self.clicked.connect(self._click)

    def _click(self):
        if self._timer.isActive():
            self.disarm()
            self.confirmed.emit()
        else:
            self.setText(self._armed)
            set_prop(self, "armed", True)
            self._timer.start()

    def disarm(self):
        self._timer.stop()
        self.setText(self._text)
        set_prop(self, "armed", False)


class BatteryBar(QWidget):
    """Zaokrąglony pasek baterii; pct = None -> pusty tor (stan nieznany)."""

    def __init__(self, height=10):
        super().__init__()
        self.setFixedHeight(height)
        self.pct, self.color = None, QColor(OK)

    def sizeHint(self):
        return QSize(120, self.height())

    def set(self, pct, color):
        color = QColor(color)
        if (pct, color) != (self.pct, self.color):
            self.pct, self.color = pct, color
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TRACK))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        if self.pct:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(0, 0, max(w * self.pct / 100, h), h), h / 2, h / 2)   # min. szerokość = kropka


class Segmented(QWidget):
    """Przełącznik z dwoma opcjami obok siebie ([Result | Physical])."""

    changed = Signal(str)

    def __init__(self, options):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for n, (key, text) in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("seg", "left" if n == 0 else "right" if n == len(options) - 1 else "true")
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _c=False, k=key: self.changed.emit(k))

    def set(self, key):
        self.buttons[key].setChecked(True)

    def value(self):
        return next(k for k, b in self.buttons.items() if b.isChecked())
