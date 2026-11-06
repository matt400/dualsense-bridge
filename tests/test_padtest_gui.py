"""Window: the Test view (the third switch under the pad) - the test panel instead of editing, the game output lock only for
the tested pad, touch and triggers on the drawing, motion charts, drift measurement with "Zastosuj", gyro calibration,
leaving the test in TV mode by holding ○ and from the pad (Options + Create, no mouse). Screenshot: tests/test_view.png."""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

C.cfg["tray"] = False
import rx_drift as D
import rx_padcal
import rx_profiles as P
import rx_qt
from proto import (BODY, CAL_OK, CAL_RUNNING, CALF_STORED, CAP_CALIB, CMD_GYRO_CAL, CMD_RAW_STICKS, RUMBLE_HIDRAW,
                   T_CALRES, T_CLICK, T_CMD, T_IDENT, T_TOUCH)
from chk import ok
pump = lambda ms=40: QTest.qWait(ms)
A = "aa:aa:aa:aa:aa:01"
SHOTS = os.environ.get("SHOTS") or os.path.dirname(__file__)
def activate(w):
    """Active window: the offscreen platform does not activate a shown window again through activateWindow()."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(w)
    pump()


cmds = lambda: [BODY[T_CMD].unpack_from(pkt, 4) for t, _i, _sq, pkt in sent if t == T_CMD]

feed(T_IDENT, 0, A.encode()); state(0)
S.sender_info[0] = (9, RUMBLE_HIDRAW | CAP_CALIB, 0)
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 860); win.show(); pump(150)
panel = win.test_panel

# ---------- switch and lock ----------
assert list(win.seg.buttons) == ["out", "in", "test"] and win.right_stack.currentIndex() == 0
assert not S.states[0]["blocked"]
sent.clear()
win.seg.buttons["test"].click(); pump()
assert win.view == "test" and win.right_stack.currentIndex() == 1 and win.pad.test
assert S.states[0]["blocked"] and not S.states[1]["blocked"], "only the tested pad locked"
assert cmds() == [(CMD_GYRO_CAL, 0)] * 3, f"entering the test asks the sender for the calibration state: {cmds()}"
assert "na żywo" in win.pad_hint.text()
panel.block.setChecked(False); pump()
assert not S.states[0]["blocked"]
panel.block.setChecked(True); pump()
assert S.states[0]["blocked"]
win.select_pad(1); pump()
assert S.states[1]["blocked"] and not S.states[0]["blocked"], "the lock follows the chosen pad"
win.select_pad(0); pump()
win.on_part("cross")
assert win.part is None, "in the test a click on a pad part does not open remapping"
ok("Test: the panel instead of editing, lock only for the chosen pad (switch), a click on the pad does not remap")

# ---------- touchpad as mouse: the cursor works, the "Testuj touchpad" button captures it ----------
import rx_testmode as TM
S.mouse_mode[0] = False; panel.refresh(); pump()
assert not panel.tp_box.isVisible(), "DS4 touch: the touchpad block hidden (nothing to capture)"
S.mouse_mode[0] = True; panel.refresh(); pump()
assert panel.tp_box.isVisible() and panel.tp_btn.isEnabled() and panel.tp_btn.text() == "Testuj touchpad"
assert "steruje teraz kursorem" in panel.tp_hint.text()
QTest.mouseClick(panel.tp_btn, Qt.MouseButton.LeftButton); pump()
assert TM.capture_slot == 0 and "Zakończ test touchpada" in panel.tp_btn.text() and "Kursor stoi" in panel.tp_hint.text()
QTest.mouseClick(panel.tp_btn, Qt.MouseButton.LeftButton); pump()
assert TM.capture_slot is None and panel.tp_btn.text() == "Testuj touchpad", "the button ends the capture"
panel.toggle_touchpad(); state(0, mask=BIT[316]); state(0); panel.refresh()
assert TM.capture_slot is None and panel.tp_btn.text() == "Testuj touchpad", "PS on the pad ends the capture, the button comes back"
panel.toggle_touchpad(); win.select_pad(1); pump()
assert TM.capture_slot is None, "changing the tested pad ends the capture"
win.select_pad(0); pump()
panel.toggle_touchpad(); assert TM.capture_slot == 0
other = QWidget(); other.show(); activate(other)
assert TM.capture_slot is None, "window in the background (game) - the touchpad goes back to the mouse"
other.close(); activate(win)
S.mouse_mode[0] = False; panel.refresh(); pump()
assert not panel.tp_box.isVisible()
S.mouse_mode[0] = True; panel.refresh()                              # slot 0 is the mouse by default - the sections below assume it
ok("Test: the touchpad block only in mouse mode, the button turns capture on / off, PS, a pad change and losing focus also end it")

# ---------- live: touch, triggers, motion, sticks ----------
state(0, mask=BIT[304], lt=120, rt=255, sticks=(131, 126, 128, 128), gyro=(2048, -1024, 0))
feed(T_TOUCH, 0, 1, 1, 960, 470, 1, 2, 300, 100)
feed(T_CLICK, 0, 1)
for _k in range(5):
    win.live_tick()
pump()
assert win.pad.live["l2"] == 120 and win.pad.live["r2"] == 255 and "cross" in win.pad.live["on"], "rysunek fizycznie"
assert win.pad.touch_live == (((960, 470), (300, 100)), 1)
assert panel.gyro_chart.data[-1] == (2.0, -1.0, 0.0) and abs(panel.acc_chart.data[-1][2] - 1.0) < 1e-9
assert panel.scope.stick_pos["left"] == (D.unit(131), D.unit(126))
win.grab().save(os.path.join(SHOTS, "test_view.png"))
feed(T_CLICK, 0, 0)
ok("live: buttons and triggers (values) on the drawing, 2 fingers and the touchpad click, gyro °/s and accelerometer g, sticks")

# ---------- drift: measurement -> proposal -> Zastosuj ----------
sent.clear()
QTest.mouseClick(panel.measure, Qt.MouseButton.LeftButton); pump()
assert panel.run is not None and not panel.measure.isEnabled() and cmds() == [(CMD_RAW_STICKS, D.RAW_S)] * 3
t0 = panel.run.t0
panel._drift_tick(t0 + D.SETTLE_S)
for lx in (132, 126, 133):
    state(0, sticks=(lx, 128, 128, 129))
panel._drift_tick(t0 + D.SETTLE_S + D.MEASURE_S); panel.refresh(); pump()
assert panel.run is None and panel.result is not None and cmds()[-1] == (CMD_RAW_STICKS, 0)
assert "3.9%" in panel.drift_res.text() and "→" in panel.drift_res.text() and panel.apply.isEnabled(), panel.drift_res.text()
assert panel.scope.peak["left"] == panel.result.left.peak
QTest.mouseClick(panel.apply, Qt.MouseButton.LeftButton); pump()
assert P.drift(0) == {"left": 0.05, "right": D.MIN_DZ} and "✓" in panel.drift_res.text() and not panel.apply.isEnabled()
assert "5%" in panel.corr.text() and panel.clear_btn.isVisible() and panel.scope.dz == {"left": 0.05, "right": 0.03}
win.grab().save(os.path.join(SHOTS, "test_view_drift.png"))
panel.clear_btn.click(); panel.clear_btn.click(); pump()
assert P.drift(0) == {} and not panel.clear_btn.isVisible()
panel.start_drift(); panel._drift_tick(panel.run.t0 + D.SETTLE_S)
state(0, sticks=(250, 128, 128, 128))
panel._drift_tick(panel.run.t0 + D.SETTLE_S + D.MEASURE_S); panel.refresh()
assert panel.result.moved and not panel.apply.isEnabled() and "ruszała" in panel.drift_res.text()
state(0)
ok("drift: sticks without the sender zone during the measurement, result and proposal, Zastosuj saves the pad correction, Wyczyść")

# ---------- gyro calibration ----------
sent.clear()
feed(T_CALRES, 0, 3, 0, 0, 0, 0, 512, 0, -256, 0)
panel.refresh()
assert "Brak zapisanej" in panel.cal_text.text() and "+0.50" in panel.cal_text.text(), panel.cal_text.text()
QTest.mouseClick(panel.cal_btn, Qt.MouseButton.LeftButton); pump()
assert cmds() == [(CMD_GYRO_CAL, 3)] * 3 and not panel.cal_btn.isEnabled() and "nie ruszaj" in panel.cal_text.text()
feed(T_CALRES, 0, CAL_RUNNING, 0, 0, 0, 0, 512, 0, -256, 0)
panel._cal_tick(time.monotonic()); panel.refresh()
assert panel.cal_t0 is not None, "'in progress' is not a result yet"
feed(T_CALRES, 0, CAL_OK, CALF_STORED, 400, 10, -20, 400, 10, -20, int(time.time()))
panel._cal_tick(time.monotonic()); panel.refresh(); pump()
assert panel.cal_t0 is None and panel.cal_btn.isEnabled()
assert "Skalibrowano" in panel.cal_text.text() and "Zapisana kalibracja" in panel.cal_text.text(), panel.cal_text.text()
panel.start_cal()
panel.cal_t0 -= 10                                               # 10 s without a reply
panel._cal_tick(time.monotonic()); panel.refresh()
assert "Brak odpowiedzi" in panel.cal_text.text(), "no reply after the time - a message instead of waiting forever"
S.sender_info[0] = (8, RUMBLE_HIDRAW, 0); panel.refresh()
assert not panel.cal_btn.isEnabled() and "nowszego nadajnika" in panel.cal_text.text()
S.sender_info[0] = (9, RUMBLE_HIDRAW | CAP_CALIB, 0)
ok("calibration: command with a time, progress, result with date and offset, no reply, old sender = a hint")

# ---------- leaving the test ----------
win.set_view("in"); pump()
assert win.right_stack.currentIndex() == 0 and not win.pad.test and not S.states[0]["blocked"]
assert win.seg.value() == "in" and "zremapować" in win.pad_hint.text()
win.set_view("test"); pump()
panel.start_drift()
win.hide(); pump()
assert not S.states[0]["blocked"] and panel.run is None and S.probe[0] is None, "hidden window: the game gets the pad"
win.show(); activate(win)
assert S.states[0]["blocked"]
other = QWidget(); other.show(); activate(other)
assert not win.isActiveWindow() and not S.states[0]["blocked"], "another window in the foreground (game) - the pad goes back to the game"
other.close(); activate(win)
assert S.states[0]["blocked"]
win.showMinimized(); pump()
assert not S.states[0]["blocked"], "a minimized window (no tray) gives the pad back too"
win.showNormal(); activate(win)
assert S.states[0]["blocked"]
win.save_settings()
assert win.settings.value("view") == "in", "the Test view (with the lock) does not come back by itself after a restart"
ok("leaving: another view, a hidden / minimized / inactive window removes the lock; Test is not remembered")

# ---------- TV mode: the tested pad does not drive the window, holding ○ leaves ----------
win.set_fullscreen(True); pump(100)
assert "○" in win.pad_hint.text()
state(0, mask=BIT[305])
assert "circle" not in win.nav._pressed(), "○ of the tested pad does not close full screen"
now = time.monotonic()
win._exit_hold(now, True); win._exit_hold(now + 0.5, True)
assert win.view == "test"
win._exit_hold(now + D.SETTLE_S + 1.0, True)
assert win.view == "in" and not S.states[0]["blocked"]
assert "circle" in win.nav._pressed(), "after the test the pad drives the window again"
state(0)
win.set_fullscreen(False); pump(100)
ok("TV mode: the tested pad does not drive the window, holding ○ goes back to the previous view")

# ---------- Options + Create from the pad: leaving the test without a mouse, also with an inactive window ----------
import rx_testmode as TM
win.seg.buttons["test"].click(); activate(win)
assert win.view == "test" and TM.active and S.states[0]["blocked"], "an open Test turns the pad shortcuts off"
assert "Options + Create" in win.pad_hint.text()
state(0, mask=TM.ESCAPE_MASK); pump()
assert win.view == "in" and not TM.active and not S.states[0]["blocked"], "leaving from the pad: view and lock"
assert "Options + Create" in win.status.text()
win.sync_block()
assert not S.states[0]["blocked"] and not TM.active, "activating the window before the signal is handled does not lock again"
state(0)
win.seg.buttons["test"].click(); activate(win)
assert TM.active and S.states[0]["blocked"], "entering Test again works as before"
other = QWidget(); other.show(); activate(other)
assert not TM.active, "window in the background (game) - pad shortcuts work"
other.close(); activate(win)
assert TM.active
win.set_view("in"); pump()
assert not TM.active
ok("Options + Create from the pad: the view returns, lock removed, Test works again; shortcuts paused only while the test window is active")

win.quit()
assert not any(s["blocked"] for s in S.states) and not TM.active
rx_padcal.reload()
ok("closing the window removes the lock")
