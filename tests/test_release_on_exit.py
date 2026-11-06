"""Receiver: regressions from the sixth review - releasing the triggers on close, the update result after a sender
restart, waking the DSU thread only with clients, a warning about another HMAC key."""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_net as N
import rx_state as S
import rx_events as ev
import rx_feedback as F
import rx_update as U
from proto import T_PING, T_STATE, TRIG2_OFF, UPD_DONE, UPD_FAILED, UPD_NO_CHANGE, UPD_NONE, UPD_RUNNING, Codec

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

# ---------- releasing trigger effects when the receiver closes ----------
out = []
send = lambda i, vals, n: out.append((i, tuple(vals), n))
F.trig_sent[:] = [((1, 2, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0), 0.0), (TRIG2_OFF, 0.0)]
F.release_triggers(send)
check(out == [(0, TRIG2_OFF, F.TRIG_REPEAT)] and F.trig_sent[0] is None,
      f"closing: a pad with effects gets 'no effects' (a pad without effects - nothing): {out}")
out.clear()
F.trig_sent[:] = [((3, 6, 0, 0), 0.0), None]
F.release_triggers(send)
check(out == [(0, (0, 0, 0, 0), F.TRIG_REPEAT)], "older sender (trig packet) - release with the same packet")
out.clear(); F.release_triggers(send)
check(out == [], "nothing the second time (already released)")

# ---------- the sender update result after its restart ----------
old = "a1b2c3d"
check(U.sender_state((old, 1, UPD_RUNNING), old) == UPD_RUNNING, "in progress: the same commit, RUNNING - waiting")
check(U.sender_state(("e4f5a6b", 2, UPD_NONE), old) == UPD_DONE,
      "a new sender process (UPD_NONE) with another commit = updated")
check("zaktualizowany" in U.sender_text(("e4f5a6b", 2, UPD_NONE), "", old) and
      "nie odpowiedział" not in U.sender_text(("e4f5a6b", 2, UPD_NONE), "", old),
      "the window does not write 'the sender did not respond' after a successful update")
check(U.sender_state((old, 1, UPD_NONE), old) == UPD_NONE and "nie odpowiedział" in U.sender_text((old, 1, UPD_NONE), "", old),
      "the same commit without a result - still 'did not respond'")
check(U.sender_state((old, 1, UPD_NO_CHANGE), old) == UPD_NO_CHANGE and U.sender_state(("x", 1, UPD_FAILED), old) == UPD_FAILED,
      "the result reported by the sender wins")
check(U.sender_state(("e4f5a6b", 2, UPD_NONE)) == UPD_NONE, "no commit from before the update - as before")

# ---------- DSU: state changes do not wake the thread without clients ----------
S.push = None
S.dsu_wake.clear()
S.dsu_active = False
S.notify(0)
check(not S.dsu_wake.is_set(), "without DSU clients notify() does not wake the DSU thread")
S.dsu_active = True
S.notify(0)
check(S.dsu_wake.is_set(), "with a DSU client - wakes as before")
S.dsu_active = False
import rx_dsu
rx_dsu.dsu_clients.clear(); S.dsu_wake.clear()
rx_dsu._subscribe(("127.0.0.1", 50000))
check(S.dsu_active and S.dsu_wake.is_set(), "a DSU subscription turns waking on (and wakes the thread for the first state)")
rx_dsu.dsu_clients.clear(); S.dsu_active = False

# ---------- another HMAC key: a warning in the log ----------
N.codec = Codec()
keyed = Codec(b"sekret")
check(N.codec.parse(keyed.pack(T_PING, 255, 1)) is None, "a receiver without a key rejects a sender with a key")
n0 = len(ev.since(0))
N.check_key(time.monotonic())
N.check_key(time.monotonic())
new = ev.since(0)[n0:]
check(len(new) == 1 and "klucz HMAC" in new[0].text and new[0].level == "warn", "one warning in the log (not every 100 ms)")
N._key_warned[0] = float("-inf")
N.codec = Codec()
N.check_key(time.monotonic())
check(len(ev.since(0)) == n0 + 1, "no rejected packets - no warning")

if fails:
    print("FAIL", fails)
    sys.exit(1)
print("HUNT6 OK")
