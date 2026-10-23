"""Receiver: the sender's Bluetooth in the window - a status dot in the header and a panel sliding in from the right (scan,
device list, Pair / Connect / Disconnect / Forget). A phone behind a TV needs no screen or mouse.

The panel (rx_qt_drawer.SideDrawer) lies over the window: a dimmed background, a click on it or Esc closes it. TV mode: the D-pad moves
only over the panel's elements, ✕ clicks, ○ closes and the focus returns to the Bluetooth icon.
Device rows are persistent (key = MAC) and updated in place - the pad focus does not jump on the refresh every
REFRESH_MS. The list order is set by the sender (connected, paired, pads, name - without RSSI, which would reshuffle it).
"""
import html
import time
from collections.abc import Callable
from functools import cache
from itertools import pairwise

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

import rx_bt as BT
from proto import (BT_ADAPTER, BT_ADAPTER_USE, BT_LIST, BT_SCAN_MAX_S, BTA_AUTO, BTA_MANUAL, BTA_PICK, BTR_NONE,
                   BTR_OK, BTR_RUNNING, BTS_OFF, BTS_OK)
from rx_i18n import _
from rx_qt_drawer import SideDrawer
from rx_qt_icons import pixmap
from rx_qt_style import ACCENT, BAD, LINE, MUTED, OK, WARN, px, set_prop
from rx_qt_widgets import ConfirmButton, Segmented, button, icon_button, icon_label, label

REFRESH_MS = 250
DOT = {"ok": OK, "scan": ACCENT, "warn": WARN, "bad": BAD, "": MUTED}
ACTION = {"pair": "Sparuj", "connect": "Połącz", "disconnect": "Rozłącz"}
ADAPTER_MODES = ((BTA_AUTO, "Auto"), (BTA_PICK, "Wybrany"), (BTA_MANUAL, "Ręcznie"))
ADAPTER_TIPS = {
    BTA_AUTO: "Najlepszy kontroler: dongle USB przed wbudowanym (zwykle mniejsze opóźnienie), pozostałe wyłączone",
    BTA_PICK: "Zawsze kontroler wybrany przyciskiem Użyj (gdy go nie ma - jak Auto)",
    BTA_MANUAL: "Nadajnik nie zmienia kontrolerów - ustawiasz je sam (rfkill, bluetoothctl)",
}
CTRL_KIND = {"usb": "dongle USB", "fixed": "wewnętrzny USB", "builtin": "wbudowany"}


@cache
def status_icon(level: str) -> QIcon:
    """The Bluetooth icon with a coloured status dot in the bottom right corner (several sizes - sharp on HiDPI)."""
    ic = QIcon()
    for size in (20, 24, 32, 48):
        pm = QPixmap(pixmap("bluetooth", size))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = size * 0.19
        p.setPen(QColor("#161922"))
        p.setBrush(QColor(DOT.get(level, MUTED)))
        p.drawEllipse(QRectF(size - 2 * r - 1, size - 2 * r - 1, 2 * r, 2 * r))
        p.end()
        ic.addPixmap(pm)
    return ic


class ElidedLabel(QLabel):
    """A label that elides long text with an ellipsis instead of widening the panel (the full text in the tooltip).
    Plain text: Bluetooth device names are set by every device in range - a QLabel in AutoText mode would show a name
    with HTML tags as formatted content (impersonating window elements)."""

    def __init__(self, role: str):
        super().__init__()
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", role)
        self.full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full(self, text: str) -> None:
        """Set the full text (kept for re-eliding on resize); the tooltip holds it escaped."""
        if text != self.full:
            self.full = text
            self.setToolTip(f"<qt>{html.escape(text)}</qt>" if text else "")   # a tooltip also interprets HTML
            self._elide()

    def resizeEvent(self, e):
        """Re-elide the text for the new width."""
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        """Show as much of the full text as fits, with a trailing ellipsis."""
        self.ensurePolished()               # the font from the QSS (the "name" role) - otherwise the width is measured with the plain font
        self.setText(self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideRight, max(0, self.width())))


class SignalBars(QWidget):
    """Signal strength as 0-4 bars (0 = no measurement - RSSI exists only during a scan)."""

    def __init__(self):
        super().__init__()
        self.n = 0
        self.setFixedSize(px(18), px(14))

    def set(self, n: int) -> None:
        """Set the number of lit bars (0-4); repaint only on change."""
        if n != self.n:
            self.n = n
            self.update()

    def paintEvent(self, _e):
        """Draw four bars of rising height; the first `n` in the OK colour, the rest dim."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        w, h = self.width(), self.height()
        bw = w / 4 - 1.5
        for k in range(4):
            bh = h * (k + 1) / 4
            p.setBrush(QColor(OK if k < self.n else LINE))
            p.drawRoundedRect(QRectF(k * (bw + 1.5), h - bh, bw, bh), 1, 1)


class DeviceRow(QFrame):
    """One device: an icon, a name, MAC / signal / state and buttons. Updated in place (update_from)."""

    action = Signal(str, str)        # (MAC, "pair" | "connect" | "disconnect" | "remove")

    def __init__(self, dev: BT.Device):
        super().__init__()
        self.setObjectName("row")
        self.mac = dev.mac
        self._kind = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(10), px(8), px(8), px(8))
        lay.setSpacing(px(8))
        self.icon = icon_label("ps" if dev.pad else "bluetooth", px(22))
        text = QVBoxLayout()
        text.setSpacing(px(2))
        self.name = ElidedLabel("name")             # a long name does not widen the panel
        info = QHBoxLayout()
        info.setSpacing(px(6))
        self.bars = SignalBars()
        self.detail = label("", "small")
        self.state = label("", "small")
        info.addWidget(self.bars)
        info.addWidget(self.detail)
        info.addWidget(self.state)
        info.addStretch(1)
        text.addWidget(self.name)
        text.addLayout(info)
        self.btn = button("", small=True)
        self.btn.clicked.connect(lambda: self.action.emit(self.mac, self._kind))
        self.forget = ConfirmButton(_("Zapomnij"), _("Zapomnieć?"), small=True, iconic="trash",
                                    tip=_("Zapomnij") + " - " + _("Usuwa parowanie z telefonu (np. pad sparowany "
                                                                  "potem z konsolą trzeba sparować od nowa)"))
        self.forget.confirmed.connect(lambda: self.action.emit(self.mac, "remove"))
        lay.addWidget(self.icon)
        lay.addLayout(text, 1)
        lay.addWidget(self.btn)
        lay.addWidget(self.forget)
        self.update_from(dev, None, False)

    def update_from(self, dev: BT.Device, busy: int | None, other_busy: bool) -> None:
        """Refresh the row from the device state: name, signal, state text, and the action button (pair / connect / disconnect by state;
        busy = the running operation code, other_busy disables the row while another device is busy)."""
        self.name.set_full(dev.name)
        self.bars.set(dev.bars())
        self.bars.setToolTip(f"{dev.rssi} dBm" if dev.rssi is not None else _("Sygnał widać w trakcie skanowania"))
        self.detail.setText(dev.mac)
        state, level = ((_("połączony"), "ok") if dev.connected else (_("sparowany"), "") if dev.paired
                        else (_("nowy"), "warn"))
        self.state.setText("·  " + state)
        set_prop(self.state, "level", level)
        self._kind = "disconnect" if dev.connected else "connect" if dev.paired else "pair"
        if busy is not None:
            self.btn.setText(_(BT.OP_TEXT.get(busy, "Operacja")) + "…")
        else:
            self.btn.setText(_(ACTION[self._kind]))
        set_prop(self.btn, "primary", self._kind != "disconnect" and busy is None)
        self.btn.setEnabled(busy is None and not other_busy)
        self.forget.setVisible(dev.paired and not dev.connected)
        self.forget.setEnabled(busy is None and not other_busy)

    def focusables(self) -> list[QWidget]:
        """The row's focusable buttons, in order (for the panel's tab order)."""
        return [self.btn, self.forget]


class ControllerRow(QFrame):
    """A sender's Bluetooth controller: an icon, the device name, kind / hciN / state and a Use button."""

    use = Signal(int)                # N from hciN

    def __init__(self, c: BT.Controller):
        super().__init__()
        self.setObjectName("row")
        self.index = c.index
        lay = QHBoxLayout(self)
        lay.setContentsMargins(px(10), px(6), px(8), px(6))
        lay.setSpacing(px(8))
        self.icon = icon_label("usb" if c.usb else "bluetooth", px(20))
        text = QVBoxLayout()
        text.setSpacing(px(2))
        self.name = ElidedLabel("name")
        self.detail = label("", "small")
        text.addWidget(self.name)
        text.addWidget(self.detail)
        self.btn = button(_("Użyj"), primary=True, small=True)
        self.btn.clicked.connect(lambda: self.use.emit(self.index))
        lay.addWidget(self.icon)
        lay.addLayout(text, 1)
        lay.addWidget(self.btn)
        self.update_from(c, False)

    def update_from(self, c: BT.Controller, busy: bool) -> None:
        """Refresh the controller row: name, kind / hciN / state text and the Use button (hidden for the active controller)."""
        self.index = c.index
        self.name.set_full(c.name)
        kind = "fixed" if c.fixed else "usb" if c.usb else "builtin"
        state, level = ((_("używany"), "ok") if c.active else (_("wyłączony"), "") if c.blocked
                        else (_("włączony"), ""))
        pin = "  ·  " + _("wybrany") if c.picked else ""
        self.detail.setText(f"{_(CTRL_KIND[kind])}  ·  hci{c.index}  ·  {state}{pin}")
        set_prop(self.detail, "level", level)
        self.btn.setVisible(not c.active)
        self.btn.setEnabled(not busy)

    def focusables(self) -> list[QWidget]:
        """The row's focusable buttons (for the panel's tab order)."""
        return [self.btn]


class BtDrawer(SideDrawer):
    """The sender's Bluetooth panel sliding in from the right over the window `host` (the window's central widget)."""

    def __init__(self, host: QWidget):
        """Create the panel and its refresh timer (started only while the panel is open)."""
        super().__init__(host, "bluetooth", _("BLUETOOTH NADAJNIKA"))
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self._keepalive = 0

    # ---------- building ----------
    def _build(self, lay: QVBoxLayout) -> None:
        """Build the panel: status, controller choice, power / scan buttons, hint, the scrollable device list and the last operation
        result."""
        self.rows: dict[str, DeviceRow] = {}
        self.status = label("", wrap=True)
        lay.addWidget(self.status)
        # choice of the sender's controller (built-in / USB dongle) - only for a sender that supports it
        self.adapter_box = QWidget()
        box = QVBoxLayout(self.adapter_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(px(4))
        box.addWidget(label(_("Kontroler Bluetooth"), "dim"))
        self.adapter_seg = Segmented([(str(m), _(t)) for m, t in ADAPTER_MODES])
        for m, _t in ADAPTER_MODES:
            self.adapter_seg.buttons[str(m)].setToolTip(_(ADAPTER_TIPS[m]))
        self.adapter_seg.changed.connect(self.set_adapter)
        box.addWidget(self.adapter_seg)
        self.ctrl_lay = QVBoxLayout()
        self.ctrl_lay.setSpacing(px(4))
        self.ctrl_rows: dict[tuple[int, str], ControllerRow] = {}
        box.addLayout(self.ctrl_lay)
        self.adapter_info = label("", "small", wrap=True)
        box.addWidget(self.adapter_info)
        lay.addWidget(self.adapter_box)
        self.power_btn = button(_("Włącz Bluetooth"), primary=True)
        self.power_btn.clicked.connect(self.power_on)
        lay.addWidget(self.power_btn)
        self.scan_btn = button("")
        self.scan_btn.clicked.connect(self.toggle_scan)
        lay.addWidget(self.scan_btn)
        self.hint = label(_("Tryb parowania DualSense: przytrzymaj Create + PS, aż lightbar zacznie szybko migać. "
                            "Skan zabiera radio połączonym padom - kończy się sam po {s} s.").format(s=BT_SCAN_MAX_S),
                          "dim", wrap=True)
        lay.addWidget(self.hint)

        body = QWidget()
        self.list_lay = QVBoxLayout(body)
        self.list_lay.setContentsMargins(0, 0, px(4), 0)
        self.list_lay.setSpacing(px(6))
        self.empty = label("", "dim", wrap=True)
        self.list_lay.addWidget(self.empty)
        self.list_lay.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(body)
        lay.addWidget(self.scroll, 1)
        self.result = label("", wrap=True)
        lay.addWidget(self.result)

    # ---------- opening ----------
    def _on_open(self) -> None:
        """Panel opened: refresh at once and start the periodic refresh."""
        self._keepalive = 0
        self.refresh()
        self.timer.start()

    def _on_close(self) -> None:
        """Panel closed: stop the periodic refresh."""
        self.timer.stop()

    def first_focus(self) -> QWidget:
        """The widget that gets the focus on opening: the scan button, or Close when scanning is not possible."""
        return self.scan_btn if self.scan_btn.isEnabled() else self.close_btn

    # ---------- actions ----------
    def power_on(self) -> None:
        """Ask the sender to switch Bluetooth on."""
        BT.power(True)
        self.refresh()

    def set_adapter(self, key: str) -> None:
        """Controller mode chosen in the switch (key = the mode number)."""
        mode = int(key)
        if mode == BTA_PICK:                 # "Picked": pin the controller the sender uses right now
            c = BT.active_controller() or next(iter(BT.controllers), None)
            if c is not None:
                BT.use_controller(c.index)
        else:
            BT.adapter(mode)
        self.refresh()

    def use_controller(self, index: int) -> None:
        """Switch the sender to controller hci<index>."""
        BT.use_controller(index)
        self.refresh()

    def toggle_scan(self) -> None:
        """Start a scan, or stop the running one."""
        s = BT.status
        BT.scan(not (s is not None and s.scanning))
        self.refresh()

    def on_action(self, mac: str, kind: str) -> None:
        """Run the pair / connect / disconnect / remove request for a device row."""
        {"pair": BT.pair, "connect": BT.connect, "disconnect": BT.disconnect, "remove": BT.remove}[kind](mac)
        self.refresh()

    # ---------- refreshing ----------
    def refresh(self) -> None:
        """Every REFRESH_MS while the panel is open: state, scan, the result of the last operation, rows; every KEEPALIVE_S
        BT_LIST (the sender sends the list while the panel is open)."""
        now = time.monotonic()
        if self._open and self.window().isVisible() and now - self._keepalive >= BT.KEEPALIVE_S:
            self._keepalive = now
            BT.keepalive()
        s, ok = BT.status, BT.supported(now)
        level, text = BT.indicator(now)
        self.status.setText(text)
        set_prop(self.status, "level", {"scan": "ok", "": ""}.get(level, level))
        state = s.state if ok and s is not None else None
        busy = BT.any_busy(now)
        self.power_btn.setVisible(state == BTS_OFF)
        self.power_btn.setEnabled(not busy)
        scanning = bool(ok and s is not None and s.scanning)
        self.scan_btn.setText(_("Zatrzymaj skanowanie ({s} s)").format(s=s.scan_left) if scanning else
                              _("Skanuj ({s} s)").format(s=BT_SCAN_MAX_S))
        set_prop(self.scan_btn, "primary", not scanning)
        self.scan_btn.setEnabled(state in (BTS_OK, BTS_OFF) and (scanning or not busy))
        self._adapter(s if ok else None, busy, now)
        self._rows(now, busy, enabled=state == BTS_OK)
        self._result(s)

    def _adapter(self, s: BT.Status | None, busy: bool, now: float) -> None:
        """Controller choice: the mode (not during our own change - no flash of the old mode), the list of controllers
        with names and a Use button, the description."""
        shown = s is not None and s.mode != 0
        self.adapter_box.setVisible(shown)
        if not shown:
            return
        if BT.busy_op("", now) not in (BT_ADAPTER, BT_ADAPTER_USE) and str(s.mode) in self.adapter_seg.buttons:
            self.adapter_seg.set(str(s.mode))
        for b in self.adapter_seg.buttons.values():
            b.setEnabled(not busy)
        self._controllers(busy)
        if s.mode == BTA_MANUAL:
            text = _(ADAPTER_TIPS[BTA_MANUAL])
        elif s.mode == BTA_AUTO and not s.has_usb:
            text = _("Dongle Bluetooth USB zwykle zmniejsza opóźnienie padów")
        else:
            text = ""
        if len(BT.controllers) > 1:
            text += ("  ·  " if text else "") + _("Po zmianie kontrolera sparuj pady ponownie (każdy ma własny adres)")
        self.adapter_info.setText(text)
        self.adapter_info.setVisible(bool(text))

    def _controllers(self, busy: bool) -> None:
        """Controller rows - persistent (the pad focus does not jump), key = index and name."""
        order = [(c.index, c.name) for c in BT.controllers]
        for key in [k for k in self.ctrl_rows if k not in order]:
            row = self.ctrl_rows.pop(key)
            self.ctrl_lay.removeWidget(row)
            row.hide()
            row.deleteLater()
        for k, c in enumerate(BT.controllers):
            row = self.ctrl_rows.get((c.index, c.name))
            if row is None:
                row = self.ctrl_rows[(c.index, c.name)] = ControllerRow(c)
                row.use.connect(self.use_controller)
            if self.ctrl_lay.indexOf(row) != k:
                self.ctrl_lay.insertWidget(k, row)
                self._tab_order()
            row.update_from(c, busy)

    def _result(self, s: BT.Status | None) -> None:
        """Show the result of the last operation (hidden for the plain list and when nothing ran)."""
        if s is None or s.op == BT_LIST or s.result == BTR_NONE:
            self.result.setText("")
            return
        self.result.setText(BT.result_text(s.op, s.result, s.mac))
        set_prop(self.result, "level", "ok" if s.result == BTR_OK else "" if s.result == BTR_RUNNING else "warn")

    def _rows(self, now: float, busy: bool, enabled: bool) -> None:
        """Sync the device rows with the list: drop the vanished ones (moving the focus off a removed row), add new ones, update all and
        fix the order."""
        devs = BT.devices if enabled else ()
        order = [d.mac for d in devs]
        for mac in [m for m in self.rows if m not in order]:
            row = self.rows.pop(mac)
            if row.isAncestorOf(self.focusWidget() or self):
                self.scan_btn.setFocus(Qt.FocusReason.OtherFocusReason)
            self.list_lay.removeWidget(row)
            row.hide()
            row.deleteLater()
        for d in devs:
            row = self.rows.get(d.mac)
            if row is None:
                row = self.rows[d.mac] = DeviceRow(d)
                row.action.connect(self.on_action)
            op = BT.busy_op(d.mac, now)
            row.update_from(d, op, busy and op is None)
        if [self.list_lay.itemAt(k).widget() for k in range(self.list_lay.count())][:len(order)] != \
                [self.rows[m] for m in order]:
            for k, mac in enumerate(order):
                self.list_lay.insertWidget(k, self.rows[mac])
            self._tab_order()
        self.empty.setVisible(not order)
        self.empty.setText(_("Brak urządzeń - uruchom skanowanie i włącz tryb parowania na padzie.") if enabled else
                           _("Lista niedostępna - Bluetooth nadajnika nie działa albo nadajnik jest offline."))

    def _tab_order(self) -> None:
        """Focus order (Tab, D-pad) as on screen: close, power, scan, rows from the top."""
        chain = [self.close_btn, *self.adapter_seg.buttons.values(),
                 *(b for r in self.ctrl_rows.values() for b in r.focusables()), self.power_btn, self.scan_btn]
        for k in range(self.list_lay.count()):
            w = self.list_lay.itemAt(k).widget()
            if isinstance(w, DeviceRow):
                chain += w.focusables()
        for a, b in pairwise(chain):
            QWidget.setTabOrder(a, b)

def indicator_button(on_click: Callable[[], None]) -> tuple[QPushButton, Callable[[float], None]]:
    """A button with the Bluetooth icon and a status dot for the window header. -> (the button, a refresh function(now))."""
    btn = icon_button("bluetooth", _("Bluetooth nadajnika"))
    btn.clicked.connect(on_click)
    last: list[tuple[str, str] | None] = [None]

    def refresh(now: float) -> None:
        level, text = BT.indicator(now)
        if (level, text) != last[0]:
            last[0] = (level, text)
            btn.setIcon(status_icon(level))
            btn.setToolTip(text + "\n" + _("Kliknij: skanowanie, parowanie i łączenie padów z telefonem"))
    return btn, refresh
