"""Receiver: Bluetooth nadajnika w oknie - kropka stanu w nagłówku i wysuwany z prawej panel (skan, lista urządzeń,
Sparuj / Połącz / Rozłącz / Zapomnij). Telefon za telewizorem nie potrzebuje ekranu ani myszy.

Panel (rx_qt_drawer.SideDrawer) leży nad oknem: przyciemnione tło, klik w nie albo Esc zamyka. Tryb TV: D-pad chodzi
tylko po elementach panelu, ✕ klika, ○ zamyka i fokus wraca na ikonę Bluetooth.
Wiersze urządzeń są trwałe (klucz = MAC) i aktualizowane w miejscu - fokus z pada nie skacze przy odświeżaniu co
REFRESH_MS. Kolejność listy ustala nadajnik (połączone, sparowane, pady, nazwa - bez RSSI, które by ją przestawiało).
"""
import time
from collections.abc import Callable
from functools import cache
from itertools import pairwise

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

import rx_bt as BT
from proto import (BT_ADAPTER, BT_LIST, BT_SCAN_MAX_S, BTA_AUTO, BTA_BUILTIN, BTA_MANUAL, BTR_NONE, BTR_OK,
                   BTR_RUNNING, BTS_OFF, BTS_OK)
from rx_i18n import _
from rx_qt_drawer import SideDrawer
from rx_qt_icons import pixmap
from rx_qt_style import ACCENT, BAD, LINE, MUTED, OK, WARN, px, set_prop
from rx_qt_widgets import ConfirmButton, Segmented, button, icon_button, icon_label, label

REFRESH_MS = 250
DOT = {"ok": OK, "scan": ACCENT, "warn": WARN, "bad": BAD, "": MUTED}
ACTION = {"pair": "Sparuj", "connect": "Połącz", "disconnect": "Rozłącz"}
ADAPTER_MODES = ((BTA_AUTO, "Auto"), (BTA_BUILTIN, "Wbudowany"), (BTA_MANUAL, "Ręcznie"))
ADAPTER_TIPS = {
    BTA_AUTO: "Najlepszy kontroler: dongle USB przed wbudowanym (zwykle mniejsze opóźnienie), pozostałe wyłączone",
    BTA_BUILTIN: "Wbudowany kontroler, dongle USB wyłączony",
    BTA_MANUAL: "Nadajnik nie zmienia kontrolerów - ustawiasz je sam (rfkill, bluetoothctl)",
}


@cache
def status_icon(level: str) -> QIcon:
    """Ikona Bluetooth z kolorową kropką stanu w prawym dolnym rogu (kilka rozmiarów - ostra na HiDPI)."""
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
    """Etykieta, która przycina długi tekst wielokropkiem zamiast poszerzać panel (pełny tekst w podpowiedzi)."""

    def __init__(self, role: str):
        super().__init__()
        self.setProperty("role", role)
        self.full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full(self, text: str) -> None:
        if text != self.full:
            self.full = text
            self.setToolTip(text)
            self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        self.ensurePolished()               # czcionka z QSS (rola "name") - inaczej liczy szerokość zwykłą czcionką
        self.setText(self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideRight, max(0, self.width())))


class SignalBars(QWidget):
    """Siła sygnału 0-4 kreski (0 = brak pomiaru - RSSI jest tylko w trakcie skanu)."""

    def __init__(self):
        super().__init__()
        self.n = 0
        self.setFixedSize(px(18), px(14))

    def set(self, n: int) -> None:
        if n != self.n:
            self.n = n
            self.update()

    def paintEvent(self, _e):
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
    """Jedno urządzenie: ikona, nazwa, MAC / sygnał / stan i przyciski. Aktualizowane w miejscu (update)."""

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
        self.name = ElidedLabel("name")             # długa nazwa nie poszerza panelu
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
        return [self.btn, self.forget]


class BtDrawer(SideDrawer):
    """Wysuwany z prawej panel Bluetooth nadajnika nad oknem `host` (centralny widżet okna)."""

    def __init__(self, host: QWidget):
        super().__init__(host, "bluetooth", _("BLUETOOTH NADAJNIKA"))
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self._keepalive = 0

    # ---------- budowa ----------
    def _build(self, lay: QVBoxLayout) -> None:
        self.rows: dict[str, DeviceRow] = {}
        self.status = label("", wrap=True)
        lay.addWidget(self.status)
        # wybór kontrolera nadajnika (wbudowany / dongle USB) - tylko nadajnik, który go obsługuje
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

    # ---------- otwieranie ----------
    def _on_open(self) -> None:
        self._keepalive = 0
        self.refresh()
        self.timer.start()

    def _on_close(self) -> None:
        self.timer.stop()

    def first_focus(self) -> QWidget:
        return self.scan_btn if self.scan_btn.isEnabled() else self.close_btn

    # ---------- akcje ----------
    def power_on(self) -> None:
        BT.power(True)
        self.refresh()

    def set_adapter(self, key: str) -> None:
        BT.adapter(int(key))
        self.refresh()

    def toggle_scan(self) -> None:
        s = BT.status
        BT.scan(not (s is not None and s.scanning))
        self.refresh()

    def on_action(self, mac: str, kind: str) -> None:
        {"pair": BT.pair, "connect": BT.connect, "disconnect": BT.disconnect, "remove": BT.remove}[kind](mac)
        self.refresh()

    # ---------- odświeżanie ----------
    def refresh(self) -> None:
        """Co REFRESH_MS przy otwartym panelu: stan, skan, wynik ostatniej operacji, wiersze; co KEEPALIVE_S
        BT_LIST (nadajnik wysyła listę, dopóki panel jest otwarty)."""
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
        """Przełącznik kontrolera: stan z nadajnika (nie w trakcie własnej zmiany - bez mignięcia starym trybem)."""
        shown = s is not None and s.mode != 0
        self.adapter_box.setVisible(shown)
        if not shown:
            return
        if BT.busy_op("", now) != BT_ADAPTER and str(s.mode) in self.adapter_seg.buttons:
            self.adapter_seg.set(str(s.mode))
        for b in self.adapter_seg.buttons.values():
            b.setEnabled(not busy)
        if s.mode == BTA_MANUAL:
            text = _(ADAPTER_TIPS[BTA_MANUAL])
        elif s.on_usb:
            text = _("Używany: dongle USB")
        else:
            text = _("Używany: wbudowany")
            if s.mode == BTA_AUTO and not s.has_usb:
                text += "  ·  " + _("Dongle Bluetooth USB zwykle zmniejsza opóźnienie padów")
        if s.has_usb and s.has_builtin:
            text += "  ·  " + _("Po zmianie kontrolera sparuj pady ponownie (każdy ma własny adres)")
        self.adapter_info.setText(text)

    def _result(self, s: BT.Status | None) -> None:
        if s is None or s.op == BT_LIST or s.result == BTR_NONE:
            self.result.setText("")
            return
        self.result.setText(BT.result_text(s.op, s.result, s.mac))
        set_prop(self.result, "level", "ok" if s.result == BTR_OK else "" if s.result == BTR_RUNNING else "warn")

    def _rows(self, now: float, busy: bool, enabled: bool) -> None:
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
        """Kolejność fokusu (Tab, D-pad) jak na ekranie: zamknij, zasilanie, skan, wiersze od góry."""
        chain = [self.close_btn, *self.adapter_seg.buttons.values(), self.power_btn, self.scan_btn]
        for k in range(self.list_lay.count()):
            w = self.list_lay.itemAt(k).widget()
            if isinstance(w, DeviceRow):
                chain += w.focusables()
        for a, b in pairwise(chain):
            QWidget.setTabOrder(a, b)

def indicator_button(on_click: Callable[[], None]) -> tuple[QPushButton, Callable[[float], None]]:
    """Przycisk z ikoną Bluetooth i kropką stanu do nagłówka okna. -> (przycisk, funkcja odświeżenia(now))."""
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
