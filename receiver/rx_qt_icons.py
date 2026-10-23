"""Receiver: icons of the pad buttons and a few functions (QPainter, no image files) - instead of names in the window.

icon(name)  -> a QIcon for a pad input ("cross", "ps", "dpad_up", "l2"...) or a function ("crown", "mouse",
               "tv", "power"); sharp on HiDPI screens (painted at several sizes), cached after the first use.
tip(name)   -> the full name for a tooltip (an icon without text needs a tooltip).
Symbol colours as in the pad drawing (rx_qt_pad): triangle green, circle red, cross blue, square pink.
"""
from rx_i18n import _
from functools import cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from rx_buttons import LABELS

FG, DIM, BODY, GOLD = QColor("#eceef4"), QColor("#8d93a5"), QColor("#2c313e"), QColor("#ffb300")
FACE = {"triangle": "#4cd08a", "circle": "#ff6b6b", "cross": "#5b9bff", "square": "#e58bd2"}
BADGES = {"l1": "L1", "r1": "R1", "l2": "L2", "r2": "R2", "l3": "L3", "r3": "R3"}
EXTRA_TIPS = {"crown": "Pad master", "mouse": "Touchpad jako mysz", "touchpad": "Touchpad jako dotyk DS4",
              "tv": "Tryb konsoli", "power": "Wyłącz", "restart": "Uruchom ponownie", "window": "Ekran",
              "fullscreen": "Pełny ekran", "rumble": "Wibracja", "gear": "Ustawienia", "update": "Aktualizuj",
              "backup": "Kopie ustawień", "stopwatch": "Opóźnienie", "plus": "Nowy profil gry",
              "press": "Przycisk → gra", "reset": "Wyzeruj", "log": "Dziennik zdarzeń", "copy": "Kopiuj",
              "trash": "Wyczyść", "phone": "Nadajnik", "lightbar": "Lightbar", "wake": "Wake-on-LAN",
              "target": "Dryf gałek", "gyro": "Kalibracja żyroskopu", "shield": "Blokada wyjścia do gry",
              "pulse": "Ruch pada", "bluetooth": "Bluetooth nadajnika", "keys": "Skróty pada"}
SIZES = (16, 20, 24, 32, 48)


def tip(name):
    """The full, translated name of an input or function for a tooltip (a pad label, else an extra tip, else the name itself)."""
    return _(LABELS.get(name) or EXTRA_TIPS.get(name, name))


def _pen(color, w):
    """A round-capped, round-joined pen of the given colour and width."""
    p = QPen(QColor(color), w)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


def _disc(p, fill=BODY) -> None:
    """A round "plate" of a button - the background of the face button and PS icons."""
    p.setPen(_pen("#565d70", 1.2))
    p.setBrush(fill)
    p.drawEllipse(QRectF(1.5, 1.5, 21, 21))


def _face(p, name) -> None:
    """A face button: the round plate with its coloured symbol (cross, circle, square or triangle)."""
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


def _text(p, text, rect, px, color=FG, weight=QFont.Weight.Bold) -> None:
    """Draw text centred in rect with the Segoe UI font at a pixel size."""
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    f.setWeight(weight)
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _badge(p, text) -> None:
    """L1 / R2 / L3...: a rounded badge (triggers lower and wider at the top, stick clicks round)."""
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


def _dpad(p, name) -> None:
    """The D-pad cross with the given direction highlighted."""
    arms = {"dpad_up": QRectF(9, 2, 6, 9), "dpad_down": QRectF(9, 13, 6, 9),
            "dpad_left": QRectF(2, 9, 9, 6), "dpad_right": QRectF(13, 9, 9, 6)}
    p.setPen(Qt.PenStyle.NoPen)
    for k, r in arms.items():
        p.setBrush(GOLD if k == name else BODY)
        p.drawRoundedRect(r, 1.6, 1.6)
    p.setBrush(BODY)
    p.drawRect(QRectF(9, 9, 6, 6))


def _ps(p) -> None:
    """The PS button: a dark plate with the letters PS."""
    _disc(p, QColor("#2a2e3a"))
    _text(p, "PS", QRectF(0, 0, 24, 24), 9)


def _share(p) -> None:
    """Create / Share: three short rays (as on the pad)."""
    p.setPen(_pen(FG, 1.8))
    for x1, y1, x2, y2 in ((12, 5, 12, 10), (6.5, 9, 9.5, 12.5), (17.5, 9, 14.5, 12.5)):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.drawEllipse(QPointF(12, 16.5), 2.2, 2.2)


def _options(p) -> None:
    """Options: three horizontal lines."""
    p.setPen(_pen(FG, 1.8))
    for y in (7, 12, 17):
        p.drawLine(QPointF(6, y), QPointF(18, y))


def _crown(p) -> None:
    path = QPainterPath(QPointF(4, 18))
    for pt in ((3, 7.5), (8.5, 12), (12, 5), (15.5, 12), (21, 7.5), (20, 18)):
        path.lineTo(*pt)
    path.closeSubpath()
    p.setPen(_pen("#b37d00", 1.0))
    p.setBrush(GOLD)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(4, 18.5, 16, 2.5), 1, 1)


def _mouse(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7, 3.5, 10, 17), 5, 5)
    p.drawLine(QPointF(12, 4), QPointF(12, 9))


def _touchpad(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(3, 6, 18, 12), 3, 3)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(GOLD)
    p.drawEllipse(QPointF(14, 12), 2.2, 2.2)


def _restart(p) -> None:
    p.setPen(_pen(FG, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 290 * 16)
    head = QPainterPath(QPointF(19.5, 3.5))
    head.lineTo(19.5, 9)
    head.lineTo(14, 9)
    p.drawPath(head)


def _window(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
    p.drawLine(QPointF(3, 9), QPointF(21, 9))


def _fullscreen(p) -> None:
    p.setPen(_pen(FG, 1.8))
    for x, y, dx, dy in ((4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + 5 * dx, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + 5 * dy))


def _rumble(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(8, 4, 8, 16), 2, 2)
    for x in (4.5, 19.5):
        path = QPainterPath(QPointF(x, 7))
        for k, y in enumerate((9.5, 12, 14.5, 17)):
            path.lineTo(x + (1.5 if k % 2 == 0 else -1.5) * (1 if x < 12 else -1), y)
        p.drawPath(path)


def _gear(p) -> None:
    import math
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    radii = (9, 6.8)                       # teeth and gaps alternating
    p.drawPolygon(QPolygonF([QPointF(12 + radii[k % 2] * math.cos(math.pi * k / 8), 12 + radii[k % 2] * math.sin(math.pi * k / 8))
                             for k in range(16)]))
    p.drawEllipse(QPointF(12, 12), 2.8, 2.8)


def _update(p) -> None:
    """An arrow down into a "tray" - downloading a new version."""
    p.setPen(_pen(FG, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(12, 4), QPointF(12, 14.5))
    head = QPainterPath(QPointF(7.5, 10.5))
    head.lineTo(12, 15)
    head.lineTo(16.5, 10.5)
    p.drawPath(head)
    tray = QPainterPath(QPointF(4.5, 15.5))
    tray.lineTo(4.5, 19.5)
    tray.lineTo(19.5, 19.5)
    tray.lineTo(19.5, 15.5)
    p.drawPath(tray)


def _tv(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(3, 5, 18, 12), 2, 2)
    p.drawLine(QPointF(9, 20), QPointF(15, 20))


def _power(p) -> None:
    p.setPen(_pen(FG, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5.5, 5.5, 13, 13), 120 * 16, 300 * 16)
    p.drawLine(QPointF(12, 3.5), QPointF(12, 11))


def _backup(p) -> None:
    """Two sheets one on another - a file copy."""
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(8.5, 3.5, 11, 14), 2, 2)
    p.setBrush(QColor("#161922"))
    p.drawRoundedRect(QRectF(4.5, 7.5, 11, 13), 2, 2)
    p.drawLine(QPointF(7.5, 12), QPointF(12.5, 12))
    p.drawLine(QPointF(7.5, 15.5), QPointF(12.5, 15.5))


def _stopwatch(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12, 13.5), 7, 7)
    p.drawLine(QPointF(10, 3.5), QPointF(14, 3.5))
    p.drawLine(QPointF(12, 3.5), QPointF(12, 6.5))
    p.drawLine(QPointF(12, 13.5), QPointF(15, 10.5))


def _plus(p) -> None:
    p.setPen(_pen(FG, 2.0))
    p.drawLine(QPointF(12, 5), QPointF(12, 19))
    p.drawLine(QPointF(5, 12), QPointF(19, 12))


def _press(p) -> None:
    """A finger (an arrow) pressing a round button - the latency of presses only."""
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(5, 13, 14, 7))
    p.drawLine(QPointF(12, 3), QPointF(12, 11))
    p.drawLine(QPointF(9, 8), QPointF(12, 11))
    p.drawLine(QPointF(15, 8), QPointF(12, 11))


def _reset(p) -> None:
    """An arrow turning back to zero."""
    p.setPen(_pen(DIM, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5, 5, 14, 14), 100 * 16, 290 * 16)
    p.drawLine(QPointF(5.5, 4.5), QPointF(5.5, 9.5))
    p.drawLine(QPointF(5.5, 9.5), QPointF(10.5, 9.5))


def _log(p) -> None:
    """A list of entries with bullets."""
    p.setPen(_pen(FG, 1.7))
    for y in (7, 12, 17):
        p.drawPoint(QPointF(5.5, y))
        p.drawLine(QPointF(9, y), QPointF(19, y))


def _copy(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(9, 4, 10, 12), 2, 2)
    p.setBrush(QColor("#161922"))
    p.drawRoundedRect(QRectF(5, 8, 10, 12), 2, 2)


def _trash(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(5, 7), QPointF(19, 7))
    p.drawLine(QPointF(10, 7), QPointF(10.5, 4.5))
    p.drawLine(QPointF(10.5, 4.5), QPointF(13.5, 4.5))
    p.drawLine(QPointF(13.5, 4.5), QPointF(14, 7))
    path = QPainterPath(QPointF(7, 7))
    path.lineTo(8, 20)
    path.lineTo(16, 20)
    path.lineTo(17, 7)
    p.drawPath(path)


def _phone(p) -> None:
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7, 3, 10, 18), 2.5, 2.5)
    p.drawLine(QPointF(11, 18), QPointF(13, 18))


def _lightbar(p) -> None:
    """A glowing strip (the lightbar) with a halo."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(91, 155, 255, 70))
    p.drawRoundedRect(QRectF(3, 8, 18, 8), 4, 4)
    p.setBrush(QColor("#5b9bff"))
    p.drawRoundedRect(QRectF(5, 10.5, 14, 3), 1.5, 1.5)


def _wake(p) -> None:
    """A power symbol with a network wave - waking the PC over the network."""
    p.setPen(_pen(FG, 1.7))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(4, 6, 12, 12), 130 * 16, 280 * 16)
    p.drawLine(QPointF(10, 4), QPointF(10, 11))
    p.setPen(_pen(GOLD, 1.5))
    p.drawArc(QRectF(13, 3, 8, 8), -40 * 16, 120 * 16)
    p.drawArc(QRectF(15.5, 5.5, 3, 3), -40 * 16, 120 * 16)


def _target(p) -> None:
    """A crosshair with an off-centre dot - a stick that "escapes" from the centre (drift)."""
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12, 12), 8, 8)
    p.setPen(_pen(DIM, 1.2))
    p.drawLine(QPointF(12, 2.5), QPointF(12, 6))
    p.drawLine(QPointF(12, 18), QPointF(12, 21.5))
    p.drawLine(QPointF(2.5, 12), QPointF(6, 12))
    p.drawLine(QPointF(18, 12), QPointF(21.5, 12))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ffb547"))
    p.drawEllipse(QPointF(14.5, 9.5), 2.4, 2.4)


def _gyro(p) -> None:
    """A gyroscope: a circle with two orbits and an axis."""
    p.setPen(_pen(FG, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.setPen(_pen("#5b9bff", 1.5))
    p.drawEllipse(QPointF(12, 12), 8.5, 3.4)
    p.setPen(_pen(DIM, 1.3))
    p.drawEllipse(QPointF(12, 12), 3.4, 8.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(FG)
    p.drawEllipse(QPointF(12, 12), 1.8, 1.8)


def _shield(p) -> None:
    """A shield with a horizontal bar - output to the game blocked."""
    path = QPainterPath(QPointF(12, 3))
    path.lineTo(19, 6)
    path.cubicTo(19, 13, 16.5, 18, 12, 21)
    path.cubicTo(7.5, 18, 5, 13, 5, 6)
    path.closeSubpath()
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(_pen("#ff6b6b", 2.0))
    p.drawLine(QPointF(9, 11.5), QPointF(15, 11.5))


def _pulse(p) -> None:
    """A chart with a wave - live pad motion (gyro and accelerometer)."""
    p.setPen(_pen(DIM, 1.2))
    p.drawLine(QPointF(3, 20), QPointF(21, 20))
    path = QPainterPath(QPointF(3, 13))
    for x, y in ((7, 13), (9.5, 6), (12.5, 18), (15, 10), (17, 13), (21, 13)):
        path.lineTo(x, y)
    p.setPen(_pen("#3ecf8e", 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _bluetooth(p) -> None:
    """The Bluetooth rune: a vertical line and two "beaks" to the right."""
    path = QPainterPath(QPointF(7, 8))
    for x, y in ((17, 16.5), (12, 21), (12, 3), (17, 7.5), (7, 16)):
        path.lineTo(x, y)
    p.setPen(_pen("#5b9bff", 1.9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _usb(p) -> None:
    """A USB plug: the housing and a metal tip with two holes - a Bluetooth dongle."""
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7.5, 10, 9, 11), 2, 2)
    p.drawRect(QRectF(9, 3.5, 6, 6.5))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(FG))
    p.drawRect(QRectF(10.3, 5.5, 1.4, 1.6))
    p.drawRect(QRectF(12.3, 5.5, 1.4, 1.6))


def _keys(p) -> None:
    """A pad outline with buttons - the list of pad shortcuts."""
    p.setPen(_pen(FG, 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(7, 7))
    path.lineTo(17, 7)
    path.cubicTo(20.5, 7, 22, 15, 21, 18)
    path.cubicTo(20, 20.5, 17.5, 19, 16, 16)
    path.lineTo(8, 16)
    path.cubicTo(6.5, 19, 4, 20.5, 3, 18)
    path.cubicTo(2, 15, 3.5, 7, 7, 7)
    p.drawPath(path)
    p.setPen(_pen(DIM, 1.4))
    p.drawLine(QPointF(6, 11.5), QPointF(9, 11.5))
    p.drawLine(QPointF(7.5, 10), QPointF(7.5, 13))
    p.setPen(Qt.PenStyle.NoPen)
    for (x, y), c in zip(((16.5, 9.8), (18.3, 11.6), (16.5, 13.4), (14.7, 11.6)), FACE.values(), strict=True):
        p.setBrush(QColor(c))
        p.drawEllipse(QPointF(x, y), 1.1, 1.1)


def _draw(p, name) -> None:
    """Paint the icon `name` on the 24x24 grid by dispatching to its painter."""
    if name in FACE:
        _face(p, name)
    elif name in BADGES:
        _badge(p, BADGES[name])
    elif name.startswith("dpad_"):
        _dpad(p, name)
    else:
        {"ps": _ps, "share": _share, "options": _options, "crown": _crown, "mouse": _mouse,
         "tv": _tv, "power": _power, "touchpad": _touchpad, "restart": _restart, "window": _window,
         "fullscreen": _fullscreen, "rumble": _rumble, "gear": _gear, "update": _update, "backup": _backup,
         "stopwatch": _stopwatch, "plus": _plus, "press": _press, "reset": _reset, "log": _log, "copy": _copy,
         "trash": _trash, "phone": _phone, "lightbar": _lightbar, "wake": _wake, "target": _target, "gyro": _gyro,
         "shield": _shield, "pulse": _pulse, "bluetooth": _bluetooth, "keys": _keys, "usb": _usb}[name](p)


def pixmap(name, size: int = 24, dim: bool = False):
    """An icon painted on a 24x24 grid and scaled to `size` px (dim = dimmed, e.g. an inactive crown)."""
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
def icon(name, dim: bool = False):
    """A QIcon with the picture rendered at all SIZES (cached per name and dim flag)."""
    ic = QIcon()
    for s in SIZES:
        ic.addPixmap(pixmap(name, s, dim))
    return ic


def has_icon(name):
    """Whether an icon with this name exists."""
    return name in FACE or name in BADGES or name.startswith("dpad_") or name in (
        "ps", "share", "options", "crown", "mouse", "touchpad", "tv", "power", "restart", "window", "fullscreen", "rumble", "gear",
        "update", "backup", "stopwatch", "plus", "press", "reset", "log", "copy", "trash", "phone", "lightbar", "wake",
        "target", "gyro", "shield", "pulse", "bluetooth", "keys", "usb")
