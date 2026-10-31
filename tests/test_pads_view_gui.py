"""Okno: które karty padów widać (dynamicznie / stała liczba), zwijana kolumna padów, pionowe zakładki ustawień, podgląd celowania gyro."""
import sys, os, json
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest
from PySide6.QtCore import QSettings

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_qt, rx_settings
from proto import T_IDENT
from rx_console import Console, ConsoleConfig
from rx_settings import UiConfig
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)


def ui(**kw):
    rx_settings.apply(replace(rx_settings.cur, ui=replace(rx_settings.cur.ui, **kw)))


# ---------- UiConfig.slots: czysta logika ----------
dyn, fix = UiConfig(pads_mode="dynamic", pads_count=2), UiConfig(pads_mode="fixed", pads_count=2)
assert dyn.slots([True, False, True, False]) == [0, 2] and dyn.slots([False] * 4) == []
assert fix.slots([False] * 4) == [0, 1], "stała: pierwsze N slotów, także rozłączone"
assert fix.slots([False, False, False, True]) == [0, 1, 3], "podłączony pad spoza N też widać"
assert UiConfig(pads_mode="fixed", pads_count=4).slots([False] * 4) == [0, 1, 2, 3]
ok("UiConfig.slots: dynamiczna = tylko podłączone, stała = pierwsze N + podłączone spoza N")

# ---------- walidacja i zapis settings.json ----------
path = os.path.join(TMP, "ui_settings.json")
json.dump({"ui": {"pads_mode": "fixed", "pads_count": 3}}, open(path, "w"))
loaded = rx_settings.load(path).ui
assert (loaded.pads_mode, loaded.pads_count) == ("fixed", 3)
json.dump({"ui": {"pads_mode": "zawsze", "pads_count": 9}}, open(path, "w"))
bad = rx_settings.load(path).ui
assert (bad.pads_mode, bad.pads_count) == ("dynamic", 2), "zła wartość -> domyślna"
rx_settings.save(replace(rx_settings.defaults(), ui=UiConfig(pads_mode="fixed", pads_count=4)), path)
assert json.load(open(path))["ui"] == {"lang": "pl", "pads_mode": "fixed", "pads_count": 4}
ok("settings.json: tryb i liczba padów (zapis, odczyt, zła wartość = domyślna)")

# ---------- okno: karty ----------
rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
feed(T_IDENT, 0, b"aa:aa:aa:aa:aa:01"); state(0)
ini = os.path.join(TMP, "ui.ini")
win = rx_qt.create_window(ini); win.resize(1180, 760); win.show(); pump(120)
ui(pads_mode="dynamic"); win.slow_tick(); pump()
c0, c1 = win.cards
assert c0.isVisible() and not c1.isVisible(), "dynamicznie: niepodłączony pad 2 ukryty"
assert not win.pads_empty.isVisible()
feed(T_IDENT, 1, b"bb:bb:bb:bb:bb:02"); state(1); win.slow_tick(); pump()
assert c0.isVisible() and c1.isVisible(), "podłączony pad 2 się pojawia"
S.want[0].clear(); S.alive[0] = False; S.want[1].clear(); S.alive[1] = False
win.slow_tick(); pump()
assert not c0.isVisible() and not c1.isVisible() and win.pads_empty.isVisible(), "żaden pad: komunikat zamiast kart"
ui(pads_mode="fixed", pads_count=2); win.slow_tick(); pump()
assert c0.isVisible() and c1.isVisible() and not win.pads_empty.isVisible(), "stała liczba: karty widać bez padów"
ui(pads_mode="fixed", pads_count=1); win.slow_tick(); pump()
assert c0.isVisible() and not c1.isVisible()
ok("kolumna padów: dynamicznie tylko podłączone, stała liczba zawsze, komunikat gdy pusto")

# ---------- zwijanie kolumny ----------
ui(pads_mode="fixed", pads_count=2); win.slow_tick(); pump()
wide = win.left_col.width()
win.pads_toggle.click(); pump()
assert win.pads_collapsed and win.left_col.width() < wide // 2 and not c0.isVisible() and not win.pads_title.isVisible()
assert win.minis[0].isVisible() and win.minis[1].isVisible() and "Rozwiń" in win.pads_toggle.toolTip()
win.minis[1].click(); pump()
assert win.ed.slot == 1 and win.minis[1].property("primary") is True and win.minis[0].property("primary") is False
assert QSettings(ini, QSettings.Format.IniFormat).value("pads_collapsed") in (True, "true"), "stan zapisany w receiver_ui.ini"
ui(pads_mode="dynamic"); win.slow_tick(); pump()
assert not win.minis[0].isVisible() and not win.minis[1].isVisible(), "zwinięta + dynamicznie + brak padów: pusty pasek"
win.pads_toggle.click(); pump()
assert not win.pads_collapsed and win.left_col.width() == wide and win.pads_title.isVisible()
win.close(); pump()
win2 = rx_qt.create_window(ini)
assert not win2.pads_collapsed, "rozwinięta kolumna zapamiętana po restarcie"
win2.pads_toggle.click(); win2.save_settings()
win3 = rx_qt.create_window(ini)
assert win3.pads_collapsed and win3.left_col.width() < wide // 2, "zwinięta kolumna zapamiętana po restarcie"
ok("zwijanie: pasek z numerami padów, klik wybiera pad, stan zapamiętany")

# ---------- ustawienia: pionowe zakładki i tryb padów ----------
win.show(); pump(100)
win.open_settings(); dlg = win.settings_dlg; pump()
tabs = dlg.tabs
assert tabs.count() == 11 and tabs.tabText(6) == "Pady"
btns = tabs._buttons
assert len({b.x() for b in btns}) == 1 and all(a.y() < b.y() for a, b in zip(btns, btns[1:])), "zakładki jedna pod drugą po lewej"
assert btns[0].x() < tabs._stack.x(), "zakładki po lewej stronie treści"
assert all(not b.text().startswith(" ") and b.text() for b in btns)
tabs.setCurrentIndex(6); pump()
assert tabs.currentIndex() == 6 and btns[6].isChecked() and not btns[0].isChecked()
assert dlg.size().width() > 860 or dlg.minimumWidth() >= 720, "okno ustawień szersze niż wcześniej"
ui(pads_mode="dynamic", pads_count=2); dlg.reload(); pump()
assert dlg.pads_mode.currentData() == "dynamic" and not dlg.pads_count_row.isVisibleTo(dlg)
dlg.pads_mode.set_data("fixed"); dlg._pads_view_state()
assert dlg.pads_count_row.isVisibleTo(dlg), "liczba padów pytana tylko w trybie stałym"
dlg.pads_count.setValue(2)
dlg.save_btn.click(); pump()
assert (rx_settings.cur.ui.pads_mode, rx_settings.cur.ui.pads_count) == ("fixed", 2) and rx_settings.cur.ui.lang == "pl"
assert json.load(open(C.SETTINGS_PATH))["ui"]["pads_mode"] == "fixed", "zapis do settings.json"
win.slow_tick(); pump()
assert win.cards[1].isVisible() or win.pads_collapsed, "zmiana działa od razu, bez restartu"
ok("ustawienia: zakładki w pionie po lewej, tryb padów zapisany i zastosowany od razu")

# ---------- podgląd celowania gyro ----------
win = rx_qt.create_window(os.path.join(TMP, "ui2.ini")); win.resize(1180, 760); win.show(); pump(120)
feed(T_IDENT, 0, b"aa:aa:aa:aa:aa:01")
win.tabs.setCurrentIndex(2); pump()                               # zakładka Gyro
win.gyro_page.aim_cat.set_open(False)
win.ed.set_gyro(mode="stick", sens=1.0, anti=0.2); win.refresh_all()
state(0, gyro=(0, -512, 0)); win.live_tick(); pump()
assert not win.gyro_preview.isVisible(), "sekcja zwinięta - bez podglądu"
win.gyro_page.aim_cat.set_open(True); win.live_tick(); pump()
gp = win.gyro_preview
assert gp.isVisible() and gp.parent() is win.pad, "podgląd nad rysunkiem pada, jak podgląd przechyłu"
assert (gp.pos().x() + gp.width()) <= win.pad.width() and gp.pos().y() == gp.gap
smp = gp.view.sample
assert smp.stick_mode and smp.anti == 0.2 and smp.out is not None and smp.rate[0] > 0, smp
assert "Rotation" in gp.numbers.text() or "Obrót" in gp.numbers.text()
assert "Prawa gałka" in gp.numbers.text() or "Right stick" in gp.numbers.text()
win.ed.set_gyro(mode="mouse"); win.refresh_all(); win.live_tick(); pump()
assert gp.view.sample.out is None and ("Kursor" in gp.numbers.text() or "Cursor" in gp.numbers.text())
win.ed.set_gyro(mode="off"); win.refresh_all(); win.live_tick(); pump()
assert gp.isVisible() and ("wyłączone" in gp.status.text() or "off" in gp.status.text()), gp.status.text()
tilt_cat = win.gyro_page.tilt.cats[0]
tilt_cat.set_open(True); win.live_tick(); pump()
tp = win.tilt_preview
assert tp.isVisible() and tp.y_offset == gp.height() + gp.gap and tp.pos().y() >= gp.pos().y() + gp.height(), \
    "oba podglądy otwarte: przechył pod celowaniem, nie nakładają się"
win.gyro_page.aim_cat.set_open(False); win.live_tick(); pump()
assert not gp.isVisible() and tp.isVisible() and tp.y_offset == 0 and tp.pos().y() == tp.gap
win.tabs.setCurrentIndex(0); win.live_tick(); pump()
assert not tp.isVisible() and not gp.isVisible(), "inna zakładka - bez podglądów"
ok("podgląd gyro: tylko przy rozwiniętej sekcji, tryby stick / mysz / wyłączone, oba podglądy się nie nakładają")
print("PADS VIEW GUI OK")
