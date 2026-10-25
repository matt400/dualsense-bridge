"""Okno: menu boczne nie wymusza wysokiego okna - przy skali TV okno mieści się na ekranie 1280x720 (TV 2560x1440 przy 200 %)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtTest import QTest

C.cfg["tray"] = False
C.CONSOLE_PATH = os.path.join(TMP, "console.json")
import rx_audio, rx_console, rx_qt
from rx_console import Console, ConsoleConfig
from rx_qt_sidebar import MIN_BUTTON
from rx_qt_style import TV_SCALE, px
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 760); win.show(); pump(120)

# ---------- skala TV: minimalna wysokość okna zostawia miejsce na pasek zadań, ramkę i margines ----------
SCREEN_H, TASKBAR, FRAME, MARGIN = 720, 48, 40, 24                # TV 1280x720 po skalowaniu 200 %
for k in (1.0, TV_SCALE):
    win.apply_scale(k); pump(120)
    assert win.minimumSizeHint().height() <= SCREEN_H - TASKBAR - FRAME - MARGIN, \
        f"skala {k}: minimalna wysokość okna {win.minimumSizeHint().height()} px nie mieści się na ekranie TV"
ok("skala TV: minimalna wysokość okna mieści się na ekranie 1280x720")

# ---------- przyciski kurczą się przy niskim oknie i wracają przy wysokim ----------
win.resize(1180, 900); pump(120)
full = win.side.buttons[0].height()
assert full == px(40), f"wysokie okno: pełne przyciski ({full} px)"
win.resize(win.width(), win.minimumSizeHint().height()); pump(120)
small = win.side.buttons[0].height()
assert px(MIN_BUTTON) <= small < full, f"niskie okno: przyciski się zmniejszają ({small} < {full} px)"
assert win.side.buttons[-1].geometry().bottom() <= win.side.height(), "ostatnia ikona (zamknij) mieści się w menu"
win.resize(1180, 900); pump(120)
assert win.side.buttons[0].height() == full, "po powiększeniu okna przyciski wracają do pełnego rozmiaru"
ok("menu boczne: przyciski kurczą się i rosną razem z oknem")

print("SIDEBAR FIT GUI OK")
