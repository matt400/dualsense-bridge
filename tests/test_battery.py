"""Receiver: the sender device's battery (build3 -> S.sender_batt) and the SCFG_BATT announcement."""
import os, socket, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, S)
import rx_events as ev
import rx_net
from proto import BATT_CHARGING, SCFG_BATT, T_BUILD, T_BUILD2, T_BUILD3, Codec

ok = lambda m: print("OK  ", m)
cod = Codec()
MAC, BC = bytes.fromhex("112233445566"), socket.inet_aton("10.0.0.255")


def build3(byte):
    rx_net.on_build(cod.pack(T_BUILD3, 255, b"abc1234", 5, 0, MAC, BC, byte), time.monotonic(), T_BUILD3)


# ---------- build3 ----------
assert S.sender_batt is None
build3(87 | BATT_CHARGING)
assert S.sender_batt == (87, True) and S.sender_build[0] == "abc1234" and S.sender_wol == ("11:22:33:44:55:66", "10.0.0.255")
build3(40)
assert S.sender_batt == (40, False)
build3(0x7F)
assert S.sender_batt == (100, False), "a value above 100 is clamped"
ok("build3: percent and charging flag go to S.sender_batt, build / Wake-on-LAN fields as in build2")

# ---------- a packet without a battery clears it ----------
rx_net.on_build(cod.pack(T_BUILD2, 255, b"abc1234", 5, 0, MAC, BC), time.monotonic(), T_BUILD2)
assert S.sender_batt is None, "the sender lost its battery / is older: no stale percentage"
build3(50)
rx_net.on_build(cod.pack(T_BUILD, 255, b"abc1234", 5, 0), time.monotonic(), T_BUILD)
assert S.sender_batt is None
ok("build / build2 clear the battery")

# ---------- the dispatch path (a real packet) ----------
S.sender_batt = None
pkt = cod.pack(T_BUILD3, 255, b"abc1234", 5, 0, MAC, BC, 61 | BATT_CHARGING)
assert rx_net._on_control(T_BUILD3, 255, 1, pkt, ("192.168.1.50", 5005), time.monotonic(), 0)
assert S.sender_batt == (61, True) and S.peer == ("192.168.1.50", 5005), "build3 reaches on_build through _on_control"
ok("build3 through the receive path")

# ---------- offline forgets it ----------
w = rx_net.SenderWatch()
ev.clear()
S.peer = ("192.168.1.50", 5005)
S.last_pkt = 100.0
w.tick(100.5)
assert w.online and S.sender_batt is not None
w.tick(100.5 + S.SENDER_TIMEOUT + 1)
assert not w.online and S.sender_batt is None
ok("the sender goes offline -> the battery is forgotten")

# ---------- scfg announces build3 ----------
assert rx_net.scfg_values()[0] & SCFG_BATT, "every receiver of this version understands build3"
ok("scfg always carries SCFG_BATT")
