"""Receiver: stick inversion over the full range; a PS button held while a pad is enabled does not act on release.
    python3 tests/test_buttons_edges.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_buttons as B

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.01)
    return pred()


# ---------- odwrócenie gałki: pełne wychylenie w obie strony ----------
inv = {"left": {"src": "left", "inv_x": True, "inv_y": True}}
check(B.apply_sticks(inv, 255, 0, 128, 128)[:2] == (0, 255), "odwrócona gałka: 255 -> 0 i 0 -> 255 (było 255 -> 1)")
check(B.apply_sticks(inv, 128, 128, 128, 128)[:2] == (128, 128), "odwrócona gałka: środek zostaje środkiem")
check(all(abs(B._inv(B._inv(v)) - v) <= 1 for v in range(256)), "podwójne odwrócenie wraca do wartości (±1)")

# ---------- PS trzymane przy włączaniu pada ----------
PS = BIT[316]
H.reset(1); N._digital[1] = None
calls.clear()
C.PS_HOLD_ACTION = "gamebar"
state(1, mask=PS)                                   # pierwszy stan po połączeniu: PS trzymane od włączania
time.sleep(C.PS_HOLD_S + 0.15)
check(not any(c[0] == "k" for c in calls), "PS trzymane od połączenia nie otwiera Game Bar")
state(1, mask=0); time.sleep(0.02)
check(not S.states[1]["mask"] & PS, "puszczenie PS z włączania pada nie wysyła PS do gry")
time.sleep(0.1)
state(1, mask=PS); state(1, mask=0)
check(S.states[1]["mask"] & PS, "kolejne krótkie PS działa normalnie (impuls dla gry)")
time.sleep(0.15); state(1)

sys.exit(1 if fails else 0)
