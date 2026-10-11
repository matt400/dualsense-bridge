"""Receiver: ikony przycisków pada i kilku funkcji (QPainter, bez plików graficznych) - zamiast nazw w oknie.

icon(name)  -> QIcon dla wejścia pada ("cross", "ps", "mute", "dpad_up", "l2"...) albo funkcji ("crown", "mouse",
               "tv", "power"); ostre na ekranach HiDPI (rysowane w kilku rozmiarach), zapamiętywane po pierwszym użyciu.
tip(name)   -> pełna nazwa do podpowiedzi (ikona bez tekstu musi mieć tooltip).
Kolory symboli jak na rysunku pada (rx_qt_pad): trójkąt zielony, kółko czerwone, krzyżyk niebieski, kwadrat różowy.
"""
from functools import cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

from rx_buttons import LABELS

FG, DIM, BODY, GOLD = QColor("#e8eaf0"), QColor("#8b91a1"), QColor("#3a3f4d"), QColor("#ffb300")
FACE = {"triangle": "#4cd08a", "circle": "#ff6b6b", "cross": "#5b9bff", "square": "#e58bd2"}
BADGES = {"l1": "L1", "r1": "R1", "l2": "L2", "r2": "R2", "l3": "L3", "r3": "R3"}
EXTRA_TIPS = {"crown": "Pad master", "mouse": "Touchpad jako mysz", "touchpad": "Touchpad jako dotyk DS4",
              "tv": "Tryb konsoli", "power": "Wyłącz"}
SIZES = (16, 20, 24, 32, 48)


def tip(name):
    return LABELS.get(name) or EXTRA_TIPS.get(name, name)


def _pen(color, w):
    p = QPen(QColor(color), w)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


def _disc(p, fill=BODY):
    """Okrągła "nakładka" przycisku - tło ikon przycisków twarzowych, PS i mute."""
    p.setPen(_pen("#565d70", 1.2))
    p.setBrush(fill)
    p.drawEllipse(QRectF(1.5, 1.5, 21, 21))


def _face(p, name):
    _disc(p)
    p.setPen(_pen(FACE[name], 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "cross":
        p.drawLine(QPointF(8, 8), QPointF(16, 16))
        p.drawLine(QPointF(16, 8), QPointF(8, 16))
    elif name == "circle":
        p.drawEllipse(QPointF(12, 12), 4.6, 4.6)
    elif name == "square":
        p.drawRoundedRect(QRectF(7.8, 7.8, 8.4, 8.4), 1, 1)
    else:
        path = QPainterPath(QPointF(12, 6.8))
        path.lineTo(17, 15.5)
        path.lineTo(7, 15.5)
        path.closeSubpath()
        p.drawPath(path)


def _text(p, text, rect, px, color=FG, weight=QFont.Weight.Bold):
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setWeight(weight)
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _badge(p, text):
    """L1 / R2 / L3...: zaokrąglona plakietka (spusty niższe i szersze u góry, kliki gałek okrągłe)."""
    p.setPen(_pen("#565d70", 1.2))
    p.setBrush(BODY)
    if text[1] == "3":
        p.drawEllipse(QRectF(1.5, 1.5, 21, 21))
    elif text[1] == "2":
        path = QPainterPath(QPointF(3, 20))
        path.cubicTo(3, 7, 7, 3, 12, 3)
        path.cubicTo(17, 3, 21, 7, 21, 20)
        path.closeSubpath()
        p.drawPath(path)
    else:
        p.drawRoundedRect(QRectF(1.5, 6, 21, 12), 6, 6)
    _text(p, text, QRectF(0, 1 if text[1] == "2" else 0, 24, 24), 9)


def _dpad(p, name):
    """Krzyż D-pada z podświetlonym kierunkiem."""
    arms = {"dpad_up": QRectF(9, 2, 6, 9), "dpad_down": QRectF(9, 13, 6, 9),
            "dpad_left": QRectF(2, 9, 9, 6), "dpad_right": QRectF(13, 9, 9, 6)}
    p.setPen(Qt.PenStyle.NoPen)
    for k, r in arms.items():
        p.setBrush(GOLD if k == name else BODY)
        p.drawRoundedRect(r, 1.6, 1.6)
    p.setBrush(BODY)
    p.drawRect(QRectF(9, 9, 6, 6))


def _ps(p):
    _disc(p, QColor("#2a2e3a"))
    _text(p, "PS", QRectF(0, 0, 24, 24), 9)


def _mute(p):
    """Mikrofon z ukośną kreską - przycisk wyciszenia pod przyciskiem PS."""
    _disc(p, QColor("#2a2e3a"))
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(9.6, 5.5, 4.8, 8.5), 2.4, 2.4)
    arc = QPainterPath(QPointF(7.5, 11.5))
    arc.cubicTo(7.5, 17, 16.5, 17, 16.5, 11.5)
    p.drawPath(arc)
    p.drawLine(QPointF(12, 16), QPointF(12, 18.5))
    p.setPen(_pen("#ff6b6b", 1.8))
    p.drawLine(QPointF(6.5, 6), QPointF(17.5, 18))


def _share(p):
    """Create / Share: trzy krótkie promienie (jak na padzie)."""
    p.setPen(_pen(FG, 1.8))
    for x1, y1, x2, y2 in ((12, 5, 12, 10), (6.5, 9, 9.5, 12.5), (17.5, 9, 14.5, 12.5)):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.drawEllipse(QPointF(12, 16.5), 2.2, 2.2)


def _options(p):
    p.setPen(_pen(FG, 1.8))
    for y in (7, 12, 17):
        p.drawLine(QPointF(6, y), QPointF(18, y))


def _crown(p):
    path = QPainterPath(QPointF(4, 18))
    for pt in ((3, 7.5), (8.5, 12), (12, 5), (15.5, 12), (21, 7.5), (20, 18)):
        path.lineTo(*pt)
    path.closeSubpath()
    p.setPen(_pen("#b37d00", 1.0))
    p.setBrush(GOLD)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(4, 18.5, 16, 2.5), 1, 1)


def _mouse(p):
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7, 3.5, 10, 17), 5, 5)
    p.drawLine(QPointF(12, 4), QPointF(12, 9))


def _touchpad(p):
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(3, 6, 18, 12), 3, 3)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(GOLD)
    p.drawEllipse(QPointF(14, 12), 2.2, 2.2)


def _restart(p):
    p.setPen(_pen(FG, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 290 * 16)
    head = QPainterPath(QPointF(19.5, 3.5))
    head.lineTo(19.5, 9)
    head.lineTo(14, 9)
    p.drawPath(head)


def _tv(p):
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(3, 5, 18, 12), 2, 2)
    p.drawLine(QPointF(9, 20), QPointF(15, 20))


def _power(p):
    p.setPen(_pen(FG, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5.5, 5.5, 13, 13), 120 * 16, 300 * 16)
    p.drawLine(QPointF(12, 3.5), QPointF(12, 11))


def _draw(p, name):
    if name in FACE:
        _face(p, name)
    elif name in BADGES:
        _badge(p, BADGES[name])
    elif name.startswith("dpad_"):
        _dpad(p, name)
    else:
        {"ps": _ps, "mute": _mute, "share": _share, "options": _options, "crown": _crown, "mouse": _mouse,
         "tv": _tv, "power": _power, "touchpad": _touchpad, "restart": _restart}[name](p)


def pixmap(name, size=24, dim=False):
    """Ikona narysowana w siatce 24x24 i przeskalowana do `size` px (dim = przygaszona, np. nieaktywna korona)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    p.scale(size / 24, size / 24)
    if dim:
        p.setOpacity(0.35)
    _draw(p, name)
    p.end()
    return pm


@cache
def icon(name, dim=False):
    ic = QIcon()
    for s in SIZES:
        ic.addPixmap(pixmap(name, s, dim))
    return ic


def has_icon(name):
    return name in FACE or name in BADGES or name.startswith("dpad_") or name in (
        "ps", "mute", "share", "options", "crown", "mouse", "touchpad", "tv", "power", "restart")
