"""Receiver: concurrent rx_settings.update() calls do not lose changes.
    python3 tests/test_settings_update_races.py receiver"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_settings
from dataclasses import replace

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# Each thread keeps replacing its own section with a new object. update() is a read-modify-write: without the lock a thread
# that read `cur` before another thread's apply() writes the old section back, and that change is lost. A slow replace()
# holds the read-modify-write window open long enough for the threads to overlap every time.
def slow_replace(obj, **changes):
    time.sleep(0.001)
    return replace(obj, **changes)


rx_settings.replace = slow_replace
rx_settings.cur = rx_settings.Settings()
SECTIONS = {"mouse": rx_settings.MouseConfig, "master": rx_settings.MasterConfig, "ui": rx_settings.UiConfig}
last: dict[str, object] = {}


def upd(name):
    for _ in range(20):
        last[name] = SECTIONS[name]()            # equal to the default, but a new object: `is` tells whose write survived
        rx_settings.update(None, **{name: last[name]})


ths = [threading.Thread(target=upd, args=(n,)) for n in SECTIONS]
for x in ths: x.start()
for x in ths: x.join(10)
check(not any(x.is_alive() for x in ths), "settings: parallel update() calls finish (no deadlock)")
lost = [n for n in SECTIONS if getattr(rx_settings.cur, n) is not last[n]]
check(not lost, f"settings: the last write of every thread is in cur (lost: {lost})")
rx_settings.replace = replace

seen = []
rx_settings.on_change(lambda o, n: seen.append(n))
rx_settings.update(None, mouse=replace(rx_settings.cur.mouse))
check(len(seen) == 1, "settings: a listener is still called once per update")

sys.exit(1 if fails else 0)
