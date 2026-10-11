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

# ---------- zakładki ustawień ----------
win.open_settings(); pump(100)
dlg = win.settings_dlg
assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Tryb konsoli", "Mysz", "Master", "Okno"]
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
dlg.master_pad.set_data(B)
dlg.features["ps_game"].setChecked(True); dlg.features["console"].setChecked(False)
dlg.absent.set_data("nobody"); dlg.claim.setChecked(False)
dlg.actions["sleep"].set_data("square")
icons = [dlg.actions["sleep"].itemIcon(i).isNull() for i in range(1, dlg.actions["sleep"].count())]
assert not any(icons), "lista przycisków akcji to ikony"
assert dlg.actions["sleep"].toolTip() == "Square", dlg.actions["sleep"].toolTip()

S.restart = False; closed = []
QApplication.instance().quit = lambda: closed.append(True)
dlg.save_btn.click(); pump()
m, mo = rx_settings.cur.master, rx_settings.cur.mouse
assert m.pad == B and m.when_absent == "nobody" and not m.claim_hold and "ps_game" in m.only_master and "console" not in m.only_master
assert m.actions["sleep"] == "square" and mo.sens == 3.0 and mo.click_freeze_ms == 250 and mo.click_rewind_ms == 0 and mo.drag_threshold == 100
assert M.sens == [3.0] * C.NUM_PADS, "czułość działa od razu"
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
assert win.part_page.title.text() == "R2 (trigger)" and not win.part_page.title_icon.pixmap().isNull()
targets = [win.part_page.dst.itemData(i) for i in range(win.part_page.dst.count())]
assert "mute" not in targets and "ps" in targets and len(targets) == 18, targets
assert win.part_page.dst.itemText(0) == "" and not win.part_page.dst.itemIcon(0).isNull(), "cele jako ikony"
win.grab().save(os.path.join(os.path.dirname(__file__), "gui_master2.png"))
ok("remap: tytuł z ikoną, cele jako ikony z podpowiedzią")
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

# ---------- przycisk Restart w nagłówku i ikona okna ----------
from PySide6.QtWidgets import QPushButton
btn = next(b for b in win.findChildren(QPushButton) if b.text() == "Restart")
S.restart = False; closed.clear()
btn.click(); pump()
assert S.restart is False and btn.text() == "Restart?", "pierwszy klik tylko uzbraja"
btn.click(); pump()
assert S.restart is True and closed, "drugi klik: restart receivera"
S.restart = False
assert not QApplication.instance().windowIcon().isNull() and len(win.windowIcon().availableSizes()) >= 4, "ikona pada w kilku rozmiarach"
ok("Restart: dwa kliknięcia, restart receivera; ikona aplikacji ustawiona")
print("GUI USTAWIEŃ (MYSZ / MASTER) OK")
