"""Receiver: console mode - a restart during the test run, an empty list of allowed programs blocks nothing.
    python3 tests/test_console_edges.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_console

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.01)
    return pred()


# ---------- tryb konsoli: restart w trakcie "Przetestuj" ----------
saved_ctl = rx_console.ctl
c = rx_console.Console(rx_console.ConsoleConfig())
rx_console.ctl = c
c.on, c._revert = True, (time.monotonic() + 10, c.cfg)
check(not rx_console.is_on(), "test trybu konsoli w toku: restart nie zapamiętuje ON (test sam wraca do OFF)")
c._revert = None
check(rx_console.is_on(), "zwykły tryb ON: restart go zachowuje")
rx_console.ctl = saved_ctl

# ---------- tryb konsoli: pusta lista dozwolonych programów = bez blokady ----------
cc = rx_console.Console(rx_console.ConsoleConfig(allowed_apps=()))
FG[0] = "explorer.exe"
check(cc.blocked_by() is None, "pusta lista dozwolonych: pulpit nie blokuje (wcześniej blokowało wszystko)")
FG[0] = "eldenring.exe"
check(cc.blocked_by() is None, "pusta lista dozwolonych: bez blokady także w grze")
cc = rx_console.Console(rx_console.ConsoleConfig())
check(cc.blocked_by() == "eldenring.exe", "domyślna lista: gra blokuje")
FG[0] = None

sys.exit(1 if fails else 0)
