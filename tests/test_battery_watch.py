"""Receiver: the pad low-battery rule (rx_status.BatteryWatch) and the tray tooltip line - no Qt."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, S)
import rx_status as ST
from proto import STATE_CHARGING

from chk import ok

LOW = ST.low_pct()
w = ST.BatteryWatch(2)


def read(i, pct, flags=0, alive=True):
    S.batt_info[i] = (pct, flags)
    S.alive[i] = alive
    return w.check(i, pct, flags)


# ---------- one warning per discharge ----------
assert not read(0, LOW + 20), "a healthy pad: nothing"
assert read(0, LOW), "at the threshold: the warning"
assert not read(0, LOW - 1) and not read(0, LOW), "the same discharge warns once"
assert w.warned[0] and not w.warned[1], "per pad"
ok("the warning comes once per discharge and per pad")

# ---------- re-arming ----------
assert not read(0, LOW + 5) and w.warned[0], "just above the threshold is still the same discharge"
assert not read(0, LOW + 6) and not w.warned[0], "climbing past the margin re-arms"
assert read(0, LOW), "and the next discharge warns again"
assert not read(0, LOW, STATE_CHARGING) and not w.warned[0], "plugging the cable re-arms (and a charging pad is never 'low')"
ok("the warning re-arms after charging or a recovery above threshold + margin")

# ---------- not warned for a pad that is gone or unknown ----------
assert not read(1, LOW, alive=False), "a disconnected pad does not warn"
assert not read(1, 255), "an unknown level does not warn"
assert not w.warned[1]
ok("a disconnected pad or an unknown level never warns")

# ---------- the tooltip line ----------
assert ST.battery_tip(0, 80, 0) == "Pad 1: 80%"
assert ST.battery_tip(1, 80, STATE_CHARGING).startswith("Pad 2: 80% (")
assert ST.battery_tip(0, 255, 0) == "Pad 1: b/d"
ok("tray tooltip line: level, charging note, unknown level")
