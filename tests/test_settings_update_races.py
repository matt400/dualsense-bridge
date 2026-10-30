"""Receiver: concurrent rx_settings.update() calls do not lose changes.
    python3 tests/test_settings_update_races.py receiver"""
import os, sys, threading
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_settings
from dataclasses import replace

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# 3. concurrent rx_settings.update() calls do not lose changes
rx_settings.cur = rx_settings.Settings()
def upd(name):
    for k in range(50):
        cur = getattr(rx_settings.cur, name)
        rx_settings.update(None, **{name: cur})
ths = [threading.Thread(target=upd, args=(n,)) for n in ("mouse", "master", "ui")]
for x in ths: x.start()
for x in ths: x.join()
check(isinstance(rx_settings.cur, rx_settings.Settings), "settings: parallel update() calls finish without a deadlock")
mouse_new = replace(rx_settings.cur.mouse)
seen = []
rx_settings.on_change(lambda o, n: seen.append(n))
rx_settings.update(None, mouse=mouse_new)
check(len(seen) == 1, "settings: a listener is still called once per update")

sys.exit(1 if fails else 0)
