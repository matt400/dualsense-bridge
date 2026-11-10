"""Settings window: the Mouse and Master tabs (save without a restart), the master crown on the pad card, button icons."""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_master, rx_qt, rx_qt_screens, rx_qt_update, rx_settings
from proto import T_IDENT
from rx_console import Console, ConsoleConfig
from chk import ok
pump = lambda ms=40: QTest.qWait(ms)
A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
feed(T_IDENT, 0, A.encode()); feed(T_IDENT, 1, B.encode()); state(0); state(1)

win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 760); win.show(); pump(150)
os.makedirs(os.path.join(os.path.dirname(__file__)), exist_ok=True)
win.grab().save(os.path.join(os.path.dirname(__file__), "gui_master.png"))

# ---------- crown on the pad card ----------
card0, card1 = win.cards
assert not rx_master.is_master(0) and card0.crown.isEnabled()
card1.crown.click(); pump()
assert rx_settings.cur.master.pad == B and rx_master.is_master(1)
assert json.load(open(C.SETTINGS_PATH))["master"]["pad"] == B, "korona zapisuje settings.json"
assert "zdjąć" in card1.crown.toolTip() and "zostaje" in card0.crown.toolTip()
card0.crown.click(); pump()
assert rx_settings.cur.master.pad == A
card0.crown.click(); pump()
assert rx_settings.cur.master.pad == "", "a second click on the master crown - no master"
ok("crown: click = master, click on the master = no master, saved to the file")

# ---------- update info: a bar with a description under the header, not just a colour ----------
import rx_update
import time as _t
assert [rx_qt.n_changes(n) for n in (1, 2, 5, 12, 22, 25)] == ["1 zmiana", "2 zmiany", "5 zmian", "12 zmian", "22 zmiany",
                                                               "25 zmian"]
win.slow_tick(); pump()
assert win.banner.isHidden(), "nothing to report - no bar"
rx_update.available = rx_update.Available(3, "Nowy tryb konsoli", "abc1234")
win.slow_tick(); pump()
assert win.banner.isVisible() and "Dostępna aktualizacja: 3 zmiany  -  Nowy tryb konsoli" in win.banner_text.text(), \
    win.banner_text.text()
assert not hasattr(win, "banner_btn"), "the bar only informs - the Update button is in the header"
S.sender_build = ("0000000", 1_700_000_000, 0, _t.monotonic()); S.last_pkt = _t.monotonic()
rx_update._version = ("1111111", 1_700_000_100, "x")
win.online = None; win.slow_tick(); pump()
assert "Nadajnik ma inną wersję (0000000) niż receiver (1111111)" in win.banner_text.text(), win.banner_text.text()
win.banner.grab().save(os.path.join(os.path.dirname(__file__), "gui_update_banner.png"))
rx_update.available = rx_update.Available(0)
S.sender_build = ("1111111", 1_700_000_100, 0, _t.monotonic())
win.online = None; win.slow_tick(); pump()
assert win.banner.isHidden(), "current version on GitHub and the same on the sender - the bar disappears"
S.sender_build, S.last_pkt, rx_update._version = None, 0.0, None         # further tests: sender offline
win.online = None; win.slow_tick()
ok("update: the bar 'Dostępna aktualizacja: N zmian - tytuł' and 'Nadajnik ma inną wersję', the Update button")

# ---------- settings tabs ----------
win.open_settings(); pump(100)
dlg = win.settings_dlg
assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Tryb konsoli", "Mysz", "Master", "Lightbar", "Haptyka i głośnik", "Wake-on-LAN", "Pady", "Nadajnik", "Okno", "Kopie", "Bezpieczeństwo"]
assert dlg.toggle_button == "r2" and "R2" in dlg.pick_name.text() and not dlg.pick_icon.pixmap().isNull()
assert dlg.auto_on.isChecked(), "default: console mode when the master connects"
assert not dlg.tabs.tabIcon(2).isNull(), "tabs with icons"
dlg.tabs.setCurrentIndex(1); pump()
assert not dlg.test_btn.isVisible(), "Test only on the console mode tab"
dlg.sens.slider.setValue(dlg.sens.slider.maximum()); dlg.freeze.set(250); dlg.rewind.set(0); dlg.drag.set(100)
dlg.tabs.setCurrentIndex(2); pump()
dlg.grab().save(os.path.join(os.path.dirname(__file__), "gui_settings_master.png"))
pads = [dlg.master_pad.itemData(i) for i in range(dlg.master_pad.count())]
assert pads == ["", A, B], pads
dlg.master_pad.set_data(B); dlg.master_pad.activated.emit(dlg.master_pad.currentIndex())   # the user's choice
dlg.features["ps_game"].setChecked(True); dlg.features["console"].setChecked(False)
dlg.absent.set_data("nobody"); dlg.claim.setChecked(False)
dlg.actions["swap"].set_data("")                          # square by default too - the window rejects the same button for two actions
dlg.actions["sleep"].set_data("square")
icons = [dlg.actions["sleep"].itemIcon(i).isNull() for i in range(1, dlg.actions["sleep"].count())]
assert not any(icons), "the action button list is icons"
assert dlg.actions["sleep"].toolTip() == "Kwadrat", dlg.actions["sleep"].toolTip()

S.restart = False; closed = []
QApplication.instance().quit = lambda: closed.append(True)
dlg.save_btn.click(); pump()
m, mo = rx_settings.cur.master, rx_settings.cur.mouse
assert m.pad == B and m.when_absent == "nobody" and not m.claim_hold and "ps_game" in m.only_master and "console" not in m.only_master
assert m.actions["sleep"] == "square" and mo.sens == 5.0 and mo.click_freeze_ms == 250 and mo.click_rewind_ms == 0 and mo.drag_threshold == 100
assert M.sens == [5.0] * C.NUM_PADS, "sensitivity works at once"
assert S.restart is False and not closed, "mouse / master without a restart (console mode unchanged)"
assert "Zapisano" in dlg.status.text()
saved = json.load(open(C.SETTINGS_PATH))
assert saved["mouse"]["click_freeze_ms"] == 250 and saved["master"]["only_master"] == [k for k in rx_settings.MASTER_FEATURES if k in m.only_master]
ok("saving mouse and master: works at once, without a restart, settings.json")

# ---------- Settings -> Pads: backend, DLL path, fixed order; restart as administrator ----------
import rx_ds4, rx_hm, rx_winapi
dlg.tabs.setCurrentIndex(6); pump()
assert dlg.vpad_backend.currentData() == "auto" and dlg.vpad_fixed.isChecked() and dlg.vpad_dll.text() == ""
assert dlg.vpad_dll.placeholderText() == "automatycznie" and dlg.vpad_auto.isChecked()
real_admin = rx_winapi.is_admin
rx_winapi.is_admin = lambda: False
dlg.vpad_backend.set_data("hidmaestro"); dlg._vpad_state(); pump()
assert dlg.vpad_admin.isVisible() and "administratora" in dlg.vpad_now.text(), dlg.vpad_now.text()
dlg.grab().save(os.path.join(os.path.dirname(__file__), "gui_settings_pads.png"))
dlg.vpad_backend.set_data("vigem"); dlg._vpad_state(); pump()
assert not dlg.vpad_admin.isVisible(), "ViGEm does not need administrator"
asked = []
win.restart = lambda admin=False: asked.append(admin)
dlg.elevate.disconnect(); dlg.elevate.connect(lambda: win.restart(admin=True))
dlg.vpad_backend.set_data("hidmaestro"); dlg._vpad_state(); dlg.vpad_admin.click(); pump()
assert asked == [True], asked
del win.restart
rx_winapi.is_admin = real_admin
dlg.vpad_fixed.setChecked(False); dlg.vpad_dll.setText("  D:\\HM  "); dlg.vpad_auto.setChecked(False)
dlg.save_btn.click(); pump()
v = rx_settings.cur.vpad
assert (v.backend, v.fixed_slots, v.hm_dll) == ("hidmaestro", False, "D:\\HM"), v
assert json.load(open(C.SETTINGS_PATH))["vpad"] == {"backend": "hidmaestro", "fixed_slots": False, "hm_dll": "D:\\HM",
                                                     "hm_auto": False}
assert S.restart is False, "pads work without a restart"
rx_settings.apply(rx_settings.defaults())
ok("Settings -> Pads: backend / DLL / order saved at once, restart as administrator when it is missing")

# ---------- the same button in two actions ----------
dlg.actions["pause"].set_data("square"); dlg.tabs.setCurrentIndex(0)
dlg.save_btn.click(); pump()
assert dlg.tabs.currentIndex() == 2 and dlg.status.property("level") == "bad"
assert rx_settings.cur.master.actions["pause"] != "square", "bad settings were not applied"
dlg.actions["pause"].set_data("options")
ok("two actions on one button: a message, nothing saved")

# ---------- a console mode change still restarts ----------
dlg.set_button("cross"); dlg.save_btn.click(); pump()
assert S.restart is True and closed and json.load(open(C.CONSOLE_PATH))["toggle_button"] == "cross"
S.restart = False
ok("console mode change: save + restart as before")

# ---------- ikony w remapowaniu ----------
win.on_part("r2"); pump()
assert win.part_page.title.text() == "R2 (spust)" and not win.part_page.title_icon.pixmap().isNull()
targets = [win.part_page.dst.itemData(i) for i in range(win.part_page.dst.count())]
assert "mute" not in targets and "ps" in targets and len(targets) == 18, targets
assert win.part_page.dst.itemText(0) == "Krzyżyk (X)" and not win.part_page.dst.itemIcon(0).isNull(), "targets: icon + name"
win.grab().save(os.path.join(os.path.dirname(__file__), "gui_master2.png"))
ok("remap: title with an icon, targets with an icon and a name")
# ---------- window screen ----------
dlg.present(); pump()
area = dlg.screen().availableGeometry()
assert area.contains(dlg.frameGeometry()) or dlg.size() == dlg.size().expandedTo(dlg.minimumSizeHint()), \
    ("the settings window in the work area (not under the taskbar)", area, dlg.frameGeometry())
assert area.contains(dlg.save_btn.mapToGlobal(dlg.save_btn.rect().bottomRight())), "przycisk Zapisz widoczny"
ok("the settings window fits on the screen, Save visible")
scr = [dlg.screen_box.itemData(i) for i in range(dlg.screen_box.count())]
names = [k for k, _ in rx_qt_screens.screens()]
assert scr == ["", *names], (scr, names)
dlg.screen_box.set_data(names[0]); dlg.tabs.setCurrentIndex(8)
dlg.save_btn.click(); pump()
assert win.screen_choice() == names[0] and win.settings.value("screen") == names[0], "chosen screen stored"
win.move(-5000, -5000); win.place_on_screen()
assert QApplication.screens()[0].availableGeometry().contains(win.frameGeometry().center()), "window moved to the chosen screen"
win.set_screen_choice("NIE-MA-TAKIEGO"); dlg.hide(); dlg.present(); pump()
assert dlg.screen_box.currentData() == "NIE-MA-TAKIEGO" and "niepodłączony" in dlg.screen_box.currentText()
win.move(-5000, -5000)
assert win.place_on_screen() is win.screen(), "no chosen screen -> the screen the window is on now"
area = win.screen().availableGeometry()
g = win.frameGeometry()
assert (abs(g.center().x() - area.center().x()) <= 1 if g.width() <= area.width() else g.left() == area.left()), \
    ("the window centred on the screen (bigger than the screen: from the left edge)", g, area)
win.set_screen_choice("")
# window mode: the whole work area (no native maximize) or a chosen resolution
from PySide6.QtCore import QSize
area = win.screen().availableGeometry()
assert win.window_mode() == "maximized"
win.place_on_screen()
g = win.frameGeometry()
assert not win.isMaximized() and g.topLeft() == area.topLeft() and g.width() >= area.width() and g.height() >= area.height(), \
    ("maximized = the whole work area (no less than the layout minimum)", g, area)
want = QSize(max(rx_qt_screens.MIN_WINDOW.width(), area.width() - 200), max(rx_qt_screens.MIN_WINDOW.height(), area.height() - 200))
win.set_window_mode("size", want); pump(50)
assert win.window_mode() == "size" and win.settings.value("window_size") == f"{want.width()}x{want.height()}"
if want.width() <= area.width() - rx_qt_screens.FIT_MARGIN and want.height() <= area.height() - rx_qt_screens.FIT_MARGIN:
    assert win.size() == want.expandedTo(win.minimumSize()), ("chosen resolution", win.size(), want)
assert rx_qt_screens.parse_size("1280x720") == QSize(1280, 720) and rx_qt_screens.parse_size("śmieci") is None
assert rx_qt_screens.parse_size("100x100") is None, "too small a resolution rejected"
dlg.hide(); dlg.present(); pump()
assert dlg.win_mode.currentData() == "size" and dlg.win_res.isEnabled() and dlg.win_res.currentData() == f"{want.width()}x{want.height()}"
dlg.win_mode.set_data("maximized"); dlg._window_state()
assert not dlg.win_res.isEnabled(), "the resolution list only for the chosen-resolution mode"
dlg.save_btn.click(); pump(100)
assert win.window_mode() == "maximized" and win.frameGeometry().topLeft() == area.topLeft(), "Settings -> Window -> whole work area"
win.set_window_mode("size", want)
ok("window mode: whole work area / chosen resolution, saved in the settings")
# a screen smaller than the window (TV 1280x720 at 200 %): the window shrunk and centred, then back to its size
pref = win._pref
def fits():
    g, m = win.frameGeometry(), win.minimumSizeHint().expandedTo(win.minimumSize())
    lim = area.size() - QSize(rx_qt_screens.FIT_MARGIN, rx_qt_screens.FIT_MARGIN)
    assert g.left() >= area.left() and g.top() >= area.top(), (area, g)          # buttons always visible
    assert win.width() <= max(lim.width(), m.width()) and win.height() <= max(lim.height(), m.height()), (area, g, m)
win._pref = area.size() + QSize(400, 400)
win.place_on_screen(); fits()
small = win.minimumSizeHint().expandedTo(QSize(300, 300))
win._pref = small; win.place_on_screen()
assert win.size() == small.boundedTo(area.size()) or win.size() == small, (win.size(), small)
win._pref = area.size() + QSize(400, 400)
win._screens_settled(); fits()
# a screen change during a game: the window is not moved, but after the game exits it goes back to the centre (next refresh)
import rx_games, rx_state as _S
_S.game = "forzahorizon5.exe"; win.move(-5000, -5000)
win._screens_settled()
assert win._place_pending and win.frameGeometry().left() == -5000, "in a game the window stays (the game changed the resolution)"
win.slow_tick()
assert win._place_pending, "game still in the foreground - waits"
_S.game = "explorer.exe"; win.slow_tick()
assert not win._place_pending, "after the game exits the window is laid out again"
fits()
_S.game = None
win._pref = pref
ok("window screen: screen list, save, move, missing screen, always centred and fits")

# ---------- full screen, language, allowed programs, rumble test ----------
import os
is_ci = os.getenv("CI") == "true"


def until(cond, timeout=3.0):
    """Pump the event loop until cond() holds (the window manager changes the window state asynchronously)."""
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        pump(20)
    return cond()


def full(on):
    """The window is (not) full screen; CI (Xvfb) does not report the physical state reliably, so only the setting is checked there."""
    return win.fullscreen() == on and (is_ci or win.isFullScreen() == on)


assert not win.fullscreen()
win.toggle_fullscreen()
assert until(lambda: full(True)) and win.settings.value("fullscreen") in (True, "true"), "F11: full screen"

win.fs_btn.click()
assert until(lambda: full(False)), "F11 / button: back to a window"

dlg.hide(); dlg.present(); pump()
assert dlg.win_mode.currentData() == "size" and dlg.lang.currentData() == "pl"
dlg.win_mode.set_data("full"); dlg.save_btn.click()
assert until(lambda: full(True)), "Settings -> Window -> Full screen"
win.set_fullscreen(False); pump(100)
assert "steam.exe" in dlg.apps.toPlainText().split()
dlg.hide(); dlg.present(); pump()
dlg.apps.setPlainText("Steam.exe\n  Moja Gra.exe \n\nsteam.exe")
S.restart = False; closed.clear()
dlg.save_btn.click(); pump()
assert S.restart is True and json.load(open(C.CONSOLE_PATH))["allowed_apps"] == ["steam.exe", "moja gra.exe"], \
    "program list: lower case, no empty ones or repeats, names with a space; a change = restart"
S.restart = False; closed.clear()
rx_console.ctl.cfg = rx_console.load(C.CONSOLE_PATH)       # as after a restart
dlg.hide(); dlg.present(); pump()
dlg.lang.set_data("en"); dlg.save_btn.click(); pump()
assert S.restart is True and json.load(open(C.SETTINGS_PATH))["ui"]["lang"] == "en", "a language change = save + restart"
S.restart = False; closed.clear()
import rx_i18n
rx_i18n.set_lang("en"); w2 = rx_qt.create_window(os.path.join(TMP, "ui2.ini")); w2.show(); pump(100)
from PySide6.QtWidgets import QPushButton as _QPB
texts = {b.text() for b in w2.findChildren(_QPB)}
tips = {b.toolTip().split(" - ")[0] for b in w2.findChildren(_QPB) if b.property("iconic")}
assert {"Save", "Revert"} <= texts and {"Settings", "Quit", "Update", "Restart"} <= tips, (texts, tips)
assert w2.tabs.tabText(0) == "Selected" and w2.cards[0].off.toolTip().startswith("Power off")
w2.hide(); rx_i18n.set_lang("pl")
pulses = []
F.pulse = lambda i, kind, force=False: pulses.append((i, kind, force))
card0.rumble.click(); pump()
assert pulses == [(0, "test", True)] and card0.rumble.toolTip().startswith("Test wibracji"), pulses
ok("full screen (F11, button, settings), language (restart, the window in English), program list, rumble test")

# ---------- Restart button in the side menu and the window icon ----------
from PySide6.QtWidgets import QPushButton
btn = next(b for b in win.findChildren(QPushButton) if b.toolTip().startswith("Restart"))
S.restart = False; closed.clear()
btn.click(); pump()
assert S.restart is False and btn.compact and btn.text() == "" and btn.property("armed") is True, "the first click only arms (red button, question in a tooltip)"
btn.click(); pump()
assert S.restart is True and closed, "second click: receiver restart"
S.restart = False
assert not QApplication.instance().windowIcon().isNull() and len(win.windowIcon().availableSizes()) >= 4, "pad icon in several sizes"
ok("Restart: two clicks, receiver restart; application icon set")

# ---------- Aktualizuj: git pull w tle, komunikat, restart po pobraniu ----------
import rx_update
rx_qt_update.UPDATE_RESTART_MS = 50
results = iter([rx_update.Result(True, False, "Masz najnowszą wersję"),
                rx_update.Result(True, True, "Zaktualizowano (2 zmian): aaaaaaa → bbbbbbb", "a", "b")])
rx_update.update = lambda **_: (time.sleep(0.1), next(results))[1]
btn = win.update_btn
assert btn.text() == "" and btn.toolTip().startswith("Aktualizuj") and not btn.icon().isNull(), "sama ikona"
S.restart = False; closed.clear()
btn.click(); btn.click(); pump(20)
assert not btn.isEnabled() and btn.text() == "" and btn.label_text.startswith("Aktualizuję…") and btn.label_text.endswith("%"), "in progress: locked, percent in a tooltip (narrow menu)"
assert win.update_ring.running() and not btn.icon().isNull(), "a spinner turns next to the text"
win.update_progress.emit(60); pump(); assert btn.label_text == "Aktualizuję… 60%" == btn.toolTip(), btn.label_text
win.update_progress.emit(30); pump(); assert btn.label_text == "Aktualizuję… 60%", "the percent does not go back"
pump(300)
assert btn.isEnabled() and "najnowszą" in win.status.text() and S.restart is False, "no changes - no restart"
assert btn.label_text == "" and not win.update_ring.running(), "after the update the icon alone again"
win.update_progress.emit(50); pump(); assert btn.label_text == "", "a late signal after the end is ignored"
btn.click(); btn.click(); pump(500)
assert S.restart is True and closed and "Zaktualizowano" in btn.toolTip(), "new version -> receiver restart"
S.restart = False
ok("Update: confirmation, git pull in the background, message, restart only after a new version was fetched")

# ---------- versions and the sender update ----------
import rx_net
from proto import BODY, CMD_UPDATE, T_BUILD, T_CMD, UPD_DONE
assert win.version_lbl.text().startswith("wersja ") and win.version_lbl.toolTip()
rx_update._version = ("1111111", 1791700000, "Receiver")
def build(h, st):
    rx_net.on_build(rx_net.codec.pack(T_BUILD, 255, h, 1791600000, st), time.monotonic())
S.last_pkt = time.monotonic(); build(b"2222222", 0); win.slow_tick(); pump()
assert "2222222" in win.sender.text() and win.sender.property("level") == "warn" and "Aktualizuj" in win.sender.toolTip(), \
    "a sender version other than the receiver -> yellow, a hint"
build(b"1111111", 0); win.slow_tick(); pump()
assert win.sender.property("level") == "ok", "same version - green"
rx_update.update = lambda **_: rx_update.Result(True, True, "Zaktualizowano (1 zmian): 1111111 → 3333333")
rx_qt_update.UPDATE_RESTART_MS = 50
sent.clear(); S.restart = False; closed.clear(); btn.setEnabled(True)   # po poprzedniej aktualizacji czeka na restart
btn.click(); btn.click(); pump(150)
cmds = [p for p in sent if p[0] == T_CMD]
assert cmds and cmds[0][1] == 255 and BODY[T_CMD].unpack_from(cmds[0][3], 4)[0] == CMD_UPDATE, "update command to the sender"
assert S.restart is False, "the receiver waits for the sender result"
S.last_pkt = time.monotonic(); build(b"1111111", UPD_DONE); pump(500)
assert "nadajnik: zaktualizowany" in btn.toolTip() and S.restart is True, btn.toolTip()
S.restart = False
ok("version in the header, sender version (different = yellow), Update: command to the sender, waiting for its result")

# the sender does not finish: a countdown in the window, then a restart without it (its version shows later at "Nadajnik online")
rx_qt_update.UPDATE_WAIT_S = 1.0
S.last_pkt = time.monotonic(); build(b"1111111", 0)
sent.clear(); S.restart = False; closed.clear(); btn.setEnabled(True)
btn.click(); btn.click(); pump(300)
assert S.restart is False and "Czekam na nadajnik" in win.status.text(), win.status.text()
pump(1500)
assert S.restart is True and "jeszcze pobiera" in btn.toolTip(), btn.toolTip()
S.restart = False; rx_qt_update.UPDATE_WAIT_S = 20.0

# "no response": the pip stage gets its own limit, the git stage does not
def slow_update(stage):
    def run(**_):
        win.update_progress.emit(stage); time.sleep(0.7)
        return rx_update.Result(True, False, "Masz najnowszą wersję")
    return run
rx_qt_update.UPDATE_STUCK_S = 0.2
S.sender_build = None; S.last_pkt = 0.0      # no sender: the receiver result ends the update at once
S.restart = False; btn.setEnabled(True)
rx_update.update = slow_update(rx_update.PCT_BACKED_UP)
btn.click(); btn.click(); pump(450)
assert not btn.isEnabled() and "nie odpowiada" not in win.status.text(), "pip in progress - that is not a hung git"
pump(700)
rx_update.update = slow_update(rx_update.PCT_PULL)
btn.click(); btn.click(); pump(450)
assert btn.isEnabled() and "nie odpowiada (git)" in win.status.text(), win.status.text()
pump(700)
rx_qt_update.UPDATE_STUCK_S = 150.0
ok("Update: countdown while waiting for the sender; a hang counted from the last stage, pip has its own limit")
offs = []
rx_console.power_off = lambda why: offs.append(why) or True
rx_qt.session_end()
assert offs == ["wyłączanie Windows"], offs
import ctypes.wintypes
msg = ctypes.wintypes.MSG(message=rx_qt.WM_QUERYENDSESSION)
assert rx_qt.SessionEndFilter().nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
assert offs == ["wyłączanie Windows"] * 2, "WM_QUERYENDSESSION -> console mode OFF before Windows shuts down"
ok("the end of the Windows session turns console mode off")
# ---------- Settings -> Backups: list, backup now, restore (2 clicks) -> restart ----------
import rx_backup, rx_qt_console
rx_qt_console.RESTORE_RESTART_MS = 50
with open(C.SETTINGS_PATH, "w") as f: f.write('{"mouse": {"sens": 2.0}}')
dlg = win.settings_dlg; dlg.hide(); dlg.present(); pump()
dlg.tabs.setCurrentIndex(9); pump()
n0 = len(rx_backup.list_backups())
dlg._backup_now(); pump()
rows = [dlg.backup_rows.itemAt(k).widget() for k in range(dlg.backup_rows.count())]
assert len(rx_backup.list_backups()) == n0 + 1 and len(rows) == min(n0 + 1, rx_backup.KEEP) and "✓" in dlg.status.text()
with open(C.SETTINGS_PATH, "w") as f: f.write('{"mouse": {"sens": 3.0}}')
restore = next(b for b in rows[0].findChildren(QPushButton))
S.restart = False; closed.clear()
restore.click(); pump(); assert S.restart is False and json.load(open(C.SETTINGS_PATH))["mouse"]["sens"] == 3.0
restore.click(); pump(300)
assert json.load(open(C.SETTINGS_PATH))["mouse"]["sens"] == 2.0 and S.restart is True, "restore -> restart"
assert rx_backup.list_backups()[0].reason == "restore" and not dlg.save_btn.isEnabled(), "Zapisz zablokowany do restartu"
S.restart = False
ok("Settings -> Backups: backup now, restore after the second click, backup of the current ones, restart")
# ---------- pad connection -> window to the front, but not in a game ----------
import rx_winapi
fronts, real_front, real_fg = [], win.bring_to_front, rx_winapi.foreground_window
win.bring_to_front = lambda: fronts.append(1)
def set_want(states):
    for w, on in zip(S.want, states):
        w.set() if on else w.clear()
for fg, alive, want in (((1, "eldenring.exe"), [False, False], 0), ((1, "eldenring.exe"), [True, False], 0),
                        ((1, "explorer.exe"), [True, False], 0), ((1, "steam.exe"), [True, True], 1),
                        ((1, None), [False, False], 1), ((1, None), [True, False], 1),
                        ((0, None), [False, False], 1), ((0, None), [True, False], 2)):
    rx_winapi.foreground_window = lambda fg=fg: fg
    set_want(alive); win._pads_connected()
    assert len(fronts) == want, (fg, alive, fronts)
# a brief silence (stale_s): alive goes off and back, but the pad is still connected (want) - the window stays where it was
rx_winapi.foreground_window = lambda: (1, "steam.exe")
set_want([True, False]); win._pads_connected(); n = len(fronts)
S.alive[:] = [False, False]; win._pads_connected(); S.alive[:] = [True, False]; win._pads_connected()
assert len(fronts) == n, "a gap in the signal is not a new pad connection"
win.bring_to_front, rx_winapi.foreground_window = real_front, real_fg
set_want([False] * len(S.want)); S.alive[:] = [False] * len(S.alive); win._pads_connected()
ok("pad connection: window to the front on the desktop / in Steam, not in a game or with an unknown program; not after a gap")
print("SETTINGS GUI (MOUSE / MASTER) OK")
