"""Receiver: the background git check and an update never run at once.
    python3 tests/test_update_vs_check.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa

import threading
from chk import check, fails, wait_for                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- git: the background check and the update do not run at the same time ----------
import rx_update
held = threading.Event(); release = threading.Event()
def hold():
    with rx_update._git_lock:
        held.set(); release.wait(2)
th = threading.Thread(target=hold); th.start(); held.wait(2)
real_isdir = rx_update.os.path.isdir
rx_update.os.path.isdir = lambda p: True
t0 = time.monotonic(); r = rx_update.check_available(TMP, git="git")
check(r is None and time.monotonic() - t0 < 0.1, "update in progress -> background check skipped at once")
rx_update.gitupdate.git_pull, real_pull = (lambda root, git, **kw: (rx_update.UPD_NO_CHANGE, "", "a", "a", [])), rx_update.gitupdate.git_pull
threading.Timer(0.3, release.set).start()
t0 = time.monotonic(); rx_update.update(TMP, git="git", pip=False)
check(time.monotonic() - t0 >= 0.25, "the update waits for the running check to finish")
rx_update.gitupdate.git_pull, rx_update.os.path.isdir = real_pull, real_isdir
th.join(2)

sys.exit(1 if fails else 0)
