"""Receiver: jedno okno do wszystkiego (PySide6): pady z baterią i łączem po lewej, rysunek pada na środku
(klik = remapowanie), profile / gyro / zmiany po prawej, ikona w zasobniku.

Szybkość: rysunek pada odświeża się 30 razy na sekundę tylko, gdy okno jest widoczne i tylko, gdy stan pada się
zmienił; reszta (karty, zasobnik) co 400 ms, także przy ukrytym oknie (podpowiedź zasobnika, niski stan baterii).
Zmiany profilu działają na pad na żywo po 150 ms od ostatniego ruchu suwaka, zapis do remap.json jest osobno (Save).
"""
import os
import signal
from functools import cache
import sys
import time

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMainWindow, QMenu, QSystemTrayIcon, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

import rx_games
import rx_master
import rx_profiles as P
import rx_state as S
import rx_status as ST
import rx_winapi as W
from rx_buttons import profile_size
from rx_config import C
from rx_editor import Editor
from rx_icon import ICON_LOW, ICON_OK, icon_png, render_icon
from rx_qt_console import ConsoleSettings
from rx_qt_icons import icon, pixmap
from rx_qt_pad import PadView
from rx_qt_panels import ChangesPage, GyroPage, PartPage, ProfileBar
from rx_qt_style import LEVEL, apply_theme, set_prop
from rx_qt_widgets import BatteryBar, ConfirmButton, Segmented, button, label

LIVE_MS, SLOW_MS, APPLY_MS = 33, 400, 150
NOTE_S = 6.0           # tyle sekund komunikat (np. "Saved") zasłania status profilu
VIEWS = (("out", "Result"), ("in", "Physical"))


@cache
def make_icon(rgb):
    """Ikona pada w kilku rozmiarach - ostra w zasobniku, na pasku zadań i przy skalowaniu 150-200 %."""
    ic = QIcon()
    for size in (16, 24, 32, 48, 64):
        pm = QPixmap()
        pm.loadFromData(icon_png(render_icon(rgb, size)))
        ic.addPixmap(pm)
    return ic


class PadCard(QFrame):
    """Karta pada po lewej: stan połączenia, bateria, jakość łącza, profil, wyłączenie, korona mastera.
    Klik = edytuj ten pad; klik w koronę = ten pad zostaje masterem (drugi klik - bez mastera)."""

    clicked = Signal(int)
    master_changed = Signal()

    def __init__(self, slot):
        super().__init__()
        self.slot, self._key = slot, None
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.status = label("")
        self.crown = QToolButton()
        self.crown.setObjectName("crown")
        self.crown.setIconSize(QSize(20, 20))
        self.crown.setCursor(Qt.CursorShape.PointingHandCursor)
        self.crown.clicked.connect(self.toggle_master)
        head.addWidget(label(f"Pad {slot + 1}", "name"))
        head.addWidget(self.crown)
        head.addStretch(1)
        head.addWidget(self.status)
        lay.addLayout(head)
        batt = QHBoxLayout()
        self.bar, self.pct = BatteryBar(), label("", "val")
        self.pct.setMinimumWidth(48)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        batt.addWidget(self.bar, 1)
        batt.addWidget(self.pct)
        lay.addLayout(batt)
        self.net, self.rate = label("", "dim"), label("", "dim")        # jakość łącza: sieć / tempo
        self.mode, self.profile = label("", "dim"), label("", "dim")    # DS4 i touchpad / profil
        self.mode_icon = label("")
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addWidget(self.mode_icon)
        mode_row.addWidget(self.mode, 1)
        for lb in (self.net, self.rate):
            lay.addWidget(lb)
        lay.addLayout(mode_row)
        lay.addWidget(self.profile)
        row = QHBoxLayout()
        self.off = ConfirmButton("Power off", "Turn off?", small=True)
        self.off.setToolTip("Disconnects the pad over Bluetooth - it switches off")
        self.off.confirmed.connect(lambda: P.power_off(slot))
        row.addWidget(self.off)
        row.addStretch(1)
        lay.addLayout(row)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.slot)

    def toggle_master(self):
        ok, msg = rx_master.set_master(None if rx_master.is_master(self.slot) else self.slot)
        if not ok:
            print(f"Master: {msg}")
        self._key = None
        self.master_changed.emit()

    def set_selected(self, on):
        set_prop(self, "selected", on)

    def update_state(self, now):
        """Odświeża kartę (tylko to, co się zmieniło). -> True, gdy bateria pada jest na wyczerpaniu."""
        i = self.slot
        net, rate = ST.link_lines(i, now)
        level = ST.link_level(i, now) if net else ""
        for lb, text in ((self.net, net), (self.rate, rate)):   # zmienia się co sekundę - osobno od reszty karty
            if text != lb.text():
                lb.setText(text)
            lb.setVisible(bool(text))
            set_prop(lb, "level", level)
        pct, flags = S.batt_info[i]
        low = ST.is_low(i, C.LOW_BATT)
        alive, want = bool(S.alive[i]), S.want[i].is_set()
        master = rx_master.is_master(i)
        key = (alive, want, pct, flags, S.plugged[i], S.mouse_mode[i], profile_size(S.remap_maps[i]), S.active[i],
               master, S.pad_id[i])
        if key == self._key:
            return low                        # nic się nie zmieniło - nie dotykaj widżetów
        self._key = key
        self.crown.setIcon(icon("crown", dim=not master))
        self.crown.setToolTip("Pad master - kliknij, aby zdjąć" if master else "Kliknij: ten pad zostaje masterem")
        self.crown.setEnabled(master or alive or want)
        self.off.setEnabled(alive)
        set_prop(self, "low", low)
        self.status.setText("●  connected" if alive else "●  no signal" if want else "○  not connected")
        set_prop(self.status, "level", "ok" if alive else "warn" if want else "")
        charging = bool(flags & 1)
        if pct > 100:                         # stan baterii nieznany
            self.bar.set(None, LEVEL["ok"])
            self.pct.setText("n/a")
        else:
            self.bar.set(pct, LEVEL[ST.battery_level(pct, charging, C.LOW_BATT)])
            self.pct.setText(f"{pct}%" + (" ⚡" if charging else ""))
        set_prop(self.pct, "level", "bad" if low else "")
        n = profile_size(S.remap_maps[i])
        self.mode.setText(f"DS4: {'on' if S.plugged[i] else 'off'}  ·  touchpad: {'mouse' if S.mouse_mode[i] else 'DS4'}")
        pm = pixmap("mouse" if S.mouse_mode[i] else "touchpad", 32)
        pm.setDevicePixelRatio(2)
        self.mode_icon.setPixmap(pm)
        self.mode_icon.setToolTip("Touchpad jako mysz" if S.mouse_mode[i] else "Touchpad jako dotyk DS4")
        self.profile.setText(f"profile: {S.profile_label(S.active[i])}" + (f" ({n})" if n else ""))
        return low


class MainWindow(QMainWindow):
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
        self.view = view if view in dict(VIEWS) else "out"
        self.part = None                       # zaznaczona część pada (nazwa) albo None
        self.warned = [False] * C.NUM_PADS     # powiadomienie o niskiej baterii wysłane raz na rozładowanie
        self.note_text, self.note_until = None, 0.0
        self.low_any, self.tip, self.online = False, None, None
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

        self._build()
        self.tray = self._make_tray()
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save)
        self.select_pad(self.ed.slot)
        self.pad.set_selected(None)
        geo = settings.value("geometry")
        if not (geo and self.restoreGeometry(geo)):
            self.resize(1160, 720)
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())
        self.slow_tick()
        self.slow_timer.start()                # działa także przy ukrytym oknie (zasobnik, bateria, Ctrl+C)

    # ---------- budowa ----------
    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        body = QHBoxLayout()
        body.setContentsMargins(16, 16, 16, 16)
        body.setSpacing(16)
        body.addWidget(self._left())
        body.addLayout(self._center(), 1)
        body.addWidget(self._right())
        outer.addLayout(body, 1)
        self.setMinimumSize(1000, 640)

    def _header(self):
        bar = QFrame()
        bar.setObjectName("header")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 10, 16, 10)
        title = label("DualSense bridge")
        title.setObjectName("title")
        self.sender = label("")
        settings_btn = button("Ustawienia", small=True)
        settings_btn.setToolTip("Tryb konsoli, mysz touchpada, pad master")
        settings_btn.clicked.connect(self.open_settings)
        restart_btn = ConfirmButton("Restart", "Restart?", small=True)
        restart_btn.setIcon(icon("restart"))
        restart_btn.setToolTip("Uruchamia receiver od nowa (pady na chwilę znikną, niezapisane zmiany profilu przepadną)")
        restart_btn.confirmed.connect(self.restart)
        quit_btn = ConfirmButton("Quit", "Quit?", small=True)
        quit_btn.setToolTip("Stops the receiver - the virtual pads disappear")
        quit_btn.confirmed.connect(self.quit)
        lay.addWidget(title)
        lay.addStretch(1)
        lay.addWidget(self.sender)
        lay.addSpacing(12)
        lay.addWidget(settings_btn)
        lay.addWidget(restart_btn)
        lay.addWidget(quit_btn)
        return bar

    def _left(self):
        w = QWidget()
        w.setFixedWidth(236)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.cards = [PadCard(i) for i in range(C.NUM_PADS)]
        for c in self.cards:
            c.clicked.connect(self.select_pad)
            c.master_changed.connect(self.slow_tick)
            lay.addWidget(c)
        lay.addStretch(1)
        return w

    def _center(self):
        lay = QVBoxLayout()
        lay.setSpacing(10)
        self.pad = PadView()
        self.pad.part_clicked.connect(self.on_part)
        lay.addWidget(self.pad, 1)
        row = QHBoxLayout()
        row.addWidget(label("Click any part of the pad to remap or tune it.", "dim"), 1)
        row.addWidget(label("Drawing shows:", "dim"))
        self.seg = Segmented(VIEWS)
        self.seg.set(self.view)
        self.seg.setToolTip("Result = what the game gets after your remaps. Physical = what the pad itself is doing.")
        self.seg.changed.connect(self.set_view)
        row.addWidget(self.seg)
        lay.addLayout(row)
        return lay

    def _right(self):
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(330)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
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
        for page, name in ((self.part_page, "Selected"), (self.gyro_page, "Gyro"), (self.changes_page, "Changes")):
            self.tabs.addTab(page, name)
        for page in (self.part_page, self.gyro_page):
            page.edited.connect(self.on_edit)
        self.changes_page.undone.connect(self.on_undone)
        lay.addWidget(self.tabs, 1)
        self.status = label("", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.save_btn = button("Save", primary=True)
        self.save_btn.setToolTip("Write the profile to remap.json (Ctrl+S)")
        self.save_btn.clicked.connect(self.save)
        revert = button("Revert")
        revert.setToolTip("Go back to the saved version of this profile")
        revert.clicked.connect(self.revert)
        reset = ConfirmButton("Reset all", "Reset?")
        reset.setToolTip("Clear every change in this profile")
        reset.confirmed.connect(self.reset)
        for b in (self.save_btn, revert, reset):
            row.addWidget(b)
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
        menu.addAction("Show window", self.bring_to_front)
        menu.addAction("Ustawienia", self.open_settings)
        menu.addAction(icon("restart"), "Restart", self.restart)
        menu.addAction("Quit", self.quit)
        tray.setContextMenu(menu)
        self._tray_menu = menu                 # Qt nie przejmuje własności menu - trzeba je trzymać
        tray.setToolTip("DualSense bridge")
        tray.activated.connect(self._tray_clicked)
        tray.show()
        return tray

    # ---------- okno ----------
    def bring_to_front(self):
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
        super().changeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.live_tick()
        self.live_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.live_timer.stop()                 # ukryte okno nie odrysowuje pada

    def save_settings(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("slot", self.ed.slot)
        self.settings.setValue("view", self.view)
        self.settings.sync()

    def open_settings(self):
        if self.settings_dlg is None:
            self.settings_dlg = ConsoleSettings(self)
            self.settings_dlg.saved.connect(self.restart)
            self.settings_dlg.applied.connect(self.slow_tick)
        self.settings_dlg.present()

    def restart(self):
        """Restart (przycisk, zasobnik, zapisane ustawienia): zwykłe zamknięcie okna, a receiver sam startuje od nowa
        po zwolnieniu portów i ViGEm."""
        print("Restart requested from the window")
        S.restart = True
        self.quit()

    def quit(self):
        self.flush()
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
        self.refresh_all()

    def set_view(self, view):
        self.view = view
        self.live_tick()

    def on_part(self, name):
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
        self.tabs.setTabText(2, f"Changes ({n})" if n else "Changes")

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
        set_prop(self.save_btn, "primary", self.ed.modified() or level == "warn")

    def live_tick(self):
        """Rysunek pada na żywo (30 Hz, tylko widoczne okno)."""
        live = P.live(self.ed.slot)
        s = "_out" if self.view == "out" else ""
        self.pad.set_live(live["pressed" + s], live["ls" + s], live["rs" + s], live["l2" + s], live["r2" + s])
        self.gyro_page.set_live(self.ed.key == live["active"], live["gyro_on"])

    def slow_tick(self):
        """Co 400 ms; błąd w jednym przebiegu nie może zatrzymać odświeżania na zawsze."""
        try:
            self._slow()
        except Exception as e:
            print(f"GUI refresh error: {e!r}")

    def _slow(self):
        now = time.monotonic()
        online = S.sender_online(now)
        if online != self.online:
            self.online = online
            src = S.peer[0] if S.peer else ""
            self.sender.setText(("●  Sender online" + (f"  ({src})" if src else "")) if online else "●  Sender offline")
            set_prop(self.sender, "level", "ok" if online else "bad")
        low_any, tips = False, []
        for c in self.cards:
            i = c.slot
            low_any = c.update_state(now) or low_any
            pct, flags = S.batt_info[i]
            charging, known = bool(flags & 1), pct <= 100
            if S.want[i].is_set() or S.alive[i]:
                tips.append(f"Pad {i + 1}: " + (f"{pct}%" + (" (charging)" if charging else "") if known else "n/a"))
            if ST.is_low(i, C.LOW_BATT) and not self.warned[i]:
                self.warned[i] = True
                if self.tray and C.cfg["low_batt_notify"]:
                    self.tray.showMessage("Low battery", f"Pad {i + 1}: {pct}%", QSystemTrayIcon.MessageIcon.Warning, 8000)
            elif self.warned[i] and (charging or (known and pct > C.LOW_BATT + 5)):
                self.warned[i] = False
        title = "DualSense bridge - LOW BATTERY" if low_any else "DualSense bridge"
        if title != self.windowTitle():
            self.setWindowTitle(title)
        if self.ed.sync():                     # np. nadeszło id pada - profil pada zmienił klucz
            self.refresh_all()
        else:
            self.update_profile_bar()
            self.update_status()
        tip = ("DualSense bridge\nSender: " + ("online" if online else "offline") + "\n"
               + ("\n".join(tips) if tips else "No pads connected"))
        if self.tray and (tip != self.tip or low_any != self.low_any):
            self.tray.setToolTip(tip)
            self.tray.setIcon(self.icons[low_any])
        self.tip, self.low_any = tip, low_any


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def create_window(settings_path=None):
    """QApplication (jeśli jeszcze nie ma) + okno. Osobno od run_gui, żeby testy mogły sterować oknem bez pętli."""
    W.set_app_id()                             # ikona pada na pasku zadań zamiast ikony Pythona
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("DualSense bridge")
    app.setWindowIcon(make_icon(ICON_OK))      # także okno ustawień i powiadomienia
    app.setQuitOnLastWindowClosed(False)       # zamknięcie okna = do zasobnika
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
