"""Receiver: a stale Bluetooth busy state is dropped; apply_tv retries after a WinAPI error.
    python3 tests/test_bt_busy_and_tv_retry.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from rxenv import *                                              # noqa
import rx_bt as BT
import rx_display as D

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# 1. a sender that went silent in the middle of an operation does not keep the buttons locked
old = BT.status
BT.status = BT.Status(1, 0, 0, 0, BT.BT_PAIR, BT.BTR_RUNNING, "aa:bb:cc:dd:ee:ff", 100.0)
check(BT.any_busy(101.0) and BT.busy_op("aa:bb:cc:dd:ee:ff", 101.0) == BT.BT_PAIR, "bt: a fresh running operation is busy")
check(not BT.any_busy(100.0 + BT.STALE_S + 1) and BT.busy_op("aa:bb:cc:dd:ee:ff", 100.0 + BT.STALE_S + 1) is None,
      "bt: a stale running operation is not busy")
BT.status = old

# 2. apply_tv retries active_sources() after an OSError instead of aborting the TV switch
calls = {"n": 0}
def flaky():
    calls["n"] += 1
    if calls["n"] < 3:
        raise OSError("DisplayConfigGetDeviceInfo(source name) failed")
    return [("adapter", 1, r"\\.\DISPLAY2")]
real_sleep = time.sleep
orig = (D.active_sources, D.set_resolution, D.set_scale, D.time.sleep)
D.active_sources, D.set_resolution, D.set_scale = flaky, (lambda dev, w, h: f"{w}x{h}"), (lambda a, s, p: f"scale {p}%")
D.time.sleep = lambda _s: None
try:
    out = D.apply_tv(1920, 1080, 100, settle_s=5.0)
    check(calls["n"] == 3 and "1920x1080" in out, "apply_tv: survives transient OSError from active_sources")
    D.active_sources = lambda: (_ for _ in ()).throw(OSError("boom"))
    D.time.sleep = lambda _s: real_sleep(0.01)
    try:
        D.apply_tv(1920, 1080, 0, settle_s=0.05)
        check(False, "apply_tv: a permanent OSError still ends in an error")
    except RuntimeError as e:
        check("kept failing" in str(e), "apply_tv: a permanent OSError still ends in an error")
finally:
    D.active_sources, D.set_resolution, D.set_scale, D.time.sleep = orig

sys.exit(1 if fails else 0)
