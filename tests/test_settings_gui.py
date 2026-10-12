"""Okno ustawień: zakładki Mysz i Master (zapis bez restartu), korona mastera na karcie pada, ikony przycisków."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_master, rx_qt, rx_settings
from proto import T_IDENT
from rx_console import Console, ConsoleConfig
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)
A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
feed(T_IDENT, 0, A.encode()); feed(T_IDENT, 1, B.encode()); state(0); state(1)

win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 760); win.show(); pump(150)
os.makedirs(os.path.join(os.path.dirname(__file__)), exist_ok=True)
win.grab().save(os.path.join(os.path.dirname(__file__), "gui_master.png"))

# ---------- korona na karcie pada ----------
card0, card1 = win.cards
assert not rx_master.is_master(0) and card0.crown.isEnabled()
card1.crown.click(); pump()
assert rx_settings.cur.master.pad == B and rx_master.is_master(1)
assert json.load(open(C.SETTINGS_PATH))["master"]["pad"] == B, "korona zapisuje settings.json"
assert "zdjąć" in card1.crown.toolTip() and "zostaje" in card0.crown.toolTip()
card0.crown.click(); pump()
assert rx_settings.cur.master.pad == A
card0.crown.click(); pump()
assert rx_settings.cur.master.pad == "", "drugi klik na koronie mastera - bez mastera"
ok("korona: klik = master, klik na masterze = brak mastera, zapis do pliku")

# ---------- informacja o aktualizacji: pasek z opisem pod nagłówkiem, nie sam kolor ----------
import rx_update
import time as _t
assert [rx_qt.n_changes(n) for n in (1, 2, 5, 12, 22, 25)] == ["1 zmiana", "2 zmiany", "5 zmian", "12 zmian", "22 zmiany",
                                                               "25 zmian"]
win.slow_tick(); pump()
assert win.banner.isHidden(), "nic do zgłoszenia - bez paska"
rx_update.available = rx_update.Available(3, "Nowy tryb konsoli", "abc1234")
win.slow_tick(); pump()
assert win.banner.isVisible() and "Dostępna aktualizacja: 3 zmiany  -  Nowy tryb konsoli" in win.banner_text.text(), \
    win.banner_text.text()
assert win.banner_btn.isEnabled()
S.sender_build = ("0000000", 1_700_000_000, 0, _t.monotonic()); S.last_pkt = _t.monotonic()
rx_update._version = ("1111111", 1_700_000_100, "x")
win.online = None; win.slow_tick(); pump()
assert "Nadajnik ma inną wersję (0000000) niż receiver (1111111)" in win.banner_text.text(), win.banner_text.text()
win.banner.grab().save(os.path.join(os.path.dirname(__file__), "gui_update_banner.png"))
rx_update.available = rx_update.Available(0)
S.sender_build = ("1111111", 1_700_000_100, 0, _t.monotonic())
win.online = None; win.slow_tick(); pump()
assert win.banner.isHidden(), "aktualna wersja na GitHubie i ta sama w nadajniku - pasek znika"
S.sender_build, S.last_pkt, rx_update._version = None, 0.0, None         # dalsze testy: nadajnik offline
win.online = None; win.slow_tick()
ok("aktualizacja: pasek 'Dostępna aktualizacja: N zmian - tytuł' i 'Nadajnik ma inną wersję', przycisk Aktualizuj")

# ---------- zakładki ustawień ----------
win.open_settings(); pump(100)
dlg = win.settings_dlg
assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Tryb konsoli", "Mysz", "Master", "Okno", "Kopie"]
assert dlg.toggle_button == "r2" and "R2" in dlg.pick_name.text() and not dlg.pick_icon.pixmap().isNull()
assert dlg.auto_on.isChecked(), "domyślnie: tryb konsoli po połączeniu mastera"
assert not dlg.tabs.tabIcon(2).isNull(), "zakładki z ikonami"
dlg.tabs.setCurrentIndex(1); pump()
assert not dlg.test_btn.isVisible(), "Przetestuj tylko na zakładce trybu konsoli"
dlg.sens.slider.setValue(dlg.sens.slider.maximum()); dlg.freeze.set(250); dlg.rewind.set(0); dlg.drag.set(100)
dlg.tabs.setCurrentIndex(2); pump()
dlg.grab().save(os.path.join(os.path.dirname(__file__), "gui_settings_master.png"))
pads = [dlg.master_pad.itemData(i) for i in range(dlg.master_pad.count())]
assert pads == ["", A, B], pads
dlg.master_pad.set_data(B); dlg.master_pad.activated.emit(dlg.master_pad.currentIndex())   # wybór użytkownika
dlg.features["ps_game"].setChecked(True); dlg.features["console"].setChecked(False)
dlg.absent.set_data("nobody"); dlg.claim.setChecked(False)
dlg.actions["sleep"].set_data("square")
icons = [dlg.actions["sleep"].itemIcon(i).isNull() for i in range(1, dlg.actions["sleep"].count())]
assert not any(icons), "lista przycisków akcji to ikony"
assert dlg.actions["sleep"].toolTip() == "Kwadrat", dlg.actions["sleep"].toolTip()

S.restart = False; closed = []
QApplication.instance().quit = lambda: closed.append(True)
dlg.save_btn.click(); pump()
m, mo = rx_settings.cur.master, rx_settings.cur.mouse
assert m.pad == B and m.when_absent == "nobody" and not m.claim_hold and "ps_game" in m.only_master and "console" not in m.only_master
assert m.actions["sleep"] == "square" and mo.sens == 5.0 and mo.click_freeze_ms == 250 and mo.click_rewind_ms == 0 and mo.drag_threshold == 100
assert M.sens == [5.0] * C.NUM_PADS, "czułość działa od razu"
assert S.restart is False and not closed, "mysz / master bez restartu (tryb konsoli bez zmian)"
assert "Zapisano" in dlg.status.text()
saved = json.load(open(C.SETTINGS_PATH))
assert saved["mouse"]["click_freeze_ms"] == 250 and saved["master"]["only_master"] == [k for k in rx_settings.MASTER_FEATURES if k in m.only_master]
ok("zapis myszy i mastera: działa od razu, bez restartu, settings.json")

# ---------- ten sam przycisk w dwóch akcjach ----------
dlg.actions["pause"].set_data("square"); dlg.tabs.setCurrentIndex(0)
dlg.save_btn.click(); pump()
assert dlg.tabs.currentIndex() == 2 and dlg.status.property("level") == "bad"
assert rx_settings.cur.master.actions["pause"] != "square", "błędne ustawienia nie zostały zastosowane"
dlg.actions["pause"].set_data("options")
ok("dwie akcje na jednym przycisku: komunikat, nic nie zapisane")

# ---------- zmiana trybu konsoli dalej restartuje ----------
dlg.set_button("cross"); dlg.save_btn.click(); pump()
assert S.restart is True and closed and json.load(open(C.CONSOLE_PATH))["toggle_button"] == "cross"
S.restart = False
ok("zmiana trybu konsoli: zapis + restart jak wcześniej")

# ---------- ikony w remapowaniu ----------
win.on_part("r2"); pump()
assert win.part_page.title.text() == "R2 (spust)" and not win.part_page.title_icon.pixmap().isNull()
targets = [win.part_page.dst.itemData(i) for i in range(win.part_page.dst.count())]
assert "mute" not in targets and "ps" in targets and len(targets) == 18, targets
assert win.part_page.dst.itemText(0) == "Krzyżyk (X)" and not win.part_page.dst.itemIcon(0).isNull(), "cele: ikona + nazwa"
win.grab().save(os.path.join(os.path.dirname(__file__), "gui_master2.png"))
ok("remap: tytuł z ikoną, cele z ikoną i nazwą")
# ---------- ekran okna ----------
dlg.present(); pump()
scr = [dlg.screen_box.itemData(i) for i in range(dlg.screen_box.count())]
names = [k for k, _ in rx_qt.screens()]
assert scr == ["", *names], (scr, names)
dlg.screen_box.set_data(names[0]); dlg.tabs.setCurrentIndex(3)
dlg.save_btn.click(); pump()
assert win.screen_choice() == names[0] and win.settings.value("screen") == names[0], "wybrany ekran zapisany"
win.move(-5000, -5000); win.place_on_screen()
assert QApplication.screens()[0].availableGeometry().contains(win.frameGeometry().center()), "okno przeniesione na wybrany ekran"
win.set_screen_choice("NIE-MA-TAKIEGO"); dlg.hide(); dlg.present(); pump()
assert dlg.screen_box.currentData() == "NIE-MA-TAKIEGO" and "niepodłączony" in dlg.screen_box.currentText()
assert win.place_on_screen() is False, "brak wybranego ekranu -> okno zostaje"
win.set_screen_choice("")
ok("ekran okna: lista ekranów, zapis, przeniesienie, brak ekranu")

# ---------- pełny ekran, język, dozwolone programy, test wibracji ----------
assert not win.fullscreen()
win.toggle_fullscreen(); pump(100)
assert win.fullscreen() and win.isFullScreen() and win.settings.value("fullscreen") in (True, "true")
win.fs_btn.click(); pump(100)
assert not win.fullscreen() and not win.isFullScreen(), "F11 / przycisk: z powrotem okno"
dlg.hide(); dlg.present(); pump()
assert dlg.win_mode.currentData() == "window" and dlg.lang.currentData() == "pl"
dlg.win_mode.set_data("full"); dlg.save_btn.click(); pump(100)
assert win.fullscreen() and win.isFullScreen(), "Ustawienia -> Okno -> Pełny ekran"
win.set_fullscreen(False); pump(100)
assert "steam.exe" in dlg.apps.toPlainText().split()
dlg.hide(); dlg.present(); pump()
dlg.apps.setPlainText("Steam.exe\n  Moja Gra.exe \n\nsteam.exe")
S.restart = False; closed.clear()
dlg.save_btn.click(); pump()
assert S.restart is True and json.load(open(C.CONSOLE_PATH))["allowed_apps"] == ["steam.exe", "moja gra.exe"], \
    "lista programów: małe litery, bez pustych i powtórek, nazwy ze spacją; zmiana = restart"
S.restart = False; closed.clear()
rx_console.ctl.cfg = rx_console.load(C.CONSOLE_PATH)       # jak po restarcie
dlg.hide(); dlg.present(); pump()
dlg.lang.set_data("en"); dlg.save_btn.click(); pump()
assert S.restart is True and json.load(open(C.SETTINGS_PATH))["ui"]["lang"] == "en", "zmiana języka = zapis + restart"
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
ok("pełny ekran (F11, przycisk, ustawienia), język (restart, okno po angielsku), lista programów, test wibracji")

# ---------- przycisk Restart w nagłówku i ikona okna ----------
from PySide6.QtWidgets import QPushButton
btn = next(b for b in win.findChildren(QPushButton) if b.toolTip().startswith("Restart"))
S.restart = False; closed.clear()
btn.click(); pump()
assert S.restart is False and btn.text() == "Restart?", "pierwszy klik tylko uzbraja (ikona pokazuje pytanie)"
btn.click(); pump()
assert S.restart is True and closed, "drugi klik: restart receivera"
S.restart = False
assert not QApplication.instance().windowIcon().isNull() and len(win.windowIcon().availableSizes()) >= 4, "ikona pada w kilku rozmiarach"
ok("Restart: dwa kliknięcia, restart receivera; ikona aplikacji ustawiona")

# ---------- Aktualizuj: git pull w tle, komunikat, restart po pobraniu ----------
import rx_update
rx_qt.UPDATE_RESTART_MS = 50
results = iter([rx_update.Result(True, False, "Masz najnowszą wersję"),
                rx_update.Result(True, True, "Zaktualizowano (2 zmian): aaaaaaa → bbbbbbb", "a", "b")])
rx_update.update = lambda: (time.sleep(0.1), next(results))[1]
import time
btn = win.update_btn
assert btn.text() == "" and btn.toolTip().startswith("Aktualizuj") and not btn.icon().isNull(), "sama ikona"
S.restart = False; closed.clear()
btn.click(); btn.click(); pump(20)
assert not btn.isEnabled() and btn.text() == "Aktualizuję…", "w trakcie: przycisk zablokowany"
pump(300)
assert btn.isEnabled() and "najnowszą" in win.status.text() and S.restart is False, "brak zmian - bez restartu"
btn.click(); btn.click(); pump(500)
assert S.restart is True and closed and "Zaktualizowano" in btn.toolTip(), "nowa wersja -> restart receivera"
S.restart = False
ok("Aktualizuj: potwierdzenie, git pull w tle, komunikat, restart tylko po pobraniu nowej wersji")

# ---------- wersje i aktualizacja nadajnika ----------
import rx_net
from proto import BODY, CMD_UPDATE, T_BUILD, T_CMD, UPD_DONE
assert win.version_lbl.text().startswith("wersja ") and win.version_lbl.toolTip()
rx_update._version = ("1111111", 1791700000, "Receiver")
def build(h, st):
    rx_net.on_build(rx_net.codec.pack(T_BUILD, 255, h, 1791600000, st), time.monotonic())
S.last_pkt = time.monotonic(); build(b"2222222", 0); win.slow_tick(); pump()
assert "2222222" in win.sender.text() and win.sender.property("level") == "warn" and "Aktualizuj" in win.sender.toolTip(), \
    "inna wersja nadajnika niż receivera -> żółty, podpowiedź"
build(b"1111111", 0); win.slow_tick(); pump()
assert win.sender.property("level") == "ok", "ta sama wersja - zielony"
rx_update.update = lambda: rx_update.Result(True, True, "Zaktualizowano (1 zmian): 1111111 → 3333333")
rx_qt.UPDATE_RESTART_MS = 50
sent.clear(); S.restart = False; closed.clear(); btn.setEnabled(True)   # po poprzedniej aktualizacji czeka na restart
btn.click(); btn.click(); pump(150)
cmds = [p for p in sent if p[0] == T_CMD]
assert cmds and cmds[0][1] == 255 and BODY[T_CMD].unpack_from(cmds[0][3], 4)[0] == CMD_UPDATE, "polecenie aktualizacji do nadajnika"
assert S.restart is False, "receiver czeka na wynik nadajnika"
S.last_pkt = time.monotonic(); build(b"1111111", UPD_DONE); pump(500)
assert "nadajnik: zaktualizowany" in btn.toolTip() and S.restart is True, btn.toolTip()
S.restart = False
ok("wersja w nagłówku, wersja nadajnika (inna = żółty), Aktualizuj: polecenie do nadajnika, czekanie na jego wynik")
offs = []
rx_console.power_off = lambda why: offs.append(why) or True
rx_qt.session_end()
assert offs == ["wyłączanie Windows"], offs
import ctypes.wintypes
msg = ctypes.wintypes.MSG(message=rx_qt.WM_QUERYENDSESSION)
assert rx_qt.SessionEndFilter().nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
assert offs == ["wyłączanie Windows"] * 2, "WM_QUERYENDSESSION -> tryb konsoli OFF przed wyłączeniem Windows"
ok("koniec sesji Windows wyłącza tryb konsoli")
# ---------- Ustawienia -> Kopie: lista, kopia teraz, przywrócenie (2 kliknięcia) -> restart ----------
import rx_backup, rx_qt_console
rx_qt_console.RESTORE_RESTART_MS = 50
with open(C.SETTINGS_PATH, "w") as f: f.write('{"mouse": {"sens": 2.0}}')
dlg = win.settings_dlg; dlg.hide(); dlg.present(); pump()
dlg.tabs.setCurrentIndex(4); pump()
n0 = len(rx_backup.list_backups())
dlg._backup_now(); pump()
rows = [dlg.backup_rows.itemAt(k).widget() for k in range(dlg.backup_rows.count())]
assert len(rx_backup.list_backups()) == n0 + 1 and len(rows) == min(n0 + 1, rx_backup.KEEP) and "✓" in dlg.status.text()
with open(C.SETTINGS_PATH, "w") as f: f.write('{"mouse": {"sens": 3.0}}')
restore = next(b for b in rows[0].findChildren(QPushButton))
S.restart = False; closed.clear()
restore.click(); pump(); assert S.restart is False and json.load(open(C.SETTINGS_PATH))["mouse"]["sens"] == 3.0
restore.click(); pump(300)
assert json.load(open(C.SETTINGS_PATH))["mouse"]["sens"] == 2.0 and S.restart is True, "przywrócenie -> restart"
assert rx_backup.list_backups()[0].reason == "restore" and not dlg.save_btn.isEnabled(), "Zapisz zablokowany do restartu"
S.restart = False
ok("Ustawienia -> Kopie: kopia teraz, przywrócenie po drugim kliknięciu, kopia bieżących, restart")
print("GUI USTAWIEŃ (MYSZ / MASTER) OK")
