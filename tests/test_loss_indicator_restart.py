"""Receiver: regressions from the eighteenth review.
  - packet loss indicator: a gap longer than ~1000 packets (1-4 s) counted as a sender restart - 0 % loss
    exactly at the biggest dropouts; a restart (a new random number) is still not a loss
  - a quick receiver restart (< 3 s): v3 packets with the clock token of the old process gave a false
    "expired packets (replayed recording)" in the log; a real recording after the clock sync still warns
    python3 tests/test_loss_indicator_restart.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_events as ev

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- packet loss over a long gap ----------
def run(gap_packets, gap_s, rate=500):
    """1 s of packets every 1/rate, silence gap_s, a number jump of gap_packets, then packets again -> link_loss [%]."""
    link = N.LinkStats(2)
    t, sq = 100.0, 1000
    link.t0 = t
    for _ in range(rate):
        link.packet(sq, t); sq += 1; t += 1 / rate
    link.tick(t + 0.01)                                  # window 1: rate measured
    t += 0.01
    t += gap_s
    sq = (sq + gap_packets) & 0xFFFF
    for _ in range(50):
        link.packet(sq, t); sq = (sq + 1) & 0xFFFF; t += 1 / rate
    link.tick(t + 1.0)
    return S.link_loss


loss = run(gap_packets=1500, gap_s=3.0)
check(loss is not None and loss > 90, f"a 3 s gap (1500 packets lost) = ~97 % loss, not 0 % ({loss})")
check((run(gap_packets=20, gap_s=0.04) or 0) > 20, "a short gap still counted as before")
loss = run(gap_packets=30000, gap_s=2.0)
check(loss is None or loss < 1, f"a sender restart (a jump of 30000 after 2 s) is not a loss ({loss})")

# ---------- false warning about expired packets after a restart ----------
codec = N.codec
warned = []
orig_warn = ev.warn
ev.warn = lambda text, *a, **k: warned.append(text)
try:
    N._first_ping[0] = None
    N._auth_warned["stale"] = float("-inf")
    now = 1000.0
    codec.stale_t = now - 0.2                            # a v3 packet with the old clock, before a ping came
    N.check_key(now)
    check(not warned, "packets with the previous process's clock before the first ping - no warning")
    N._first_ping[0] = now                               # the first ping of the new process
    codec.stale_t = now + 0.3                            # the sender has not got a pong2 yet
    N.check_key(now + 0.4)
    check(not warned, "right after the first ping (the sender syncs its clock) - no warning")
    codec.stale_t = now + 5.0                            # po synchronizacji: prawdziwe nagranie
    N.check_key(now + 5.1)
    check(len(warned) == 1, "an expired packet after the clock sync - a warning as before")
    N._first_ping[0] = None
    N.note_ping(123, 456)
    check(N._first_ping[0] is not None, "the first ping remembered")
finally:
    ev.warn = orig_warn

print("ALL OK" if not fails else f"FAIL {len(fails)}")
sys.exit(1 if fails else 0)
