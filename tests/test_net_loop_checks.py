"""Receiver: net loop checks, peer takeover by pings, the repeat scheduler, the latency summary.
    python3 tests/test_net_loop_checks.py receiver"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# 1. periodic checks keep running when _receive() yields nothing (junk datagram / timeout)
calls = {"stale": 0}
orig_recv, orig_stale = N._receive, N.check_stale
def junk_recv():
    time.sleep(0.005)
    return None
N._receive = junk_recv
N.check_stale = lambda now: calls.__setitem__("stale", calls["stale"] + 1)
S.stop.clear()
th = threading.Thread(target=N.net_loop, daemon=True); th.start()
time.sleep(0.5)
S.stop.set(); th.join(2)
N._receive, N.check_stale = orig_recv, orig_stale
S.stop.clear()
check(calls["stale"] >= 2, f"net_loop runs the silence checks without any valid packet ({calls['stale']})")

# 2. a replayed ping from another address does not take the peer over while the sender is alive (only with a key)
real, other = ("10.0.0.1", 5000), ("10.0.0.9", 6000)
old_key = C.KEY
try:
    C.KEY = b"k" * 16
    S.peer = None; N._peer_t[0] = float("-inf")
    t = time.monotonic()
    N._adopt_peer(real, t)
    N._adopt_peer(other, t + 0.1, from_ping=True)
    check(S.peer == real, "key: a ping from a foreign address does not move the peer")
    N._adopt_peer(other, t + S.SENDER_TIMEOUT + 0.5, from_ping=True)
    check(S.peer == other, "key: after the sender went quiet a ping may move the peer")
    C.KEY = None
    S.peer = real; N._peer_t[0] = time.monotonic()
    N._adopt_peer(other, time.monotonic() + 0.1, from_ping=True)
    check(S.peer == other, "no key: the old behaviour (a ping moves the peer)")
finally:
    C.KEY = old_key
    S.peer = None

# 4. repeat scheduler: one copy now, the rest later, same packet; flush sends the pending ones at once
sent = []
orig_peer = N.send_peer
N.send_peer = lambda pkt: sent.append((time.monotonic(), pkt))
try:
    N.send_repeat = ORIG_SEND_REPEAT
    t0 = time.monotonic()
    N.send_repeat(b"abc", 3)
    check(len(sent) == 1, "repeat: the first copy goes out at once")
    time.sleep(N.REPEAT_DELAYS[1] + 0.05)
    check(len(sent) == 3 and all(p == b"abc" for _t, p in sent), f"repeat: the remaining copies follow ({len(sent)})")
    check(sent[2][0] - t0 >= N.REPEAT_DELAYS[1] - 0.005, "repeat: copies are not early")
    sent.clear()
    N.send_repeat(b"x", 3); N.flush_repeats()
    check(len(sent) == 3, "repeat: flush_repeats sends the pending copies immediately")
    time.sleep(N.REPEAT_DELAYS[-1] + 0.05)
    check(len(sent) == 3, "repeat: nothing is sent twice after the flush")
    before = threading.active_count()
    for _ in range(200):
        N.send_repeat(b"y", 3)
    check(threading.active_count() <= before + 1, "repeat: no thread per copy")
    N.flush_repeats()
finally:
    N.send_peer = orig_peer

# 6. latency summary still filters by window
import rx_latency as L
L._samples.clear()
check(L.summary(0) is None, "latency: no samples -> None")

sys.exit(1 if fails else 0)
