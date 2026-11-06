"""Sender: command repeats (cmd, btcmd, cfgset) pass the number filter separately for each command.
The receiver sends every command 3x with the same number. When the first copy of the first command is lost and the second command has already
arrived, a repeat of the first must not count as late (one filter key per packet type lost such a command for good).
Copies of the same command and an older value of the same setting must still be rejected.
    python3 tests/test_repeat_filter_tx.py sender"""
import sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])      # no key: this checks the number filter, not the v3 codec's repeat memory
import cfgspec
import sender_net as net
from proto import BT_CONNECT, BT_LIST, CFG_SET, CMD_GYRO_CAL, CMD_POWER_OFF, T_BTCMD, T_CFGSET, T_CMD, Codec, SeqFilter

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


class FakeSock:
    def __init__(s, pkts=()):
        s.pkts = list(pkts)
    def recv(s, n):
        if s.pkts:
            return s.pkts.pop(0)
        raise BlockingIOError


net.init()
pc = Codec()                                                  # receiver on the other side, no key
cfgs, cmds, bts = [], [], []
net.on_cfg = lambda authenticated, op, key, ref, number: cfgs.append((key, number))
net.on_command = lambda idx, cmd, arg: cmds.append((idx, cmd, arg))
net.on_bt = lambda op, arg, mac: bts.append((op, arg))


def deliver(*packets):
    """Deliver the packets in this order to a fresh filter; return what reached the handlers."""
    net.fb_filter = SeqFilter(1.0)
    for lst in (cfgs, cmds, bts):
        lst.clear()
    net.sock = FakeSock(packets)
    net.on_readable()
    return list(cfgs), list(cmds), list(bts)


def cfg(name, value):
    return pc.pack(T_CFGSET, 255, CFG_SET, cfgspec.BY_KEY[name], 1, value)


HB, VOL = cfgspec.BY_KEY["heartbeat_s"], cfgspec.BY_KEY["speaker_volume"]

# ---------- cfgset ----------
a, b = cfg("heartbeat_s", 0.05), cfg("speaker_volume", 120)
got = deliver(b, a)[0]                                        # the first copy of a got lost: b, then a's repeat
check(sorted(k for k, _ in got) == sorted((HB, VOL)), f"cfgset: a repeat of an older setting after a newer, different setting gets through ({got})")
got = deliver(a, b, a, b)[0]
check(len(got) == 2, f"cfgset: copies of the same packet are rejected ({got})")
old, new = cfg("heartbeat_s", 0.05), cfg("heartbeat_s", 0.06)
got = deliver(new, old)[0]
check(got == [(HB, 0.06)], f"cfgset: an older value of the same setting after a newer one is rejected ({got})")

# ---------- cmd ----------
c1, c2 = pc.pack(T_CMD, 0, CMD_GYRO_CAL, 3), pc.pack(T_CMD, 0, CMD_POWER_OFF, 0)
got = deliver(c2, c1)[1]
check({g[1] for g in got} == {CMD_GYRO_CAL, CMD_POWER_OFF}, f"cmd: a repeat of another command after a newer command gets through ({got})")
got = deliver(c1, c2, c1, c2)[1]
check(len(got) == 2, f"cmd: copies of the same command are rejected ({got})")
old, new = pc.pack(T_CMD, 0, CMD_GYRO_CAL, 3), pc.pack(T_CMD, 0, CMD_GYRO_CAL, 0)
got = deliver(new, old)[1]
check(got == [(0, CMD_GYRO_CAL, 0)], f"cmd: an older command of the same kind after a newer one is rejected ({got})")
other = pc.pack(T_CMD, 1, CMD_GYRO_CAL, 3)
got = deliver(pc.pack(T_CMD, 0, CMD_GYRO_CAL, 3), other)[1]
check(len(got) == 2, f"cmd: commands for different pads do not block each other ({got})")

# ---------- btcmd ----------
mac = b"\xaa\xbb\xcc\xdd\xee\xff"
b1, b2 = pc.pack(T_BTCMD, 255, BT_LIST, 0, bytes(6)), pc.pack(T_BTCMD, 255, BT_CONNECT, 0, mac)
got = deliver(b2, b1)[2]
check({g[0] for g in got} == {BT_LIST, BT_CONNECT}, f"btcmd: a repeat of another operation after a newer operation gets through ({got})")
got = deliver(b1, b2, b1, b2)[2]
check(len(got) == 2, f"btcmd: copies of the same operation are rejected ({got})")

sys.exit(1 if fails else 0)
