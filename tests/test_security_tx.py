"""Sender: security review - protocol v3 with a key (freshness token, rejecting replayed recordings
and signed v2 packets after the switch, the switch remembered), v3 probe ping, the PC clock after a receiver restart.
    python3 tests/test_security_tx.py sender"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1", "--key", "sekret"])
from sender_config import C
import proto
import sender_net as net
from proto import (BODY, CMD_POWER_OFF, FRESH_PAST_MS, T_CMD, T_PING, T_PONG, T_PONG2, T_STATE, Codec, EpochClock,
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


TMP = tempfile.mkdtemp()
C.AUTH_FILE = os.path.join(TMP, "auth.json")
net.init()
check(net.codec.key == b"sekret" and not net.codec.v3, "key from --key, v2 format before the switch")

# ---------- receiver (v3) on the other side: PC clock, tokens from the sender clock ----------
pc_clock = EpochClock()
sender_t0 = []                                   # the sender's last ping (t0 on its clock, receive time)
def pc_token():
    if not sender_t0:
        return None
    t0, at = sender_t0[-1]
    return ((t0 + time.perf_counter_ns() - at) // 1_000_000) & 0xFFFFFFFF
pc = Codec(b"sekret", token=pc_token, now_ms=pc_clock.ms32)
pc.v3 = True

cmds = []
net.on_command = lambda i, cmd, arg: cmds.append((i, cmd, arg))

# ---------- v3 probe ping and the switch ----------
net.sock = FakeSock()
net.pings_sent = 0
t0 = net.clock.ns()
net.emit(T_PING, 255, t0)
net.send_raw(net.codec.pack(T_PING, 255, t0, v3=True))
v2, v3 = net.sock.sent
check(v2[0] >> 4 == proto.VER and v3[0] >> 4 == proto.VER3, "with a key before the switch: v2 ping and the v3 probe ping")
check(pc.parse(v2) is None and pc.legacy_t > 0, "the receiver after the switch rejects a signed v2 (recording / old version)")
p = pc.parse(v3)
check(p is not None and p[0] == T_PING, "v3 probe ping without the PC clock (token 0) accepted - pings are not checked")
sender_t0.append((t0, time.perf_counter_ns()))

pong2 = pc.pack(T_PONG2, 255, t0, pc_clock.ns() // 1000)
pong = pc.pack(T_PONG, 255, t0)
net.sock = FakeSock([pong2, pong])
net.on_readable()
check(net.codec.v3 and net.pc_clock.offset_us is not None, "v3 pong from the receiver: the sender switches to v3, PC clock known")
check(open(C.AUTH_FILE).read().strip() == f'{{"v3": "{proto.key_fingerprint(b"sekret")}"}}', "switch stored (key fingerprint)")

# ---------- commands: fresh once, repeat rejected, expired rejected, v2 rejected ----------
cmd = pc.pack(T_CMD, 0, CMD_POWER_OFF, 0)
net.sock = FakeSock([cmd, cmd])
net.on_readable()
check(cmds == [(0, CMD_POWER_OFF, 0)], f"v3 command run once, an exact repeat rejected: {cmds}")

old = Codec(b"sekret", token=lambda: (net.clock.ms32() - FRESH_PAST_MS - 2000) & 0xFFFFFFFF); old.v3 = True
cmds.clear(); stale = net.codec.stale
net.sock = FakeSock([old.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and net.codec.stale == stale + 1, "nagranie sprzed kilku sekund (znacznik poza oknem) odrzucone")

legacy = Codec(b"sekret")
net.codec.upgraded_t = float("-inf")             # right after the switch v2 means packets in flight (no warning)
net.sock = FakeSock([legacy.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and net.codec.legacy_t > 0, "after the switch a signed v2 packet (a recording from before the update) rejected")

other = EpochClock()                              # a recording from a previous sender run: a different clock
rec = Codec(b"sekret", token=other.ms32); rec.v3 = True
net.sock = FakeSock([rec.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds, "a token from another run (random clock offset) rejected")

wrong = Codec(b"inny", token=pc_token); wrong.v3 = True
net.sock = FakeSock([wrong.pack(T_CMD, 0, CMD_POWER_OFF, 0)])
net.on_readable()
check(not cmds and time.monotonic() - net.codec.mismatch_t < 1, "another key: rejected and reported as a key mismatch")

# ---------- sender on v3: state with the PC time, without the PC clock nothing goes out ----------
net.sock = FakeSock()
net.emit(T_STATE, 0, *([0] * 17))
p = pc.parse(net.sock.sent[-1])
check(p is not None and p[0] == T_STATE, "v3 state with the PC time token accepted by the receiver")
saved = net.pc_clock
net.pc_clock = net.PcClock()
net.sock = FakeSock()
net.emit(T_STATE, 0, *([0] * 17))
net.emit(T_PING, 255, net.clock.ns())
check(len(net.sock.sent) == 1 and pc.parse(net.sock.sent[0])[0] == T_PING,
      "no PC clock: the state does not go out (the receiver would reject it anyway), the ping does - the receiver answers it")
net.pc_clock = saved

# ---------- sender restart: the switch remembered ----------
net.codec = None
net.init()
check(net.codec.v3, "sender restart: v3 at once (no window for recorded v2 packets)")
C.KEY = b"nowy"; net.init()
check(not net.codec.v3, "key change: the switch starts over (another fingerprint)")
C.KEY = b""; net.init()
check(not net.codec.v3 and net.codec.pack(T_PING, 255, 1)[0] >> 4 == proto.VER, "no key: always v2 as before")
C.KEY = b"sekret"; net.init()

# ---------- PC clock: an offset jump (a new receiver process) clears the old measurements ----------
clk = net.PcClock()
t = net.clock.ns()
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
