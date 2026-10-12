"""Receiver: rysunek pada (QPainter) - klikalne części, podświetlenie na żywo i plakietki zmian profilu.

Kształty pochodzą z rysunku SVG edytora w przeglądarce (ten sam układ 800x520); rysowane bezpośrednio, więc
odświeżenie 30 razy na sekundę kosztuje niewiele, a trafianie w części jest dokładne (kontur, nie prostokąt).
Klawiatura / pad w trybie TV (rx_qt_tv): ← / → przechodzą po częściach, Enter / Spacja wybiera część.
Tryb testu (set_test): bez plakietek profilu i bez klikania; wartości spustów nad nimi, palce i klik na touchpadzie.
"""
from rx_i18n import _
import re

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient, QTransform)
from PySide6.QtWidgets import QSizePolicy, QWidget

import rx_buttons as B
from rx_editor import L3_OF, SHORT
from rx_qt_style import ACCENT as _ACCENT
from rx_qt_style import BAD as _BAD
from rx_qt_style import BG as _BG

VIEW_X, VIEW_Y, VIEW_W, VIEW_H = 44, 24, 712, 484     # kadr rysunku (współrzędne SVG 800x520 bez pustych marginesów)
ACCENT, BAD, DARK = QColor(_ACCENT), QColor(_BAD), QColor(_BG)
SHAPE, EDGE, ARROW = QColor("#343a48"), QColor("#525a6d"), QColor("#8e96ab")
LABEL = QColor("#c9cddb")
WHITE = QColor("#ffffff")
TRIG_FX = QColor("#a78bfa")                           # plakietka oporu spustu (jak odcinek Bluetooth - fiolet)
# kolejność części przy przechodzeniu strzałkami (tryb TV): od lewej do prawej, mniej więcej po rysunku
NAV_ORDER = ("l2", "l1", "dpad_up", "dpad_left", "dpad_right", "dpad_down", "share", "left_stick", "ps", "right_stick",
             "options", "triangle", "square", "circle", "cross", "r1", "r2")
STICK_TRAVEL = 14      # o tyle (jednostek rysunku) przesuwa się gałka przy pełnym wychyleniu
TOUCH_RECT = QRectF(300, 112, 200, 96)                # touchpad na rysunku; dotyk z nadajnika w skali DS4 1920x943
TOUCH_W, TOUCH_H = 1919, 942
FINGER = (QColor("#ffb547"), QColor("#5bc0ff"))       # pierwszy / drugi palec

_TOKENS = re.compile(r"[A-Za-z]|-?\d+(?:\.\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4}


def svg_path(d):
    """Ścieżka SVG (tylko polecenia bezwzględne M L H V C Q Z - tyle wystarcza rysunkowi) -> QPainterPath."""
    path, toks, i, cmd, x, y = QPainterPath(), _TOKENS.findall(d), 0, None, 0.0, 0.0
    while i < len(toks):
        if toks[i].isalpha():
            cmd, i = toks[i], i + 1
        if cmd == "Z":
            path.closeSubpath()
            cmd = None
            continue
        n = _ARGS[cmd]
        a = [float(v) for v in toks[i:i + n]]
        i += n
        if cmd == "M":
            path.moveTo(*a)
            x, y = a
        elif cmd == "L":
            path.lineTo(*a)
            x, y = a
        elif cmd == "H":
            x = a[0]
            path.lineTo(x, y)
        elif cmd == "V":
            y = a[0]
            path.lineTo(x, y)
        elif cmd == "C":
            path.cubicTo(*a)
            x, y = a[4:]
        else:
            path.quadTo(*a)
            x, y = a[2:]
    return path


def _circle(cx, cy, r):
    p = QPainterPath()
    p.addEllipse(QPointF(cx, cy), r, r)
    return p


def _round_rect(x, y, w, h, r, rot=None):
    p = QPainterPath()
    p.addRoundedRect(QRectF(x, y, w, h), r, r)
    if rot:
        angle, cx, cy = rot
        p = QTransform().translate(cx, cy).rotate(angle).translate(-cx, -cy).map(p)
    return p


def _poly(*pts):
    p = QPainterPath()
    p.moveTo(*pts[0])
    for pt in pts[1:]:
        p.lineTo(*pt)
    p.closeSubpath()
    return p


class Part:
    """Klikalna część pada: kontur, opcjonalnie strzałka / symbol / napis."""
    __slots__ = ("name", "path", "tip", "arrow", "sym", "label", "rot")

    def __init__(self, name, path, tip, arrow=None, sym=None, label=None, rot=None):
        self.name, self.path, self.tip = name, path, tip
        self.arrow, self.sym, self.label, self.rot = arrow, sym, label, rot     # label = (tekst, x, y)


def _build_parts():
    """Części w kolejności rysowania (ostatnia na wierzchu - tę sprawdza kliknięcie najpierw)."""
    L = {k: _(v) for k, v in B.LABELS.items()}
    sym = QPainterPath()
    sym.moveTo(587, 232)
    sym.lineTo(603, 248)
    sym.moveTo(603, 232)
    sym.lineTo(587, 248)
    square = QPainterPath()
    square.addRoundedRect(QRectF(547, 192, 16, 16), 1.5, 1.5)
    return [
        Part("l2", svg_path("M128 84 C130 50 170 36 214 40 C236 42 246 56 248 82 Z"), L["l2"], label=("L2", 190, 68)),
        Part("r2", svg_path("M672 84 C670 50 630 36 586 40 C564 42 554 56 552 82 Z"), L["r2"], label=("R2", 610, 68)),
        Part("l1", _round_rect(132, 86, 124, 24, 12, (-9, 194, 98)), L["l1"], label=("L1", 192, 107), rot=(-9, 194, 98)),
        Part("r1", _round_rect(544, 86, 124, 24, 12, (9, 606, 98)), L["r1"], label=("R1", 608, 107), rot=(9, 606, 98)),
        Part("share", _round_rect(266, 136, 12, 38, 6), L["share"]),
        Part("options", _round_rect(522, 136, 12, 38, 6), L["options"]),
        Part("ps", _circle(400, 252, 16), L["ps"], label=("PS", 400, 256)),
        Part("dpad_up", svg_path("M191 190 V168 Q191 160 199 160 H211 Q219 160 219 168 V190 Z"), L["dpad_up"],
             arrow=svg_path("M205 166 L198 178 H212 Z")),
        Part("dpad_down", svg_path("M191 210 V232 Q191 240 199 240 H211 Q219 240 219 232 V210 Z"), L["dpad_down"],
             arrow=svg_path("M205 234 L198 222 H212 Z")),
        Part("dpad_left", svg_path("M195 186 H173 Q165 186 165 194 V206 Q165 214 173 214 H195 Z"), L["dpad_left"],
             arrow=svg_path("M171 200 L183 193 V207 Z")),
        Part("dpad_right", svg_path("M215 186 H237 Q245 186 245 194 V206 Q245 214 237 214 H215 Z"), L["dpad_right"],
             arrow=svg_path("M239 200 L227 193 V207 Z")),
        Part("triangle", _circle(595, 160, 19), L["triangle"], sym=(_poly((595, 150), (586, 166), (604, 166)), "#4cd08a")),
        Part("circle", _circle(635, 200, 19), L["circle"], sym=(_circle(635, 200, 8.5), "#ff6b6b")),
        Part("cross", _circle(595, 240, 19), L["cross"], sym=(sym, "#5b9bff")),
        Part("square", _circle(555, 200, 19), L["square"], sym=(square, "#e58bd2")),
        Part("left_stick", _circle(305, 285, 42), _("Lewa gałka (klik = L3)")),
        Part("right_stick", _circle(495, 285, 42), _("Prawa gałka (klik = R3)")),
    ]


BODY = ("M400 96 C360 96 318 98 268 104 C200 112 150 130 118 190 C84 256 62 360 66 430 C69 478 112 496 144 466 "
        "C176 436 206 372 246 350 C290 328 340 338 400 338 C460 338 510 328 554 350 C594 372 624 436 656 466 "
        "C688 496 731 478 734 430 C738 360 716 256 682 190 C650 130 600 112 532 104 C482 98 440 96 400 96 Z")
STICK_CENTER = {"left_stick": (305, 285), "right_stick": (495, 285)}


def _lerp(a, b, t):
    return round(a + (b - a) * t)


class PadView(QWidget):
    """Rysunek pada. Sygnał part_clicked(nazwa) - nazwa wejścia (cross, l2, dpad_up...) albo left_stick / right_stick."""
    part_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parts = _build_parts()
        self.by_name = {p.name: p for p in self.parts}
        self.body = svg_path(BODY)
        self.touch = _round_rect(300, 112, 200, 96, 24)
        self.mic = _round_rect(391, 292, 18, 8, 4)          # przycisk mikrofonu - tylko rysunek (nie trafia do PC)
        self.selected = None
        self.hover = None
        self.live = {"on": frozenset(), "ls": (128, 128), "rs": (128, 128), "l2": 0, "r2": 0}
        self.badges = []
        self.test = False                                 # widok Test: bez plakietek i klikania, dotyk na rysunku
        self.touch_live = ((), 0)                         # (palce (x, y) w skali DS4, klik touchpada 0/1/2)
        self._tf = self._inv = QTransform()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMinimumSize(320, 240)      # okno 1280 px (TV 2560×1440 przy 200 %) mieści się też w trybie TV
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---------- dane ----------
    def set_selected(self, name):
        if name != self.selected:
            self.selected = name
            self.update()

    def set_test(self, on):
        if on != self.test:
            self.test = on
            self.selected = self.hover = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def set_touch(self, touch, click):
        """Dotyk na żywo (widok Test): palce ((aktywny, id, x, y), ...) i klik touchpada."""
        live = (tuple((f[2], f[3]) for f in touch if f[0]), int(click))
        if live != self.touch_live:
            self.touch_live = live
            self.update()

    def set_profile(self, prof):
        """Plakietki zmian profilu przy częściach pada (→ ○, ∅, ∿, gyro)."""
        badges = self._badges(prof)
        if badges != self.badges:
            self.badges = badges
            self.update()

    def set_live(self, pressed, ls, rs, l2, r2):
        """Stan na żywo (to, co ma pokazać rysunek); odrysowuje tylko, gdy się zmienił."""
        on = frozenset(pressed) | {s for s, b in L3_OF.items() if b in pressed}
        live = {"on": on, "ls": tuple(ls), "rs": tuple(rs), "l2": l2, "r2": r2}
        if live != self.live:
            self.live = live
            self.update()

    def _badges(self, prof):
        out = []
        for src, dst in prof["buttons"].items():
            name = src if src in self.by_name else next((s for s, b in L3_OF.items() if b == src), None)
            if name is None:
                continue
            r = self.by_name[name].path.boundingRect()
            cy = r.top() - 10 if src in B.TRIGGERS else r.bottom() + 12
            out.append((r.center().x(), cy, "→ " + ("∅" if dst is None else SHORT[dst]), dst is None))
        for out_name, e in prof["sticks"].items():
            r = self.by_name[out_name + "_stick"].path.boundingRect()
            tag = "" if e["src"] == out_name else "∅" if e["src"] == "none" else "← " + e["src"][0].upper()
            if e["inv_x"] or e["inv_y"]:
                tag += " " + ("⇄" if e["inv_x"] else "") + ("⇅" if e["inv_y"] else "")
            out.append((r.center().x(), r.top() - 8, tag.strip(), e["src"] == "none"))
        for out_name in ("left", "right"):
            if out_name in prof["tuning"]:
                r = self.by_name[out_name + "_stick"].path.boundingRect()
                out.append((r.left() - 16 if out_name == "left" else r.right() + 16, r.center().y(), "∿", False))
        trig = prof.get("triggers", {})
        game_rv = (trig.get("rumble") or {}).get("on")
        for name in B.TRIGGERS:                           # efekt spustu DualSense: obok spustu, na zewnątrz
            e = trig.get(name)
            tag = {"res": _("opór {n}"), "click": _("klik {n}"), "vib": _("drgania {n}"), "off": _("bez efektu")}.get(
                (e or {}).get("mode"), "").format(n=(e or {}).get("force", (e or {}).get("amp")))
            if game_rv:
                tag = (tag + " + " if tag else "") + _("wibr. gry")
            if tag:
                r = self.by_name[name].path.boundingRect()
                out.append((r.left() - 20 if name == "l2" else r.right() + 20, r.center().y() + 8, tag, "fx"))
        mode = prof["gyro"].get("mode", "off")
        if mode != "off":
            r = self.by_name["right_stick"].path.boundingRect()
            out.append((r.center().x(), r.bottom() + 30, "gyro · mouse" if mode == "mouse" else "gyro → R", False))
        return out

    # ---------- mysz ----------
    def _hit(self, pos):
        pt = self._inv.map(QPointF(pos))
        return next((p.name for p in reversed(self.parts) if p.path.contains(pt)), None)

    def mouseMoveEvent(self, e):
        name = self._hit(e.position())
        if name != self.hover:
            self.hover = name
            pointer = name and not self.test
            self.setCursor(Qt.CursorShape.PointingHandCursor if pointer else Qt.CursorShape.ArrowCursor)
            self.setToolTip(self.by_name[name].tip if name else "")
            self.update()

    def leaveEvent(self, _e):
        if self.hover is not None:
            self.hover = None
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.test:
            name = self._hit(e.position())
            if name:
                self.part_clicked.emit(name)

    def keyPressEvent(self, e):
        """← / → : poprzednia / następna część (podświetlona jak pod kursorem), Enter / Spacja: wybierz."""
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            cur = self.hover if self.hover in NAV_ORDER else self.selected if self.selected in NAV_ORDER else None
            step = 1 if k == Qt.Key.Key_Right else -1
            i = NAV_ORDER.index(cur) + step if cur else (0 if step > 0 else -1)
            self.hover = NAV_ORDER[i % len(NAV_ORDER)]
            self.setToolTip(self.by_name[self.hover].tip)
            self.update()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.hover and not self.test:
            self.part_clicked.emit(self.hover)
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.hover = None
        self.update()

    # ---------- rysowanie ----------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self._stage(p)
        k = min(self.width() / VIEW_W, self.height() / VIEW_H)
        self._tf = (QTransform().translate((self.width() - VIEW_W * k) / 2, (self.height() - VIEW_H * k) / 2)
                    .scale(k, k).translate(-VIEW_X, -VIEW_Y))
        self._inv = self._tf.inverted()[0]
        p.setTransform(self._tf)
        for part in self.parts[:2]:                    # spusty są za korpusem
            self._part(p, part)
        g = QLinearGradient(0, 96, 0, 496)
        g.setColorAt(0, QColor("#353b4b"))
        g.setColorAt(1, QColor("#1f232e"))
        p.setPen(QPen(QColor("#4d5468"), 2.5))
        p.setBrush(g)
        p.drawPath(self.body)
        self._decor(p)
        for part in self.parts[2:-2]:
            self._part(p, part)
        for part in self.parts[-2:]:
            self._stick(p, part)
        if self.test:
            self._test_overlay(p)
            return
        for cx, cy, text, bad in self.badges:
            self._badge(p, cx, cy, text, bad)

    def _test_overlay(self, p):
        """Widok Test: wartość spustów (0-255) nad nimi, palce na touchpadzie."""
        for name in B.TRIGGERS:
            r = self.by_name[name].path.boundingRect()
            self._badge(p, r.center().x(), r.top() - 10, str(self.live[name]), False)
        r = TOUCH_RECT
        for n, (x, y) in enumerate(self.touch_live[0][:2]):
            pt = QPointF(r.left() + x / TOUCH_W * r.width(), r.top() + y / TOUCH_H * r.height())
            p.setPen(QPen(WHITE, 1.5))
            p.setBrush(FINGER[n])
            p.drawEllipse(pt, 6.5, 6.5)

    def _stage(self, p):
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QRadialGradient(r.center().x(), r.top() + r.height() * 0.38, max(r.width(), r.height()) * 0.7)
        g.setColorAt(0, QColor("#1d2230"))
        g.setColorAt(1, QColor("#12151c"))
        p.setPen(QPen(ACCENT if self.hasFocus() else QColor("#252a36"), 1.5 if self.hasFocus() else 1))
        p.setBrush(g)
        p.drawRoundedRect(r, 16, 16)

    def _decor(self, p):
        """Elementy bez remapowania: touchpad z paskami świetlnymi, mikrofon, podstawa D-pada."""
        click = self.test and self.touch_live[1]
        p.setPen(QPen(ACCENT if click else QColor("#434a5c"), 3 if click else 2))
        p.setBrush(QColor("#1b2b4d") if click else QColor("#1a1d25"))
        p.drawPath(self.touch)
        p.setFont(self._font(12, QFont.Weight.Medium))
        p.setPen(WHITE if click else QColor("#5d6478"))
        self._text(p, "TOUCHPAD" + (" ×2" if click == 2 else ""), 400, 165)
        bar = QPen(QColor(61, 139, 255, 217), 4)
        bar.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(bar)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(svg_path("M293 122 Q282 160 293 200"))
        p.drawPath(svg_path("M507 122 Q518 160 507 200"))
        p.setPen(QPen(QColor("#434a5c"), 1))
        p.setBrush(QColor("#2a2e3a"))
        p.drawPath(self.mic)
        p.drawPath(_circle(205, 200, 13))

    def _part(self, p, part):
        on = part.name in self.live["on"]
        fill = ACCENT if on else SHAPE
        if part.name in B.TRIGGERS:                       # spust: płynne wypełnienie zależne od wychylenia
            v = self.live[part.name] / 255
            if v > 0.02 and not on:
                fill = QColor(_lerp(SHAPE.red(), ACCENT.red(), v), _lerp(SHAPE.green(), ACCENT.green(), v),
                              _lerp(SHAPE.blue(), ACCENT.blue(), v))
        p.setPen(self._edge(part.name))
        p.setBrush(fill)
        p.drawPath(part.path)
        if part.arrow:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(WHITE if on else ARROW)
            p.drawPath(part.arrow)
        if part.sym:
            path, color = part.sym
            pen = QPen(WHITE if on else QColor(color), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        if part.label:
            p.setFont(self._font(12, QFont.Weight.DemiBold))
            p.setPen(WHITE if on else LABEL)
            text, x, y = part.label
            if part.rot:
                p.save()
                angle, cx, cy = part.rot
                p.translate(cx, cy)
                p.rotate(angle)
                p.translate(-cx, -cy)
                self._text(p, text, x, y)
                p.restore()
            else:
                self._text(p, text, x, y)

    def _stick(self, p, part):
        on = part.name in self.live["on"]
        cx, cy = STICK_CENTER[part.name]
        x, y = self.live["ls" if part.name == "left_stick" else "rs"]
        p.setPen(self._edge(part.name, QColor("#4a5062"), 2))
        p.setBrush(QColor("#1b2b4d") if on else QColor("#161922"))
        p.drawPath(part.path)
        ox, oy = (x - 128) / 127 * STICK_TRAVEL, (y - 128) / 127 * STICK_TRAVEL
        p.setPen(QPen(QColor("#5b6278"), 1.5))
        p.setBrush(ACCENT if on else SHAPE)
        p.drawEllipse(QPointF(cx + ox, cy + oy), 28, 28)
        p.setPen(QPen(EDGE, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx + ox, cy + oy), 17, 17)

    def _edge(self, name, base=EDGE, width=1.5):
        """Kontur części: zaznaczona = akcent i gruby, pod kursorem / wybrana z pada = biały."""
        if name == self.selected:
            return QPen(ACCENT, 3.5)
        if name == self.hover:
            return QPen(WHITE, 3 if self.hasFocus() else width)
        return QPen(base, width)

    def _badge(self, p, cx, cy, text, kind):
        """Plakietka zmiany: kind True = wyłączone (czerwona), "fx" = opór spustu (fiolet), inaczej akcent."""
        p.setFont(self._font(11, QFont.Weight.Bold))
        w = QFontMetricsF(p.font()).horizontalAdvance(text)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TRIG_FX if kind == "fx" else BAD if kind else ACCENT)
        p.drawRoundedRect(QRectF(cx - w / 2 - 7, cy - 9, w + 14, 18), 9, 9)
        p.setPen(WHITE if kind is False else DARK)
        self._text(p, text, cx, cy + 4)

    @staticmethod
    def _font(px, weight):
        f = QFont("Segoe UI")
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    @staticmethod
    def _text(p, text, cx, y):
        """Napis wyśrodkowany poziomo w cx, linia bazowa na y (jak text-anchor: middle w SVG)."""
        p.drawText(QPointF(cx - QFontMetricsF(p.font()).horizontalAdvance(text) / 2, y), text)

