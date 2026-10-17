"""Receiver: widok Test (prawy panel okna) - ruch pada na wykresach, dryf gałek, kalibracja żyroskopu.

Działa na wybranym padzie (karta po lewej). Na czas widoku gra może dostawać neutralny stan pada (blokada - domyślnie
włączona, pamiętana w receiver_ui.ini), żeby testowanie przycisków nie klikało w grze; rysunek pada pokazuje stan
fizyczny. Odświeżanie: tick() z zegara okna (30 Hz) tylko, gdy widok jest widoczny.
  Ruch:      gyro [°/s] i akcelerometr [g], 3 osie, ostatnie CHART_S sekund, skala dopasowuje się do ruchu.
  Gałki:     położenie (skala pierwiastkowa - małe odchyłki widać), strefa martwa z korekcji dryfu i ostatni pomiar;
             "Zmierz dryf" (rx_drift) -> propozycja strefy martwej -> "Zastosuj" zapisuje korekcję pada (rx_padcal).
  Żyroskop:  "Kalibruj" - nadajnik liczy offset leżącego pada i zapisuje go per pad (proto CMD_GYRO_CAL).
"""
from rx_i18n import _
import math
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QSizePolicy, QWidget

import rx_drift as D
import rx_profiles as P
import rx_state as S
from proto import CAL_FEW, CAL_MOVED, CAL_OK, CAL_RUNNING, CAL_STATUS, CALF_STORED
from rx_config import C
from rx_qt_icons import icon
from rx_qt_panels import _vbox, scrolled
from rx_qt_style import ACCENT, BORDER, DIM, FG, LINE, MUTED, OK, TRACK, WARN, px, set_prop
from rx_qt_widgets import ConfirmButton, SegmentBar, button, icon_label, label

CHART_S = 5.0
AXES = (("X", "#ff6b6b"), ("Y", OK), ("Z", "#5b9bff"))
CAL_S = 3                       # kalibracja żyroskopu z okna [s]
CAL_WAIT_S = 3.0                # ponad czas kalibracji: tyle czekamy na odpowiedź nadajnika
QUERY_S = 2.0                   # bez stanu kalibracji (zgubiony pakiet) - pytamy nadajnik ponownie co tyle sekund
TRAIL = 24                      # tyle ostatnich położeń gałki widać jako ślad


def _nice(v):
    """Zaokrąglenie skali wykresu w górę do 1 / 2 / 5 × 10^k."""
    if v <= 0:
        return 1.0
    k = 10 ** math.floor(math.log10(v))
    return next(m * k for m in (1, 2, 5, 10) if m * k >= v)


def _font(size, bold=False):
    f = QFont("Segoe UI")
    f.setPixelSize(px(size))
    f.setBold(bold)
    return f


def pct(v, digits=0):
    """Ułamek 0..1 -> "6%" / "4.2%"."""
    return f"{v * 100:.{digits}f}%"


class MotionChart(QWidget):
    """Przewijany wykres 3 osi (ostatnie CHART_S s); skala symetryczna, dopasowana do największej wartości w oknie."""

    def __init__(self, title, unit, min_range, digits):
        super().__init__()
        self.title, self.unit, self.min_range, self.digits = title, unit, min_range, digits
        self.data = deque(maxlen=round(CHART_S * 1000 / 33))
        self.setMinimumHeight(px(96))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(px(300), px(96))

    def rescale(self):
        self.setMinimumHeight(px(96))
        self.updateGeometry()

    def push(self, values):
        self.data.append(tuple(values))
        self.update()

    def clear(self):
        self.data.clear()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(TRACK))
        p.drawRoundedRect(r, px(8), px(8))
        top = px(20)
        plot = r.adjusted(px(6), top, -px(6), -px(6))
        rng = _nice(max([self.min_range, *(abs(v) for row in self.data for v in row)]) * 1.1)
        mid = plot.center().y()
        p.setPen(QPen(QColor(LINE), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), mid), QPointF(plot.right(), mid))
        n = self.data.maxlen
        if len(self.data) >= 2:
            step = plot.width() / (n - 1)
            x0 = plot.right() - (len(self.data) - 1) * step
            for k, (_name, color) in enumerate(AXES):
                ky = plot.height() / 2 / rng
                # jedna linia łamana zamiast ścieżki budowanej punkt po punkcie (wykres odświeża się kilkadziesiąt razy/s)
                pts = QPolygonF([QPointF(x0 + j * step, mid - row[k] * ky) for j, row in enumerate(self.data)])
                p.setPen(QPen(QColor(color), 1.6))
                p.drawPolyline(pts)
        p.setFont(_font(11, True))
        p.setPen(QColor(DIM))
        p.drawText(QPointF(r.left() + px(8), r.top() + px(14)), self.title)
        x = r.left() + px(8) + QFontMetricsF(p.font()).horizontalAdvance(self.title) + px(10)
        p.setFont(_font(11))
        fm = QFontMetricsF(p.font())
        last = self.data[-1] if self.data else (0, 0, 0)
        for (name, color), v in zip(AXES, last, strict=True):
            text = f"{name} {v:+.{self.digits}f}"
            p.setPen(QColor(color))
            p.drawText(QPointF(x, r.top() + px(14)), text)
            x += fm.horizontalAdvance(text) + px(8)
        scale = f"±{rng:g} {self.unit}"
        p.setPen(QColor(MUTED))
        p.drawText(QPointF(r.right() - px(8) - fm.horizontalAdvance(scale), r.top() + px(14)), scale)


class StickScope(QWidget):
    """Obie gałki na żywo. Skala pierwiastkowa (r -> √r): odchyłka 1 % zajmuje 10 % promienia, więc dryf widać, a
    pełne wychylenie dalej sięga krawędzi. Kółko przerywane = strefa martwa z korekcji dryfu, żółte = szczyt
    ostatniego pomiaru."""

    RINGS = (0.05, 0.25)

    def __init__(self):
        super().__init__()
        self.pos = {"left": (0.0, 0.0), "right": (0.0, 0.0)}
        self.trail = {"left": deque(maxlen=TRAIL), "right": deque(maxlen=TRAIL)}
        self.dz = {}                           # korekcja dryfu {gałka: dz}
        self.peak = {}                         # ostatni pomiar {gałka: szczyt}
        self.setMinimumHeight(px(132))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(px(300), px(132))

    def rescale(self):
        self.setMinimumHeight(px(132))
        self.updateGeometry()

    def set_pos(self, ls, rs):
        pos = {"left": (D.unit(ls[0]), D.unit(ls[1])), "right": (D.unit(rs[0]), D.unit(rs[1]))}
        if pos != self.pos:
            for k, v in pos.items():
                self.trail[k].append(v)
            self.pos = pos
            self.update()

    def set_marks(self, dz, peak):
        if (dz, peak) != (self.dz, self.peak):
            self.dz, self.peak = dict(dz), dict(peak)
            self.update()

    def clear(self):
        for t in self.trail.values():
            t.clear()
        self.peak = {}
        self.update()

    @staticmethod
    def _r(v):
        return math.sqrt(min(1.0, max(0.0, v)))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        rad = min(h / 2 - px(14), w / 4 - px(8))
        for k, name in enumerate(("left", "right")):
            c = QPointF(w * (0.25 + 0.5 * k), h / 2 - px(6))
            p.setPen(QPen(QColor(BORDER), 1))
            p.setBrush(QColor(TRACK))
            p.drawEllipse(c, rad, rad)
            p.setPen(QPen(QColor(LINE), 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for ring in self.RINGS:
                p.drawEllipse(c, rad * self._r(ring), rad * self._r(ring))
            p.drawLine(QPointF(c.x() - rad, c.y()), QPointF(c.x() + rad, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - rad), QPointF(c.x(), c.y() + rad))
            if d := self.dz.get(name):
                p.setPen(QPen(QColor(ACCENT), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(QColor(76, 141, 255, 40))
                p.drawEllipse(c, rad * self._r(d), rad * self._r(d))
            if pk := self.peak.get(name):
                p.setPen(QPen(QColor(WARN), 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, rad * self._r(pk), rad * self._r(pk))
            p.setPen(Qt.PenStyle.NoPen)
            trail = list(self.trail[name])
            for j, (x, y) in enumerate(trail[:-1]):
                p.setBrush(QColor(255, 255, 255, 25 + 120 * j // TRAIL))
                p.drawEllipse(self._pt(c, rad, x, y), px(2), px(2))
            x, y = self.pos[name]
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.setBrush(QColor(ACCENT))
            p.drawEllipse(self._pt(c, rad, x, y), px(5), px(5))
            p.setFont(_font(11, True))
            p.setPen(QColor(DIM))
            text = "L" if name == "left" else "R"
            p.drawText(QPointF(c.x() - rad, c.y() - rad + px(10)), text)
            p.setFont(_font(11))
            p.setPen(QColor(FG))
            val = f"{x:+.3f}  {y:+.3f}"
            fm = QFontMetricsF(p.font())
            p.drawText(QPointF(c.x() - fm.horizontalAdvance(val) / 2, c.y() + rad + px(14)), val)

    def _pt(self, c, rad, x, y):
        r = math.hypot(x, y)
        k = rad * self._r(r) / r if r else 0.0
        return QPointF(c.x() + x * k, c.y() + y * k)


def _head(icon_name, text):
    row = QHBoxLayout()
    row.setSpacing(px(6))
    row.addWidget(icon_label(icon_name, px(16)))
    row.addWidget(label(text, "h2"), 1)
    return row


class TestPanel(QWidget):
    """Prawy panel w widoku Test. block_changed(bool) - użytkownik przełączył blokadę wyjścia do gry."""

    block_changed = Signal(bool)

    def __init__(self, block_on=True):
        super().__init__()
        self.slot = 0
        self.run = None                  # rx_drift.DriftRun w trakcie
        self.result = None               # ostatni wynik pomiaru dryfu (rx_drift.DriftResult)
        self.cal_t0 = None               # kalibracja gyro: kiedy wysłano polecenie (None = nic nie trwa)
        self.cal_note = None             # (tekst, poziom) wyniku kalibracji, który ma zostać widoczny
        self.query_t = 0.0               # kiedy ostatnio pytaliśmy nadajnik o stan kalibracji
        body = QWidget()
        lay = _vbox(body)
        _vbox(self).addWidget(scrolled(body))

        row = QHBoxLayout()
        row.setSpacing(px(6))
        row.addWidget(icon_label("shield", px(18)))
        self.block = QCheckBox(_("Blokuj wyjście do gry"))
        self.block.setToolTip(_("Na czas testu gra dostaje puszczone przyciski i gałki w środku tego pada; skróty pada, "
                                "mysz touchpada i tryb konsoli też nie reagują. Działa tylko przy aktywnym oknie - po "
                                "przejściu do gry albo wyjściu z testu pad od razu do niej wraca."))
        self.block.setChecked(block_on)
        self.block.toggled.connect(self.block_changed.emit)
        row.addWidget(self.block, 1)
        lay.addLayout(row)

        lay.addLayout(_head("pulse", _("RUCH")))
        self.gyro_chart = MotionChart(_("Gyro"), "°/s", 10.0, 1)
        self.acc_chart = MotionChart(_("Akcelerometr"), "g", 1.2, 2)
        lay.addWidget(self.gyro_chart)
        lay.addWidget(self.acc_chart)

        lay.addLayout(_head("target", _("GAŁKI - DRYF")))
        self.scope = StickScope()
        self.scope.setToolTip(_("Skala pierwiastkowa: małe odchyłki są powiększone. Kropkowane okręgi: 5% i 25% "
                                "wychylenia; niebieski: strefa martwa z korekcji dryfu; żółty: szczyt ostatniego pomiaru."))
        lay.addWidget(self.scope)
        self.drift_hint = label(_("Puść gałki i odłóż pad - pomiar trwa {s} s.").format(s=f"{D.MEASURE_S:g}"), "dim", wrap=True)
        lay.addWidget(self.drift_hint)
        self.drift_bar = SegmentBar(px(5))
        lay.addWidget(self.drift_bar)
        self.drift_res = label("", wrap=True)
        lay.addWidget(self.drift_res)
        row = QHBoxLayout()
        row.setSpacing(px(8))
        self.measure = button(_("Zmierz dryf"))
        self.measure.setIcon(icon("target"))
        self.measure.clicked.connect(self.start_drift)
        self.apply = button(_("Zastosuj"), primary=True)
        self.apply.setToolTip(_("Zapisuje proponowaną strefę martwą jako korekcję dryfu tego pada - działa od razu "
                                "we wszystkich jego profilach (także profilach gier)."))
        self.apply.clicked.connect(self.apply_drift)
        row.addWidget(self.measure, 1)
        row.addWidget(self.apply, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(px(6))
        self.corr = label("", "dim", wrap=True)
        self.clear_btn = ConfirmButton(_("Wyczyść"), _("Wyczyścić?"), iconic="trash",
                                       tip=_("Wyczyść") + " - " + _("Usuwa korekcję dryfu tego pada"))
        self.clear_btn.confirmed.connect(self.clear_drift)
        row.addWidget(self.corr, 1)
        row.addWidget(self.clear_btn)
        lay.addLayout(row)

        lay.addLayout(_head("gyro", _("ŻYROSKOP - KALIBRACJA")))
        lay.addWidget(label(_("Połóż pad na płaskiej, stabilnej powierzchni i nie dotykaj go przez {s} s.")
                            .format(s=CAL_S), "dim", wrap=True))
        self.cal_bar = SegmentBar(px(5))
        lay.addWidget(self.cal_bar)
        self.cal_text = label("", wrap=True)
        lay.addWidget(self.cal_text)
        self.cal_btn = button(_("Kalibruj żyroskop"))
        self.cal_btn.setIcon(icon("gyro"))
        self.cal_btn.setToolTip(_("Nadajnik liczy offset żyroskopu i zapisuje go na telefonie dla tego pada - "
                                  "po restarcie nie trzeba już kalibrować przy starcie."))
        self.cal_btn.clicked.connect(self.start_cal)
        lay.addWidget(self.cal_btn)
        lay.addStretch(1)
        self._buttons = (self.measure, self.apply, self.cal_btn)
        self.reset_view()

    # ---------- wybór pada / widok ----------
    def set_slot(self, slot):
        if slot != self.slot:
            self.slot = slot
            self.reset_view()

    def activate(self, on):
        """Wejście do widoku Test (stan kalibracji z nadajnika) / wyjście (przerwij pomiar)."""
        if on:
            self._query(time.monotonic())
        else:
            self.cancel()

    def _query(self, now):
        self.query_t = now
        P.query_gyro_cal(self.slot)

    def cancel(self):
        if self.run is not None:
            self.run.cancel()
            self.run = None

    def reset_view(self):
        self.cancel()
        self.result = None
        self.cal_t0 = self.cal_note = None
        self.gyro_chart.clear()
        self.acc_chart.clear()
        self.scope.clear()
        self._bar(self.drift_bar, None)
        self._bar(self.cal_bar, None)
        self._result("", "")
        self._query(time.monotonic())
        self.refresh()

    @staticmethod
    def _bar(bar, p):
        """Pasek postępu: p = 0..1, None = schowany (nic nie trwa)."""
        bar.setVisible(p is not None)
        if p is not None:
            bar.set([(p, ACCENT), (1 - p, LINE)])

    def _result(self, text, level):
        self.drift_res.setText(text)
        self.drift_res.setVisible(bool(text))
        set_prop(self.drift_res, "level", level)

    def rescale(self):
        for w in (self.gyro_chart, self.acc_chart, self.scope):
            w.rescale()
        for b in self._buttons:
            b.setIconSize(QSize(px(18), px(18)))
        self.drift_bar.setFixedHeight(px(5))
        self.cal_bar.setFixedHeight(px(5))

    # ---------- dryf ----------
    def start_drift(self):
        if not S.alive[self.slot]:
            return
        self.cancel()
        self.result = None
        self.scope.clear()
        self.run = D.DriftRun(self.slot, time.monotonic(), P.raw_sticks if P.sender_calib(self.slot) else None)
        self.refresh()

    def apply_drift(self):
        if self.result is None or self.result.moved:
            return
        vals = {n: self.result.stick(n).dz for n in ("left", "right")}
        ok, msg = P.set_drift(self.slot, vals)
        self._result(("✓ " + _("Zastosowano: L {l}, R {r}").format(l=pct(vals["left"]), r=pct(vals["right"])))
                     if ok else msg, "ok" if ok else "bad")
        self.result = None
        self.refresh()

    def clear_drift(self):
        ok, msg = P.set_drift(self.slot, {"left": 0, "right": 0})
        self._result(("✓ " + _("Korekcja dryfu usunięta")) if ok else msg, "ok" if ok else "bad")
        self.refresh()

    def _drift_tick(self, now):
        if self.run is None:
            return
        if not S.alive[self.slot]:
            self.cancel()
            self._bar(self.drift_bar, None)
            self.refresh()
            return
        res = self.run.tick(now)
        self._bar(self.drift_bar, self.run.progress(now))
        if res is None and self.run.phase != "done":
            return
        self.run = None
        self._bar(self.drift_bar, None)
        self.result = res
        self._show_result(res)
        self.refresh()

    def _show_result(self, res):
        if res is None:
            self._result(_("Brak próbek - pad niepodłączony?"), "bad")
            return
        if res.moved:
            self._result(_("Gałka się ruszała - puść obie gałki i zmierz jeszcze raz."), "bad")
            return
        lines = [_("{s}: dryf {peak} → strefa martwa {dz}").format(
                     s=_("Lewa") if n == "left" else _("Prawa"), peak=pct(res.stick(n).peak, 1), dz=pct(res.stick(n).dz))
                 for n in ("left", "right")]
        if not P.sender_calib(self.slot):
            lines.append(_("Nadajnik w starszej wersji przycina odchyłki do ~2% - kliknij Aktualizuj, żeby mierzyć dokładnie."))
        self._result("\n".join(lines), "")

    # ---------- żyroskop ----------
    def start_cal(self):
        if P.calibrate_gyro(self.slot, CAL_S):
            self.cal_t0, self.cal_note = time.monotonic(), None
        self.refresh()

    def _cal_tick(self, now):
        if self.cal_t0 is None:
            if (S.gyro_cal[self.slot] is None and now - self.query_t >= QUERY_S and S.alive[self.slot]
                    and P.sender_calib(self.slot)):
                self._query(now)             # odpowiedź na pytanie o stan zginęła (UDP) - jeszcze raz
            return
        r = S.gyro_cal[self.slot]
        done = r is not None and r[5] >= self.cal_t0 and r[0] not in (CAL_RUNNING, CAL_STATUS)
        if done or now - self.cal_t0 > CAL_S + CAL_WAIT_S:
            self.cal_t0 = None
            self._bar(self.cal_bar, None)
            if done:
                saved = bool(r[1] & CALF_STORED)
                self.cal_note = {CAL_OK: ("✓ " + (_("Skalibrowano i zapisano na telefonie.") if saved else
                                                  _("Skalibrowano (pad bez MAC - bez zapisu, do restartu nadajnika).")),
                                          "ok"),
                                 CAL_MOVED: (_("Pad się ruszał - połóż go nieruchomo i spróbuj jeszcze raz."), "bad"),
                                 CAL_FEW: (_("Brak danych z czujnika ruchu pada."), "bad")}.get(r[0])
            else:
                self.cal_note = (_("Brak odpowiedzi nadajnika."), "bad")
            return
        self._bar(self.cal_bar, min(1.0, (now - self.cal_t0) / CAL_S))

    def _cal_status(self):
        """(tekst, poziom) stanu kalibracji pada."""
        i = self.slot
        if not S.alive[i]:
            return _("Pad niepodłączony."), ""
        if not P.sender_calib(i):
            return _("Kalibracja z okna wymaga nowszego nadajnika - kliknij Aktualizuj."), "warn"
        if self.cal_t0 is not None:
            return _("Kalibruję - nie ruszaj pada…"), ""
        r = S.gyro_cal[i]
        head = self.cal_note
        if r is None:
            return (head[0], head[1]) if head else (_("Sprawdzam stan kalibracji…"), "")
        _status, flags, _stored, cur, t, _rx = r
        if flags & CALF_STORED and t:
            base = _("Zapisana kalibracja z {date}.").format(date=time.strftime("%Y-%m-%d %H:%M", time.localtime(t)))
        else:
            base = _("Brak zapisanej kalibracji - nadajnik kalibruje pad przy starcie (pad musi wtedy leżeć).")
        offs = "  ".join(f"{n} {v / C.GYRO_RES:+.2f}" for (n, _c), v in zip(AXES, cur, strict=True))
        text = base + "\n" + _("Offset teraz [°/s]: {v}").format(v=offs)
        if head:
            return head[0] + "\n" + text, head[1]
        return text, ""

    # ---------- odświeżanie ----------
    def refresh(self):
        """Przyciski i opisy (zmiana stanu, nie co klatkę)."""
        i = self.slot
        alive = bool(S.alive[i])
        busy = self.run is not None
        self.measure.setEnabled(alive and not busy)
        self.measure.setText(_("Mierzę…") if busy else _("Zmierz dryf"))
        self.apply.setEnabled(self.result is not None and not self.result.moved and not busy)
        d = P.drift(i)
        self.corr.setText(_("Korekcja dryfu: L {l} · R {r}").format(l=pct(d["left"]) if d.get("left") else "—",
                                                                   r=pct(d["right"]) if d.get("right") else "—")
                          if d else _("Bez korekcji dryfu - strefa martwa tylko z profilu."))
        self.clear_btn.setVisible(bool(d))
        peak = {} if self.result is None or self.result.moved else {n: self.result.stick(n).peak for n in ("left", "right")}
        self.scope.set_marks(d, peak)
        self.cal_btn.setEnabled(alive and P.sender_calib(i) and self.cal_t0 is None)
        text, level = self._cal_status()
        if text != self.cal_text.text():
            self.cal_text.setText(text)
        set_prop(self.cal_text, "level", level)

    def tick(self, now, live):
        """Z zegara okna (30 Hz): wykresy, gałki, postęp pomiaru i kalibracji. live = rx_profiles.live() pada."""
        t = P.test_live(self.slot)
        self.gyro_chart.push(v / C.GYRO_RES for v in t["gyro"])
        self.acc_chart.push(v / C.ACC_RES for v in t["acc"])
        self.scope.set_pos(live["ls"], live["rs"])
        self._drift_tick(now)
        self._cal_tick(now)
        self.refresh()
        return t

