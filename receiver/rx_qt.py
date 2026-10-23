"""Receiver: one window for everything (PySide6): pads with the battery and latency on the left, the pad drawing in the centre
(a click = remapping), profiles / gyro / changes on the right, an icon in the tray. Full screen = the TV mode: a bigger interface
(readable from a couch) and controlling the window with the pad (rx_qt_tv).

Speed: the pad drawing refreshes 30 times a second only while the window is visible and only when the pad state
changed; the rest (cards, tray) every 400 ms, with a hidden window too (the tray tooltip, a low battery).
Profile changes reach the pad live 150 ms after the last slider move, saving to remap.json is separate (Save).
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
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMenu, QSplitter, QStackedWidget,
                               QSystemTrayIcon, QTabWidget, QToolButton, QVBoxLayout, QWidget)

import rx_console
import rx_ds4
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
from rx_qt_gstick import TiltPreview
from rx_qt_pad import PadView
from rx_qt_panels import ChangesPage, GyroPage, PartPage, ProfileBar
from rx_qt_screens import ScreenPlacement
from rx_qt_style import DIM, LEVEL, SEGMENT, TV_SCALE, apply_theme, px, set_prop
from rx_qt_test import TestPanel
from rx_qt_tv import PadNav
from rx_qt_widgets import BatteryBar, ConfirmButton, Segmented, SegmentBar, button, icon_button, icon_label, label

LIVE_MS, SLOW_MS, APPLY_MS = 33, 400, 150
UPDATE_WAIT_S = 60.0       # how long at most we wait for the sender update result (git pull on the phone)
UPDATE_RESTART_MS = 2500   # after a successful update: the message is visible this long, then the receiver restarts
UPDATE_STUCK_S = 150.0     # git longer than this (hung despite the limits) -> the button returns, the result will work anyway
NOTE_S = 6.0           # for this many seconds a message (e.g. "Saved") covers the profile status
VIEWS = (("out", "Wynik"), ("in", "Fizycznie"), ("test", "Test"))
EXIT_HOLD_S = 1.0      # the Test view in TV mode: how long ○ of the pad under test must be held to leave the test
DEFAULT_SIZE = QSize(1160, 720)


@cache
def make_icon(rgb):
    """The pad icon at several sizes - sharp in the tray, on the taskbar and with 150-200 % scaling."""
    ic = QIcon()
    for size in (16, 24, 32, 48, 64):
        pm = QPixmap()
        pm.loadFromData(icon_png(render_icon(rgb, size)))
        ic.addPixmap(pm)
    return ic


def n_changes(n):
    """"1 zmiana", "3 zmiany", "5 zmian", "22 zmiany" (the Polish inflection) / "1 change", "N changes"."""
    if n == 1:
        return _("{n} zmiana").format(n=n)
    few = n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
    return (_("{n} zmiany") if few else _("{n} zmian")).format(n=n)


def _profile_label(key):
    """The profile name for display: \"default\" is translated, a game profile keeps its program name."""
    label = S.profile_label(key)
    return _("domyślny") if label == "default" else label


STATS_HELP = "zwykle = 95 na 100 pomiarów było tak szybkich albo szybszych, max = najwolniejszy pomiar"


class PadCard(QFrame):
    """The pad card on the left - only what matters at first glance: the battery, the pad -> game latency (with a bar of
    segments), the profile and the touchpad mode; details (the latency breakdown, ping, loss, Hz) in a tooltip. A problem
    (no signal, rumble unavailable) shows as one warning line. A disconnected pad = the name only.
    A click = edit this pad; a click on the crown = this pad becomes the master (a second click - no master).
    """

    clicked = Signal(int)
    master_changed = Signal()

    def __init__(self, slot) -> None:
        """Build the card: the header (state dot, crown, rumble test, power off), the status line and the body with the battery, latency,
        button latency and the profile line.
        """
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
        self.status = label("", "dim")               # only when the pad does not work: "not connected" / "no signal"
        lay.addWidget(self.status)

        self.body = QWidget()                         # everything below - for a connected pad only
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
        self.lat = label("", "big")                  # the average pad -> game latency (or the ping when there is no measurement)
        self.lat_stats = label("", "small")          # usually ≤ p95 · max
        self.mode_icon = label("")
        lat.addWidget(self.lat_icon)
        lat.addWidget(self.lat)
        lat.addWidget(self.lat_stats, 0, Qt.AlignmentFlag.AlignBottom)
        lat.addStretch(1)
        lat.addWidget(self.mode_icon)
        bl.addLayout(lat)
        self.lat_bar = SegmentBar(px(5))
        bl.addWidget(self.lat_bar)
        self.btn_row = QWidget()                      # button -> game: presses only (no sticks and gyro)
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
        self.warn = label("", wrap=True)              # one problem line (rumble unavailable)
        lay.addWidget(self.warn)

    def mousePressEvent(self, e) -> None:
        """A left click selects this pad for editing."""
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.slot)

    def test_rumble(self) -> None:
        """Send a short rumble and lightbar blink to this pad, even with confirmations off."""
        print(f"Controller {self.slot}: rumble test from the window (feedback -> sender)")
        F.pulse(self.slot, "test", force=True)

    def toggle_master(self) -> None:
        """Make this pad the master, or clear the master when it already is."""
        ok, msg = rx_master.set_master(None if rx_master.is_master(self.slot) else self.slot)
        if not ok:
            print(f"Master: {msg}")
        self._key = None
        self.master_changed.emit()

    def set_selected(self, on) -> None:
        """Mark the card as the selected pad."""
        set_prop(self, "selected", on)

    def reset_buttons(self) -> None:
        """Clear the button -> game latency measurement of this pad."""
        rx_latency.reset_buttons(self.slot)
        self._lat = None
        self._update_latency(time.monotonic())

    def _update_latency(self, now) -> None:
        """Latency: changes on every refresh - separately from the rest of the card (only when something changed)."""
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
        """HTML rows of the latency breakdown, each with its bar colour."""
        return [f"<span style='color:{SEGMENT[k]}'>■</span>&nbsp; {name}: <b>{v:.2f} ms</b>"
                for k, name, v in ST.latency_parts(summ)]

    @staticmethod
    def _latency_tip(summ, link):
        """A tooltip (HTML): the latency breakdown with the colours of the bar, the link, a note about the measurement."""
        dim = f"<span style='color:{DIM}'>{{}}</span>"
        if summ is None:
            rows = [f"<b>{_('Opóźnienie pad → gra')}</b>",
                    dim.format(_("Brak pomiaru: pad leży albo nadajnik jest w starszej wersji (Aktualizuj)."))]
        else:
            rows = [f"<b>{_('Opóźnienie pad → gra')}: {summ.avg:.1f} ms</b> &nbsp;"
                    + dim.format(_("średnia · {stats} ms · {n} próbek z 5 s").format(stats=ST.stats_text(summ), n=summ.n)),
                    dim.format(_(STATS_HELP)),
                    *PadCard._rows(summ),
                    dim.format(_("Średnie odcinków. Stała część radia Bluetooth (~1–2 ms) jest niemierzalna."))]
        if link:
            rows.append(dim.format(link))
        return "<br>".join(rows)

    @staticmethod
    def _button_tip(summ):
        """A tooltip (HTML) for the button -> game latency: the statistics, the breakdown and what is measured."""
        dim = f"<span style='color:{DIM}'>{{}}</span>"
        return "<br>".join([
            f"<b>{_('Przycisk → gra')}: {summ.avg:.1f} ms</b> &nbsp;"
            + dim.format(_("średnia · {stats} ms · {n} z ostatnich {keep} naciśnięć").format(
                stats=ST.stats_text(summ), n=summ.n, keep=rx_latency.BUTTON_KEEP)),
            dim.format(_(STATS_HELP)),
            *PadCard._rows(summ),
            dim.format(_("Tylko przyciski, D-pad i spusty (wciśnięcie / puszczenie) - bez gałek i żyroskopu."))])

    def update_state(self, now):
        """Refresh the card (only what changed). -> True when the pad's battery is running low."""
        i = self.slot
        alive, want = bool(S.alive[i]), S.want[i].is_set()
        shown = alive and self.isVisible()     # the window in the tray: no latency median computing
        if shown:
            self._update_latency(now)
        pct, flags = S.batt_info[i]
        low = ST.is_low(i, ST.low_pct())
        master = rx_master.is_master(i)
        kind = rx_ds4.kinds[i] if i < len(rx_ds4.kinds) else None
        key = (alive, want, pct, flags, S.plugged[i], kind, S.mouse_mode[i], S.profile_changes(i), S.active[i],
               master, S.pad_id[i], S.sender_info[i], ST.low_pct())
        if key == self._key:
            return low                        # nothing changed - do not touch the widgets
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
        if pct > 100:                         # the battery level is unknown
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
                                  + _("Wirtualny pad: {pad}  ·  touchpad: {tp}").format(
                                      pad=rx_ds4.LABELS[kind] if S.plugged[i] and kind else _("wył."),
                                      tp=_("mysz") if S.mouse_mode[i] else _("dotyk w grze")))
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


class MainWindow(ScreenPlacement):
    """The main window: the pad cards, the pad drawing, the profile editor, the Test view, the drawers (Bluetooth, shortcuts), the log and
    the tray icon.
    """
    update_done = Signal(object)          # rx_update.Result from the update thread
    test_escaped = Signal()               # Options + Create on the pad ended the Test view (the network thread, rx_testmode)

    def __init__(self, settings) -> None:
        """Restore the saved state (pad, view, geometry), create the timers, build the UI and the tray, then fit the window to its screen
        and start the slow refresh.
        """
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
        self.view = view if view in dict(VIEWS) and view != "test" else "out"     # the test (with the lock) not from startup
        self.edit_view = self.view             # the last Result / Physical view - leaving the test returns to it
        self.exit_hold = None                  # the Test view in TV mode: since when ○ has been held
        self.part = None                       # the selected pad part (name) or None
        self.warned = [False] * C.NUM_PADS     # a low battery notification sent once per discharge
        self.note_text, self.note_until = None, 0.0
        self.low_any, self.tip, self.online = False, None, None
        self._banner_sig, self._upd_notified, self._sender_stale = None, None, None   # the update banner
        self.settings_dlg = None               # the console mode settings window (created on first opening)

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

        if self.fullscreen():                  # TV mode from startup: the scale before building (sizes in px())
            apply_theme(QApplication.instance(), TV_SCALE)
        self._build()
        self.nav = PadNav(self)                # controlling the window with the pad in TV mode
        self.tray = self._make_tray()
        self.update_done.connect(self._update_finished)
        self.test_escaped.connect(self._test_escaped)
        rx_testmode.on_escape = self.test_escaped.emit
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.save)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self).activated.connect(self.toggle_fullscreen)
        self.select_pad(self.ed.slot)
        self.pad.set_selected(None)
        self.root.setFocus()
        self._fitting = False                  # place_on_screen resizes the window - this is not the user's choice
        self._connected = (False,) * C.NUM_PADS   # pads connected at the previous refresh (the window comes to the front)
        self._place_pending = False            # the screens changed during a game - the window goes to the centre after leaving it
        geo = settings.value("geometry")
        if not (geo and self.restoreGeometry(geo)):
            self.resize(DEFAULT_SIZE)
        normal = self.normalGeometry().size()
        self._pref = normal if normal.isValid() and not normal.isEmpty() else DEFAULT_SIZE   # the size chosen by the user
        self.place_on_screen()
        self._watch_screens()
        split = settings.value("split")
        if split:
            self.split.restoreState(split)
        else:
            self.split.setSizes([px(560), px(190)])
        self.log_btn.setChecked(settings.value("log_open", "false") in (True, "true"))
        self.log_panel.setVisible(self.log_btn.isChecked())
        self.slow_tick()
        self.slow_timer.start()                # works with a hidden window too (the tray, battery, Ctrl+C)

    # ---------- building ----------
    def _build(self) -> None:
        """Build the layout: the header, the update banner, the three columns (pads, drawing, editor), the log under a splitter and the
        drawers over the window.
        """
        root = QWidget()
        root.setObjectName("root")
        root.setFocusPolicy(Qt.FocusPolicy.ClickFocus)   # the startup focus on the background: no focus frame on the first button
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
        self.log_panel = LogPanel()             # the event log: slides out from the bottom (the header icon), the height can be set
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(px(4))
        self.split.addWidget(main)
        self.split.addWidget(self.log_panel)
        self.split.setStretchFactor(0, 1)
        self.log_panel.setMinimumHeight(px(120))
        outer.addWidget(self.split, 1)
        self.bt_drawer = BtDrawer(root)          # slides in over the window from the right (the Bluetooth icon in the header)
        self.keys_drawer = KeysDrawer(root)      # the list of pad shortcuts (the pad icon in the header)
        self.drawers = {self.bt_drawer: self.bt_btn, self.keys_drawer: self.keys_btn}
        for d in self.drawers:
            d.opened.connect(lambda on, d=d: self._drawer_opened(d, on))
        # the minimum size comes from the layout (columns + the smallest pad drawing) - an explicit minimum smaller than that
        # allowed squeezing the window so that the right panel overlapped the drawing

    def _header(self):
        """The name and version on the left; on the right the sender state and icon-only buttons (tooltips with descriptions)."""
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
        self.bt_btn, self._bt_refresh = indicator_button(self.toggle_bt)   # the sender's Bluetooth: a status dot + the panel
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
        """A bar under the header, visible only when there is something to do: a new version on GitHub and / or a sender
        on a different version than the receiver. A text with a description (not colour alone) and an Update button.
        """
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

    def _refresh_banner(self) -> None:
        """Show the update banner when a newer version exists or the sender runs a different one (rebuilt only on change); notify through
        the tray once per new version.
        """
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
        self.banner_btn.setEnabled(self.update_btn.isEnabled())   # blocked during an update, as in the header
        self.banner.setVisible(bool(lines))
        if self.tray and av is not None and av.behind and av.commit != self._upd_notified:
            self._upd_notified = av.commit       # one notification per new version, not every 30 minutes
            self.tray.showMessage(_("Dostępna aktualizacja: {n}").format(n=n_changes(av.behind)),
                                  av.subject or _("Kliknij Aktualizuj w oknie"), QSystemTrayIcon.MessageIcon.Information, 8000)

    def _left(self):
        """The left column: one card per pad slot."""
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
        """The centre: the pad drawing, the hint and the Result / Physical / Test switch."""
        lay = QVBoxLayout()
        lay.setSpacing(px(10))
        self.pad = PadView()
        self.pad.part_clicked.connect(self.on_part)
        lay.addWidget(self.pad, 1)
        self.tilt_preview = TiltPreview(self.pad)     # over the pad's top-right corner while a tilt category is expanded
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
        """The right column: the profile editor (profile bar, tabs, status, Save / Revert) stacked with the Test panel."""
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(px(340))
        self.right_col = panel
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(px(14), px(14), px(14), px(14))
        self.right_stack = QStackedWidget()      # profile editing or the test panel (the Test view)
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
        self.changes_page.reset.confirmed.connect(self.reset)       # "Delete all" - on the list of changes, not in the footer
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
        """Create the tray icon and its menu (None when the tray is disabled or unavailable)."""
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
        self._tray_menu = menu                 # Qt does not take ownership of the menu - it must be kept
        tray.setToolTip("DualSense bridge")
        tray.activated.connect(self._tray_clicked)
        tray.show()
        return tray

    # ---------- scale ----------
    def apply_scale(self, k) -> None:
        """The interface scale (TV mode): the whole application's QSS + fixed column widths and icon sizes."""
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

    def _pads_connected(self) -> None:
        """A newly connected pad -> the window to the front, but not during a game (the same program list as the console mode).
        A connection = S.want (like the "connected" log entry and the console mode auto-ON), not S.alive: alive fades
        after stale_s (0.5 s) of silence - a momentary signal gap pulled the window to the front, e.g. over Steam Big Picture.
        """
        conn = tuple(w.is_set() for w in S.want)
        new = [i for i, (now, before) in enumerate(zip(conn, self._connected, strict=False)) if now and not before]
        self._connected = conn
        if not new:
            return
        hwnd, exe = W.foreground_window()
        if (hwnd and exe is None) or rx_games.in_game(exe or ""):  # no access to the program = maybe a game - do not touch
            print(f"Pad {new[0] + 1} connected - window stays in the background ({exe or 'unknown program'} in front)")
            return
        print(f"Pad {new[0] + 1} connected - window to the front")
        self.bring_to_front()

    def _tray_clicked(self, reason) -> None:
        """A click on the tray icon toggles the window: hide it when it is active, otherwise bring it to the front."""
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and self.isActiveWindow():
                self.hide()
            else:
                self.bring_to_front()

    def closeEvent(self, e) -> None:
        """The X hides the window to the tray (the receiver keeps running); without a tray - an ordinary minimise."""
        e.ignore()
        self.save_settings()
        if self.tray:
            self.hide()
        else:
            self.showMinimized()

    def changeEvent(self, e) -> None:
        """Minimising goes to the tray; any state or activation change re-evaluates the Test lock."""
        if e.type() == QEvent.Type.WindowStateChange and self.tray and self.isMinimized():
            QTimer.singleShot(0, self.hide)    # minimising = to the tray, without a taskbar button
        if e.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            self.sync_block()                  # the test lock only while the window is active (a click into the game = the pad returns)
        super().changeEvent(e)

    def showEvent(self, e) -> None:
        """On showing: apply the Test lock and start the live pad drawing."""
        super().showEvent(e)
        self.sync_block()
        self.live_tick()
        self.live_timer.start()

    def hideEvent(self, e) -> None:
        """On hiding: stop the live drawing, cancel a drift measurement and release the Test lock."""
        super().hideEvent(e)
        self.live_timer.stop()                 # a hidden window does not repaint the pad
        self.tilt_preview.idle()               # ... and the tilt preview does not keep the engine busy
        self.test_panel.cancel()               # a drift measurement does not run in the background
        self.sync_block()                      # a hidden window (the tray) - the game gets the pad again

    def toggle_bt(self) -> None:
        """Open / close the Bluetooth panel."""
        self.bt_drawer.toggle()

    def toggle_keys(self) -> None:
        """Open / close the shortcuts panel."""
        self.keys_drawer.toggle()

    def _drawer_opened(self, drawer, on) -> None:
        """Only one panel open at a time; when closed, the focus returns to its icon (TV mode - the pad still knows where it is)."""
        if on:
            for other in self.drawers:
                if other is not drawer:
                    other.close_drawer()
        elif self.fullscreen():
            self.drawers[drawer].setFocus(Qt.FocusReason.TabFocusReason)

    def show_log(self, on) -> None:
        """The log panel at the bottom of the window (the header icon)."""
        self.log_panel.setVisible(on)
        self.log_btn.setToolTip(_("Schowaj dziennik zdarzeń") if on else
                                _("Dziennik zdarzeń") + " - " + _("Połączenia padów, master, tryb konsoli, błędy, aktualizacje"))

    def save_settings(self) -> None:
        """Store the window geometry, splitter, log state, selected pad, view and the Test lock option in receiver_ui.ini."""
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split", self.split.saveState())
        self.settings.setValue("log_open", self.log_btn.isChecked())
        self.settings.setValue("slot", self.ed.slot)
        self.settings.setValue("view", self.edit_view)
        self.settings.setValue("test_block", self.test_panel.block.isChecked())
        self.settings.sync()

    def open_settings(self) -> None:
        """Open the settings window (created on first use)."""
        if self.settings_dlg is None:
            self.settings_dlg = ConsoleSettings(self)
            self.settings_dlg.saved.connect(self.restart)
            self.settings_dlg.elevate.connect(lambda: self.restart(admin=True))
            self.settings_dlg.applied.connect(self.slow_tick)
        self.settings_dlg.present()

    def _sender_label(self, online, build) -> None:
        """"● Sender online (ip) · a1b2c3d"; a different version than the receiver -> yellow and a hint to update."""
        if not online:
            self._sender_stale = None
            self.sender.setText(_("●  Nadajnik offline"))
            self.sender.setToolTip("")
            set_prop(self.sender, "level", "bad")
            return
        src = S.peer[0] if S.peer else ""
        mine = rx_update.version()[0]
        text = _("●  Nadajnik online")                 # the address is in the tooltip - the text fits at 1000 px
        if build and build[0]:
            text += f"  ·  {build[0]}"
        stale = bool(build and build[0] and mine and build[0] != mine)
        self._sender_stale = (build[0], mine) if stale else None   # the description in the update banner (not only colour)
        tip = (_("Nadajnik: wersja {s}, receiver: {r} - kliknij Aktualizuj").format(
                   s=rx_update.describe(*build), r=rx_update.describe(*rx_update.version()[:2])) if stale else
               _("Nadajnik: wersja {s}").format(s=rx_update.describe(*build)) if build and build[0] else
               _("Nadajnik nie podaje wersji (starsza wersja albo nie klon git)"))
        self.sender.setText(text)
        self.sender.setToolTip((_("Adres nadajnika: {ip}").format(ip=src) + "\n" if src else "") + tip)
        set_prop(self.sender, "level", "warn" if stale else "ok")

    def start_update(self) -> None:
        """Update both sides: a command to the sender (git pull on the phone, it restarts itself) and the receiver's git pull
        in a separate thread; the result returns by a signal to the window thread, then we wait for the sender's result.
        """
        if not self.update_btn.isEnabled():
            return
        self.update_btn.setEnabled(False)
        self.update_btn.setText(_("Aktualizuję…"))
        self.note(_("Pobieranie aktualizacji…"), "")
        self._upd_sender = S.sender_online() and S.sender_build is not None   # a new sender answers with a result
        self._upd_t0 = time.monotonic()
        self._upd_result = None
        self._upd_run = run = getattr(self, "_upd_run", 0) + 1
        QTimer.singleShot(int(UPDATE_STUCK_S * 1000), lambda: self._update_stuck(run))
        self._upd_commit = S.sender_build[0] if S.sender_build else ""   # a new commit after the restart = updated
        S.sender_update_msg = ""                 # the reason from the previous attempt must not describe this one
        if S.sender_build is not None:
            S.sender_build = (*S.sender_build[:2], UPD_RUNNING, time.monotonic())
        if S.sender_online():
            rx_net.send_cmd(255, CMD_UPDATE)
        threading.Thread(target=self._update_thread, name="update", daemon=True).start()

    def _update_thread(self) -> None:
        """An exception (git hung, no access to the folder) must not leave the button stuck forever on "Updating…"."""
        try:
            r = rx_update.update()
        except Exception as e:
            r = rx_update.Result(False, False, _("Aktualizacja nie powiodła się: {err}").format(err=repr(e)))
        self.update_done.emit(r)

    def _update_stuck(self, run) -> None:
        """The update thread did not finish (git hung) - the button must not hang on "Updating…" until the receiver
        restarts. A late result still works (a restart after the code is fetched).
        """
        if run != self._upd_run or self._upd_result is not None:
            return
        print(f"Update: no result from git after {UPDATE_STUCK_S:g}s")
        text = _("Aktualizacja nie odpowiada (git) - spróbuj ponownie albo uruchom update.bat")
        ev.error(text)
        self.update_btn.disarm()
        self.update_btn.setEnabled(True)
        self.note(text, "bad")

    def _update_finished(self, r) -> None:
        """The receiver is ready - we wait (UPDATE_WAIT_S at most) for the sender's result, then a message and a restart."""
        print(f"Update: {r.text}")
        self._upd_result = r
        self._update_poll()

    def _update_poll(self) -> None:
        """Wait (polling every 250 ms) for the sender to finish updating, then show the combined result, notify the log and restart when
        the code changed.
        """
        r = self._upd_result
        if r is None:
            return
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
        self.update_btn.disarm()                   # the icon only (the "Updating…" label existed only during the update)
        self.update_btn.setEnabled(not r.changed)
        self.update_btn.setToolTip(text)
        self.note(text, "ok" if r.ok else "bad")
        if r.ok and not r.changed:
            rx_update.available = rx_update.Available(0)      # checked a moment ago: nothing new
        if r.changed:
            QTimer.singleShot(UPDATE_RESTART_MS, self.restart)

    def restart(self, admin: bool = False) -> None:
        """A restart (the button, the tray, saved settings): an ordinary window close, and the receiver starts again by itself
        after releasing the ports and virtual pads. admin: the new process as administrator (UAC, HIDMaestro).
        """
        print("Restart requested from the window" + (" (as administrator)" if admin else ""))
        S.restart = "admin" if admin else True
        self.quit()

    def quit(self) -> None:
        """Shut the window down: flush edits, release the Test lock and shortcuts, save the state and leave the application."""
        self.flush()
        self.test_panel.cancel()
        rx_testmode.set_active(False)          # the pad shortcuts return
        for i in range(C.NUM_PADS):            # closing from the Test view: the output to the game returns before cleanup
            P.block(i, False)
        self.save_settings()
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    # ---------- selection and changes ----------
    def select_pad(self, slot) -> None:
        """Edit another pad: apply pending changes, switch the editor and the Test panel, then refresh everything."""
        self.flush()
        if slot != self.ed.slot:
            self.ed.set_slot(slot)
            self.profile_bar.close_new()
        for c in self.cards:
            c.set_selected(c.slot == slot)
        self.test_panel.set_slot(slot)
        self.sync_block()
        self.refresh_all()

    def set_view(self, view) -> None:
        """Switch between Result, Physical and Test; entering or leaving Test activates / cancels the Test panel and updates the lock."""
        was_test, self.view = self.view == "test", view
        if view != "test":
            self.edit_view = view
        elif not was_test:
            rx_testmode.rearm()                # entering Test after leaving from the pad (Options + Create) - works again
        self.seg.set(view)
        self.exit_hold = None
        self._apply_view()
        if was_test != (view == "test"):
            self.test_panel.activate(view == "test")
        self.sync_block()
        self.live_tick()

    def _apply_view(self) -> None:
        """The Test view: the test panel on the right, the drawing without badges and clicking, the hint under the pad."""
        test = self.view == "test"
        self.right_stack.setCurrentIndex(1 if test else 0)
        self.pad.set_test(test)
        if test:
            hint = _("Naciskaj przyciski, ruszaj gałkami, dotykaj touchpada - wszystko widać na żywo.")
            if self.fullscreen():
                hint += " " + _("Przytrzymaj ○, aby wyjść z testu.")
            hint += " " + _("Skróty padów nie działają; Options + Create (pad master) kończy test.")
        else:
            hint = _("Kliknij część pada, aby ją zremapować.")
            self.pad.set_selected(self.part)
        self.pad_hint.setText(hint)

    def sync_block(self) -> None:
        """The output lock to the game: only the pad under test, only in the Test view, with the option on and the window active -
        going to a game (another window in front), minimising or hiding gives the pad back at once.
        """
        test = (self.view == "test" and not rx_testmode.escaped and self.isVisible() and not self.isMinimized()
                and self.isActiveWindow())
        rx_testmode.set_active(test)           # all pads' shortcuts stand still, the master's Options + Create leaves
        on = test and self.test_panel.block.isChecked()
        for i in range(C.NUM_PADS):
            P.block(i, on and i == self.ed.slot)

    def _test_escaped(self) -> None:
        """Options + Create from the pad (rx_testmode): the lock is already lifted on the network thread - the window returns to the
        previous view.
        """
        if self.view == "test":
            self.set_view(self.edit_view)
            self.note(_("Wyjście z widoku Test z pada (Options + Create)"), "ok")

    def on_block_toggled(self, _on) -> None:
        """The Test lock option was toggled: remember it and apply it."""
        self.save_settings()
        self.sync_block()

    def on_part(self, name) -> None:
        """A pad part was clicked: show its editor (ignored in the Test view)."""
        if self.view == "test":
            return
        self.part = name
        self.pad.set_selected(name)
        self.part_page.show_part(name)
        self.tabs.setCurrentIndex(0)

    def on_profile(self, key) -> None:
        """Another profile was chosen in the list."""
        self.flush()
        self.ed.select(key)
        self.refresh_all()

    def on_create(self, text, copy_current) -> None:
        """Create a game profile for the typed program."""
        self.flush()
        ok, msg = self.ed.new_game(text, copy_current)
        if ok:
            self.profile_bar.close_new()
            self.refresh_all()
        self.note(msg, "warn" if ok else "bad")

    def on_delete(self) -> None:
        """Delete the current game profile."""
        self.flush()
        ok, msg = self.ed.delete_current()
        self.refresh_all()
        self.note(("✓ " if ok else "") + msg, "ok" if ok else "bad")

    def on_edit(self) -> None:
        """A change from a panel: refresh the badges and the list of changes; it reaches the pad after a short delay."""
        self.pad.set_profile(self.ed.prof)
        self.update_changes()
        self.update_status()
        self.apply_timer.start()

    def on_undone(self) -> None:
        """A change was undone from the list: refresh the panels and send the profile."""
        self.refresh_all()
        self.apply_timer.start()

    def flush(self) -> None:
        """A change waiting to be sent must reach the old profile before switching."""
        if self.apply_timer.isActive():
            self.do_apply(False)

    def do_apply(self, save) -> bool:
        """Send the edited profile to the pad (and save it to remap.json when `save`); -> False and a message on failure."""
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

    def save(self) -> None:
        """Save the profile (Ctrl+S)."""
        self.do_apply(True)

    def revert(self) -> None:
        """Go back to the saved version of the profile."""
        self.ed.revert()
        self.refresh_all()
        self.do_apply(False)

    def reset(self) -> None:
        """Remove all changes of the profile (Delete all)."""
        self.ed.reset_all()
        self.refresh_all()
        self.do_apply(False)

    # ---------- refreshing the view ----------
    def refresh_all(self) -> None:
        """Reload every panel from the current profile."""
        self.update_profile_bar()
        self.part_page.refresh()
        self.gyro_page.refresh()
        self.pad.set_profile(self.ed.prof)
        self.update_changes()
        self.update_status()

    def update_profile_bar(self) -> None:
        """Refresh the profile list (with the programs seen recently)."""
        self.profile_bar.reload(self.ed.profiles(), self.ed.key, self.ed.info(), list(rx_games.recent))

    def update_changes(self) -> None:
        """Refresh the list of changes and the tab title with their count."""
        n = self.changes_page.refresh()
        self.tabs.setTabText(2, _("Zmiany ({n})").format(n=n) if n else _("Zmiany"))

    def note(self, text, level) -> None:
        """A message covering the profile status for a few seconds (saved, an error, a profile created)."""
        self.note_text, self.note_until = (text, level), time.monotonic() + NOTE_S
        self.update_status()

    def update_status(self) -> None:
        """Show the transient note, or else the profile status, and enable Save / Revert accordingly."""
        if self.note_text and time.monotonic() < self.note_until:
            level, text = self.note_text[1], self.note_text[0]
        else:
            level, text = self.ed.status()
        self.status.setText(text)
        set_prop(self.status, "level", level)
        modified = self.ed.modified()
        set_prop(self.save_btn, "primary", modified or level == "warn")
        self.revert_btn.setEnabled(modified)

    def live_tick(self) -> None:
        """The live pad drawing (30 Hz, only a visible window); in TV mode also controlling the window with the pad."""
        now = time.monotonic()
        self.nav.tick(now)
        live = P.live(self.ed.slot)
        s = "_out" if self.view == "out" else ""
        pressed, ls, rs = live["pressed" + s], live["ls" + s], live["rs" + s]
        if live["tilt"] and not s:             # the tilt drives an input: draw what the game gets for it, not the pad's own input
            pressed, ls, rs = self._with_tilt(live, pressed, ls, rs)
        self.pad.set_live(pressed, ls, rs, live["l2" + s], live["r2" + s])
        if self.view == "test":
            t = self.test_panel.tick(now, live)
            self.pad.set_touch(t["touch"], t["tp"])
            self._exit_hold(now, "circle" in live["pressed"])
        else:
            self.gyro_page.set_live(self.ed.key == live["active"], live["gyro_on"])
        on_gyro = self.view != "test" and self.tabs.currentWidget() is self.gyro_page
        if on_gyro:
            self.gyro_page.tilt.tick()
        tilt = self.gyro_page.tilt
        self.tilt_preview.tick(on_gyro and tilt.any_open(), self.ed.slot, tilt.cfg)

    @staticmethod
    def _with_tilt(live, pressed, ls, rs):
        """The view of the pad's own input, with the stick / d-pad / button that the tilt drives replaced by the output."""
        target = live["tilt"]
        if target == "left":
            ls = live["ls_out"]
        elif target == "right":
            rs = live["rs_out"]
        keep = {n for n in live["pressed_out"] if n in ("l3", "r3") or (target == "dpad" and n.startswith("dpad"))}
        return sorted(set(pressed) | keep), ls, rs

    def _exit_hold(self, now, held) -> None:
        """TV mode: a held ○ of the pad under test (which then does not control the window) leaves the Test view."""
        if not (held and self.fullscreen()):
            self.exit_hold = None
        elif self.exit_hold is None:
            self.exit_hold = now
        elif now - self.exit_hold >= EXIT_HOLD_S:
            self.set_view(self.edit_view)

    def slow_tick(self) -> None:
        """Every 400 ms; an error in one pass must not stop the refreshing forever."""
        try:
            self._slow()
        except Exception as e:
            print(f"GUI refresh error: {e!r}")

    def _slow(self) -> None:
        """The 400 ms pass: place the window after a game, bring it forward for a new pad, refresh the sender label, banner, Bluetooth
        indicator, pad cards, low battery notices, the window title, the editor sync and the tray tooltip / icon.
        """
        now = time.monotonic()
        if self._place_pending and not rx_games.in_game():
            self._screens_settled()
        self._pads_connected()
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
        if self.ed.sync():                     # e.g. the pad id arrived - the pad's profile changed its key
            self.refresh_all()
        else:
            self.update_profile_bar()
            self.update_status()
            self.part_page.update_caps()       # the sender arrived / updated - the trigger resistance warning
        tip = (f"DualSense bridge\n{_('Nadajnik')}: " + ("online" if online else "offline") + "\n"
               + ("\n".join(tips) if tips else _("Brak podłączonych padów")))
        if self.tray and (tip != self.tip or low_any != self.low_any):
            self.tray.setToolTip(tip)
            self.tray.setIcon(self.icons[low_any])
        self.tip, self.low_any = tip, low_any
        self.log_panel.tick()


def _int(v, default):
    """int(v), or `default` when it is not a number."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


WM_QUERYENDSESSION = 0x0011


def session_end(*_args) -> None:
    """Windows is shutting down / restarting / logging off (Start, Big Picture, shutdown.exe): the console mode on -> OFF
    before the process is closed, so that after startup the picture is not on the TV only.
    """
    if rx_console.power_off(_("wyłączanie Windows")):
        print("Windows session ending - console mode switched off")


class SessionEndFilter(QAbstractNativeEventFilter):
    """WM_QUERYENDSESSION straight from the message loop - works also when Qt does not raise commitDataRequest."""
    def nativeEventFilter(self, event_type, message):
        """Look for WM_QUERYENDSESSION in native Windows messages and run session_end; never swallows the message."""
        if event_type in (b"windows_generic_MSG", "windows_generic_MSG"):
            import ctypes.wintypes
            if ctypes.wintypes.MSG.from_address(int(message)).message == WM_QUERYENDSESSION:
                session_end()
        return False, 0


_session_filter = None      # a reference - the filter must not be garbage collected


def _watch_session_end(app) -> None:
    """Hook the Windows session end (Qt's commitDataRequest and, on Windows, the native filter) once."""
    global _session_filter
    if _session_filter is not None:
        return
    _session_filter = SessionEndFilter()
    app.commitDataRequest.connect(session_end)
    if sys.platform == "win32":
        app.installNativeEventFilter(_session_filter)


def create_window(settings_path=None):
    """A QApplication (if there is none yet) + the window. Separate from run_gui so that tests can drive the window without a loop."""
    W.set_app_id()                             # the pad icon on the taskbar instead of Python's
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("DualSense bridge")
    app.setWindowIcon(make_icon(ICON_OK))      # the settings window and notifications too
    app.setQuitOnLastWindowClosed(False)       # closing the window = to the tray
    _watch_session_end(app)                    # Windows shutdown with the console mode -> both screens first
    apply_theme(app)
    path = settings_path or os.path.join(os.path.dirname(C.REMAP_PATH), "receiver_ui.ini")
    return MainWindow(QSettings(path, QSettings.Format.IniFormat))


def run_gui() -> None:
    """Run the GUI: optionally wait for the first pad, create the window and the update checker, handle Ctrl+C, then run the Qt loop and
    hide the tray icon on exit.
    """
    if C.cfg["gui_wait_pad"]:      # an option: do not create the window or the icon until some pad connects
        while not S.stop.is_set() and not any(w.is_set() for w in S.want):
            S.stop.wait(0.25)
        if S.stop.is_set():
            return
    win = create_window()
    rx_update.start_checker(S.stop)          # in the background: whether GitHub has a newer version (the header text)
    app = QApplication.instance()
    try:
        signal.signal(signal.SIGINT, lambda *_: app.quit())   # Ctrl+C in the console = an ordinary close (pad cleanup)
    except ValueError:
        pass                                                   # not the main thread
    if win.tray is None or C.cfg["gui_start_visible"]:        # without the icon the window is the only way to operate
        win.bring_to_front()
    try:
        app.exec()
    finally:
        if win.tray:
            win.tray.hide()
