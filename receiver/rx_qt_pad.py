"""Receiver: the pad drawing (QPainter) - clickable parts, live highlighting and profile change badges.

The shapes come from the browser editor's SVG drawing (the same 800x520 layout); they are painted directly, so
a refresh 30 times a second costs little, and hit-testing is exact (the outline, not a rectangle).
Keyboard / pad in TV mode (rx_qt_tv): ← / → move over the parts, Enter / Space picks a part.
Test mode (set_test): no profile badges and no clicking; trigger values above them, fingers and the touchpad click.
"""
import functools
import re
from collections.abc import Iterable, Sequence
from typing import Any, TypeAlias

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFocusEvent, QFont, QFontMetricsF, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPainterPath,
                           QPaintEvent, QPen, QRadialGradient, QTransform)
from PySide6.QtWidgets import QSizePolicy, QWidget

import rx_buttons as B
from rx_editor import L3_OF, SHORT
from rx_i18n import _
from rx_qt_style import ACCENT as _ACCENT
from rx_qt_style import BAD as _BAD
from rx_qt_style import BG as _BG
from rx_shape import Profile

VIEW_X, VIEW_Y, VIEW_W, VIEW_H = 44, 24, 712, 484     # the drawing's frame (SVG 800x520 coordinates without the empty margins)
ACCENT, BAD, DARK = QColor(_ACCENT), QColor(_BAD), QColor(_BG)
SHAPE, EDGE, ARROW = QColor("#343a48"), QColor("#525a6d"), QColor("#8e96ab")
LABEL = QColor("#c9cddb")
WHITE = QColor("#ffffff")
TRIG_FX = QColor("#a78bfa")                           # the trigger resistance badge (like the Bluetooth segment - violet)
# the order of parts when moving with the arrows (TV mode): left to right, roughly following the drawing
NAV_ORDER = ("l2", "l1", "dpad_up", "dpad_left", "dpad_right", "dpad_down", "share", "left_stick", "ps", "right_stick",
             "options", "triangle", "square", "circle", "cross", "r1", "r2")
STICK_TRAVEL = 14      # by this much (drawing units) the stick knob moves at full deflection
TOUCH_RECT = QRectF(300, 112, 200, 96)                # the touchpad in the drawing; the sender's touch is in the DS4 1920x943 scale
TOUCH_W, TOUCH_H = 1919, 942
TOUCH_CLICK_TWO = 2     # touchpad click state: 0 = none, 1 = pressed, 2 = pressed with two fingers
TRIG_VISIBLE = 0.02     # a trigger deflection below 2 % is not drawn (the part stays in its rest colour)
FINGER = (QColor("#ffb547"), QColor("#5bc0ff"))       # first / second finger

_TOKENS = re.compile(r"[A-Za-z]|-?\d+(?:\.\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4}
Badge: TypeAlias = tuple[float, float, str, bool | str]    # (x, y, text, kind): kind True = disabled, "fx" = trigger effect


def svg_path(d: str) -> QPainterPath:
    """An SVG path (absolute commands M L H V C Q Z only - enough for this drawing) -> a QPainterPath.

    Tokens are commands (letters) and numbers; a command's arguments are read in groups of _ARGS[cmd], and a repeated
    group reuses the command.
    """
    path, toks, i, cmd, x, y = QPainterPath(), _TOKENS.findall(d), 0, "", 0.0, 0.0
    while i < len(toks):
        if toks[i].isalpha():
            cmd, i = toks[i], i + 1
        if cmd == "Z":
            path.closeSubpath()
            cmd = ""
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


def _circle(cx: float, cy: float, r: float) -> QPainterPath:
    """A circle path with centre (cx, cy) and radius r."""
    p = QPainterPath()
    p.addEllipse(QPointF(cx, cy), r, r)
    return p


def _round_rect(x: float, y: float, w: float, h: float, r: float, rot: tuple[float, float, float] | None = None) -> QPainterPath:
    """A rounded rectangle path, optionally rotated by rot = (angle, cx, cy) around that point."""
    p = QPainterPath()
    p.addRoundedRect(QRectF(x, y, w, h), r, r)
    if rot:
        angle, cx, cy = rot
        p = QTransform().translate(cx, cy).rotate(angle).translate(-cx, -cy).map(p)
    return p


def _poly(*pts: Any) -> QPainterPath:
    """A closed polygon path through the given points."""
    p = QPainterPath()
    p.moveTo(*pts[0])
    for pt in pts[1:]:
        p.lineTo(*pt)
    p.closeSubpath()
    return p


class Part:
    """A clickable part of the pad: an outline, optionally an arrow / symbol / label."""
    __slots__ = ("name", "path", "tip", "arrow", "sym", "label", "rot")

    def __init__(self, name: str, path: QPainterPath, tip: str, arrow: QPainterPath | None = None,
                 sym: tuple[QPainterPath, str] | None = None, label: tuple[str, float, float] | None = None,
                 rot: tuple[float, float, float] | None = None) -> None:
        """Store the part's name, outline path, tooltip and optional arrow / symbol / label / rotation."""
        self.name, self.path, self.tip = name, path, tip
        self.arrow, self.sym, self.label, self.rot = arrow, sym, label, rot     # label = (text, x, y)


def _build_parts() -> list[Part]:
    """The parts in drawing order (the last one is on top - the one a click checks first)."""
    labels = {k: _(v) for k, v in B.LABELS.items()}
    sym = QPainterPath()
    sym.moveTo(587, 232)
    sym.lineTo(603, 248)
    sym.moveTo(603, 232)
    sym.lineTo(587, 248)
    square = QPainterPath()
    square.addRoundedRect(QRectF(547, 192, 16, 16), 1.5, 1.5)
    return [
        Part("l2", svg_path("M128 84 C130 50 170 36 214 40 C236 42 246 56 248 82 Z"), labels["l2"], label=("L2", 190, 68)),
        Part("r2", svg_path("M672 84 C670 50 630 36 586 40 C564 42 554 56 552 82 Z"), labels["r2"], label=("R2", 610, 68)),
        Part("l1", _round_rect(132, 86, 124, 24, 12, (-9, 194, 98)), labels["l1"], label=("L1", 192, 107), rot=(-9, 194, 98)),
        Part("r1", _round_rect(544, 86, 124, 24, 12, (9, 606, 98)), labels["r1"], label=("R1", 608, 107), rot=(9, 606, 98)),
        Part("share", _round_rect(266, 136, 12, 38, 6), labels["share"]),
        Part("options", _round_rect(522, 136, 12, 38, 6), labels["options"]),
        Part("ps", _circle(400, 252, 16), labels["ps"], label=("PS", 400, 256)),
        Part("dpad_up", svg_path("M191 190 V168 Q191 160 199 160 H211 Q219 160 219 168 V190 Z"), labels["dpad_up"],
             arrow=svg_path("M205 166 L198 178 H212 Z")),
        Part("dpad_down", svg_path("M191 210 V232 Q191 240 199 240 H211 Q219 240 219 232 V210 Z"), labels["dpad_down"],
             arrow=svg_path("M205 234 L198 222 H212 Z")),
        Part("dpad_left", svg_path("M195 186 H173 Q165 186 165 194 V206 Q165 214 173 214 H195 Z"), labels["dpad_left"],
             arrow=svg_path("M171 200 L183 193 V207 Z")),
        Part("dpad_right", svg_path("M215 186 H237 Q245 186 245 194 V206 Q245 214 237 214 H215 Z"), labels["dpad_right"],
             arrow=svg_path("M239 200 L227 193 V207 Z")),
        Part("triangle", _circle(595, 160, 19), labels["triangle"], sym=(_poly((595, 150), (586, 166), (604, 166)), "#4cd08a")),
        Part("circle", _circle(635, 200, 19), labels["circle"], sym=(_circle(635, 200, 8.5), "#ff6b6b")),
        Part("cross", _circle(595, 240, 19), labels["cross"], sym=(sym, "#5b9bff")),
        Part("square", _circle(555, 200, 19), labels["square"], sym=(square, "#e58bd2")),
        Part("left_stick", _circle(305, 285, 42), _("Lewa gałka (klik = L3)")),
        Part("right_stick", _circle(495, 285, 42), _("Prawa gałka (klik = R3)")),
    ]


BODY = ("M400 96 C360 96 318 98 268 104 C200 112 150 130 118 190 C84 256 62 360 66 430 C69 478 112 496 144 466 "
        "C176 436 206 372 246 350 C290 328 340 338 400 338 C460 338 510 328 554 350 C594 372 624 436 656 466 "
        "C688 496 731 478 734 430 C738 360 716 256 682 190 C650 130 600 112 532 104 C482 98 440 96 400 96 Z")
STICK_CENTER = {"left_stick": (305, 285), "right_stick": (495, 285)}


def _lerp(a: float, b: float, t: float) -> int:
    """Linear interpolation from a to b by t (0..1), rounded to an int."""
    return round(a + (b - a) * t)


class PadView(QWidget):
    """The pad drawing. Signal part_clicked(name) - an input name (cross, l2, dpad_up...) or left_stick / right_stick."""
    part_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the parts and the static shapes, and set the initial live state (nothing pressed, sticks centred)."""
        super().__init__(parent)
        self.parts = _build_parts()
        self.by_name = {p.name: p for p in self.parts}
        self.body = svg_path(BODY)
        self.touch = _round_rect(300, 112, 200, 96, 24)
        self.mic = _round_rect(391, 292, 18, 8, 4)          # the microphone button - drawing only (it does not reach the PC)
        self.selected: str | None = None
        self.hover: str | None = None
        self.live: dict[str, Any] = {"on": frozenset(), "ls": (128, 128), "rs": (128, 128), "l2": 0, "r2": 0}
        self.badges: list[Badge] = []
        self.test = False                                 # the Test view: no badges and no clicking, touch on the drawing
        self.touch_live = ((), 0)                         # (fingers (x, y) in the DS4 scale, touchpad click 0/1/2)
        self._tf = self._inv = QTransform()
        # paint resources that do not change between frames: the body gradient is in drawing units (the painter transform
        # scales it), the stage gradient only depends on the widget size
        self._body_grad = QLinearGradient(0, 96, 0, 496)
        self._body_grad.setColorAt(0, QColor("#353b4b"))
        self._body_grad.setColorAt(1, QColor("#1f232e"))
        self._stage_grad: tuple[tuple[float, float], QRadialGradient] | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMinimumSize(320, 240)      # a 1280 px window (TV 2560×1440 at 200 %) fits in TV mode too
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---------- data ----------
    def set_selected(self, name: str | None) -> None:
        """Mark the part being edited (drawn with the accent outline)."""
        if name != self.selected:
            self.selected = name
            self.update()

    def set_test(self, on: bool) -> None:
        """Switch the Test view on / off: it clears the selection and hover and shows the live touch instead of the badges."""
        if on != self.test:
            self.test = on
            self.selected = self.hover = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def set_touch(self, touch: Sequence[Sequence[int]], click: int) -> None:
        """Live touch (the Test view): fingers ((active, id, x, y), ...) and the touchpad click."""
        live = (tuple((f[2], f[3]) for f in touch if f[0]), int(click))
        if live != self.touch_live:
            self.touch_live = live
            self.update()

    def set_profile(self, prof: Profile) -> None:
        """Profile change badges next to the pad's parts (→ ○, ∅, ∿, gyro)."""
        badges = self._badges(prof)
        if badges != self.badges:
            self.badges = badges
            self.update()

    def set_live(self, pressed: Iterable[str], ls: Sequence[int], rs: Sequence[int], l2: int, r2: int) -> None:
        """Live state (what the drawing should show); repaints only when it changed."""
        on = frozenset(pressed) | {s for s, b in L3_OF.items() if b in pressed}
        live = {"on": on, "ls": tuple(ls), "rs": tuple(rs), "l2": l2, "r2": r2}
        if live != self.live:
            self.live = live
            self.update()

    def _badges(self, prof: Profile) -> list[Badge]:
        """Compute the badges for a profile as (x, y, text, kind).

        Button remaps, stick source / inversion, tuning, trigger effects and the gyro mode.
        """
        out: list[Badge] = []
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
        for name in B.TRIGGERS:                           # a DualSense trigger effect: next to the trigger, on the outside
            e = trig.get(name)
            tag = {"res": _("opór {n}"), "click": _("klik {n}"), "vib": _("drgania {n}"), "off": _("bez efektu")}.get(
                (e or {}).get("mode", ""), "").format(n=(e or {}).get("force", (e or {}).get("amp")))
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

    # ---------- mouse ----------
    def _hit(self, pos: QPointF) -> str | None:
        """The name of the topmost part under a widget position (mapped back to drawing coordinates), or None."""
        pt = self._inv.map(QPointF(pos))
        return next((p.name for p in reversed(self.parts) if p.path.contains(pt)), None)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        """Track the hovered part: update the cursor, tooltip and highlight."""
        name = self._hit(e.position())
        if name != self.hover:
            self.hover = name
            pointer = name and not self.test
            self.setCursor(Qt.CursorShape.PointingHandCursor if pointer else Qt.CursorShape.ArrowCursor)
            self.setToolTip(self.by_name[name].tip if name else "")
            self.update()

    def leaveEvent(self, _e: QEvent) -> None:
        """The cursor left the widget: drop the hover highlight."""
        if self.hover is not None:
            self.hover = None
            self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        """A left click on a part emits part_clicked (not in the Test view)."""
        if e.button() == Qt.MouseButton.LeftButton and not self.test:
            name = self._hit(e.position())
            if name:
                self.part_clicked.emit(name)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        """← / → : the previous / next part (highlighted as under the cursor), Enter / Space: pick."""
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

    def focusInEvent(self, e: QFocusEvent) -> None:
        """Repaint to show the focus frame."""
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e: QFocusEvent) -> None:
        """Drop the keyboard hover and repaint without the focus frame."""
        super().focusOutEvent(e)
        self.hover = None
        self.update()

    # ---------- drawing ----------
    def paintEvent(self, _e: QPaintEvent) -> None:
        """Paint the stage, then the drawing scaled to fit.

        Triggers behind the body, the body, decorations, buttons, sticks, and finally the badges (or the Test overlay).
        """
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self._stage(p)
        k = min(self.width() / VIEW_W, self.height() / VIEW_H)
        self._tf = (QTransform().translate((self.width() - VIEW_W * k) / 2, (self.height() - VIEW_H * k) / 2)
                    .scale(k, k).translate(-VIEW_X, -VIEW_Y))
        self._inv = self._tf.inverted()[0]
        p.setTransform(self._tf)
        for part in self.parts[:2]:                    # the triggers are behind the body
            self._part(p, part)
        p.setPen(QPen(QColor("#4d5468"), 2.5))
        p.setBrush(self._body_grad)
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

    def _test_overlay(self, p: QPainter) -> None:
        """The Test view: trigger values (0-255) above them, fingers on the touchpad."""
        for name in B.TRIGGERS:
            r = self.by_name[name].path.boundingRect()
            self._badge(p, r.center().x(), r.top() - 10, str(self.live[name]), False)
        r = TOUCH_RECT
        for n, (x, y) in enumerate(self.touch_live[0][:2]):
            pt = QPointF(r.left() + x / TOUCH_W * r.width(), r.top() + y / TOUCH_H * r.height())
            p.setPen(QPen(WHITE, 1.5))
            p.setBrush(FINGER[n])
            p.drawEllipse(pt, 6.5, 6.5)

    def _stage(self, p: QPainter) -> None:
        """The rounded background panel; the border turns accent-coloured when the widget has focus."""
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        size = (r.width(), r.height())
        if self._stage_grad is None or self._stage_grad[0] != size:     # rebuilt only after a resize
            g = QRadialGradient(r.center().x(), r.top() + r.height() * 0.38, max(r.width(), r.height()) * 0.7)
            g.setColorAt(0, QColor("#1d2230"))
            g.setColorAt(1, QColor("#12151c"))
            self._stage_grad = (size, g)
        p.setPen(QPen(ACCENT if self.hasFocus() else QColor("#252a36"), 1.5 if self.hasFocus() else 1))
        p.setBrush(self._stage_grad[1])
        p.drawRoundedRect(r, 16, 16)

    def _decor(self, p: QPainter) -> None:
        """Elements without remapping: the touchpad with light bars, the microphone, the D-pad base."""
        click = self.test and self.touch_live[1]
        p.setPen(QPen(ACCENT if click else QColor("#434a5c"), 3 if click else 2))
        p.setBrush(QColor("#1b2b4d") if click else QColor("#1a1d25"))
        p.drawPath(self.touch)
        p.setFont(self._font(12, QFont.Weight.Medium))
        p.setPen(WHITE if click else QColor("#5d6478"))
        self._text(p, "TOUCHPAD" + (" ×2" if click == TOUCH_CLICK_TWO else ""), 400, 165)
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

    def _part(self, p: QPainter, part: Part) -> None:
        """Paint one part: fill (accent when pressed, a trigger blends with its deflection), outline, arrow, symbol and label."""
        on = part.name in self.live["on"]
        fill = ACCENT if on else SHAPE
        if part.name in B.TRIGGERS:                       # a trigger: a smooth fill depending on the deflection
            v = self.live[part.name] / 255
            if v > TRIG_VISIBLE and not on:
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

    def _stick(self, p: QPainter, part: Part) -> None:
        """Paint a stick: the well, then the knob offset by the live deflection and its inner ring."""
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

    def _edge(self, name: str, base: QColor = EDGE, width: float = 1.5) -> QPen:
        """A part's outline: selected = accent and thick, under the cursor / picked from the pad = white."""
        if name == self.selected:
            return QPen(ACCENT, 3.5)
        if name == self.hover:
            return QPen(WHITE, 3 if self.hasFocus() else width)
        return QPen(base, width)

    def _badge(self, p: QPainter, cx: float, cy: float, text: str, kind: bool | str) -> None:
        """A change badge: kind True = disabled (red), "fx" = trigger resistance (violet), otherwise the accent."""
        p.setFont(self._font(11, QFont.Weight.Bold))
        w = QFontMetricsF(p.font()).horizontalAdvance(text)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TRIG_FX if kind == "fx" else BAD if kind else ACCENT)
        p.drawRoundedRect(QRectF(cx - w / 2 - 7, cy - 9, w + 14, 18), 9, 9)
        p.setPen(WHITE if kind is False else DARK)
        self._text(p, text, cx, cy + 4)

    @staticmethod
    @functools.cache
    def _font(px: int, weight: QFont.Weight) -> QFont:
        """The drawing font (Segoe UI) at a pixel size and weight; built once per combination (painting asks for it every frame)."""
        f = QFont("Segoe UI")
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    @staticmethod
    def _text(p: QPainter, text: str, cx: float, y: float) -> None:
        """Text centred horizontally at cx, the baseline at y (like text-anchor: middle in SVG)."""
        p.drawText(QPointF(cx - QFontMetricsF(p.font()).horizontalAdvance(text) / 2, y), text)

