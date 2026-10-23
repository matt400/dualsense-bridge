"""Sender: the battery of the device the sender runs on (sender_batt) and the build3 packet that carries it."""
import os, sys, tempfile, time
from unittest import mock
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_batt as B
import sender_net as net
import sender_update
import sender_wol as W
from proto import BATT_CHARGING, BODY, SCFG_BATT, SCFG_WOL, T_BUILD, T_BUILD2, T_BUILD3, T_SCFG, Codec

ok = lambda m: print("OK  ", m)
cod = Codec()


def supply(root, name, **files):
    """A fake /sys/class/power_supply/<name> with the given files."""
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for k, v in files.items():
        with open(os.path.join(d, k), "w") as f:
            f.write(f"{v}\n")


def reset():
    B._path, B._next_scan = None, 0.0


# ---------- finding the device's own battery ----------
root = tempfile.mkdtemp()
assert B.find(root) is None and B.find(os.path.join(root, "nope")) is None, "no entries / no folder - no battery"
supply(root, "AC", type="Mains", online=1)
supply(root, "ps-controller-battery-aa:bb", type="Battery", capacity=55, status="Discharging", scope="Device")
supply(root, "hid-mouse", type="Battery", capacity=70, scope="Device")
supply(root, "ps-controller-battery-cc:dd", type="Battery", capacity=60)           # the pad's entry without a scope
supply(root, "BAT_gone", type="Battery", capacity=80, present=0)
assert B.find(root) is None, "a Mains adapter, a pad, a mouse and an absent battery are not the device's battery"
supply(root, "qcom-battery", type="Battery", capacity=87, status="Charging")
assert B.find(root) == os.path.join(root, "qcom-battery")
ok("sender_batt.find: only the device's own battery (not a pad, a peripheral, an adapter or an absent battery)")

# ---------- the battery byte ----------
reset()
assert B.read(root, now=1000.0) == 87 | BATT_CHARGING
supply(root, "qcom-battery", type="Battery", capacity=87, status="Discharging")
assert B.read(root, now=1001.0) == 87
supply(root, "qcom-battery", type="Battery", capacity=100, status="Full")
assert B.read(root, now=1002.0) == 100, "full is not charging: no wave in the window"
supply(root, "qcom-battery", type="Battery", capacity=130, status="Charging")
assert B.read(root, now=1003.0) == 100 | BATT_CHARGING, "a bad value is clamped, it never reaches the percent mask"
assert B.read(root, now=1004.0) & ~BATT_CHARGING <= 100
ok("sender_batt.read: percent | BATT_CHARGING only while charging, clamped to 100")

# ---------- no battery: not found now, found later; a vanished battery ----------
reset()
empty = tempfile.mkdtemp()
assert B.read(empty, now=2000.0) is None
supply(empty, "BAT0", type="Battery", capacity=42, status="Discharging")
assert B.read(empty, now=2000.0 + B.RESCAN_S - 1) is None, "no rescan before RESCAN_S (a Raspberry Pi asks every 5 s)"
assert B.read(empty, now=2000.0 + B.RESCAN_S + 1) == 42
os.remove(os.path.join(empty, "BAT0", "capacity"))
assert B.read(empty, now=2200.0) is None and B._path is None, "a battery that went away is forgotten"
ok("sender_batt.read: no battery -> None, a late battery is found after RESCAN_S, a vanished one is forgotten")

# ---------- protocol ----------
assert BODY[T_BUILD3].size == 23 and proto.WIRE[T_BUILD3] == proto.WIRE[T_BUILD2] == T_BUILD
p = cod.pack(T_BUILD3, 255, b"abc", 1, 0, b"\1" * 6, b"\0" * 4, 87 | BATT_CHARGING)
assert cod.parse(p)[0] == T_BUILD3
assert cod.parse(cod.pack(T_BUILD2, 255, b"abc", 1, 0, b"\1" * 6, b"\0" * 4))[0] == T_BUILD2, "build2 unchanged"
key = Codec(b"0123456789abcdef")
assert key.parse(key.pack(T_BUILD3, 255, b"abc", 1, 0, b"\1" * 6, b"\0" * 4, 5, v3=False))[0] == T_BUILD3, "signed v2"
ok("protocol: build3 is told apart from build / build2 by its length, also with an HMAC")

# ---------- scfg: SCFG_BATT announces build3 ----------
net.batt_t = net.scfg_t = 0.0
assert not net.batt_receiver()
net._on_scfg(1, cod.pack(T_SCFG, 255, SCFG_WOL, bytes(6), bytes(4), 7, 1000))
assert net.scfg_t and not net.batt_receiver(), "an older receiver: scfg without SCFG_BATT"
net._on_scfg(2, cod.pack(T_SCFG, 255, SCFG_WOL | SCFG_BATT, bytes(6), bytes(4), 7, 1000))
assert net.batt_receiver() and net.batt_receiver(time.monotonic() + 1)
assert not net.batt_receiver(time.monotonic() + net.NEW_RECEIVER_S + 1), "the announcement expires like scfg"
ok("scfg with SCFG_BATT -> batt_receiver() (and it expires when the receiver goes silent)")

# ---------- send_build: build3 only with a battery AND a receiver that knows it ----------
out = []
real_emit = net.emit
net.emit = lambda t, i, *v: out.append((t, i, *v))
W.cfg = W.WolCfg(); W.learned = "11:22:33:44:55:66"; W._bcast_cache = ("192.168.1.20", "192.168.1.255")
sender_update.version = ("abc1234", 1_700_000_000, "x")
now = time.monotonic()
with mock.patch.object(B, "read", lambda: 87 | BATT_CHARGING):
    net.scfg_t, net.batt_t = 0.0, 0.0
    sender_update.send_build()
    assert out[-1][0] == T_BUILD, "no scfg at all: the plain build"
    net.scfg_t, net.batt_t = now, 0.0
    sender_update.send_build()
    assert out[-1][0] == T_BUILD2, "a receiver without SCFG_BATT must never get build3 (it would reject the length)"
    net.scfg_t = net.batt_t = now
    sender_update.send_build()
    assert out[-1][0] == T_BUILD3 and out[-1][-1] == 87 | BATT_CHARGING and out[-1][5] == bytes.fromhex("112233445566"), out[-1]
with mock.patch.object(B, "read", lambda: None):
    sender_update.send_build()
    assert out[-1][0] == T_BUILD2, "a device without a battery (Raspberry Pi) keeps sending build2"
net.emit = real_emit
net.scfg_t = net.batt_t = 0.0
ok("send_build: build3 only with a battery and a receiver that announced SCFG_BATT, otherwise build2 / build as before")
