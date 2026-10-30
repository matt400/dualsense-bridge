"""Receiver: Segmented.value with nothing checked; tilt settings are saved with a debounce.
    python3 tests/test_tilt_settings_widgets.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# 3 + 4. widgets (need Qt)
try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:
    print("SKIP  widgets (no PySide6)")
    sys.exit(1 if fails else 0)
app = QApplication.instance() or QApplication([])
from rx_qt_widgets import Segmented
seg = Segmented([("a", "A"), ("b", "B")])
check(seg.value() == "a", "Segmented.value: nothing checked -> first option, no StopIteration")
seg.set("b")
check(seg.value() == "b", "Segmented.value: the checked option")

import rx_qt_gstick as QG
sec = QG.TiltSection()
live, saved = [], []
sec.changed.connect(live.append)
sec.committed.connect(lambda slot, cfg: saved.append((slot, cfg)))
for v in (5, 6, 7, 8, 9):                                        # a slider drag: many steps in a row
    sec.sl["deadzone"].slider.setValue(sec.sl["deadzone"].slider.value() + 1)
check(len(live) == 5 and not saved, "tilt: every step reaches the engine, nothing is written yet")
QTest.qWait(QG.SAVE_DELAY_MS + 150)
check(len(saved) == 1 and saved[0][1] is live[-1], "tilt: one write with the last value after the drag")
sec.sl["deadzone"].slider.setValue(sec.sl["deadzone"].slider.value() + 1)
sec.set_slot(1)
check(len(saved) == 2 and saved[1][0] == 0, "tilt: switching pads flushes the edit to the pad it was made on")

sec.cal_note.setText("Środek ustawiony.")
sec.set_slot(0)
check(sec.cal_note.text() == "", "tilt: the calibration note does not follow to another pad")

sys.exit(1 if fails else 0)
