"""Receiver: console mode - a restart during the test run, an empty list of allowed programs blocks nothing.
    python3 tests/test_console_edges.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_console

from chk import check, fails, wait_for                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- console mode: a restart during "Przetestuj" ----------
saved_ctl = rx_console.ctl
c = rx_console.Console(rx_console.ConsoleConfig())
rx_console.ctl = c
c.on, c._revert = True, (time.monotonic() + 10, c.cfg)
check(not rx_console.is_on(), "console mode test in progress: a restart does not remember ON (the test goes back to OFF by itself)")
c._revert = None
check(rx_console.is_on(), "plain ON mode: a restart keeps it")
rx_console.ctl = saved_ctl

# ---------- console mode: an empty allow list = no block ----------
cc = rx_console.Console(rx_console.ConsoleConfig(allowed_apps=()))
FG[0] = "explorer.exe"
check(cc.blocked_by() is None, "empty allow list: the desktop does not block (it blocked everything before)")
FG[0] = "eldenring.exe"
check(cc.blocked_by() is None, "empty allow list: no block in a game either")
cc = rx_console.Console(rx_console.ConsoleConfig())
check(cc.blocked_by() == "eldenring.exe", "default list: a game blocks")
FG[0] = None

sys.exit(1 if fails else 0)
