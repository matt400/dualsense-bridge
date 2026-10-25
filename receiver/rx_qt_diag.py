"""Receiver: the Diagnostics tab - live network and Bluetooth link numbers with a chart for each.

It replaces the pad drawing and the profile editor while the side menu's Diagnostics icon is on (rx_qt: main_stack). Two kinds of
card: the link to the sender (round trip, jitter, packet loss, packets per second) and one card per connected pad (the pad's
report rate, Bluetooth signal and link quality). Every tile shows the current value in the colour of its level, the minimum /
average / maximum of the last minute and a chart of that minute (rx_diag keeps the history, also while the tab is closed).

While the tab is visible it keeps the sender's Bluetooth reporting alive (the same keep-alive as the Bluetooth panel), because the
pads' RSSI and link quality are read by the sender only for a window that is interested.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

import rx_bt as BT
import rx_diag
import rx_state as S
from rx_i18n import _
from rx_qt_style import ACCENT_HI, DIM, LEVEL, MUTED, TRACK, px, set_prop
from rx_qt_widgets import label

CHART_H = 64            # chart height [px at scale 1]
LINE_W = 1.8
FILL_ALPHA = 70
SECONDS_PER_MIN, MINUTES_PER_HOUR, HOURS_PER_DAY = 60, 60, 24
DECIMALS_BELOW = 10     # values under this show one decimal place


def age_text(seconds: float) -> str:
    """How long ago, compactly: "12 s", "5 min", "1 h 12 min", "2 d 3 h" (units are the same in every language)."""
    s = int(seconds)
    if s < SECONDS_PER_MIN:
        return f"{s} s"
    m = s // SECONDS_PER_MIN
    if m < MINUTES_PER_HOUR:
        return f"{m} min"
    h, m = divmod(m, MINUTES_PER_HOUR)
    if h < HOURS_PER_DAY:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, HOURS_PER_DAY)
    return f"{d} d {h} h" if h else f"{d} d"


def number(v: float | None, unit: str = "", digits: int | None = None) -> str:
    """A value for a tile: "—" when missing, otherwise with 1 decimal below 10 and none above (or `digits` decimals)."""
    if v is None:
        return "—"
    d = digits if digits is not None else (1 if abs(v) < DECIMALS_BELOW else 0)
    return f"{v:.{d}f}".replace("-", "−") + (f" {unit}" if unit else "")


class Chart(QWidget):
    """The last minute of one series as a line with a soft fill; gaps (None) break the line, the newest value is at the right edge.

    The vertical scale follows the data, but never zooms in below `floor_span` (a flat 0.3 ms line must not look like a storm);
    `zero_based` pins the bottom to 0 (rates, delays), otherwise the range hugs the data (RSSI).
    """

    def __init__(self, series: rx_diag.Series, floor_span: float, zero_based: bool = True,
                 fixed: tuple[float, float] | None = None) -> None:
        """`fixed` = a constant (min, max) range, e.g. 0-100 % for a quality."""
        super().__init__()
        self.series, self.floor_span, self.zero_based, self.fixed = series, floor_span, zero_based, fixed
        self.color = QColor(ACCENT_HI)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(px(CHART_H))

    def set_level(self, level: str) -> None:
        """The line colour: the level's colour, the accent when there is no judgement."""
        c = QColor(LEVEL.get(level, ACCENT_HI))
        if c != self.color:
            self.color = c
            self.update()

    def rescale(self) -> None:
        """The height for the current scale."""
        self.setFixedHeight(px(CHART_H))

    def _range(self, real: list[float]) -> tuple[float, float]:
        """The vertical range of the chart for the current data."""
        if self.fixed is not None:
            return self.fixed
        lo, hi = (0.0 if self.zero_based else min(real)), max(real)
        if hi - lo < self.floor_span:
            if self.zero_based:
                hi = lo + self.floor_span
            else:
                mid = (hi + lo) / 2
                lo, hi = mid - self.floor_span / 2, mid + self.floor_span / 2
        return lo, hi

    def _runs(self, values: list[float | None], area: QRectF, lo: float, hi: float) -> list[list[QPointF]]:
        """The points of the line, split into runs at the gaps; the newest sample sits at the right edge."""
        span = (hi - lo) or 1.0
        step = area.width() / (rx_diag.HISTORY - 1)
        first = rx_diag.HISTORY - len(values)
        runs: list[list[QPointF]] = [[]]
        for i, v in enumerate(values):
            if v is None:
                if runs[-1]:
                    runs.append([])
                continue
            y = area.bottom() - (min(max(v, lo), hi) - lo) / span * area.height()
            runs[-1].append(QPointF(area.left() + (first + i) * step, y))
        return [r for r in runs if r]

    def _draw_runs(self, p: QPainter, runs: list[list[QPointF]], area: QRectF) -> None:
        """Each run as a line over a fill that fades to the bottom; a single sample is a dot."""
        grad = QLinearGradient(0, area.top(), 0, area.bottom())
        fill = QColor(self.color)
        fill.setAlpha(FILL_ALPHA)
        grad.setColorAt(0, fill)
        fill.setAlpha(0)
        grad.setColorAt(1, fill)
        for run in runs:
            path = QPainterPath(run[0])
            for pt in run[1:]:
                path.lineTo(pt)
            shape = QPainterPath(path)
            shape.lineTo(QPointF(run[-1].x(), area.bottom()))
            shape.lineTo(QPointF(run[0].x(), area.bottom()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawPath(shape)
            p.setPen(QPen(self.color, px(LINE_W), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

    def paintEvent(self, _e: QPaintEvent) -> None:
        """Draw the background, two guide lines, the line with its fill, the newest point and the top of the scale."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(TRACK))
        p.drawRoundedRect(QRectF(self.rect()), px(8), px(8))
        values = list(self.series.values)
        real = [v for v in values if v is not None]
        area = QRectF(self.rect()).adjusted(px(8), px(8), -px(8), -px(8))
        if not real:
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, "—")
            return
        lo, hi = self._range(real)
        p.setPen(QPen(QColor(MUTED), 1, Qt.PenStyle.DotLine))
        for frac in (1 / 3, 2 / 3):
            y = area.top() + area.height() * frac
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        runs = self._runs(values, area, lo, hi)
        self._draw_runs(p, runs, area)
        if values[-1] is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.color)
            p.drawEllipse(runs[-1][-1], px(3), px(3))
        p.setPen(QColor(DIM))
        f = p.font()
        f.setPixelSize(px(10))
        p.setFont(f)
        top = number(hi, digits=0 if hi >= DECIMALS_BELOW else 1)
        p.drawText(QRectF(area.left(), area.top() - px(6), area.width(), px(12)), Qt.AlignmentFlag.AlignLeft, top)


class Tile(QFrame):
    """One metric: a title, the current value (coloured by its level), a line of min / average / max and the chart."""

    def __init__(self, title: str, series: rx_diag.Series, unit: str, floor_span: float, tip: str, zero_based: bool = True,
                 fixed: tuple[float, float] | None = None, digits: int | None = None) -> None:
        """`unit` is appended to the numbers; `tip` explains the metric (tooltip of the whole tile)."""
        super().__init__()
        self.setObjectName("tile")
        self.unit, self.digits, self.series = unit, digits, series
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(12), px(10), px(12), px(12))
        lay.setSpacing(px(4))
        self.title = label(title, "h2")
        self.value = label("—", "h3")
        self.sub = label("", "small")
        self.chart = Chart(series, floor_span, zero_based, fixed)
        for w in (self.title, self.value, self.sub, self.chart):
            lay.addWidget(w)
        self.setToolTip(tip)
        self._sig: tuple[str, str] | None = None

    def show_value(self, value: float | None, level: str, extra: str = "") -> None:
        """The current value, its level and the statistics line (`extra` replaces it, e.g. the loss counters)."""
        text = number(value, self.unit, self.digits)
        st = self.series.stats()
        sub = extra or (_("min {lo} · śr {avg} · max {hi}").format(lo=number(st[0], digits=self.digits),
                                                                   avg=number(st[1], digits=self.digits),
                                                                   hi=number(st[2], digits=self.digits)) if st else "")
        if (text, level, sub) != self._sig:                # the labels are touched only when something changed
            self._sig = (text, level, sub)
            self.value.setText(text)
            set_prop(self.value, "level", level)
            self.sub.setText(sub)
        self.chart.set_level(level)
        self.chart.update()

    def rescale(self) -> None:
        """Fixed sizes for the current scale."""
        self.chart.rescale()


def _card(title_text: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    """A rounded card with a header row (the title on the left; the caller adds a status on the right) and a body layout."""
    box = QFrame()
    box.setObjectName("group")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(px(14), px(12), px(14), px(14))
    lay.setSpacing(px(10))
    head = QHBoxLayout()
    head.setSpacing(px(8))
    head.addWidget(label(title_text, "h2"))
    head.addStretch(1)
    lay.addLayout(head)
    return box, lay, head


def _row(tiles: list[Tile]) -> QHBoxLayout:
    """The tiles of a card side by side, equally wide."""
    row = QHBoxLayout()
    row.setSpacing(px(10))
    for t in tiles:
        row.addWidget(t, 1)
    return row


class PadCard(QFrame):
    """The Bluetooth link of one pad: report rate, signal (RSSI), link quality, and how long it has been connected."""

    def __init__(self, slot: int) -> None:
        """Build the card of pad `slot`; it is shown only while that pad is connected."""
        super().__init__()
        self.slot = slot
        box, lay, head = _card(_("Pad {n}").format(n=slot + 1))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        self.info = label("", "dim")
        head.addWidget(self.info)
        d = rx_diag.pads[slot]
        self.hz = Tile(_("Raport pada (Bluetooth)"), d.hz, "Hz", 50.0,
                       _("Ile raportów na sekundę nadajnik dostaje od pada przez Bluetooth. "
                         "Spadki oznaczają, że radio gubi albo opóźnia pakiety."), digits=0)
        self.rssi = Tile(_("Sygnał Bluetooth (RSSI)"), d.rssi, "dBm", 6.0,
                         _("Moc sygnału pada odczytana przez kontroler Bluetooth nadajnika, w dBm: im bliżej zera, tym lepiej "
                           "(powyżej -65 dobrze, poniżej -80 słabo). Brak danych: starszy nadajnik albo kontroler nie podaje odczytu."),
                         zero_based=False, digits=0)
        self.quality = Tile(_("Jakość łącza Bluetooth"), d.quality, "%", 100.0,
                            _("Jakość łącza według kontrolera Bluetooth nadajnika (0-100 %). "
                              "Wiele kontrolerów jej nie liczy - wtedy widać kreskę."),
                            fixed=(0.0, 100.0), digits=0)
        lay.addLayout(_row([self.hz, self.rssi, self.quality]))
        self.tiles = (self.hz, self.rssi, self.quality)

    def refresh(self, now: float) -> None:
        """Update the numbers from rx_diag."""
        i = self.slot
        d = rx_diag.pads[i]
        since = S.connected_at[i]
        parts = [_("połączony od {t}").format(t=age_text(now - since))] if since is not None else []
        if d.connects > 1:
            parts.append(_("ponowne połączenia: {n}").format(n=d.connects - 1))
        self.info.setText("  ·  ".join(parts))
        self.hz.show_value(d.hz.last(), "")
        rssi = d.rssi.last()
        self.rssi.show_value(rssi, rx_diag.rssi_level(rssi))
        q = d.quality.last()
        self.quality.show_value(q, rx_diag.quality_level(q))

    def rescale(self) -> None:
        """Sizes for the current scale."""
        for t in self.tiles:
            t.rescale()


class DiagPage(QScrollArea):
    """The whole tab: the link card on top, a card per connected pad below, scrolling when the window is low."""

    def __init__(self) -> None:
        """Build the cards; they are filled by refresh()."""
        super().__init__()
        self.setObjectName("diag")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, px(4), 0)
        lay.setSpacing(px(14))
        n = rx_diag.net
        net_box, net_lay, head = _card(_("SIEĆ  ·  NADAJNIK ↔ RECEIVER"))
        self.status = label("", "dim")
        head.addWidget(self.status)
        self.rtt = Tile(_("Opóźnienie sieci (RTT)"), n.rtt, "ms", 4.0,
                        _("Czas obiegu pinga: receiver → nadajnik → receiver (mediana z ostatnich pomiarów nadajnika). "
                          "Na kablu zwykle poniżej 2 ms."))
        self.jitter = Tile(_("Jitter"), n.jitter, "ms", 2.0,
                           _("Jak bardzo odstępy między pingami nadajnika różnią się po drodze (RFC 3550). "
                             "Skoki jittera to opóźnienia, które czujesz jako szarpnięcia."))
        self.loss = Tile(_("Utrata pakietów"), n.loss, "%", 1.0,
                         _("Odsetek pakietów nadajnika, których zabrakło w ostatniej sekundzie (luki w numeracji). "
                           "Spóźnione i zdublowane pakiety są liczone osobno i nie wliczają się do strat."), digits=1)
        self.pps = Tile(_("Pakiety stanu na sekundę"), n.pps, "/s", 100.0,
                        _("Ile pakietów stanu padów dociera na sekundę (wszystkie pady razem)."), digits=0)
        net_lay.addLayout(_row([self.rtt, self.jitter, self.loss, self.pps]))
        lay.addWidget(net_box)
        self.pad_cards = [PadCard(i) for i in range(len(rx_diag.pads))]
        for c in self.pad_cards:
            lay.addWidget(c)
        self.empty = label(_("Brak podłączonych padów"), "dim")
        lay.addWidget(self.empty)
        lay.addStretch(1)
        self._keepalive = 0.0

    def refresh(self, now: float | None = None) -> None:
        """Update every card from rx_diag; keep the sender's Bluetooth reporting alive while the tab is on screen."""
        now = time.monotonic() if now is None else now
        if self.isVisible() and BT.supported(now) and now - self._keepalive >= BT.KEEPALIVE_S:
            self._keepalive = now
            BT.keepalive()
        online = S.sender_online(now)
        n = rx_diag.net
        src = S.peer[0] if S.peer else ""
        online_text = _("●  Nadajnik online") + (f"  ·  {src}" if src else "")
        self.status.setText(online_text if online else _("●  Nadajnik offline"))
        set_prop(self.status, "level", "ok" if online else "bad")
        self.rtt.show_value(n.rtt.last(), rx_diag.rtt_level(n.rtt.last()))
        self.jitter.show_value(n.jitter.last(), rx_diag.jitter_level(n.jitter.last()))
        self.loss.show_value(n.loss.last(), rx_diag.loss_level(n.loss.last()),
                             _("utracone {lost} · spóźnione {late} · zdublowane {dup}").format(lost=n.lost, late=n.late, dup=n.dup))
        self.pps.show_value(n.pps.last(), "")
        shown = 0
        for c in self.pad_cards:
            on = bool(S.alive[c.slot])
            c.setVisible(on)
            if on:
                shown += 1
                c.refresh(now)
        self.empty.setVisible(not shown)

    def rescale(self) -> None:
        """Sizes for the current scale (TV mode)."""
        for c in self.pad_cards:
            c.rescale()
        for t in (self.rtt, self.jitter, self.loss, self.pps):
            t.rescale()

