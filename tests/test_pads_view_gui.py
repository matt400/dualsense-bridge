"""Window: which pad cards are visible (dynamic / fixed count), the foldable pad column, vertical settings tabs, the gyro aim preview."""
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
from chk import ok
pump = lambda ms=40: QTest.qWait(ms)


def ui(**kw):
    rx_settings.apply(replace(rx_settings.cur, ui=replace(rx_settings.cur.ui, **kw)))


# ---------- UiConfig.slots: czysta logika ----------
dyn, fix = UiConfig(pads_mode="dynamic", pads_count=2), UiConfig(pads_mode="fixed", pads_count=2)
assert dyn.slots([True, False, True, False]) == [0, 2] and dyn.slots([False] * 4) == []
assert fix.slots([False] * 4) == [0, 1], "fixed: the first N slots, disconnected ones too"
assert fix.slots([False, False, False, True]) == [0, 1, 3], "a connected pad beyond N is visible too"
assert UiConfig(pads_mode="fixed", pads_count=4).slots([False] * 4) == [0, 1, 2, 3]
ok("UiConfig.slots: dynamic = only connected, fixed = the first N + connected beyond N")

# ---------- validation and saving settings.json ----------
path = os.path.join(TMP, "ui_settings.json")
json.dump({"ui": {"pads_mode": "fixed", "pads_count": 3}}, open(path, "w"))
loaded = rx_settings.load(path).ui
assert (loaded.pads_mode, loaded.pads_count) == ("fixed", 3)
json.dump({"ui": {"pads_mode": "zawsze", "pads_count": 9}}, open(path, "w"))
bad = rx_settings.load(path).ui
assert (bad.pads_mode, bad.pads_count) == ("dynamic", 2), "bad value -> default"
rx_settings.save(replace(rx_settings.defaults(), ui=UiConfig(pads_mode="fixed", pads_count=4)), path)
assert json.load(open(path))["ui"] == {"lang": "pl", "pads_mode": "fixed", "pads_count": 4}
ok("settings.json: pad mode and count (write, read, bad value = default)")

# ---------- window: cards ----------
rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
feed(T_IDENT, 0, b"aa:aa:aa:aa:aa:01"); state(0)
ini = os.path.join(TMP, "ui.ini")
win = rx_qt.create_window(ini); win.resize(1180, 760); win.show(); pump(120)
ui(pads_mode="dynamic"); win.slow_tick(); pump()
c0, c1 = win.cards
assert c0.isVisible() and not c1.isVisible(), "dynamic: disconnected pad 2 hidden"
assert not win.pads_empty.isVisible()
feed(T_IDENT, 1, b"bb:bb:bb:bb:bb:02"); state(1); win.slow_tick(); pump()
assert c0.isVisible() and c1.isVisible(), "connected pad 2 shows up"
S.want[0].clear(); S.alive[0] = False; S.want[1].clear(); S.alive[1] = False
win.slow_tick(); pump()
assert not c0.isVisible() and not c1.isVisible() and win.pads_empty.isVisible(), "no pad: a message instead of cards"
ui(pads_mode="fixed", pads_count=2); win.slow_tick(); pump()
assert c0.isVisible() and c1.isVisible() and not win.pads_empty.isVisible(), "fixed count: cards visible without pads"
ui(pads_mode="fixed", pads_count=1); win.slow_tick(); pump()
assert c0.isVisible() and not c1.isVisible()
ok("pad column: dynamic only connected, fixed count always, a message when empty")

# ---------- zwijanie kolumny ----------
ui(pads_mode="fixed", pads_count=2); win.slow_tick(); pump()
wide = win.left_col.width()
win.pads_toggle.click(); pump()
assert win.pads_collapsed and win.left_col.width() < wide // 2 and not c0.isVisible() and not win.pads_title.isVisible()
assert win.minis[0].isVisible() and win.minis[1].isVisible() and "Rozwiń" in win.pads_toggle.toolTip()
win.minis[1].click(); pump()
assert win.ed.slot == 1 and win.minis[1].property("primary") is True and win.minis[0].property("primary") is False
assert QSettings(ini, QSettings.Format.IniFormat).value("pads_collapsed") in (True, "true"), "state stored in receiver_ui.ini"
ui(pads_mode="dynamic"); win.slow_tick(); pump()
assert not win.minis[0].isVisible() and not win.minis[1].isVisible(), "folded + dynamic + no pads: an empty bar"
win.pads_toggle.click(); pump()
assert not win.pads_collapsed and win.left_col.width() == wide and win.pads_title.isVisible()
win.close(); pump()
win2 = rx_qt.create_window(ini)
assert not win2.pads_collapsed, "unfolded column remembered after a restart"
win2.pads_toggle.click(); win2.save_settings()
win3 = rx_qt.create_window(ini)
assert win3.pads_collapsed and win3.left_col.width() < wide // 2, "folded column remembered after a restart"
ok("folding: a bar with pad numbers, a click picks the pad, state remembered")

# ---------- settings: vertical tabs and pad mode ----------
win.show(); pump(100)
win.open_settings(); dlg = win.settings_dlg; pump()
tabs = dlg.tabs
assert tabs.count() == 11 and tabs.tabText(6) == "Pady"
btns = tabs._buttons
assert len({b.x() for b in btns}) == 1 and all(a.y() < b.y() for a, b in zip(btns, btns[1:])), "tabs one under another on the left"
assert btns[0].x() < tabs._stack.x(), "tabs left of the content"
assert all(not b.text().startswith(" ") and b.text() for b in btns)
tabs.setCurrentIndex(6); pump()
assert tabs.currentIndex() == 6 and btns[6].isChecked() and not btns[0].isChecked()
assert dlg.size().width() > 860 or dlg.minimumWidth() >= 720, "the settings window is wider than before"
ui(pads_mode="dynamic", pads_count=2); dlg.reload(); pump()
assert dlg.pads_mode.currentData() == "dynamic" and not dlg.pads_count_row.isVisibleTo(dlg)
dlg.pads_mode.set_data("fixed"); dlg._pads_view_state()
assert dlg.pads_count_row.isVisibleTo(dlg), "the pad count asked only in fixed mode"
dlg.pads_count.setValue(2)
dlg.save_btn.click(); pump()
assert (rx_settings.cur.ui.pads_mode, rx_settings.cur.ui.pads_count) == ("fixed", 2) and rx_settings.cur.ui.lang == "pl"
assert json.load(open(C.SETTINGS_PATH))["ui"]["pads_mode"] == "fixed", "saved to settings.json"
win.slow_tick(); pump()
assert win.cards[1].isVisible() or win.pads_collapsed, "the change works at once, without a restart"
ok("settings: vertical tabs on the left, pad mode saved and applied at once")

# ---------- gyro aim preview ----------
win = rx_qt.create_window(os.path.join(TMP, "ui2.ini")); win.resize(1180, 760); win.show(); pump(120)
feed(T_IDENT, 0, b"aa:aa:aa:aa:aa:01")
win.tabs.setCurrentIndex(2); pump()                               # Gyro tab
win.gyro_page.aim_cat.set_open(False)
win.ed.set_gyro(mode="stick", sens=1.0, anti=0.2); win.refresh_all()
state(0, gyro=(0, -512, 0)); win.live_tick(); pump()
assert not win.gyro_preview.isVisible(), "section folded - no preview"
win.gyro_page.aim_cat.set_open(True); win.live_tick(); pump()
gp = win.gyro_preview
assert gp.isVisible() and gp.parent() is win.pad, "the preview above the pad drawing, like the tilt preview"
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
    "both previews open: tilt below aim, they do not overlap"
win.gyro_page.aim_cat.set_open(False); win.live_tick(); pump()
assert not gp.isVisible() and tp.isVisible() and tp.y_offset == 0 and tp.pos().y() == tp.gap
win.tabs.setCurrentIndex(0); win.live_tick(); pump()
assert not tp.isVisible() and not gp.isVisible(), "another tab - no previews"
ok("gyro preview: only with the section unfolded, stick / mouse / off modes, the two previews do not overlap")
print("PADS VIEW GUI OK")
