import sys, os, time, json
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")            # no screen and no Xvfb
from rxenv import *                                              # noqa
import rx_i18n; rx_i18n.set_lang("en")              # assertions on the English window texts
from proto import T_CMD
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QToolButton

C.cfg["tray"] = False
import rx_editor, rx_games, rx_qt
win = rx_qt.create_window(os.path.join(TMP, "ui.ini"))
app = QApplication.instance()
win.resize(1160, 720); win.show()
from chk import ok
pump = lambda ms=60: QTest.qWait(ms)
base = S.profile_key(0)
pump(150)


def click_part(name):
    """A real mouse click in the middle of a part of the pad drawing."""
    part = win.pad.by_name[name]
    p = win.pad._tf.map(part.path.boundingRect().center())
    QTest.mouseClick(win.pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(p.x()), round(p.y())))
    pump(20)


# ---------- every part of the drawing is clickable and hits the right name ----------
for part in win.pad.parts:
    p = win.pad._tf.map(part.path.boundingRect().center())
    assert win.pad._hit(QPointF(p)) == part.name, part.name
assert win.pad._hit(QPointF(2, 2)) is None
ok("pad drawing: every part hit by a click, the background not")

# ---------- pad cards: link, battery, game profile, power-off ----------
S.alive[0] = True; S.batt_info[0] = (64, 0); S.batt_info[1] = (12, 0)
S.info[0] = (1.2, 540, 500, time.monotonic()); S.link_loss = 0.0; S.pps[0] = 250.0
S.last_pkt = time.monotonic(); S.peer = ("192.168.1.50", 5005)
store.save(base + "@doom.exe", {}); S.set_game("doom.exe")
import rx_settings
rx_settings.apply(replace(rx_settings.cur, ui=replace(rx_settings.cur.ui, pads_mode="fixed", pads_count=2)))   # the card of a disconnected pad is visible too
win.slow_tick(); pump(20)
c0, c1 = win.cards
assert c0.lat.text() == "ping 1.2 ms" and not c0.lat_bar.isVisible(), "no latency measurement: ping"
tip = c0.lat.toolTip()
assert "loss 0.0%" in tip and "pad 540 Hz" in tip and "250 pkt/s" in tip and "older version" in tip, tip
assert c0.profile.text().startswith("profile: doom.exe"), c0.profile.text()
assert c0.pct.text() == "64%" and c1.pct.text() == "12%" and "connected" in c0.status.text() and "not connected" in c1.status.text()
assert c0.off.isEnabled() and not c1.off.isEnabled() and not c1.body.isVisible() and c1.property("off") is True
assert not c0.status.isVisible() and c1.status.isVisible(), "status only for a disconnected pad / no signal"
# pad -> game latency measurement: median on the card, segments on the bar and in the tooltip
import rx_latency
from proto import LAT_NONE
t_ns = time.perf_counter_ns()
for k in range(20):
    rx_latency.add(0, (t_ns - 3_000_000, 1500, LAT_NONE if k % 2 else 400, 600), t_ns - 2_000_000 + k)
win.slow_tick(); pump(20)
assert c0.lat.text().endswith(" ms") and c0.lat_stats.text().startswith("usually ≤ ") and " · max " in c0.lat_stats.text() \
    and c0.lat_bar.isVisible(), (c0.lat.text(), c0.lat_stats.text())
assert [col for _v, col in c0.lat_bar.parts] == [rx_qt.SEGMENT[k] for k in ("bt", "phone", "net", "pc")], c0.lat_bar.parts
assert "Phone (kernel → send)" in c0.lat.toolTip() and "Network" in c0.lat.toolTip() and "average" in c0.lat.toolTip()
assert not c0.btn_row.isVisible(), "no presses - no button -> game row"
rx_latency.add_button(0, (t_ns - 3_000_000, 1000, 200, 500), t_ns - 2_000_000)
win.slow_tick(); pump(20)
assert c0.btn_row.isVisible() and c0.btn_lat.text() == "2.7 ms" and c0.btn_stats.text() == "usually ≤ 2.7 · max 2.7", \
    (c0.btn_lat.text(), c0.btn_stats.text())
assert "Button → game" in c0.btn_lat.toolTip() and "1 of the last 100 presses" in c0.btn_lat.toolTip(), c0.btn_lat.toolTip()
rx_latency.clear(0); win.slow_tick()
assert c0.btn_row.isVisible(), "silence / end of the 5 s window does not clear the last presses"
c0.btn_reset.click(); pump(5)
assert not c0.btn_row.isVisible() and rx_latency.buttons(0) is None, "reset z karty"
ok("pad card: pad -> game latency (mean, p95, max) with segments, button -> game with reset, ping before the measurement")
assert "online" in win.sender.text() and "192.168.1.50" in win.sender.toolTip()   # address in the tooltip (narrow window)
S.batt_info[0] = (10, 0); win.slow_tick()
assert c0.property("low") is True and win.windowTitle().endswith("LOW BATTERY") and win.warned[0]
S.batt_info[0] = (64, 0); win.slow_tick()
assert c0.property("low") is False and win.windowTitle() == "DualSense bridge" and not win.warned[0]
sent.clear()
c0.off.click(); assert c0.off.text() == "Turn off?" and not sent, "the first click only arms the button"
c0.off.click(); assert sum(1 for x in sent if x[0] == T_CMD) == 3 and c0.off.text() == "", "icon without text after the confirmation"
ok("pad cards: link, battery, low level, game profile, power-off on the second click")

# ---------- click on a part -> editor; sliders work live ----------
S.set_game(None)
click_part("right_stick"); assert win.part == "right_stick" and win.part_page.stack.currentIndex() == 2
click_part("r2"); assert win.part_page.stack.currentIndex() == 1 and win.part_page.title.text() == "R2 (trigger)" and win.part_page.trig.isVisible()
# trigger effect: mode (pad profile: no "as in the pad profile"), mode sliders, profile, badge, warnings
pp, rp = win.part_page, win.rumble_page
modes = [pp.fx_mode.itemData(k) for k in range(pp.fx_mode.count())]
assert modes == ["off", "res", "click", "vib"] and pp.fx_mode.currentData() == "off", modes
assert not any(r.isVisible() for r in pp.fx.values()) and not rp.gain.isVisibleTo(rp)
pp.fx_mode.changed.emit("res"); pump(50)
assert pp.fx["start"].isVisible() and pp.fx["force"].isVisible() and not pp.fx["pos"].isVisible()
pp.fx["force"].slider.setValue(5); pp.fx["start"].slider.setValue(3); pump(300)
assert store.get(base)["triggers"] == {"r2": {"mode": "res", "start": 3, "force": 6}}, store.get(base)["triggers"]
assert any(b[3] == "fx" and b[2] == "resist 6" for b in win.pad.badges)
assert any("Resistance R2: from 30%, strength 6/8" == t for t, _u in win.ed.changes())
assert pp.fx_warn.isVisible() and "older sender" in pp.fx_warn.text(), "pad without v6 sender info"
S.sender_info[0] = (6, 7, 0); win.slow_tick(); assert not pp.fx_warn.isVisible()
S.sender_info[0] = (6, 1, 0); win.slow_tick(); assert pp.fx_warn.isVisible() and "HID" in pp.fx_warn.text()
S.sender_info[0] = (7, 7, 0)
pp.fx_mode.changed.emit("click"); pump(300)
assert pp.fx["pos"].isVisible() and not pp.fx["start"].isVisible() and pp.fx["force"].val.text() == "6 / 8"
assert store.get(base)["triggers"] == {"r2": {"mode": "click", "pos": 5, "force": 6}}, "strength remembered on a mode change"
win.slow_tick(); assert pp.fx_warn.isVisible() and "newer sender" in pp.fx_warn.text(), "click needs a v8 sender"
S.sender_info[0] = (8, 15, 0); win.slow_tick(); assert not pp.fx_warn.isVisible()
rp.mode.changed.emit("on"); pump(300)
assert rp.gain.isVisibleTo(rp) and store.get(base)["triggers"]["rumble"] == {"on": True, "gain": 100, "freq": 40}
assert any(b[3] == "fx" and b[2] == "click 6 + game rumble" for b in win.pad.badges)
assert any(b[3] == "fx" and b[2] == "game rumble" for b in win.pad.badges), "L2 without an effect - game rumble only"
rp.gain.slider.setValue(5); pump(300)
assert store.get(base)["triggers"]["rumble"]["gain"] == 60
assert not rp.warn.isVisibleTo(rp)
S.sender_info[0] = (7, 7, 0); win.slow_tick(); assert rp.warn.isVisibleTo(rp) and "newer sender" in rp.warn.text()
S.sender_info[0] = None
pp.fx_mode.changed.emit("off"); rp.mode.changed.emit("off"); pump(300)
assert not rp.gain.isVisibleTo(rp) and not rp.warn.isVisibleTo(rp)
assert "triggers" not in store.get(base) or store.get(base)["triggers"] == {}
assert not pp.trig_adv.is_open(), "trigger response folded until something in it changes"
ok("trigger effect: modes and their sliders, game rumble, profile, badges, sender warnings")
click_part("cross"); assert not win.part_page.trig.isVisible()
win.part_page.dst.changed.emit("circle"); pump(300)
assert store.get(base)["buttons"] == {"cross": "circle"} and S.remap_maps[0]["buttons"] == {"cross": "circle"}
assert "Applied live" in win.status.text() and win.tabs.tabText(4) == "Changes (1)"
click_part("r2")
win.part_page.trig_adv.set_open(True)
win.part_page.tdz.slider.setValue(30); pump(300)
assert store.get(base)["tuning"] == {"r2": {"dz": 30, "max": 255}}
click_part("left_stick")
win.part_page.dz.slider.setValue(12); win.part_page.curve.slider.setValue(round((1.5 - 0.3) / 0.05)); pump(300)
assert store.get(base)["tuning"]["left"] == {"dz": 0.12, "outer": 1.0, "curve": 1.5, "anti": 0.0}
win.part_page.invx.setChecked(True); win.part_page.ssrc.changed.emit("none"); pump(300)
assert store.get(base)["sticks"]["left"] == {"src": "none", "inv_x": True, "inv_y": False}
win.part_page.l3.changed.emit("__none"); pump(300)
assert store.get(base)["buttons"]["l3"] is None
assert any(b[2] == "→ ∅" and b[3] for b in win.pad.badges) and any(b[2] == "∿" for b in win.pad.badges)
ok("click on the pad: part choice, button, trigger, stick (zone, curve, swap, L3), badges")

# ---------- gyro ----------
win.tabs.setCurrentIndex(2)
gp = win.gyro_page   # the aiming settings sit in a category that starts collapsed
assert not gp.aim_cat.is_open()
gp.aim_cat.set_open(True)
assert not gp.body.isVisibleTo(gp) and gp.off_hint.isVisibleTo(gp)
win.gyro_page.mode.changed.emit("stick"); win.gyro_page.hold.changed.emit("l2"); pump(20)
assert gp.body.isVisibleTo(gp) and gp.anti.isVisibleTo(gp)
win.gyro_page.sens.slider.setValue(round((1.75 - 0.1) / 0.05)); pump(300)
assert store.get(base)["gyro"]["mode"] == "stick" and store.get(base)["gyro"]["hold"] == "l2" and store.get(base)["gyro"]["sens"] == 1.75
win.gyro_page.mode.changed.emit("mouse"); pump(20); assert not gp.anti.isVisibleTo(gp)
win.gyro_page.mode.changed.emit("stick"); pump(300)
state(0, lt=200, gyro=(0, -1024 * 40, 0)); win.live_tick()
assert "Gyro active" in win.gyro_page.live.text(), win.gyro_page.live.text()
state(0, lt=0, gyro=(0, 0, 0)); win.live_tick()
assert "Gyro idle — hold L2" in win.gyro_page.live.text(), win.gyro_page.live.text()
ok("gyro: mode, activation, sensitivity, live state row")

# ---------- change list, undo, save ----------
win.tabs.setCurrentIndex(4)
assert win.tabs.tabText(4) == "Changes (6)", win.tabs.tabText(4)
texts = [lb.text() for lb in win.changes_page.box.findChildren(QLabel)]
assert any("Gyro: right stick, hold L2" in t for t in texts) and any("Left stick response" in t for t in texts), texts
first = win.changes_page.box.findChildren(QFrame, "row")[0]
first.findChild(QToolButton).click(); pump(300)          # × at the first item (Cross → Circle)
assert "cross" not in store.get(base)["buttons"] and win.tabs.tabText(4) == "Changes (5)"
win.save_btn.click(); pump(100)
saved = json.load(open(C.REMAP_PATH))["profiles"][base]
assert saved["gyro"]["hold"] == "l2" and saved["buttons"] == {"l3": None} and "Saved" in win.status.text() and win.status.property("level") == "ok"
ok("change list: undo of an item, save to remap.json")

# ---------- game profiles from the window ----------
bar = win.profile_bar
rx_games.recent[:] = ["eldenring.exe", "cyberpunk2077.exe"]
win.slow_tick(); bar._open_new()
assert bar.form.isVisible() and bar.exe.text() == "eldenring.exe", "hint from the recent games"
bar.exe.setText("not/a game"); bar._create(); pump(20)
assert bar.form.isVisible() and "eldenring.exe" in win.status.text() and win.status.property("level") == "bad"
bar.exe.setText("EldenRing"); bar._create(); pump(20)
key = base + "@eldenring.exe"
assert not bar.form.isVisible() and win.ed.key == key and store.has(key) and not store.in_file(key)
assert store.get(key)["gyro"]["mode"] == "stick", "copy of the current settings"
assert bar.combo.currentText().startswith("eldenring.exe") and bar.remove.isVisible()
win.part_page.l3.changed.emit("l3"); win.save_btn.click(); pump(50)
assert store.in_file(key) and key in json.load(open(C.REMAP_PATH))["profiles"]
bar.combo.changed.emit(base); pump(20)
assert win.ed.key == base and not bar.remove.isVisible() and win.tabs.tabText(4) == "Changes (5)"
bar.combo.changed.emit(key); pump(20); assert S.active[0] == key
bar.remove.click(); assert bar.remove.text() == "Sure?" and store.has(key)
bar.remove.click(); pump(20)
assert not store.has(key) and win.ed.key == base and "Profile deleted" in win.status.text()
ok("game profiles: hints, name validation, settings copy, switching, removal after confirmation")

# ---------- live view: result vs the physical pad ----------
S.alive[0] = True
state(0, mask=BIT[304]); win.live_tick()
store.save(base, {"buttons": {"cross": "circle"}}); S.reload_profile(0); win.ed.select(); win.refresh_all()
state(0, mask=BIT[304]); win.live_tick()
assert win.pad.live["on"] == {"circle"}, win.pad.live
win.seg.buttons["in"].click(); pump(20)
assert win.view == "in" and win.pad.live["on"] == {"cross"}, win.pad.live
state(0, sticks=(255, 0, 128, 128), lt=255); win.live_tick()
assert win.pad.live["ls"] == (255, 0) and win.pad.live["l2"] == 255
win.seg.buttons["out"].click(); pump(20); assert win.view == "out"
ok("live view: result after remapping / physical pad, sticks, triggers")

# ---------- pad choice, settings, hiding ----------
QTest.mouseClick(win.cards[1], Qt.MouseButton.LeftButton); pump(20)
assert win.ed.slot == 1 and win.cards[1].property("selected") is True and win.cards[0].property("selected") is False
win.save_settings()
win.ed.set_button("cross", "circle"); win.on_edit(); pump(20)
rst = win.changes_page.reset
assert rst.isVisibleTo(win.changes_page) and win.revert_btn.isEnabled()
rst.click(); assert win.ed.prof["buttons"] == {"cross": "circle"}, "the first click only arms"
rst.click(); pump(20)
assert win.ed.prof == rx_editor.empty() and win.tabs.tabText(4) == "Changes" and not rst.isVisibleTo(win.changes_page)
ok("Remove all: in the change list, after the second click, gone with an empty profile")
win.hide(); assert not win.live_timer.isActive() and win.slow_timer.isActive(), "a hidden window does not redraw the pad, but refreshes the tray"
win.show(); pump(50); assert win.live_timer.isActive()
win2 = rx_qt.create_window(os.path.join(TMP, "ui.ini"))
assert win2.ed.slot == 1 and win2.view == "out", "window settings remembered"
win2.slow_timer.stop(); win2.deleteLater()
ok("pad choice by card, remembered settings, hiding pauses redrawing")

# ---------- full screen = TV mode: bigger scale, pad control (arrows over the drawing parts) ----------
import rx_qt_style
w0 = win.right_col.width()
win.set_fullscreen(True); pump(50)
assert rx_qt_style.scale == rx_qt_style.TV_SCALE and win.right_col.width() > w0
win.pad.set_selected(None); win.pad.hover = None; win.pad.setFocus()
QTest.keyClick(win.pad, Qt.Key.Key_Right); assert win.pad.hover == "l2", win.pad.hover
QTest.keyClick(win.pad, Qt.Key.Key_Return); pump(20); assert win.part == "l2"
win.set_fullscreen(False); pump(50)
assert rx_qt_style.scale == 1.0 and win.right_col.width() == w0
ok("full screen: TV scale and back, keyboard on the pad drawing")

shot = os.path.join(os.path.dirname(__file__), "gui.png")
win.select_pad(0); click_part("left_stick"); pump(100)
win.grab().save(shot)
assert os.path.getsize(shot) > 10000
ok("window screenshot: tests/gui.png")

# ---------- update: stop waiting for the sender once its commit changed ----------
import types
from proto import UPD_RUNNING
win._upd_result = types.SimpleNamespace(text="receiver up to date", ok=True, changed=False)
win._upd_sender, win._upd_commit, win._upd_wait_t0, win._upd_left = True, "a1b2c3d", time.monotonic(), None
S.sender_build = ("a1b2c3d", 1, UPD_RUNNING, time.monotonic())
win._update_poll()
assert "sender" in win.note_text[0].lower() and win.note_text[1] == "", f"same commit, running - still waiting: {win.note_text}"
S.sender_build = ("e4f5a6b", 2, UPD_RUNNING, time.monotonic())   # the new commit is up while the state still says running
win._update_poll()
assert win.note_text[1] == "ok" and "receiver up to date" in win.note_text[0], f"a new commit = updated, waiting ends: {win.note_text}"
win._upd_result = None; S.sender_build = None
ok("update: the window stops waiting when the sender commit changed")
