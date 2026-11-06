"""Window: accessibility mode panel (Dostępność tab of the profile editor) - per-pad switch, sliders that save, range ring on the stick preview,
the "Dostosuj do ręki" wizard (proposals go into the sliders and turn the mode on), "Przywróć zalecane", failure messages,
clearing and switching pads."""
import math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabWidget

C.cfg["tray"] = False
import rx_drift as D
import rx_padcal
import rx_profiles as P
import rx_qt
import rx_reach as R
from proto import BODY, CAP_CALIB, CMD_RAW_STICKS, RUMBLE_HIDRAW, T_CMD, T_IDENT
from chk import ok
pump = lambda ms=40: QTest.qWait(ms)
PAD = "aa:aa:aa:aa:aa:03"
cmds = lambda: [BODY[T_CMD].unpack_from(pkt, 4) for t, _i, _sq, pkt in sent if t == T_CMD]


def byte(f):
    return max(0, min(255, round(128 + f * (128 if f < 0 else 127))))


def circle(r, n=240):
    return [(128, 128, byte(r * math.cos(2 * math.pi * k / n)), byte(r * math.sin(2 * math.pi * k / n))) for k in range(n)]


def run_wizard(ap, rim, rest=(128, 128, 130, 127)):
    """Runs the wizard on fake time: rest, ready, a circle with the stick at radius rim."""
    t = ap.wiz._t0
    state(0, sticks=rest); ap.tick(t + D.SETTLE_S); state(0, sticks=rest); ap.tick(t + D.SETTLE_S + D.MEASURE_S)
    t += D.SETTLE_S + D.MEASURE_S
    ap.tick(t + R.GET_READY_S)
    t += R.GET_READY_S
    if ap.wiz is not None and ap.wiz.step == "reach":
        for s in circle(rim):
            state(0, sticks=s)
        ap.tick(t + R.REACH_S)


feed(T_IDENT, 0, PAD.encode()); state(0)
S.sender_info[0] = (9, RUMBLE_HIDRAW | CAP_CALIB, 0)
rx_padcal.reload()
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 900); win.show(); pump(150)
win.activateWindow(); win.tabs.setCurrentWidget(win.access_scroll); pump()
panel = win.test_panel
ap = win.access_page
assert win.tabs.tabPosition() == QTabWidget.TabPosition.West, "editor tabs vertical on the left"
assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == ["Wybrane", "Wibracje", "Gyro", "Dostępność", "Zmiany"]
assert not hasattr(panel, "access") and ap.isVisible(), "accessibility panel is in the tab, not in the Test view"

# ---------- switch and sliders ----------
assert not ap.on.isChecked() and ap.sl["outer"].value() == 100 and not ap.restore.isEnabled() and not ap.clear_btn.isVisible()
ap.sl["outer"].slider.setValue(30)                                # 40 + 30 = 70 %
assert ap.sl["outer"].value() == 70 and P.access(0) == {}, "saved only a moment after the last move"
pump(450)
assert P.access(0)["right"]["outer"] == 0.7 and "on" not in P.access(0), "slider saved, mode still off"
state(0, sticks=(128, 128, 220, 128))
assert S.states[0]["rx"] == 220, "mode off = the game gets the stick unchanged"
assert panel.scope.reach == {}, "no range ring until turned on"
ap.on.setChecked(True); pump()
assert P.access(0)["on"] is True and S.states[0]["rx"] == 255, "on: 70% deflection = full in game"
assert panel.scope.reach == {"right": 0.7} and ap.clear_btn.isVisible()
ap.which.setCurrentIndex(1); ap.which.activated.emit(1)
assert ap.stick == "left" and ap.sl["outer"].value() == 100, "sliders show the chosen stick"
ap.sl["dz"].slider.setValue(8); pump(450)
assert P.access(0)["left"]["dz"] == 0.08 and P.access(0)["right"]["outer"] == 0.7, "the other stick stays"
ap.which.setCurrentIndex(0); ap.which.activated.emit(0)
assert ap.stick == "right" and ap.sl["outer"].value() == 70
ok("panel: sliders save with a delay, the switch turns the effect on, range ring on the preview, sticks independent")

# ---------- kreator ----------
sent.clear()
ap.choice.set_data("right")
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
assert ap.wiz is not None and cmds() == [(CMD_RAW_STICKS, D.RAW_S)] * 3 and ap.bar.isVisible() and ap.start.text() == "Przerwij"
assert "mierzę dryf" in ap.msg.text()
assert S.states[0]["blocked"], "wizard locks the game output (circling the stick does not move the game)"
run_wizard(ap, 0.62)
assert not S.states[0]["blocked"], "after the wizard the game gets the pad back"
assert ap.wiz is None and not ap.bar.isVisible() and ap.start.text() == "Dostosuj do ręki"
cfg = P.access(0)
assert cfg["on"] is True and 0.55 <= cfg["right"]["outer"] <= 0.6 and cfg["right"]["dz"] >= D.MIN_DZ, cfg
assert "✓" in ap.msg.text() and "Prawa gałka" in ap.msg.text() and cmds()[-1] == (CMD_RAW_STICKS, 0)
assert ap.sl["outer"].value() == round(cfg["right"]["outer"] * 100) and ap.restore.isEnabled(), "propozycja w suwakach"
assert cfg["left"]["dz"] == 0.08, "wizard for the right stick does not touch the left one"
proposal = ap.sl["outer"].value()
ap.sl["outer"].slider.setValue(50); pump(450)                       # manual change after the wizard
assert P.access(0)["right"]["outer"] == 0.9, "can still be changed by hand"
QTest.mouseClick(ap.restore, Qt.MouseButton.LeftButton); pump()
assert ap.sl["outer"].value() == proposal and P.access(0)["right"]["outer"] == proposal / 100, "Restore recommended"
ok("wizard: the proposal goes into the sliders and turns the mode on, manual change possible, Restore recommended goes back to the result")

# failures and abort
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
run_wizard(ap, 0.62, rest=(128, 128, 200, 128))
assert ap.wiz is None and "ruszała" in ap.msg.text() and P.access(0)["right"]["outer"] == proposal / 100, "a failure does not change the settings"
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
assert ap.wiz is None and not ap.msg.isVisible() and S.probe[0] is None and cmds()[-1] == (CMD_RAW_STICKS, 0), "Abort cleans up"
ap.choice.set_data("both")
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
assert ap.wiz.steps[-1] == ("reach", "right") and len(ap.wiz.steps) == 5
S.alive[0] = False
ap.tick(0.0)
assert ap.wiz is None and "niepodłączony" in ap.msg.text() and S.probe[0] is None, "pad gone - wizard aborted"
S.alive[0] = True
ok("wizard: a failure keeps the settings, Abort cleans up, both sticks = 5 steps, a vanished pad aborts")

# switching the tab aborts the wizard and gives the pad back to the game; a pending slider change is saved
QTest.mouseClick(ap.start, Qt.MouseButton.LeftButton); pump()
assert ap.wiz is not None and S.states[0]["blocked"]
win.tabs.setCurrentWidget(win.gyro_page); pump()
assert ap.wiz is None and not S.states[0]["blocked"] and S.probe[0] is None, "another tab aborts the wizard"
win.tabs.setCurrentWidget(win.access_scroll); pump()
ap.sl["outer"].slider.setValue(45)
win.tabs.setCurrentWidget(win.gyro_page); pump(10)
assert round(P.access(0)["right"]["outer"] * 100) == 40 + 45, "switching the tab saves the pending slider change"
win.tabs.setCurrentWidget(win.access_scroll); pump()
proposal = ap.sl["outer"].value()
ok("tabs: leaving Dostępność aborts the wizard, removes the lock and saves the slider")

# ---------- clearing and switching pads ----------
win.select_pad(1); pump()
assert ap.slot == 1 and not ap.on.isChecked() and ap.sl["outer"].value() == 100 and P.access(1) == {}, "settings are per pad"
win.select_pad(0); pump()
assert ap.slot == 0 and ap.on.isChecked() and ap.sl["outer"].value() == proposal
ap.clear_btn.click(); ap.clear_btn.click(); pump()
assert P.access(0) == {} and not ap.on.isChecked() and not ap.clear_btn.isVisible() and panel.scope.reach == {}
state(0, sticks=(128, 128, 220, 128))
assert S.states[0]["rx"] == 220, "after clearing the game gets the stick unchanged again"
win.seg.buttons["out"].click(); pump()
ok("clearing and switching: settings per pad, Clear restores normal stick behaviour")
