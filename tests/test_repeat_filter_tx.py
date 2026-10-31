"""Nadajnik: powtórki poleceń (cmd, btcmd, cfgset) przechodzą przez filtr numerów osobno dla każdego polecenia.
Receiver wysyła każde polecenie 3x z tym samym numerem. Gdy pierwsza kopia pierwszego polecenia zginie, a drugie polecenie już
dotarło, powtórka pierwszego nie może być uznana za spóźnioną (jeden klucz filtra na typ pakietu gubił takie polecenie na stałe).
Kopie tego samego polecenia i starsza wartość tego samego ustawienia nadal mają być odrzucane.
    python3 tests/test_repeat_filter_tx.py sender"""
import sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])      # bez klucza: sprawdzamy filtr numerów, nie pamięć powtórek kodeka v3
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
pc = Codec()                                                  # receiver po drugiej stronie, bez klucza
cfgs, cmds, bts = [], [], []
net.on_cfg = lambda authenticated, op, key, ref, number: cfgs.append((key, number))
net.on_command = lambda idx, cmd, arg: cmds.append((idx, cmd, arg))
net.on_bt = lambda op, arg, mac: bts.append((op, arg))


def deliver(*packets):
    """Dostarcza pakiety w tej kolejności przy świeżym filtrze; zwraca to, co dotarło do uchwytów."""
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
got = deliver(b, a)[0]                                        # pierwsza kopia a zginęła: b, potem powtórka a
check(sorted(k for k, _ in got) == sorted((HB, VOL)), f"cfgset: powtórka starszego ustawienia po nowszym innym ustawieniu dociera ({got})")
got = deliver(a, b, a, b)[0]
check(len(got) == 2, f"cfgset: kopie tego samego pakietu są odrzucane ({got})")
old, new = cfg("heartbeat_s", 0.05), cfg("heartbeat_s", 0.06)
got = deliver(new, old)[0]
check(got == [(HB, 0.06)], f"cfgset: starsza wartość tego samego ustawienia po nowszej jest odrzucana ({got})")

# ---------- cmd ----------
c1, c2 = pc.pack(T_CMD, 0, CMD_GYRO_CAL, 3), pc.pack(T_CMD, 0, CMD_POWER_OFF, 0)
got = deliver(c2, c1)[1]
check({g[1] for g in got} == {CMD_GYRO_CAL, CMD_POWER_OFF}, f"cmd: powtórka innego polecenia po nowszym poleceniu dociera ({got})")
got = deliver(c1, c2, c1, c2)[1]
check(len(got) == 2, f"cmd: kopie tego samego polecenia są odrzucane ({got})")
old, new = pc.pack(T_CMD, 0, CMD_GYRO_CAL, 3), pc.pack(T_CMD, 0, CMD_GYRO_CAL, 0)
got = deliver(new, old)[1]
check(got == [(0, CMD_GYRO_CAL, 0)], f"cmd: starsze polecenie tego samego rodzaju po nowszym jest odrzucane ({got})")
other = pc.pack(T_CMD, 1, CMD_GYRO_CAL, 3)
got = deliver(pc.pack(T_CMD, 0, CMD_GYRO_CAL, 3), other)[1]
check(len(got) == 2, f"cmd: polecenia dla różnych padów nie blokują się ({got})")

# ---------- btcmd ----------
mac = b"\xaa\xbb\xcc\xdd\xee\xff"
b1, b2 = pc.pack(T_BTCMD, 255, BT_LIST, 0, bytes(6)), pc.pack(T_BTCMD, 255, BT_CONNECT, 0, mac)
got = deliver(b2, b1)[2]
check({g[0] for g in got} == {BT_LIST, BT_CONNECT}, f"btcmd: powtórka innej operacji po nowszej operacji dociera ({got})")
got = deliver(b1, b2, b1, b2)[2]
check(len(got) == 2, f"btcmd: kopie tej samej operacji są odrzucane ({got})")

sys.exit(1 if fails else 0)
