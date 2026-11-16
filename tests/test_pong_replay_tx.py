"""Sender: a pong that answers a replayed old ping (or a ping of a previous process) is not a round trip.

A ping is exempt from the v3 freshness check, so a recorded one replayed to the receiver gets a fresh, correctly signed pong back.
Its "RTT" (seconds or hours) must not reach the RTT statistics, the info packet's median or the receiver-alive timer.
    python3 tests/test_pong_replay_tx.py sender
"""
import sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
from chk import check, fails
import sender_config; sender_config.init(["127.0.0.1"])
import sender_net as net
from proto import BODY, HEADER_LEN, T_PONG

net.init()


def pong(t0: int) -> bytes:
    """A pong body behind a dummy header, as _on_pong reads it."""
    return bytes(HEADER_LEN) + BODY[T_PONG].pack(t0)


now = net.link.clock.ns()
net.link._on_pong(pong(now - 5 * net.MAX_RTT_NS))            # a ping recorded 5 s ago and replayed
check(not net.link.rtts and not net.link.recent_rtt and net.link.pongs == 0, "a reply to an old ping does not go into the RTT stats")
check(net.link.last_pong == float("-inf"), "a reply to an old ping does not pose as a live receiver")
net.link._on_pong(pong(now + net.MAX_RTT_NS))                  # "sent" in the future: another clock base
check(not net.link.rtts and net.link.pongs == 0, "a ping from another clock base (the future) rejected too")
net.link._on_pong(pong(net.link.clock.ns() - 3_000_000))            # an ordinary 3 ms round trip
check(len(net.link.rtts) == 1 and 2.5 < net.link.rtts[0] < 1000 and net.link.pongs == 1, "a plain pong counts as before")
check(net.rtt_x10() != net.RTT_NONE, "info gets the RTT median")
sys.exit(1 if fails else 0)
