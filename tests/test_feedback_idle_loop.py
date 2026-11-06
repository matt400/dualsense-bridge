"""Receiver: regressions from the nineteenth review.
  - feedback thread: a pad without a signal (alive = False, virtual DS4 still attached) with non-zero game rumble
    computed the wait time from a negative value -> a loop without sleep (100 % CPU, the network thread loses the GIL) until detach
    python3 tests/test_feedback_idle_loop.py receiver"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

S.alive[0] = False
F.fb[0].update(large=100, small=0, set=True, t=time.monotonic() - 5.0)     # the game holds the rumble, the last report long ago
waits = []
real_wait = F.fb_event.wait
def counting_wait(timeout=None):
    waits.append(timeout)
    return real_wait(timeout)
F.fb_event.wait = counting_wait
S.stop.clear()
th = threading.Thread(target=F.feedback_thread, args=(lambda i, v: None,), daemon=True)
th.start()
time.sleep(0.4)
S.stop.set(); F.fb_event.set(); th.join(2)
check(len(waits) < 20, f"a pad without a signal does not spin the feedback loop ({len(waits)} turns in 0.4 s)")
sys.exit(1 if fails else 0)
