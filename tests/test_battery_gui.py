"""Receiver window: the sender pill with the sender device's battery (percent, charge fill, wave while charging)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest

C.cfg["tray"] = False
import rx_games
import rx_qt
import rx_qt_style as ST
rx_games.in_game = lambda exe=None: False    # the desktop: the wave may animate
ok = lambda msg: print("OK  ", msg)
pump = lambda ms=40: QTest.qWait(ms)

win = rx_qt.create_window(os.path.join(TMP, "ui.ini")); win.resize(1180, 760); win.show(); pump(150)
pill = win.sender


def refresh(batt, online=True):
    S.last_pkt = time.monotonic() if online else 0.0
    S.sender_build = ("abc1234", 1_700_000_000, 0, time.monotonic()) if online else None
    S.sender_batt = batt
    win.online = None
    win.slow_tick(); pump(60)


def px_at(frac_x, y):
    img = pill.grab().toImage()
    return QColor(img.pixel(int(img.width() * frac_x), y))


# ---------- no battery (a Raspberry Pi): the old look ----------
refresh(None)
assert "Nadajnik online" in pill.text() and "%" not in pill.text() and not pill.property("battery")
assert not pill._timer.isActive()
ok("no battery: a plain pill, no percentage, no animation")

# ---------- a battery, discharging ----------
refresh((60, False))
assert pill.text().startswith("●  Nadajnik online (60%)") and "abc1234" in pill.text(), pill.text()
assert pill.property("battery") is True and not pill._timer.isActive()
assert "Bateria nadajnika: 60%" in pill.toolTip() and "ładuje" not in pill.toolTip()
left, right = px_at(0.25, pill.height() // 2), px_at(0.9, pill.height() // 2)
assert left != right, "the left part (charged) differs from the right one (empty track)"
assert pill._color == QColor(ST.LEVEL["ok"])
ok("discharging: '(60%)' next to the label, the fill covers the charged part, no wave")

# ---------- charging: the wave runs ----------
refresh((60, True))
assert pill._timer.isActive() and "(ładuje się)" in pill.toolTip()
frames = set()
for _ in range(6):
    pump(70)
    img = pill.grab().toImage()
    frames.add(tuple(img.pixel(x, 4) for x in range(4, int(pill.width() * 0.5), 3)))
assert len(frames) > 1, "the wave moves along the top edge"
refresh((60, False))
assert not pill._timer.isActive(), "charging stopped -> the timer stops"
ok("charging: the wave moves along the top edge and stops with the charging")

# ---------- level colour ----------
refresh((10, False))
assert pill._color == QColor(ST.LEVEL["bad"])
refresh((25, False))
assert pill._color == QColor(ST.LEVEL["warn"])
refresh((100, True))
assert pill.text().startswith("●  Nadajnik online (100%)")
pill.grab()                                                       # a full pill with a wave paints without a painter error
ok("the fill colour follows the level (low red, up to 30 % amber, otherwise green)")

# ---------- a game in the foreground: no repaint ----------
paints = []
pill.update = lambda *a: paints.append(1)
rx_games.in_game = lambda exe=None: True
pump(150)
assert not paints, "a game in the foreground: the wave does not repaint"
rx_games.in_game = lambda exe=None: False
pump(150)
assert paints, "back on the desktop: the wave repaints"
del pill.update
ok("the wave pauses while a game is in the foreground")

# ---------- offline ----------
refresh((100, True), online=False)
assert "offline" in pill.text() and not pill.property("battery") and not pill._timer.isActive()
ok("offline: the pill has no battery and no animation")
