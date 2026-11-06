"""Window: the side menu does not force a tall window - at the TV scale the window fits on a 1280x720 screen (TV 2560x1440 at 200 %)."""
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
from chk import ok
pump = lambda ms=40: QTest.qWait(ms)

rx_audio.list_outputs = lambda: [("1", "Speakers")]
rx_console.ctl = Console(ConsoleConfig())
F.pulse = lambda i, kind: None
win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 760); win.show(); pump(120)

# ---------- TV scale: the minimum window height leaves room for the taskbar, the frame and a margin ----------
SCREEN_H, TASKBAR, FRAME, MARGIN = 720, 48, 40, 24                # TV 1280x720 po skalowaniu 200 %
for k in (1.0, TV_SCALE):
    win.apply_scale(k); pump(120)
    assert win.minimumSizeHint().height() <= SCREEN_H - TASKBAR - FRAME - MARGIN, \
        f"scale {k}: minimum window height {win.minimumSizeHint().height()} px does not fit on the TV screen"
ok("TV scale: the minimum window height fits on a 1280x720 screen")

# ---------- buttons shrink with a low window and come back with a tall one ----------
win.resize(1180, 900); pump(120)
full = win.side.buttons[0].height()
assert full == px(40), f"tall window: full buttons ({full} px)"
win.resize(win.width(), win.minimumSizeHint().height()); pump(120)
small = win.side.buttons[0].height()
assert px(MIN_BUTTON) <= small < full, f"low window: the buttons shrink ({small} < {full} px)"
assert win.side.buttons[-1].geometry().bottom() <= win.side.height(), "the last icon (close) fits in the menu"
win.resize(1180, 900); pump(120)
assert win.side.buttons[0].height() == full, "after the window grows the buttons go back to full size"
ok("side menu: the buttons shrink and grow with the window")

print("SIDEBAR FIT GUI OK")
