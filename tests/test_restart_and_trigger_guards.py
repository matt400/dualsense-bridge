"""Receiver: trigger effects check (empty bytes are no effect); the restart guard is cancelled after the cleanup.
    python3 tests/test_restart_and_trigger_guards.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_feedback as F

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- triggers: empty bytes are not an effect ----------
check(not F._has_effect((0, 0, bytes(F.TRIG_RAW_LEN), bytes(F.TRIG_RAW_LEN), 0)), "all zeros (raw bytes too) - no effect")
check(F._has_effect((0, 3, 0)) and F._has_effect((0, bytes(3) + b"\1")), "a non-zero number or byte - an effect")

# ---------- restart watchdog: cancelled after cleanup finishes ----------
src = open(os.path.join(RX, "receiver_controller.py"), encoding="utf-8").read()
fin = src[src.index("def _shutdown("):src.index("def main(")]
check("guard.cancel()" in fin and fin.index("rx_winapi.end_timer()") < fin.index("guard.cancel()") < fin.index("_restart_now(console_on)"),
      "receiver_controller: watchdog cancelled after cleanup, before the new process starts")

sys.exit(1 if fails else 0)
