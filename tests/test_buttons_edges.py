"""Receiver: stick inversion over the full range; a PS button held while a pad is enabled does not act on release.
    python3 tests/test_buttons_edges.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_buttons as B

from chk import check, fails, wait_for                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- stick inversion: full deflection in both directions ----------
inv = {"left": {"src": "left", "inv_x": True, "inv_y": True}}
check(B.apply_sticks(inv, 255, 0, 128, 128)[:2] == (0, 255), "inverted stick: 255 -> 0 and 0 -> 255 (was 255 -> 1)")
check(B.apply_sticks(inv, 128, 128, 128, 128)[:2] == (128, 128), "inverted stick: the centre stays the centre")
check(all(abs(B._inv(B._inv(v)) - v) <= 1 for v in range(256)), "double inversion gives the value back (±1)")

# ---------- PS held while the pad powers on ----------
PS = BIT[316]
H.reset(1); N._digital[1] = None
calls.clear()
C.PS_HOLD_ACTION = "gamebar"
state(1, mask=PS)                                   # first state after connecting: PS held since power-on
time.sleep(C.PS_HOLD_S + 0.15)
check(not any(c[0] == "k" for c in calls), "PS held since connecting does not open Game Bar")
state(1, mask=0); time.sleep(0.02)
check(not S.states[1]["mask"] & PS, "releasing PS from the pad power-on does not send PS to the game")
time.sleep(0.1)
state(1, mask=PS); state(1, mask=0)
check(S.states[1]["mask"] & PS, "the next short PS works normally (a pulse for the game)")
time.sleep(0.15); state(1)

sys.exit(1 if fails else 0)
