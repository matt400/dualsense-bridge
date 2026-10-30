"""Receiver: trigger effects check (empty bytes are no effect); the restart guard is cancelled after the cleanup.
    python3 tests/test_restart_and_trigger_guards.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_feedback as F

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- spusty: puste bajty to nie efekt ----------
check(not F._has_effect((0, 0, bytes(F.TRIG_RAW_LEN), bytes(F.TRIG_RAW_LEN), 0)), "same zera (też surowe bajty) - brak efektu")
check(F._has_effect((0, 3, 0)) and F._has_effect((0, bytes(3) + b"\1")), "liczba lub bajt różny od zera - jest efekt")

# ---------- strażnik restartu: anulowany po zakończeniu sprzątania ----------
src = open(os.path.join(RX, "receiver_controller.py"), encoding="utf-8").read()
fin = src[src.index("def _shutdown("):src.index("def main(")]
check("guard.cancel()" in fin and fin.index("rx_winapi.end_timer()") < fin.index("guard.cancel()") < fin.index("_restart_now(console_on)"),
      "receiver_controller: strażnik anulowany po sprzątaniu, przed startem nowego procesu")

sys.exit(1 if fails else 0)
