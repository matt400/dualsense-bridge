"""Receiver: jedno okno do wszystkiego (PySide6): pady z baterią i opóźnieniem po lewej, rysunek pada na środku
(klik = remapowanie), profile / gyro / zmiany po prawej, ikona w zasobniku. Pełny ekran = tryb TV: większy interfejs
(czytelny z kanapy) i sterowanie oknem padem (rx_qt_tv).

Szybkość: rysunek pada odświeża się 30 razy na sekundę tylko, gdy okno jest widoczne i tylko, gdy stan pada się
zmienił; reszta (karty, zasobnik) co 400 ms, także przy ukrytym oknie (podpowiedź zasobnika, niski stan baterii).
Zmiany profilu działają na pad na żywo po 150 ms od ostatniego ruchu suwaka, zapis do remap.json jest osobno (Save).
"""
from rx_i18n import _
import os
import signal
from functools import cache
import sys
import threading
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMainWindow, QMenu, QSplitter, QStackedWidget,
                               QSystemTrayIcon, QTabWidget, QToolButton, QVBoxLayout, QWidget)

import rx_console
import rx_events as ev
import rx_feedback as F
import rx_games
import rx_latency
import rx_master
import rx_profiles as P
import rx_state as S
import rx_net
import rx_update
import rx_status as ST
import rx_testmode
import rx_winapi as W
from proto import CMD_UPDATE, UPD_RUNNING
from rx_config import C
from rx_editor import Editor
from rx_icon import ICON_LOW, ICON_OK, icon_png, render_icon
from rx_qt_bt import BtDrawer, indicator_button
from rx_qt_keys import KeysDrawer
from rx_qt_console import ConsoleSettings
from rx_qt_icons import icon, pixmap
from rx_qt_log import LogPanel
from rx_qt_pad import PadView
from rx_qt_panels import ChangesPage, GyroPage, PartPage, ProfileBar
from rx_qt_style import DIM, LEVEL, SEGMENT, TV_SCALE, apply_theme, px, set_prop
from rx_qt_test import TestPanel
from rx_qt_tv import PadNav
from rx_qt_widgets import BatteryBar, ConfirmButton, Segmented, SegmentBar, button, icon_button, icon_label, label

LIVE_MS, SLOW_MS, APPLY_MS = 33, 400, 150
UPDATE_WAIT_S = 60.0       # tyle najwyżej czekamy na wynik aktualizacji nadajnika (git pull na telefonie)
UPDATE_RESTART_MS = 2500   # po udanej aktualizacji: tyle widać komunikat, potem restart receivera
NOTE_S = 6.0           # tyle sekund komunikat (np. "Saved") zasłania status profilu
VIEWS = (("out", "Wynik"), ("in", "Fizycznie"), ("test", "Test"))
EXIT_HOLD_S = 1.0      # widok Test w trybie TV: tyle trzeba trzymać ○ testowanego pada, żeby wyjść z testu


@cache
def make_icon(rgb):
    """Ikona pada w kilku rozmiarach - ostra w zasobniku, na pasku zadań i przy skalowaniu 150-200 %."""
    ic = QIcon()
    for size in (16, 24, 32, 48, 64):
        pm = QPixmap()
        pm.loadFromData(icon_png(render_icon(rgb, size)))
        ic.addPixmap(pm)
    return ic


def n_changes(n):
    """'1 zmiana', '3 zmiany', '5 zmian', '22 zmiany' (polska odmiana) / '1 change', 'N changes'."""
    if n == 1:
        return _("{n} zmiana").format(n=n)
    few = n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
    return (_("{n} zmiany") if few else _("{n} zmian")).format(n=n)


def _profile_label(key):
    label = S.profile_label(key)
    return _("domyślny") if label == "default" else label


def screens():
    """[(klucz, QScreen)] - klucz to nazwa ekranu z Windows (np. \\\\.\\DISPLAY2), a gdy jej brak - numer."""
    return [(sc.name() or f"#{n}", sc) for n, sc in enumerate(QApplication.screens(), 1)]


class PadCard(QFrame):
    """Karta pada po lewej - tylko to, co ważne na pierwszy rzut oka: bateria, opóźnienie pad -> gra (z paskiem
    odcinków), profil i tryb touchpada; szczegóły (rozbicie opóźnienia, ping, utrata, Hz) w podpowiedzi. Problem
    (brak sygnału, wibracja niedostępna) pokazuje się jako jedna linia ostrzeżenia. Niepodłączony pad = sama nazwa.
    Klik = edytuj ten pad; klik w koronę = ten pad zostaje masterem (drugi klik - bez mastera)."""

    clicked = Signal(int)
    master_changed = Signal()

    def __init__(self, slot):
        super().__init__()
        self.slot, self._key, self._lat = slot, None, None
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(px(14), px(12), px(10), px(12))
        lay.setSpacing(px(7))
        head = QHBoxLayout()
        head.setSpacing(px(4))
        self.dot = label("●")
        self.crown = QToolButton()
        self.crown.setObjectName("crown")
        self.crown.setIconSize(QSize(px(18), px(18)))
        self.crown.setCursor(Qt.CursorShape.PointingHandCursor)
        self.crown.clicked.connect(self.toggle_master)
        self.rumble = icon_button("rumble", _("Test wibracji") + " - "
                                  + _("Krótka wibracja i mignięcie lightbara - sprawdza, czy wibracja dochodzi do pada"), 18)
        self.rumble.clicked.connect(lambda: self.test_rumble())
        self.off = ConfirmButton(_("Wyłącz"), _("Wyłączyć?"), iconic="power",
                                 tip=_("Wyłącz") + " - " + _("Rozłącza pada przez Bluetooth - pad się wyłącza"))
        self.off.confirmed.connect(lambda: P.power_off(slot))
        head.addWidget(self.dot)
        head.addSpacing(px(2))
        head.addWidget(label(_("Pad {n}").format(n=slot + 1), "name"))
        head.addWidget(self.crown)
        head.addStretch(1)
        head.addWidget(self.rumble)
        head.addWidget(self.off)
        lay.addLayout(head)
        self.status = label("", "dim")               # tylko gdy pad nie działa: "niepodłączony" / "brak sygnału"
        lay.addWidget(self.status)

        self.body = QWidget()                         # wszystko poniżej - tylko dla podłączonego pada
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(px(7))
        batt = QHBoxLayout()
        self.bar, self.pct = BatteryBar(px(8)), label("", "val")
        self.pct.setMinimumWidth(px(44))
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        batt.addWidget(self.bar, 1)
        batt.addWidget(self.pct)
        bl.addLayout(batt)
        lat = QHBoxLayout()
        lat.setSpacing(px(6))
        self.lat_icon = icon_label("stopwatch", px(16))
        self.lat = label("", "big")                  # średnie opóźnienie pad -> gra (albo ping, gdy brak pomiaru)
        self.lat_stats = label("", "small")          # p95 · max
        self.mode_icon = label("")
        lat.addWidget(self.lat_icon)
        lat.addWidget(self.lat)
        lat.addWidget(self.lat_stats, 0, Qt.AlignmentFlag.AlignBottom)
        lat.addStretch(1)
        lat.addWidget(self.mode_icon)
        bl.addLayout(lat)
        self.lat_bar = SegmentBar(px(5))
        bl.addWidget(self.lat_bar)
        self.btn_row = QWidget()                      # przycisk -> gra: tylko naciśnięcia (bez gałek i gyro)
        br = QHBoxLayout(self.btn_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.setSpacing(px(6))
        self.btn_icon = icon_label("press", px(16))
        self.btn_lat = label("", "val")
        self.btn_stats = label("", "small")
        self.btn_reset = icon_button("reset", _("Wyzeruj pomiar przycisk → gra"), 14)
        self.btn_reset.setProperty("mini", True)
        self.btn_reset.clicked.connect(self.reset_buttons)
        br.addWidget(self.btn_icon)
        br.addWidget(self.btn_lat)
        br.addWidget(self.btn_stats, 0, Qt.AlignmentFlag.AlignBottom)
        br.addStretch(1)
        br.addWidget(self.btn_reset)
        bl.addWidget(self.btn_row)
        self.profile = label("", "dim")
        bl.addWidget(self.profile)
        lay.addWidget(self.body)
        self.warn = label("", wrap=True)              # jedna linia problemu (wibracja niedostępna)
        lay.addWidget(self.warn)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.slot)

    def test_rumble(self):
        print(f"Controller {self.slot}: rumble test from the window (feedback -> sender)")
        F.pulse(self.slot, "test", force=True)

    def toggle_master(self):
        ok, msg = rx_master.set_master(None if rx_master.is_master(self.slot) else self.slot)
        if not ok:
            print(f"Master: {msg}")
        self._key = None
        self.master_changed.emit()

    def set_selected(self, on):
        set_prop(self, "selected", on)

    def reset_buttons(self):
        rx_latency.reset_buttons(self.slot)
        self._lat = None
        self._update_latency(time.monotonic())

    def _update_latency(self, now):
        """Opóźnienie: zmienia się co odświeżenie - osobno od reszty karty (tylko gdy coś się zmieniło)."""
        i = self.slot
        summ, press = ST.latency(i), ST.button_latency(i)
        link = ST.link_text(i, now)
        level = ST.latency_level(summ, i, now) if S.alive[i] else ""
        if summ is None:
            text = ST.ping_text(i, now)
            sig = (text, "", (), link, level if text != "—" else "")
        else:
            parts = ST.latency_parts(summ)
            sig = (f"{summ.avg:.1f} ms", ST.stats_text(summ), tuple((k, round(v, 1)) for k, _t, v in parts), link, level)
        bsig = None if press is None else (f"{press.avg:.1f} ms", ST.stats_text(press), ST.lat_level(press), press.n)
        if (sig, bsig) == self._lat:
            return
        self._lat = (sig, bsig)
        self.lat.setText(sig[0])
        self.lat_stats.setText(sig[1])
        set_prop(self.lat, "level", sig[4])
        self.lat_bar.set([(v, SEGMENT[k]) for k, v in sig[2]])
        self.lat_bar.setVisible(bool(sig[2]))
        tip = self._latency_tip(summ, link)
        for w in (self.lat, self.lat_icon, self.lat_stats, self.lat_bar):
            w.setToolTip(tip)
        self.btn_row.setVisible(bsig is not None)
        if bsig is not None:
            self.btn_lat.setText(bsig[0])
            self.btn_stats.setText(bsig[1])
            set_prop(self.btn_lat, "level", bsig[2])
            btip = self._button_tip(press)
            for w in (self.btn_icon, self.btn_lat, self.btn_stats):
                w.setToolTip(btip)

    @staticmethod
    def _rows(summ):
        return [f"<span style='color:{SEGMENT[k]}'>■</span>&nbsp; {name}: <b>{v:.2f} ms</b>"
                for k, name, v in ST.latency_parts(summ)]

    @staticmethod
    def _latency_tip(summ, link):
        """Podpowiedź (HTML): rozbicie opóźnienia z kolorami jak na pasku, łącze, uwaga o pomiarze."""
        dim = f"<span style='color:{DIM}'>{{}}</span>"
        if summ is None:
            rows = [f"<b>{_('Opóźnienie pad → gra')}</b>",
                    dim.format(_("Brak pomiaru: pad leży albo nadajnik jest w starszej wersji (Aktualizuj)."))]
        else:
            rows = [f"<b>{_('Opóźnienie pad → gra')}: {summ.avg:.1f} ms</b> &nbsp;"
                    + dim.format(_("średnia · {stats} ms · {n} próbek z 5 s").format(stats=ST.stats_text(summ), n=summ.n)),
                    *PadCard._rows(summ),
                    dim.format(_("Średnie odcinków. Stała część radia Bluetooth (~1–2 ms) jest niemierzalna."))]
        if link:
            rows.append(dim.format(link))
        return "<br>".join(rows)

    @staticmethod
    def _button_tip(summ):
        dim = f"<span style='color:{DIM}'>{{}}</span>"
        return "<br>".join([
            f"<b>{_('Przycisk → gra')}: {summ.avg:.1f} ms</b> &nbsp;"
            + dim.format(_("średnia · {stats} ms · {n} z ostatnich {keep} naciśnięć").format(
                stats=ST.stats_text(summ), n=summ.n, keep=rx_latency.BUTTON_KEEP)),
            *PadCard._rows(summ),
            dim.format(_("Tylko przyciski, D-pad i spusty (wciśnięcie / puszczenie) - bez gałek i żyroskopu."))])

    def update_state(self, now):
        """Odświeża kartę (tylko to, co się zmieniło). -> True, gdy bateria pada jest na wyczerpaniu."""
        i = self.slot
        alive, want = bool(S.alive[i]), S.want[i].is_set()
        shown = alive and self.isVisible()     # okno w zasobniku: bez liczenia median opóźnienia
        if shown:
            self._update_latency(now)
        pct, flags = S.batt_info[i]
        low = ST.is_low(i, ST.low_pct())
        master = rx_master.is_master(i)
        key = (alive, want, pct, flags, S.plugged[i], S.mouse_mode[i], S.profile_changes(i), S.active[i],
               master, S.pad_id[i], S.sender_info[i], ST.low_pct())
        if key == self._key:
            return low                        # nic się nie zmieniło - nie dotykaj widżetów
        self._key = key
        self._lat = None
        self.crown.setIcon(icon("crown", dim=not master))
        self.crown.setToolTip(_("Pad master - kliknij, aby zdjąć") if master else _("Kliknij: ten pad zostaje masterem"))
        self.crown.setEnabled(master or alive or want)
        self.off.setEnabled(alive)
        self.off.setVisible(alive)
        self.rumble.setEnabled(alive)
        self.rumble.setVisible(alive)
        set_prop(self, "low", low)
        set_prop(self, "off", not (alive or want))
        self.body.setVisible(alive)
        self.status.setText(_("●  połączony") if alive else _("●  brak sygnału") if want else _("○  niepodłączony"))
        self.status.setVisible(not alive)
        set_prop(self.status, "level", "warn" if want and not alive else "")
        self.dot.setVisible(alive or want)
        set_prop(self.dot, "level", "ok" if alive else "warn")
        self.dot.setToolTip(self.status.text().strip("●○ "))
        charging = bool(flags & 1)
        if pct > 100:                         # stan baterii nieznany
            self.bar.set(None, LEVEL["ok"])
            self.pct.setText(_("b/d"))
        else:
            self.bar.set(pct, LEVEL[ST.battery_level(pct, charging, ST.low_pct())])
            self.pct.setText(f"{pct}%" + (" ⚡" if charging else ""))
        set_prop(self.pct, "level", "bad" if low else "")
        n = S.profile_changes(i)
        pm = pixmap("mouse" if S.mouse_mode[i] else "touchpad", px(18) * 2)
        pm.setDevicePixelRatio(2)
        self.mode_icon.setPixmap(pm)
        self.mode_icon.setToolTip((_("Touchpad jako mysz") if S.mouse_mode[i] else _("Touchpad jako dotyk DS4")) + "\n"
                                  + _("DS4: {ds4}  ·  touchpad: {tp}").format(
                                      ds4=_("wł.") if S.plugged[i] else _("wył."), tp=_("mysz") if S.mouse_mode[i] else "DS4"))
        self.profile.setText(_("profil: {name}").format(name=_profile_label(S.active[i])) + (f" ({n})" if n else ""))
        text, level, tip = ST.rumble_status(i)
        problem = alive and level in ("bad", "warn")
        self.warn.setText("⚠  " + text if problem else "")
        self.warn.setToolTip(tip)
        self.warn.setVisible(problem)
        set_prop(self.warn, "level", level)
        self.rumble.setToolTip(_("Test wibracji") + " - " + (text if text else _("Krótka wibracja i mignięcie lightbara - "
                                                                                 "sprawdza, czy wibracja dochodzi do pada")))
        if shown:
            self._update_latency(now)
        return low


class MainWindow(QMainWindow):
    update_done = Signal(object)          # rx_update.Result z wątku aktualizacji
    test_escaped = Signal()               # PS + △ z pada zakończyło widok Test (wątek sieci, rx_testmode)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("DualSense bridge")
        self.icons = {False: make_icon(ICON_OK), True: make_icon(ICON_LOW)}
        self.setWindowIcon(self.icons[False])
        if C.cfg["gui_topmost"]:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        slot = _int(settings.value("slot", 0), 0)
        self.ed = Editor(slot if 0 <= slot < C.NUM_PADS else 0)
        view = settings.value("view", "out")
        self.view = view if view in dict(VIEWS) and view != "test" else "out"     # test (z blokadą) nie od startu
        self.edit_view = self.view             # ostatni widok Wynik / Fizycznie - do niego wraca wyjście z testu
        self.exit_hold = None                  # widok Test w trybie TV: od kiedy ○ jest trzymane
        self.part = None                       # zaznaczona część pada (nazwa) albo None
        self.warned = [False] * C.NUM_PADS     # powiadomienie o niskiej baterii wysłane raz na rozładowanie
        self.note_text, self.note_until = None, 0.0
        self.low_any, self.tip, self.online = False, None, None
        self._banner_sig, self._upd_notified, self._sender_stale = None, None, None   # pasek aktualizacji
        self.settings_dlg = None               # okno ustawień trybu konsoli (tworzone przy pierwszym otwarciu)

        self.apply_timer = QTimer(self)
        self.apply_timer.setSingleShot(True)
        self.apply_timer.setInterval(APPLY_MS)
        self.apply_timer.timeout.connect(lambda: self.do_apply(False))
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(LIVE_MS)
        self.live_timer.timeout.connect(self.live_tick)
        self.slow_timer = QTimer(self)
        self.slow_timer.setInterval(SLOW_MS)
        self.slow_timer.timeout.connect(self.slow_tick)

        if self.fullscreen():                  # tryb TV od startu: skala przed budową (wymiary w px())
            apply_theme(QApplication.instance(), TV_SCALE)
        self._build()
        self.nav = PadNav(self)                # sterowanie oknem padem w trybie TV
        self.tray = self._make_tray()
        self.update_done.connect(self._update_finished)
        self.test_escaped.connect(self._test_escaped)
        rx_testmode.on_escape = self.test_escaped.emit
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self.toggle_fullscreen)
        self.select_pad(self.ed.slot)
        self.pad.set_selected(None)
        self.root.setFocus()
        geo = settings.value("geometry")
        if not (geo and self.restoreGeometry(geo)):
            self.resize(1160, 720)
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())
        self.place_on_screen()
        split = settings.value("split")
        if split:
            self.split.restoreState(split)
        else:
            self.split.setSizes([px(560), px(190)])
        self.log_btn.setChecked(settings.value("log_open", "false") in (True, "true"))
        self.log_panel.setVisible(self.log_btn.isChecked())
        self.slow_tick()
        self.slow_timer.start()                # działa także przy ukrytym oknie (zasobnik, bateria, Ctrl+C)

    # ---------- budowa ----------
    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        root.setFocusPolicy(Qt.FocusPolicy.ClickFocus)   # fokus startowy na tle: bez ramki fokusu na pierwszym przycisku
        self.setCentralWidget(root)
        self.root = root
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        outer.addWidget(self._banner())
        main = QWidget()
        body = QHBoxLayout(main)
        body.setContentsMargins(px(16), px(16), px(16), px(16))
        body.setSpacing(px(16))
        body.addWidget(self._left())
        body.addLayout(self._center(), 1)
        body.addWidget(self._right())
        self.log_panel = LogPanel()             # dziennik zdarzeń: wysuwany z dołu (ikona w nagłówku), wysokość do ustawienia
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(px(4))
        self.split.addWidget(main)
        self.split.addWidget(self.log_panel)
        self.split.setStretchFactor(0, 1)
        self.log_panel.setMinimumHeight(px(120))
        outer.addWidget(self.split, 1)
        self.bt_drawer = BtDrawer(root)          # wysuwany nad oknem z prawej (ikona Bluetooth w nagłówku)
        self.keys_drawer = KeysDrawer(root)      # lista skrótów pada (ikona pada w nagłówku)
        self.drawers = {self.bt_drawer: self.bt_btn, self.keys_drawer: self.keys_btn}
        for d in self.drawers:
            d.opened.connect(lambda on, d=d: self._drawer_opened(d, on))
        # minimalny rozmiar wynika z układu (kolumny + najmniejszy rysunek pada) - jawne minimum mniejsze niż on
        # pozwalało ścisnąć okno tak, że prawy panel zachodził na rysunek

    def _header(self):
        """Nazwa i wersja po lewej; po prawej stan nadajnika i przyciski z samymi ikonami (podpowiedzi z opisem)."""
        bar = QFrame()
        bar.setObjectName("header")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(px(20), px(8), px(12), px(8))
        lay.setSpacing(px(6))
        title = label("DualSense bridge")
        title.setObjectName("title")
        commit, ts, subject = rx_update.version()
        self.version_lbl = label(_("wersja {v}").format(v=rx_update.describe(commit, ts)), "small")
        self.version_lbl.setToolTip(_("Ostatnia zmiana: {s}").format(s=subject) if subject else
                                    _("Wersja nieznana - folder nie jest klonem git"))
        self.sender = label("")
        self.sender.setObjectName("pill")
        self.bt_btn, self._bt_refresh = indicator_button(self.toggle_bt)   # Bluetooth nadajnika: kropka stanu + panel
        self.keys_btn = icon_button("keys", _("Skróty pada") + " - " + _("Wszystkie skróty pada w tym receiverze"))
        self.keys_btn.clicked.connect(self.toggle_keys)
        self.log_btn = icon_button("log", _("Dziennik zdarzeń") + " - " + _("Połączenia padów, master, tryb konsoli, błędy, aktualizacje"))
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self.show_log)
        self.fs_btn = icon_button("fullscreen", _("Pełny ekran (F11)"))
        self.fs_btn.clicked.connect(self.toggle_fullscreen)
        settings_btn = icon_button("gear", _("Ustawienia") + " - " + _("Tryb konsoli, mysz touchpada, pad master, okno, język"))
        settings_btn.clicked.connect(self.open_settings)
        self.update_btn = ConfirmButton(_("Aktualizuj"), _("Aktualizować?"), iconic="update",
                                        tip=_("Aktualizuj") + " - " + _("Pobiera nową wersję z GitHuba (git pull) dla "
                                                                        "receivera i nadajnika; oba uruchamiają się od nowa"))
        self.update_btn.confirmed.connect(self.start_update)
        restart_btn = ConfirmButton("Restart", "Restart?", iconic="restart",
                                    tip="Restart - " + _("Uruchamia receiver od nowa (pady na chwilę znikną, niezapisane "
                                                         "zmiany profilu przepadną)"))
        restart_btn.confirmed.connect(self.restart)
        quit_btn = ConfirmButton(_("Zakończ"), _("Zakończyć?"), iconic="power",
                                 tip=_("Zakończ") + " - " + _("Zatrzymuje receiver - wirtualne pady znikają"))
        quit_btn.confirmed.connect(self.quit)
        lay.addWidget(title)
        lay.addSpacing(px(6))
        lay.addWidget(self.version_lbl, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addStretch(1)
        lay.addWidget(self.sender)
        lay.addSpacing(px(10))
        for b in (self.bt_btn, self.keys_btn, self.log_btn, self.fs_btn, settings_btn, self.update_btn, restart_btn,
                  quit_btn):
            lay.addWidget(b)
        self._iconic = [self.bt_btn, self.keys_btn, self.log_btn, self.fs_btn, settings_btn, self.update_btn, restart_btn, quit_btn]
        return bar

    def _banner(self):
        """Pasek pod nagłówkiem, widoczny tylko, gdy jest coś do zrobienia: nowa wersja na GitHubie i / albo nadajnik
        w innej wersji niż receiver. Napis z opisem (nie sam kolor) i przycisk Aktualizuj."""
        bar = QFrame()
        bar.setObjectName("banner")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(px(20), px(7), px(16), px(7))
        lay.setSpacing(px(10))
        self.banner_text = label("", wrap=True)
        set_prop(self.banner_text, "level", "warn")
        self.banner_btn = ConfirmButton(_("Aktualizuj"), _("Aktualizować?"), small=True)
        self.banner_btn.setIcon(icon("update"))
        self.banner_btn.setProperty("primary", True)
        self.banner_btn.confirmed.connect(self.start_update)
        lay.addWidget(self.banner_text, 1)
        lay.addWidget(self.banner_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        bar.hide()
        self.banner = bar
        return bar

    def _refresh_banner(self):
        av, stale = rx_update.available, self._sender_stale
        lines = []
        if av is not None and av.behind:
            lines.append("⬆  " + _("Dostępna aktualizacja: {n}").format(n=n_changes(av.behind))
                         + (f"  -  {av.subject}" if av.subject else ""))
        if stale:
            lines.append("⚠  " + _("Nadajnik ma inną wersję ({s}) niż receiver ({r}) - Aktualizuj pobierze tę samą na obu")
                         .format(s=stale[0], r=stale[1]))
        sig = (tuple(lines), self.update_btn.isEnabled())
        if sig == self._banner_sig:
            return
        self._banner_sig = sig
        self.banner_text.setText("\n".join(lines))
        self.banner_btn.setEnabled(self.update_btn.isEnabled())   # w trakcie aktualizacji zablokowany jak w nagłówku
        self.banner.setVisible(bool(lines))
        if self.tray and av is not None and av.behind and av.commit != self._upd_notified:
            self._upd_notified = av.commit       # jedno powiadomienie na nową wersję, nie co 30 minut
            self.tray.showMessage(_("Dostępna aktualizacja: {n}").format(n=n_changes(av.behind)),
                                  av.subject or _("Kliknij Aktualizuj w oknie"), QSystemTrayIcon.MessageIcon.Information, 8000)

    def _left(self):
        w = QWidget()
        w.setFixedWidth(px(248))
        self.left_col = w
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(12))
        self.cards = [PadCard(i) for i in range(C.NUM_PADS)]
        for c in self.cards:
            c.clicked.connect(self.select_pad)
            c.master_changed.connect(self.slow_tick)
            lay.addWidget(c)
        lay.addStretch(1)
        return w

    def _center(self):
        lay = QVBoxLayout()
        lay.setSpacing(px(10))
        self.pad = PadView()
        self.pad.part_clicked.connect(self.on_part)
        lay.addWidget(self.pad, 1)
        row = QHBoxLayout()
        self.pad_hint = label(_("Kliknij część pada, aby ją zremapować."), "dim", wrap=True)
        row.addWidget(self.pad_hint, 1)
        row.addSpacing(px(12))
        self.seg = Segmented([(k, _(t)) for k, t in VIEWS])
        self.seg.set(self.view)
        self.seg.setToolTip(_("Wynik = to, co dostaje gra po remapowaniu. Fizycznie = to, co robi sam pad. "
                              "Test = przyciski, spusty, gałki, touchpad i ruch na żywo, dryf gałek, kalibracja żyroskopu."))
        self.seg.changed.connect(self.set_view)
        row.addWidget(self.seg)
        lay.addLayout(row)
        return lay

    def _right(self):
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(px(340))
        self.right_col = panel
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(px(14), px(14), px(14), px(14))
        self.right_stack = QStackedWidget()      # edycja profilu albo panel testu (widok Test)
        outer.addWidget(self.right_stack)
        edit = QWidget()
        lay = QVBoxLayout(edit)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(10))
        self.right_stack.addWidget(edit)
        self.test_panel = TestPanel(str(self.settings.value("test_block", "true")).lower() == "true")
        self.test_panel.block_changed.connect(self.on_block_toggled)
        self.right_stack.addWidget(self.test_panel)
        self.profile_bar = ProfileBar()
        self.profile_bar.selected.connect(self.on_profile)
        self.profile_bar.create.connect(self.on_create)
        self.profile_bar.delete.connect(self.on_delete)
        lay.addWidget(self.profile_bar)
        self.part_page, self.gyro_page = PartPage(self.ed), GyroPage(self.ed)
        self.changes_page = ChangesPage(self.ed)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setDrawBase(False)
        for page, name in ((self.part_page, _("Wybrane")), (self.gyro_page, _("Gyro")), (self.changes_page, _("Zmiany"))):
            self.tabs.addTab(page, name)
        for page in (self.part_page, self.gyro_page):
            page.edited.connect(self.on_edit)
        self.changes_page.undone.connect(self.on_undone)
        self.changes_page.reset.confirmed.connect(self.reset)       # "Usuń wszystkie" - na liście zmian, nie w stopce
        lay.addWidget(self.tabs, 1)
        self.status = label("", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.setSpacing(px(8))
        self.save_btn = button(_("Zapisz"), primary=True)
        self.save_btn.setToolTip(_("Zapisz profil do remap.json (Ctrl+S)"))
        self.save_btn.clicked.connect(self.save)
        self.revert_btn = button(_("Cofnij"))
        self.revert_btn.setToolTip(_("Wróć do zapisanej wersji tego profilu"))
        self.revert_btn.clicked.connect(self.revert)
        row.addWidget(self.save_btn, 1)
        row.addWidget(self.revert_btn)
        lay.addLayout(row)
        return panel

    def _make_tray(self):
        if not C.cfg["tray"]:
            return None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("Tray unavailable - continuing without tray icon.")
            return None
        tray = QSystemTrayIcon(self.icons[False], self)
        menu = QMenu()
        menu.addAction(_("Pokaż okno"), self.bring_to_front)
        menu.addAction(_("Ustawienia"), self.open_settings)
        menu.addAction(icon("update"), _("Aktualizuj"), self.start_update)
        menu.addAction(icon("restart"), "Restart", self.restart)
        menu.addAction(_("Zakończ"), self.quit)
        tray.setContextMenu(menu)
        self._tray_menu = menu                 # Qt nie przejmuje własności menu - trzeba je trzymać
        tray.setToolTip("DualSense bridge")
        tray.activated.connect(self._tray_clicked)
        tray.show()
        return tray

    # ---------- okno ----------
    # ---------- ekran okna ----------
    def screen_choice(self):
        """Nazwa ekranu wybranego w Ustawieniach (QScreen.name()) albo "" = tam, gdzie okno było ostatnio."""
        return str(self.settings.value("screen", "") or "")

    def set_screen_choice(self, name):
        self.settings.setValue("screen", name)
        self.settings.sync()
        self.place_on_screen()

    def fullscreen(self):
        return str(self.settings.value("fullscreen", "false")).lower() == "true"

    def set_fullscreen(self, on):
        """Pełny ekran / okno (zapamiętane). Pełny ekran = tryb TV: większy interfejs i sterowanie padem; zajmuje
        wybrany ekran (Ustawienia -> Okno) albo bieżący."""
        self.settings.setValue("fullscreen", bool(on))
        self.settings.sync()
        self.fs_btn.setToolTip(_("Wyjdź z pełnego ekranu (F11)") if on else _("Pełny ekran (F11)"))
        self.apply_scale(TV_SCALE if on else 1.0)
        if self.isVisible():
            self.bring_to_front()

    def apply_scale(self, k):
        """Skala interfejsu (tryb TV): QSS całej aplikacji + stałe szerokości kolumn i rozmiary ikon."""
        from rx_qt_style import scale
        if k == scale:
            return
        apply_theme(QApplication.instance(), k)
        self.left_col.setFixedWidth(px(248))
        self.right_col.setFixedWidth(px(340))
        for b in self._iconic:
            b.setIconSize(QSize(px(20), px(20)))
        for c in self.cards:
            c.bar.setFixedHeight(px(8))
            c.lat_bar.setFixedHeight(px(5))
            c.crown.setIconSize(QSize(px(18), px(18)))
            c._key = None
        self.test_panel.rescale()
        for d in self.drawers:
            d.rescale()
        self._apply_view()
        self.slow_tick()

    def toggle_fullscreen(self):
        self.set_fullscreen(not self.fullscreen())

    def place_on_screen(self):
        """Przenosi okno na wybrany ekran (na środek, zmniejszone, jeśli się nie mieści). Wybranego ekranu nie ma
        (np. monitor wyłączony w trybie konsoli) -> okno zostaje tam, gdzie jest; Qt pilnuje, żeby było widoczne."""
        name = self.screen_choice()
        target = dict(screens()).get(name) if name else None
        if target is None or (self.screen() is target and target.availableGeometry().contains(self.frameGeometry())):
            return False
        area = target.availableGeometry()
        if self.isMaximized() or self.isFullScreen():
            self.showNormal()
        self.resize(min(self.width(), area.width() - 40), min(self.height(), area.height() - 60))
        self.move(area.center() - self.rect().center())
        return True

    def bring_to_front(self):
        if self.isFullScreen() and not self.fullscreen():
            self.showNormal()
        self.place_on_screen()
        if self.fullscreen():
            self.showFullScreen()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and self.isActiveWindow():
                self.hide()
            else:
                self.bring_to_front()

    def closeEvent(self, e):
        """Krzyżyk chowa okno do zasobnika (receiver działa dalej); bez zasobnika - zwykła minimalizacja."""
        e.ignore()
        self.save_settings()
        if self.tray:
            self.hide()
        else:
            self.showMinimized()

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange and self.tray and self.isMinimized():
            QTimer.singleShot(0, self.hide)    # minimalizacja = do zasobnika, bez przycisku na pasku zadań
        if e.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            self.sync_block()                  # blokada testu tylko przy aktywnym oknie (klik w grę = pad wraca)
        super().changeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.sync_block()
        self.live_tick()
        self.live_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.live_timer.stop()                 # ukryte okno nie odrysowuje pada
        self.test_panel.cancel()               # pomiar dryfu nie trwa w tle
        self.sync_block()                      # schowane okno (zasobnik) - gra znowu dostaje pada

    def toggle_bt(self):
        self.bt_drawer.toggle()

    def toggle_keys(self):
        self.keys_drawer.toggle()

    def _drawer_opened(self, drawer, on):
        """Naraz otwarty jeden panel; zamknięty: fokus wraca na jego ikonę (tryb TV - pad dalej wie, gdzie jest)."""
        if on:
            for other in self.drawers:
                if other is not drawer:
                    other.close_drawer()
        elif self.fullscreen():
            self.drawers[drawer].setFocus(Qt.FocusReason.TabFocusReason)

    def show_log(self, on):
        """Panel dziennika na dole okna (ikona w nagłówku)."""
        self.log_panel.setVisible(on)
        self.log_btn.setToolTip(_("Schowaj dziennik zdarzeń") if on else
                                _("Dziennik zdarzeń") + " - " + _("Połączenia padów, master, tryb konsoli, błędy, aktualizacje"))

    def save_settings(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split", self.split.saveState())
        self.settings.setValue("log_open", self.log_btn.isChecked())
        self.settings.setValue("slot", self.ed.slot)
        self.settings.setValue("view", self.edit_view)
        self.settings.setValue("test_block", self.test_panel.block.isChecked())
        self.settings.sync()

    def open_settings(self):
        if self.settings_dlg is None:
            self.settings_dlg = ConsoleSettings(self)
            self.settings_dlg.saved.connect(self.restart)
            self.settings_dlg.applied.connect(self.slow_tick)
        self.settings_dlg.present()

    def _sender_label(self, online, build):
        """'● Nadajnik online (ip) · a1b2c3d'; inna wersja niż receiver -> żółty i podpowiedź, żeby zaktualizować."""
        if not online:
            self._sender_stale = None
            self.sender.setText(_("●  Nadajnik offline"))
            self.sender.setToolTip("")
            set_prop(self.sender, "level", "bad")
            return
        src = S.peer[0] if S.peer else ""
        mine = rx_update.version()[0]
        text = _("●  Nadajnik online")                 # adres w podpowiedzi - napis mieści się przy 1000 px
        if build and build[0]:
            text += f"  ·  {build[0]}"
        stale = bool(build and build[0] and mine and build[0] != mine)
        self._sender_stale = (build[0], mine) if stale else None   # opis w pasku aktualizacji (nie tylko kolor)
        tip = (_("Nadajnik: wersja {s}, receiver: {r} - kliknij Aktualizuj").format(
                   s=rx_update.describe(*build), r=rx_update.describe(*rx_update.version()[:2])) if stale else
               _("Nadajnik: wersja {s}").format(s=rx_update.describe(*build)) if build and build[0] else
               _("Nadajnik nie podaje wersji (starsza wersja albo nie klon git)"))
        self.sender.setText(text)
        self.sender.setToolTip((_("Adres nadajnika: {ip}").format(ip=src) + "\n" if src else "") + tip)
        set_prop(self.sender, "level", "warn" if stale else "ok")

    def start_update(self):
        """Aktualizacja obu stron: polecenie do nadajnika (git pull na telefonie, sam się restartuje) i git pull
        receivera w osobnym wątku; wynik wraca sygnałem do wątku okna, potem czekamy na wynik nadajnika."""
        if not self.update_btn.isEnabled():
            return
        self.update_btn.setEnabled(False)
        self.update_btn.setText(_("Aktualizuję…"))
        self.note(_("Pobieranie aktualizacji…"), "")
        self._upd_sender = S.sender_online() and S.sender_build is not None   # nowy nadajnik odpowie wynikiem
        self._upd_t0 = time.monotonic()
        self._upd_commit = S.sender_build[0] if S.sender_build else ""   # nowy commit po restarcie = zaktualizowany
        S.sender_update_msg = ""                 # powód z poprzedniej próby nie może opisać tej
        if S.sender_build is not None:
            S.sender_build = (*S.sender_build[:2], UPD_RUNNING, time.monotonic())
        if S.sender_online():
            rx_net.send_cmd(255, CMD_UPDATE)
        threading.Thread(target=self._update_thread, name="update", daemon=True).start()

    def _update_thread(self):
        """Wyjątek (git zawieszony, brak dostępu do folderu) nie może zostawić przycisku na zawsze w "Aktualizuję…"."""
        try:
            r = rx_update.update()
        except Exception as e:
            r = rx_update.Result(False, False, _("Aktualizacja nie powiodła się: {err}").format(err=repr(e)))
        self.update_done.emit(r)

    def _update_finished(self, r):
        """Receiver gotowy - czekamy (najwyżej UPDATE_WAIT_S) na wynik nadajnika, potem komunikat i restart."""
        print(f"Update: {r.text}")
        self._upd_result = r
        self._update_poll()

    def _update_poll(self):
        r = self._upd_result
        waiting = (self._upd_sender and rx_update.sender_state(S.sender_build, self._upd_commit) == UPD_RUNNING
                   and time.monotonic() - self._upd_t0 < UPDATE_WAIT_S)
        if waiting:
            QTimer.singleShot(250, self._update_poll)
            return
        parts = [r.text]
        if S.sender_online() or S.sender_build is not None:
            parts.append(rx_update.sender_text(S.sender_build, S.sender_update_msg, self._upd_commit))
        else:
            parts.append(_("nadajnik niepołączony - zaktualizuje się następnym razem"))
        text = " · ".join(parts)
        print(f"Update: {text}")
        (ev.info if r.ok else ev.error)(_("Aktualizacja: {text}").format(text=text))
        self.update_btn.disarm()                   # sama ikona (napis "Aktualizuję…" był tylko w trakcie)
        self.update_btn.setEnabled(not r.changed)
        self.update_btn.setToolTip(text)
        self.note(text, "ok" if r.ok else "bad")
        if r.ok and not r.changed:
            rx_update.available = rx_update.Available(0)      # sprawdzone przed chwilą: nic nowego
        if r.changed:
            QTimer.singleShot(UPDATE_RESTART_MS, self.restart)

    def restart(self):
        """Restart (przycisk, zasobnik, zapisane ustawienia): zwykłe zamknięcie okna, a receiver sam startuje od nowa
        po zwolnieniu portów i ViGEm."""
        print("Restart requested from the window")
        S.restart = True
        self.quit()

    def quit(self):
        self.flush()
        self.test_panel.cancel()
        rx_testmode.set_active(False)          # skróty padów wracają
        for i in range(C.NUM_PADS):            # zamknięcie z widoku Test: wyjście do gry wraca przed sprzątaniem
            P.block(i, False)
        self.save_settings()
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    # ---------- wybór i zmiany ----------
    def select_pad(self, slot):
        self.flush()
        if slot != self.ed.slot:
            self.ed.set_slot(slot)
            self.profile_bar.close_new()
        for c in self.cards:
            c.set_selected(c.slot == slot)
        self.test_panel.set_slot(slot)
        self.sync_block()
        self.refresh_all()

    def set_view(self, view):
        was_test, self.view = self.view == "test", view
        if view != "test":
            self.edit_view = view
        elif not was_test:
            rx_testmode.rearm()                # wejście w Test po wyjściu z pada (PS + △) - znowu działa
        self.seg.set(view)
        self.exit_hold = None
        self._apply_view()
        if was_test != (view == "test"):
            self.test_panel.activate(view == "test")
        self.sync_block()
        self.live_tick()

    def _apply_view(self):
        """Widok Test: prawy panel testu, rysunek bez plakietek i klikania, podpowiedź pod padem."""
        test = self.view == "test"
        self.right_stack.setCurrentIndex(1 if test else 0)
        self.pad.set_test(test)
        if test:
            hint = _("Naciskaj przyciski, ruszaj gałkami, dotykaj touchpada - wszystko widać na żywo.")
            if self.fullscreen():
                hint += " " + _("Przytrzymaj ○, aby wyjść z testu.")
            hint += " " + _("Skróty padów nie działają; PS + △ trzymane {s} s (pad master) kończy test.").format(
                s=f"{rx_testmode.ESCAPE_S:g}")
        else:
            hint = _("Kliknij część pada, aby ją zremapować.")
            self.pad.set_selected(self.part)
        self.pad_hint.setText(hint)

    def sync_block(self):
        """Blokada wyjścia do gry: tylko testowany pad, tylko w widoku Test, przy włączonej opcji i aktywnym oknie -
        przejście do gry (inne okno na pierwszym planie), minimalizacja albo schowanie od razu oddają jej pada."""
        test = (self.view == "test" and not rx_testmode.escaped and self.isVisible() and not self.isMinimized()
                and self.isActiveWindow())
        rx_testmode.set_active(test)           # skróty wszystkich padów stoją, PS + △ mastera wychodzi
        on = test and self.test_panel.block.isChecked()
        for i in range(C.NUM_PADS):
            P.block(i, on and i == self.ed.slot)

    def _test_escaped(self):
        """PS + △ z pada (rx_testmode): blokada już zdjęta w wątku sieci - okno wraca do poprzedniego widoku."""
        if self.view == "test":
            self.set_view(self.edit_view)
            self.note(_("Wyjście z widoku Test z pada (PS + △)"), "ok")

    def on_block_toggled(self, _on):
        self.save_settings()
        self.sync_block()

    def on_part(self, name):
        if self.view == "test":
            return
        self.part = name
        self.pad.set_selected(name)
        self.part_page.show_part(name)
        self.tabs.setCurrentIndex(0)

    def on_profile(self, key):
        self.flush()
        self.ed.select(key)
        self.refresh_all()

    def on_create(self, text, copy_current):
        self.flush()
        ok, msg = self.ed.new_game(text, copy_current)
        if ok:
            self.profile_bar.close_new()
            self.refresh_all()
        self.note(msg, "warn" if ok else "bad")

    def on_delete(self):
        self.flush()
        ok, msg = self.ed.delete_current()
        self.refresh_all()
        self.note(("✓ " if ok else "") + msg, "ok" if ok else "bad")

    def on_edit(self):
        """Zmiana z panelu: odśwież plakietki i listę zmian; na pad poleci po krótkim opóźnieniu."""
        self.pad.set_profile(self.ed.prof)
        self.update_changes()
        self.update_status()
        self.apply_timer.start()

    def on_undone(self):
        self.refresh_all()
        self.apply_timer.start()

    def flush(self):
        """Zmiana czekająca na wysłanie musi trafić do starego profilu przed przełączeniem."""
        if self.apply_timer.isActive():
            self.do_apply(False)

    def do_apply(self, save):
        self.apply_timer.stop()
        ok, msg = self.ed.apply(save)
        if not ok:
            self.note(msg, "bad")
            return False
        if save:
            self.refresh_all()
            self.note("✓ " + msg, "ok")
        else:
            self.update_profile_bar()
            self.update_status()
        return True

    def save(self):
        self.do_apply(True)

    def revert(self):
        self.ed.revert()
        self.refresh_all()
        self.do_apply(False)

    def reset(self):
        self.ed.reset_all()
        self.refresh_all()
        self.do_apply(False)

    # ---------- odświeżanie widoku ----------
    def refresh_all(self):
        self.update_profile_bar()
        self.part_page.refresh()
        self.gyro_page.refresh()
        self.pad.set_profile(self.ed.prof)
        self.update_changes()
        self.update_status()

    def update_profile_bar(self):
        self.profile_bar.reload(self.ed.profiles(), self.ed.key, self.ed.info(), list(rx_games.recent))

    def update_changes(self):
        n = self.changes_page.refresh()
        self.tabs.setTabText(2, _("Zmiany ({n})").format(n=n) if n else _("Zmiany"))

    def note(self, text, level):
        """Komunikat zasłaniający status profilu na kilka sekund (zapisano, błąd, utworzono profil)."""
        self.note_text, self.note_until = (text, level), time.monotonic() + NOTE_S
        self.update_status()

    def update_status(self):
        if self.note_text and time.monotonic() < self.note_until:
            level, text = self.note_text[1], self.note_text[0]
        else:
            level, text = self.ed.status()
        self.status.setText(text)
        set_prop(self.status, "level", level)
        modified = self.ed.modified()
        set_prop(self.save_btn, "primary", modified or level == "warn")
        self.revert_btn.setEnabled(modified)

    def live_tick(self):
        """Rysunek pada na żywo (30 Hz, tylko widoczne okno); w trybie TV także sterowanie oknem padem."""
        now = time.monotonic()
        self.nav.tick(now)
        live = P.live(self.ed.slot)
        s = "_out" if self.view == "out" else ""
        self.pad.set_live(live["pressed" + s], live["ls" + s], live["rs" + s], live["l2" + s], live["r2" + s])
        if self.view == "test":
            t = self.test_panel.tick(now, live)
            self.pad.set_touch(t["touch"], t["tp"])
            self._exit_hold(now, "circle" in live["pressed"])
        else:
            self.gyro_page.set_live(self.ed.key == live["active"], live["gyro_on"])

    def _exit_hold(self, now, held):
        """Tryb TV: przytrzymane ○ testowanego pada (który nie steruje wtedy oknem) wychodzi z widoku Test."""
        if not (held and self.fullscreen()):
            self.exit_hold = None
        elif self.exit_hold is None:
            self.exit_hold = now
        elif now - self.exit_hold >= EXIT_HOLD_S:
            self.set_view(self.edit_view)

    def slow_tick(self):
        """Co 400 ms; błąd w jednym przebiegu nie może zatrzymać odświeżania na zawsze."""
        try:
            self._slow()
        except Exception as e:
            print(f"GUI refresh error: {e!r}")

    def _slow(self):
        now = time.monotonic()
        online = S.sender_online(now)
        build = S.sender_build[:2] if S.sender_build else None
        if (online, build) != self.online:
            self.online = (online, build)
            self._sender_label(online, build)
        self._refresh_banner()
        self._bt_refresh(now)
        low_any, tips = False, []
        for c in self.cards:
            i = c.slot
            low_any = c.update_state(now) or low_any
            pct, flags = S.batt_info[i]
            charging, known = bool(flags & 1), pct <= 100
            if S.want[i].is_set() or S.alive[i]:
                tips.append(_("Pad {n}").format(n=i + 1) + ": "
                            + (f"{pct}%" + (_(" (ładuje się)") if charging else "") if known else _("b/d")))
            if ST.is_low(i, ST.low_pct()) and not self.warned[i]:
                self.warned[i] = True
                ev.warn(_("{pad}: niska bateria ({pct}%)").format(pad=ev.pad_name(i), pct=pct))
                if self.tray and C.cfg["low_batt_notify"]:
                    self.tray.showMessage(_("Niska bateria"), _("Pad {n}").format(n=i + 1) + f": {pct}%",
                                          QSystemTrayIcon.MessageIcon.Warning, 8000)
            elif self.warned[i] and (charging or (known and pct > ST.low_pct() + 5)):
                self.warned[i] = False
        title = _("DualSense bridge - NISKA BATERIA") if low_any else "DualSense bridge"
        if title != self.windowTitle():
            self.setWindowTitle(title)
        if self.ed.sync():                     # np. nadeszło id pada - profil pada zmienił klucz
            self.refresh_all()
        else:
            self.update_profile_bar()
            self.update_status()
            self.part_page.update_caps()       # nadajnik doszedł / zaktualizował się - ostrzeżenie o oporze spustu
        tip = (f"DualSense bridge\n{_('Nadajnik')}: " + ("online" if online else "offline") + "\n"
               + ("\n".join(tips) if tips else _("Brak podłączonych padów")))
        if self.tray and (tip != self.tip or low_any != self.low_any):
            self.tray.setToolTip(tip)
            self.tray.setIcon(self.icons[low_any])
        self.tip, self.low_any = tip, low_any
        self.log_panel.tick()


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


WM_QUERYENDSESSION = 0x0011


def session_end(*_args):
    """Windows się wyłącza / restartuje / wylogowuje (Start, Big Picture, shutdown.exe): włączony tryb konsoli -> OFF,
    zanim proces zostanie zamknięty, żeby po starcie obraz nie był tylko na TV."""
    if rx_console.power_off(_("wyłączanie Windows")):
        print("Windows session ending - console mode switched off")


class SessionEndFilter(QAbstractNativeEventFilter):
    """WM_QUERYENDSESSION prosto z pętli komunikatów - działa też, gdy Qt nie zgłosi commitDataRequest."""
    def nativeEventFilter(self, event_type, message):
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            import ctypes.wintypes
            if ctypes.wintypes.MSG.from_address(int(message)).message == WM_QUERYENDSESSION:
                session_end()
        return False, 0


_session_filter = None      # referencja - filtr nie może zostać zebrany przez GC


def _watch_session_end(app):
    global _session_filter
    if _session_filter is not None:
        return
    _session_filter = SessionEndFilter()
    app.commitDataRequest.connect(session_end)
    if sys.platform == "win32":
        app.installNativeEventFilter(_session_filter)


def create_window(settings_path=None):
    """QApplication (jeśli jeszcze nie ma) + okno. Osobno od run_gui, żeby testy mogły sterować oknem bez pętli."""
    W.set_app_id()                             # ikona pada na pasku zadań zamiast ikony Pythona
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("DualSense bridge")
    app.setWindowIcon(make_icon(ICON_OK))      # także okno ustawień i powiadomienia
    app.setQuitOnLastWindowClosed(False)       # zamknięcie okna = do zasobnika
    _watch_session_end(app)                    # wyłączanie Windows z trybem konsoli -> najpierw oba ekrany
    apply_theme(app)
    path = settings_path or os.path.join(os.path.dirname(C.REMAP_PATH), "receiver_ui.ini")
    return MainWindow(QSettings(path, QSettings.Format.IniFormat))


def run_gui():
    if C.cfg["gui_wait_pad"]:      # opcja: nie twórz okna ani ikony, dopóki żaden pad się nie połączy
        while not S.stop.is_set() and not any(w.is_set() for w in S.want):
            S.stop.wait(0.25)
        if S.stop.is_set():
            return
    win = create_window()
    rx_update.start_checker(S.stop)          # w tle: czy na GitHubie jest nowsza wersja (napis w nagłówku)
    app = QApplication.instance()
    try:
        signal.signal(signal.SIGINT, lambda *_: app.quit())   # Ctrl+C w konsoli = zwykłe zamknięcie (czyszczenie padów)
    except ValueError:
        pass                                                   # nie główny wątek
    if win.tray is None or C.cfg["gui_start_visible"]:        # bez ikony okno jest jedynym sposobem obsługi
        win.bring_to_front()
    try:
        app.exec()
    finally:
        if win.tray:
            win.tray.hide()
