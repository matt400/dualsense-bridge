"""Window: the pad self-test block of the Test view - one button, the output lock for the whole run (even with the lock option off), a refusal
that says why, stopping by the button / leaving the view / another pad, the report (saved to reports/, copied by the button).
    python3 tests/test_selftest_gui.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

C.cfg["tray"] = False
import rx_qt
import rx_qt_selftest as QS
import rx_selftest as T
import rx_testmode
from proto import PK_DUALSENSE, RUMBLE_HIDRAW, T_IDENT
from chk import check, fails                           # noqa: E402  (shared helper, tests/chk.py)

pump = lambda ms=40: QTest.qWait(ms)


def until(pred, timeout=15.0):
    """Like wait_for, but the Qt event loop keeps running (the window's clock drives the progress and the end of the run)."""
    end = time.monotonic() + timeout
    while not pred() and time.monotonic() < end:
        pump(30)
    return bool(pred())


A = "aa:aa:aa:aa:aa:01"
feed(T_IDENT, 0, A.encode()); state(0)
S.sender_info[0] = (T.SELFTEST_SENDER - 1, RUMBLE_HIDRAW, 0)         # an old sender: no simulated sender is needed, its steps are skipped
S.pad_kind[0] = (PK_DUALSENSE, 0)
S.info[0] = (9.0, 250.0, 250.0, time.monotonic())

real_plan = T.Plan
T.Plan = lambda window_s=15: real_plan(window_s=1, idle_s=0.3, ack_s=0.2, result_s=0.2)       # the run takes ~1.5 s here
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 860); win.show(); pump(150)
panel = win.test_panel
box = panel.selftest
reports = os.path.join(TMP, "reports")

# ---------- the block and its controls ----------
check(isinstance(box, QS.SelfTestBox) and not box.busy, "the Test panel has the self-test block, nothing running")
check(list(box.window_seg.buttons) == ["5", "15", "30", "60"] and box.window_seg.value() == "15", "the latency window: 5 / 15 / 30 / 60 s, 15 s by default")
check(not box.copy_btn.isVisible() and not box.bar.isVisible(), "no report and no progress before a run")
S.alive[0] = False
box.refresh()
check(not box.start_btn.isEnabled(), "the button is off for a pad that is not connected")
S.alive[0] = True
box.refresh()
check(box.start_btn.isEnabled() and box.start_btn.text() == "Uruchom self-test", "the button starts the self-test")

# ---------- a refusal says why and leaves the lock as the user set it ----------
win.seg.buttons["out"].click(); pump()
check(not rx_testmode.active, "setup: not in the Test view")
box.start()
check(not box.busy and "nie jest zablokowane" in box.status.text() and not box.status.isHidden(), f"no run outside the Test view: the reason is shown ({box.status.text()!r})")
check(not S.states[0]["blocked"], "a refused run leaves the output to the game alone")

# ---------- a run: the lock is on even with the option off, the report comes out ----------
win.seg.buttons["test"].click(); pump()
panel.block.setChecked(False); pump()
check(not S.states[0]["blocked"], "setup: the Test lock option is off")
box.window_seg.set("5")
box.start_btn.click()
pump()
check(box.busy and box.start_btn.text() == "Przerwij self-test" and S.states[0]["blocked"], "a run: the button turns into Stop and the output to the game is blocked (option off)")
check(not box.window_seg.isEnabled(), "the window choice is locked during a run")
check(until(lambda: "/7" in box.status.text() and box.bar.isVisible(), 5) and box.busy, f"the status line and the bar follow the steps ({box.status.text()!r})")
check(until(lambda: not box.busy), "the run ends by itself")
pump(100)
check(not S.states[0]["blocked"], "the output lock goes back to what the user chose (off)")
check(box.text.startswith("# Pad self-test report (version 1)") and box.copy_btn.isVisible() and not box.bar.isVisible(), "a report, the copy button, no progress bar")
check(box.path and os.path.exists(box.path) and os.path.dirname(box.path) == reports and "Raport zapisany" in box.status.text(), f"the report was saved to reports/ ({box.path})")
check("latency_window_s\": 1" in box.text or '"window_s": 1' in box.text, "the report holds the chosen window (the plan was shrunk to 1 s by the test)")
box.copy_btn.click(); pump()
check(QApplication.clipboard().text() == box.text and "skopiowany" in box.status.text(), "Copy report puts the same text on the clipboard")
check(open(box.path, encoding="utf-8").read() == box.text, "the clipboard text is the file's text")
check("older than the self-test" in box.text, "against an old sender the report says what was skipped")

# ---------- stopping: the button, leaving the view, another pad ----------
panel.block.setChecked(True); pump()
box.start(); pump()
check(box.busy, "a second run starts")
box.start_btn.click(); pump()
check(not box.busy and "przerwany" in box.status.text() and '"result": "aborted"' in box.text, "the button stops a run: the report says it was stopped")
box.start(); pump()
win.seg.buttons["out"].click(); pump()
check(not box.busy and '"result": "aborted"' in box.text and not rx_testmode.latency_probe, "leaving the Test view stops the run")
win.seg.buttons["test"].click(); pump()
box.start(); pump()
win.select_pad(1); pump()
check(not box.busy and box.text == "", "choosing another pad stops the run and drops the old report")
win.select_pad(0); pump()

T.Plan = real_plan
sys.exit(1 if fails else 0)
