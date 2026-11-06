"""Sender: Wake-on-LAN from the pad (sender_wol), events to the receiver log (sender_events), scfg / event /
build2 packets and a local confirmation on the pad (Ctrl.local_pulse)."""
import asyncio, json, os, socket, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
TMP = tempfile.mkdtemp()
C.WOL_FILE = os.path.join(TMP, "wol.json")
import proto
import sender_events as E
import sender_net as net
import sender_update
import sender_wol as W
from proto import (BODY, E_NO_RECEIVER, E_WOL, E_WOL_FAIL, EV_ERROR, EV_INFO, KEY_BIT, PAD_KEYS, SCFG_EVENTS, SCFG_WOL,
                   T_BUILD, T_BUILD2, T_EVENT, T_SCFG, Codec, mac_bytes, magic_packet)
from sender_ctrl import Ctrl

from chk import ok
cod = Codec()

# ---------- protocol ----------
assert cod.parse(cod.pack(T_SCFG, 255, 3, b"\1" * 6, b"\0" * 4, 7, 1000))[0] == T_SCFG
assert cod.parse(cod.pack(T_EVENT, 1, 5, 0, 1, 0, b"x"))[0] == T_EVENT
assert cod.parse(cod.pack(T_BUILD2, 255, b"abc", 1, 0, b"\1" * 6, b"\0" * 4))[0] == T_BUILD2
assert cod.parse(cod.pack(T_BUILD, 255, b"abc", 1, 0))[0] == T_BUILD, "plain build unchanged"
pkt = magic_packet(bytes.fromhex("aabbccddeeff"))
assert len(pkt) == 102 and pkt[:6] == b"\xff" * 6 and pkt[6:12] == pkt[-6:] == bytes.fromhex("aabbccddeeff")
assert mac_bytes("AA-BB-CC-DD-EE-FF") == bytes.fromhex("aabbccddeeff") and mac_bytes("00:00:00:00:00:00") is None
assert mac_bytes("aa:bb") is None and mac_bytes("zz:bb:cc:dd:ee:ff") is None
ok("protocol: scfg / event / build2 by length, magic packet, MAC storage")

# ---------- events: only enabled ones, queued until the receiver replies, repeats ----------
out = []
emit = lambda *a: out.append(a)
E.set_enabled(False); E.info(E_WOL, 0, "x")
assert E.pending() == 0, "off - nothing waits"
E.set_enabled(True)
E.info(E_WOL, 1, "aa:bb:cc:dd:ee:ff")
t0 = time.monotonic()
E.flush(emit, t0 + 42)
assert len(out) == 1 and out[0][0] == T_EVENT and out[0][1] == 1 and out[0][3:6] == (EV_INFO, E_WOL, 42), out
E.flush(emit, t0 + 42)
assert len(out) == 2 and out[0][2] == out[1][2] and E.pending() == 0, "SENDS times with the same number, then gone"
for k in range(E.KEEP + 5):
    E.error(E_WOL_FAIL, 0, "e" * 200)
assert E.pending() == E.KEEP and len(E._queue[0][5]) == E.ARG_MAX, "queue limited, argument cut"
E.set_enabled(False); assert E.pending() == 0
ok("events: only enabled ones, age in seconds, SENDS repeats with one number, queue and argument limit")

E.set_enabled(True); out.clear()
net._receiver_watch(False, True)
assert E.pending() == 1 and E._queue[0][3] == E_NO_RECEIVER, "utrata receivera = zdarzenie"
net._receiver_watch(False, False)
assert E.pending() == 1, "waits for the receiver reply"
real_emit = net.emit
net.emit = emit
net._receiver_watch(True, False); net._receiver_watch(True, True)
net.emit = real_emit
assert [o[4] for o in out] == [E_NO_RECEIVER] * 2 and E.pending() == 0
E.set_enabled(False)
ok("ping loop: losing contact with the receiver as an event, sent after it comes back")

# ---------- settings from the receiver: scfg -> wol.json ----------
assert W.cfg == W.WolCfg() and not os.path.exists(C.WOL_FILE)
opt = PAD_KEYS.index(315)
assert not W.apply_scfg(SCFG_WOL, bytes(6), bytes(4), opt, 1000), "no change - no write"
assert not os.path.exists(C.WOL_FILE)
assert W.apply_scfg(SCFG_WOL | SCFG_EVENTS, bytes.fromhex("aabbccddeeff"), socket.inet_aton("192.168.1.255"),
                    PAD_KEYS.index(307), 1500)
assert W.cfg == W.WolCfg(True, "aa:bb:cc:dd:ee:ff", "192.168.1.255", PAD_KEYS.index(307), 1500, True) and E.enabled
saved = json.load(open(C.WOL_FILE))
assert saved["mac"] == "aa:bb:cc:dd:ee:ff" and saved["events"] is True and "learned_mac" in saved
W.cfg = W.WolCfg(); E.set_enabled(False)
W.load()
assert W.cfg.mac == "aa:bb:cc:dd:ee:ff" and W.cfg.hold_ms == 1500 and E.enabled, "after a sender restart from the file"
W.apply_scfg(0, bytes(6), bytes(4), PAD_KEYS.index(316), 99999)
assert W.cfg.button == opt and W.cfg.hold_ms == W.HOLD_MS[1] and not W.cfg.enabled, "PS as a pair / bad time -> defaults"
json.dump({"enabled": "tak", "mac": 5, "bcast": "nie-ip", "button": 99, "hold_ms": "x", "nowy": 1}, open(C.WOL_FILE, "w"))
W.load()
assert W.cfg == W.WolCfg(enabled=True), W.cfg
open(C.WOL_FILE, "w").write("{zepsuty")
W.load(); assert W.cfg == W.WolCfg(enabled=True)
for raw in (5, ["x"], "tekst", None):                     # valid JSON, but not an object - the service start must not fail
    json.dump(raw, open(C.WOL_FILE, "w"))
    W.load(); assert W.cfg == W.WolCfg(enabled=True), raw
ok("scfg: write to wol.json only on a change, loaded after a restart, bad values -> defaults")

# ---------- adresy: ARP, broadcast sieci ----------
arp = os.path.join(TMP, "arp")
open(arp, "w").write("IP address       HW type     Flags       HW address            Mask     Device\n"
                     "192.168.1.4      0x1         0x2         11:22:33:44:55:66     *        eth0\n"
                     "192.168.1.9      0x1         0x0         00:00:00:00:00:00     *        eth0\n")
assert W.arp_lookup("192.168.1.4", arp) == "11:22:33:44:55:66"
assert W.arp_lookup("192.168.1.9", arp) == "" and W.arp_lookup("10.0.0.1", arp) == "" and W.arp_lookup("x", "/nie") == ""
iproute = ("1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
           "3: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global dynamic eth0\\       valid_lft 80000sec\n")
busybox = "2: usb0    inet 172.16.42.1/26 scope global usb0\n"
assert W.subnet_broadcast("192.168.1.20", iproute) == "192.168.1.255"
assert W.subnet_broadcast("172.16.42.1", busybox) == "172.16.42.63", "no brd - from the mask"
assert W.subnet_broadcast("10.1.1.1", iproute) is None
W.cfg = W.WolCfg()
W._bcast_cache = ("192.168.1.20", "192.168.1.255")
W.local_ip = lambda: "192.168.1.20"
assert W.targets() == ["192.168.1.255", "255.255.255.255", C.PC_ADDR]
W.cfg = W.WolCfg(bcast="10.0.0.255")
assert W.targets() == ["10.0.0.255", C.PC_ADDR], "an entered broadcast instead of the automatic ones"
W.cfg = W.WolCfg()
real_lookup = W.arp_lookup
W.arp_lookup = lambda ip: "11:22:33:44:55:66" if ip == C.PC_ADDR else ""
W.learned = ""; W.learn()
assert W.learned == "11:22:33:44:55:66" and json.load(open(C.WOL_FILE))["learned_mac"] == W.learned
W.arp_lookup = real_lookup
assert W.target_mac() == bytes.fromhex("112233445566")
W.cfg = W.WolCfg(mac="aa:bb:cc:dd:ee:ff"); assert W.target_mac() == bytes.fromhex("aabbccddeeff"), "the entered one wins"
ok("addresses: MAC from ARP (only a complete entry, remembered), broadcast from `ip` (brd or mask), send targets")

# ---------- sending: a real UDP socket on localhost ----------
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1", 0)); rx.settimeout(2)
real_ports = proto.WOL_PORTS
W.WOL_PORTS = (rx.getsockname()[1],)
assert W.send(bytes.fromhex("aabbccddeeff"), ["127.0.0.1"]) == []
assert rx.recv(200) == magic_packet(bytes.fromhex("aabbccddeeff"))
errs = W.send(bytes(6), ["999.1.1.1"])
assert len(errs) == 1 and "999.1.1.1" in errs[0], errs
W.WOL_PORTS = real_ports
errs = W.send(bytes(6), ["999.1.1.1", "998.1.1.1"])
assert len(errs) == 2 * len(W.WOL_PORTS), "every failed send counts separately (fire compares with the number of sends)"
ok("magic packet sent to the address and port; bad address = an error description, no exception")

# no network: the same error on every port and address = a failure, not "sent" (red flash, error event)
class _Pad:
    idx, wol_timer = 0, None

    def __init__(self):
        self.pulses = []

    def local_pulse(self, kind):
        self.pulses.append(kind)


real_send, real_targets = W.send, W.targets
W.targets = lambda: ["192.168.1.255", "255.255.255.255", "192.168.1.4"]
W.send = lambda mac, addrs: [f"{a}: Network is unreachable" for a in addrs for _p in W.WOL_PORTS]
W.cfg = W.WolCfg(mac="aa:bb:cc:dd:ee:ff", events=True); E.set_enabled(True); E._queue.clear()
net.last_pong, W._last_sent = 0.0, 0.0
pad = _Pad()
assert W.fire(pad) is False and pad.pulses == ["fail"] and E._queue[-1][3] == E_WOL_FAIL, (pad.pulses, list(E._queue))
W.send = lambda mac, addrs: [f"{addrs[0]}: Network is unreachable"] * len(W.WOL_PORTS)   # one address failed
W._last_sent = 0.0
pad = _Pad()
assert W.fire(pad) is True and pad.pulses == ["wol"], "some addresses got it - sent"
W.send, W.targets = real_send, real_targets
E.set_enabled(False); E._queue.clear()
ok("Wake-on-LAN without a network (the same error everywhere) = an error, not a success; a partial error = sent")


# ---------- kombinacja na padzie ----------
class FakeCtrl:
    def __init__(self):
        self.idx, self.mask, self.wol_timer, self.pulses = 0, 0, None, []

    def local_pulse(self, kind):
        self.pulses.append(kind)


sent = []
W.send = lambda mac, addrs: sent.append((mac, addrs)) or []
W.cfg = W.WolCfg(mac="aa:bb:cc:dd:ee:ff", hold_ms=300, events=True)
E.set_enabled(True); E._queue.clear()
PS, OPT, TRI = KEY_BIT[316], KEY_BIT[315], KEY_BIT[307]


async def scenario():
    c = FakeCtrl()
    net.last_pong = 0.0                              # the receiver never replied
    W._last_sent = 0.0
    c.mask = PS; W.on_mask(c)
    assert c.wol_timer is None, "PS alone - nothing (that is how the pad turns on)"
    c.mask = PS | OPT; W.on_mask(c)
    assert c.wol_timer is not None
    await asyncio.sleep(0.1)
    c.mask = PS; W.on_mask(c)
    assert c.wol_timer is None
    await asyncio.sleep(0.4)
    assert not sent, "released too early - no send"
    c.mask = PS | OPT; W.on_mask(c)
    c.mask = PS | OPT | TRI; W.on_mask(c)           # another button meanwhile does not interrupt the hold
    await asyncio.sleep(0.4)
    assert len(sent) == 1 and sent[0][0] == bytes.fromhex("aabbccddeeff") and c.pulses == ["wol"], (sent, c.pulses)
    assert E._queue[-1][3] == E_WOL and c.wol_timer is None
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    await asyncio.sleep(0.4)
    assert len(sent) == 1, "the next send no earlier than after REPEAT_GUARD_S"
    W._last_sent = 0.0
    net.last_pong = time.monotonic()                 # PC running: the combo belongs to the receiver
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    await asyncio.sleep(0.4)
    assert len(sent) == 1 and c.pulses == ["wol"], "the receiver replies - no Wake-on-LAN"
    net.last_pong = 0.0
    W.cfg = W.WolCfg(enabled=False, mac="aa:bb:cc:dd:ee:ff", hold_ms=300)
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    assert c.wol_timer is None, "off in the receiver window"
    W.cfg = W.WolCfg(hold_ms=300, events=True); W.learned = ""
    c.mask = 0; W.on_mask(c); c.mask = PS | OPT; W.on_mask(c)
    await asyncio.sleep(0.4)
    assert len(sent) == 1 and c.pulses == ["wol", "fail"] and E._queue[-1][2:4] == [EV_ERROR, E_WOL_FAIL], \
        "unknown MAC - a red flash and an error event"

asyncio.run(scenario())
ok("combo: PS + button held for hold_ms with a silent receiver; release cancels; protection against "
   "repeats; a running receiver / off / no MAC")


# ---------- build2 only for a new receiver ----------
out.clear()
net.emit = emit
W.cfg = W.WolCfg(); W.learned = "11:22:33:44:55:66"; W._bcast_cache = ("192.168.1.20", "192.168.1.255")
net.scfg_t = 0.0
sender_update.send_build()
assert out[-1][0] == T_BUILD, "older receiver (no scfg) gets a plain build"
net.scfg_t = time.monotonic()
sender_update.send_build()
assert out[-1][0] == T_BUILD2 and out[-1][5] == bytes.fromhex("112233445566") and out[-1][6] == socket.inet_aton("192.168.1.255")
net.emit = real_emit
ok("build2 with the MAC and the Wake-on-LAN broadcast only after scfg from the receiver")

# ---------- local confirmation on the pad (without a receiver) ----------


class Rum:
    owns_lightbar, rgb = True, (0, 0, 64)

    def __init__(self):
        self.calls = []

    def set(self, small, large, rgb=None):
        self.calls.append((small, large, rgb))


async def pulse():
    c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
    c.rumble = Rum()
    c.local_pulse("wol")
    assert c.rumble.calls[-1] == (0, 200, (255, 255, 255)) and c.fb is None, "the flash is not feedback from the PC"
    await asyncio.sleep(0.6)
    assert c.rumble.calls[-1] == (0, 0, (0, 0, 64)), "back to the colour from before the flash"
    c.apply_feedback(0, 0, 1, 2, 3)
    c.local_pulse("fail")
    c.apply_feedback(10, 20, 4, 5, 6)                # the PC replied meanwhile - its state wins
    await asyncio.sleep(0.7)
    assert c.rumble.calls[-1] == (10, 20, (4, 5, 6)) and c.fb == (10, 20, 4, 5, 6), c.rumble.calls[-3:]
    # the PC went off during game rumble: the last feedback has motor power - the return after the flash does not start it again
    c.apply_feedback(180, 255, 7, 8, 9)
    c.local_pulse("wol")
    await asyncio.sleep(0.6)
    assert c.rumble.calls[-1] == (0, 0, (7, 8, 9)), c.rumble.calls[-3:]
    # a flash during a flash returns to the colour from before the first one, not to the flash colour
    c.fb = None
    c.rumble.rgb = (0, 0, 64)
    c.local_pulse("wol")
    c.rumble.rgb = (255, 255, 255)                   # the HID report has the flash colour now
    c.local_pulse("fail")
    await asyncio.sleep(0.7)
    assert c.rumble.calls[-1] == (0, 0, (0, 0, 64)), c.rumble.calls[-3:]
    c.cancel_timers()

asyncio.run(pulse())
ok("local flash: rumble and colour for a moment, back to the previous; new feedback from the PC clears it")
print("WOL TX OK")
