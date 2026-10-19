"""Receiver: małe widżety okna - suwak z wartością, przycisk z potwierdzeniem, pasek baterii, przełącznik,
zwijana sekcja "Zaawansowane", pasek odcinków opóźnienia."""
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout,
                               QWidget)

from rx_qt_style import LINE, OK, TRACK, px, set_prop


def label(text="", role=None, wrap=False):
    """Etykieta ze zwykłym tekstem: część treści przychodzi z sieci (nadajnik, nazwy Bluetooth, identyfikator pada) -
    domyślny AutoText wyświetliłby w niej tagi HTML."""
    lb = QLabel(text)
    lb.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    return lb


def button(text, primary=False, small=False):
    b = QPushButton(text)
    b.setProperty("primary", primary)
    b.setProperty("small", small)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)    # fokus z klawiatury / pada (tryb TV), klik myszą go nie zostawia
    return b


def icon_button(name, tip_text, size=20):
    """Przycisk z samą ikoną (rx_qt_icons) i podpowiedzią - nagłówek okna, karta pada."""
    from rx_qt_icons import icon
    b = button("")
    b.setProperty("iconic", True)
    b.setIcon(icon(name))
    b.setIconSize(QSize(px(size), px(size)))
    b.setToolTip(tip_text)
    return b


def spinbox(lo, hi, step=1, suffix=""):
    """Pole liczbowe. Padding z arkusza stylu nie przesuwa pola tekstowego QSpinBox (tekst przyklejony do lewej
    krawędzi, inaczej niż w QLineEdit / QComboBox) - lewy odstęp przez marginesy wewnętrznego pola."""
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.lineEdit().setTextMargins(px(10), 0, 0, 0)
    return box


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

    def __init__(self, caption, lo, hi, step, unit="", fmt=None):
        super().__init__()
        self.lo, self.step, self.unit, self.fmt = lo, step, unit, fmt   # fmt(v) -> tekst wartości (zamiast unit)
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
        if self.fmt is not None:
            return self.fmt(v)
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
    kliknięcie zmienia napis i kolor na czerwony, drugie wysyła `confirmed`. iconic: sama ikona, po uzbrojeniu napis."""

    confirmed = Signal()

    def __init__(self, text, armed="Sure?", timeout_ms=3000, small=False, iconic=None, tip=""):
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


class Collapsible(QWidget):
    """Zwijana sekcja ("▸ ZAAWANSOWANE"): rzadko zmieniane ustawienia schowane, żeby panel nie przytłaczał.
    set_open(True) z kodu, gdy w środku jest coś zmienionego (widać, że ustawienie działa)."""

    toggled = Signal(bool)

    def __init__(self, title, *items, opened=False):
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

    def is_open(self):
        return self.body.isVisibleTo(self)

    def set_open(self, on):
        on = bool(on)
        self.body.setVisible(on)
        self.head.setText(("▾  " if on else "▸  ") + self._title)
        self.toggled.emit(on)


class SegmentBar(QWidget):
    """Cienki pasek z kolorowych odcinków (składniki opóźnienia): szerokość odcinka = jego udział w sumie."""

    def __init__(self, height=6):
        super().__init__()
        self.setFixedHeight(height)
        self.parts = ()

    def sizeHint(self):
        return QSize(120, self.height())

    def set(self, parts):
        """parts = [(wartość, kolor)]; pusta lista = sam tor."""
        parts = tuple((max(0.0, v), c) for v, c in parts)
        if parts != self.parts:
            self.parts = parts
            self.update()

    def paintEvent(self, _e):
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


class Segmented(QFrame):
    """Przełącznik opcji w jednej "szynie" ([ Wynik ][ Fizycznie ]): wybrana opcja to wypukła pigułka, opcje mają
    równą szerokość i odstęp, więc nie zlewają się w jeden przycisk."""

    changed = Signal(str)
    GAP = 4

    def __init__(self, options):
        super().__init__()
        self.setObjectName("segment")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(self.GAP)
        self.group = QButtonGroup(self)
        self.buttons = {}
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

    def _equalize(self):
        """Równa szerokość opcji (szerokość najdłuższego tekstu, liczona dla pogrubionej - zaznaczonej - wersji)."""
        widths = []
        for b in self.buttons.values():
            f = b.font()
            f.setBold(True)
            widths.append(QFontMetrics(f).horizontalAdvance(b.text()))
        w = max(widths, default=0) + 36
        for b in self.buttons.values():
            b.setMinimumWidth(w)

    def set(self, key):
        self.buttons[key].setChecked(True)

    def value(self):
        return next(k for k, b in self.buttons.items() if b.isChecked())
