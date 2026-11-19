"""Sender: security review - protocol v3 with a key (freshness token, rejecting replayed recordings
and signed v2 packets), the PC clock after a receiver restart.
    python3 tests/test_security_tx.py sender"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1", "--key", "sekret"])
from sender_config import C
import proto
import sender_net as net
from proto import (BODY, CMD_POWER_OFF, FRESH_PAST_MS, T_CMD, T_PING, T_PONG2, T_STATE, Codec, EpochClock,
                   token_fresh)

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


class FakeSock:
    def __init__(s, pkts=()):
        s.pkts, s.sent = list(pkts), []
    def recv(s, n):
        if s.pkts:
            return s.pkts.pop(0)
        raise BlockingIOError
    def send(s, data):
        s.sent.append(data)


net.init()
check(net.link.codec.key == b"sekret", "key from --key")

# ---------- receiver (v3) on the other side: PC clock, tokens from the sender clock ----------
pc_clock = EpochClock()
sender_t0 = []                                   # the sender's last ping (t0 on its clock, receive time)
def pc_token():
    if not sender_t0:
        return None
    t0, at = sender_t0[-1]
    return ((t0 + time.perf_counter_ns() - at) // 1_000_000) & 0xFFFFFFFF
pc = Codec(b"sekret", token=pc_token, now_ms=pc_clock.ms32)

cmds = []
net.handlers.command = lambda i, cmd, arg: cmds.append((i, cmd, arg))

# ---------- with a key every packet is v3 from the start ----------
net.link.sock = FakeSock()
t0 = net.link.clock.ns()
net.emit(T_PING, 255, t0)
(ping,) = net.link.sock.sent
check(ping[0] >> 4 == proto.VER3, "with a key the ping is v3 at once (no switch, no probe)")
p = pc.parse(ping)
check(p is not None and p[0] == T_PING, "v3 ping without the PC clock (token 0) accepted - pings are not checked")
sender_t0.append((t0, time.perf_counter_ns()))
signed_v2 = proto.FRAMED[T_PING].pack(proto.HDR0 | T_PING, 255, 1, t0)
signed_v2 += __import__("hmac").digest(b"sekret", signed_v2, "sha256")[:proto.MAC_LEN]
check(pc.parse(signed_v2) is None and pc.legacy_t > 0, "a signed v2 packet (a version from before v3) is rejected and noted")

net.link.sock = FakeSock([pc.pack(T_PONG2, 255, t0, pc_clock.ns() // 1000)])
net.on_readable()
check(net.link.pc_clock.offset_us is not None and net.link.pongs == 1, "pong2 from the receiver: PC clock known, RTT counted")

# ---------- commands: fresh once, repeat rejected, expired rejected, v2 rejected ----------
cmd = pc.pack(T_CMD, 0, CMD_POWER_OFF, 0)
net.link.sock = FakeSock([cmd, cmd])
net.on_readable()
check(cmds == [(0, CMD_POWER_OFF, 0)], f"v3 command run once, an exact repeat rejected: {cmds}")

old = Codec(b"sekret", token=lambda: (net.link.clock.ms32() - FRESH_PAST_MS - 2000) & 0xFFFFFFFF)
cmds.clear(); stale = net.link.codec.stale
net.link.sock = FakeSock([old.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and net.link.codec.stale == stale + 1, "nagranie sprzed kilku sekund (znacznik poza oknem) odrzucone")

other = EpochClock()                              # a recording from a previous sender run: a different clock
rec = Codec(b"sekret", token=other.ms32)
net.link.sock = FakeSock([rec.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds, "a token from another run (random clock offset) rejected")

wrong = Codec(b"inny", token=pc_token)
net.link.sock = FakeSock([wrong.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and time.monotonic() - net.link.codec.mismatch_t < 1, "another key: rejected and reported as a key mismatch")

# ---------- sender on v3: state with the PC time, without the PC clock nothing goes out ----------
net.link.sock = FakeSock()
net.emit(T_STATE, 0, *([0] * 17))
p = pc.parse(net.link.sock.sent[-1])
check(p is not None and p[0] == T_STATE, "v3 state with the PC time token accepted by the receiver")
saved = net.link.pc_clock
net.link.pc_clock = net.PcClock()
net.link.sock = FakeSock()
net.emit(T_STATE, 0, *([0] * 17))
net.emit(T_PING, 255, net.link.clock.ns())
check(len(net.link.sock.sent) == 1 and pc.parse(net.link.sock.sent[0])[0] == T_PING,
      "no PC clock: the state does not go out (the receiver would reject it anyway), the ping does - the receiver answers it")
net.link.pc_clock = saved

# ---------- no key: always v2 ----------
C.KEY = b""; net.init()
check(net.link.codec.pack(T_PING, 255, 1)[0] >> 4 == proto.VER, "no key: always v2")
C.KEY = b"sekret"; net.init()

# ---------- PC clock: an offset jump (a new receiver process) clears the old measurements ----------
clk = net.PcClock()
t = net.link.clock.ns()
clk.pong(t - 100_000, 5_000_000_000, t)
first = clk.offset_us
clk.pong(t - 2_000_000, 9_000_000_000, t)         # a bigger RTT, but the PC clock moved by 4000 s
check(clk.offset_us != first and abs(clk.offset_us - first - 4_000_000_000) < 10_000,
      "a new PC clock offset wins at once despite a bigger RTT (otherwise v3 tokens are rejected for ~3 s)")

check(token_fresh(5, 0xFFFFFFF0) and token_fresh(0xFFFFFFF0, 10) and not token_fresh(0, 10_000),
      "the freshness window survives the 32-bit ms counter wrapping")

if fails:
    sys.exit(1)
print("SECURITY TX OK")
