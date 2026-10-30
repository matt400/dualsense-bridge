"""Receiver: a repeated game report does not wake the feedback thread, a change does.
    python3 tests/test_feedback_wakeups.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# 5. feedback: a repeated game report does not wake the thread, a change does
F.fb[0].update(large=0, small=0, set=False, rgb=False)
F.fb_event.clear()
F.game(0, large=100, small=0)
check(F.fb_event.is_set(), "feedback: the first report wakes the thread")
F.fb_event.clear()
F.game(0, large=100, small=0)
check(not F.fb_event.is_set(), "feedback: an unchanged report does not wake the thread")
F.game(0, large=50, small=0)
check(F.fb_event.is_set(), "feedback: a changed motor value wakes the thread")
F.fb_event.clear()
F.game(0, rgb=(1, 2, 3))
check(F.fb_event.is_set(), "feedback: a report without motors during rumble wakes the thread (gap timer)")
F.fb_event.clear()
F.fb[0].update(large=0, small=0)
F.game(0, rgb=(1, 2, 3))
check(not F.fb_event.is_set(), "feedback: the same colour with no rumble does not wake the thread")
F.game(0, rgb=(9, 2, 3))
check(F.fb_event.is_set(), "feedback: a new colour wakes the thread")

sys.exit(1 if fails else 0)
