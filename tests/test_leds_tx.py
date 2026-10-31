"""Sender: the mute button LED and the player LEDs (the leds packet, report bytes checked against independent code, the flags of the
other reports, the way from the UDP packet to the pad's HID file) and the mute button itself (a press seen on the kernel's mute LED
becomes a short MUTE_BIT pulse in the state packet)."""
import asyncio, os, sys, tempfile, zlib
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_ctrl
import sender_mute as M
import sender_net as net
import sender_rumble as R
import sender_state as state
import sender_supervisor as sup
from proto import LEDS_MUTE, LEDS_PLAYER, MUTE_BIT, MUTE_OFF, MUTE_ON, MUTE_PULSE, T_FB, T_LEDS, T_STATE, Codec, SeqFilter

TMP = tempfile.mkdtemp()
ok = lambda m: print("OK  ", m)
KEY = b"0123456789abcdef"


class Dev:
    def capabilities(self):
        return {}


_files = iter(range(1000))


def newpath():
    path = os.path.join(TMP, f"hidraw{next(_files)}")
    open(path, "wb").close()
    return path


def reports(path):
    data = open(path, "rb").read()
    return [data[k:k + 78] for k in range(0, len(data), 78)]


def common(rep):
    return rep[3:50]


def crc_ok(rep):
    return int.from_bytes(rep[-4:], "little") == zlib.crc32(b"\xa2" + rep[:-4]) & 0xFFFFFFFF


# ---------- the packet ----------
assert proto.WIRE[T_LEDS] == T_FB and proto.BODY[T_LEDS].size == 3 and proto.BODY[proto.T_FB].size == 5
assert proto.BY_SIZE[(T_FB, 3)] == T_LEDS and proto.BY_SIZE[(T_FB, 5)] == T_FB, "the leds packet is told apart from fb by its length"
assert proto.SENDER_VERSION >= proto.LEDS_SENDER == 15 and MUTE_BIT == 1 << len(proto.PAD_KEYS) and MUTE_BIT not in proto.KEY_BIT.values()
plain = Codec().pack(T_LEDS, 2, LEDS_MUTE | LEDS_PLAYER, MUTE_PULSE, 0x15)
assert len(plain) == 4 + 3 and plain[0] == (2 << 4) | T_FB and plain[4:] == bytes([3, 2, 0x15])
assert Codec().parse(plain)[0] == T_LEDS
v2 = Codec(KEY).pack(T_LEDS, 2, 1, 1, 0, v3=False)
assert len(v2) == 4 + 3 + 8 and Codec(KEY).parse(v2)[0] == T_LEDS
v3 = Codec(KEY, token=lambda: 77).pack(T_LEDS, 2, 1, 1, 0, v3=True)
assert len(v3) == 4 + 3 + 12 and v3[0] >> 4 == 3 and Codec(KEY, now_ms=lambda: 77).parse(v3)[0] == T_LEDS
ok("leds packet: wire type fb, length 3 (unique), plain / HMAC v2 / v3 parse, version 15")

# ---------- delivery: own filter key, repeats dropped, other packets unaffected ----------
got = []
net.on_leds = lambda idx, vals: got.append((idx, vals))
net.fb_filter = SeqFilter(1.0)
net.codec = Codec()
tx = Codec()
a = tx.pack(T_LEDS, 1, 3, 1, 4)
for pkt in (a, a):                    # the receiver repeats a change: the same packet (the same sequence number) twice
    p = net.codec.parse(pkt)
    net._dispatch(p[0], p[1], p[2], pkt)
assert got == [(1, (3, 1, 4))], got
b = tx.pack(T_LEDS, 1, 1, 2, 0)       # the next packet has the next sequence number
p = net.codec.parse(b)
net._dispatch(p[0], p[1], p[2], b)
assert got[-1] == (1, (1, 2, 0)) and len(got) == 2
fb = tx.pack(T_FB, 1, 1, 2, 3, 4, 5)
net._dispatch(T_FB, 1, 9, fb)         # feedback is another packet type: the leds hook is not called for it
assert len(got) == 2
net.on_leds = None
net._dispatch(T_LEDS, 1, 99, tx.pack(T_LEDS, 1, 3, 1, 4))     # no hook set: ignored without an error
ok("delivery: on_leds gets (idx, (flags, mute, player)), a repeat is dropped, no hook is no error")

# ---------- the report bytes ----------
base = R.output_report(True, 3, 40, 200)
assert common(base)[R.VALID_FLAG1_AT] == 0 and common(base)[R.MUTE_LED_AT] == 0 and common(base)[R.PLAYER_LEDS_AT] == 0, "no LEDs by default"
assert R.output_report(True, 3, 40, 200, leds=(None, None)) == base, "None = the report is byte for byte the old one"
r = R.output_report(True, 3, 40, 200, leds=(MUTE_ON, 0x15))
c = common(r)
assert (R.MUTE_LED_AT, R.PLAYER_LEDS_AT) == (8, 43) and (R.FLAG1_MUTE_LED, R.FLAG1_PLAYER_LEDS) == (0x01, 0x10)   # hid-playstation offsets
assert c[1] == 0x11 and c[8] == MUTE_ON and c[43] == 0x15 and crc_ok(r) and len(r) == 78
assert c[0] == common(base)[0] and c[2:8] == common(base)[2:8], "valid_flag0 and the motors are not touched by the LEDs"
only_mute = common(R.output_report(True, 3, 0, 0, leds=(MUTE_PULSE, None)))
assert only_mute[1] == 0x01 and only_mute[8] == MUTE_PULSE and only_mute[43] == 0
only_player = common(R.output_report(True, 3, 0, 0, leds=(None, 0xFF)))
assert only_player[1] == 0x10 and only_player[8] == 0 and only_player[43] == 0x1F, "the player LEDs are 5 bits"
both = common(R.output_report(True, 3, 0, 0, None, (1, 2, 3), None, False, (MUTE_OFF, 4)))
assert both[1] == 0x04 | 0x01 | 0x10 and both[44:47] == bytes([1, 2, 3]) and both[8] == MUTE_OFF and both[43] == 4, "lightbar flag kept (|=)"
usb = R.output_report(False, 0, 0, 0, leds=(MUTE_ON, 1))
assert usb[0] == 2 and usb[1 + 1] == 0x11 and usb[1 + 8] == MUTE_ON and usb[1 + 43] == 1
ok("output_report: bytes 8 / 43 and valid_flag1 bits 0x01 / 0x10, lightbar flag kept, 5 bits, CRC against zlib, None = unchanged report")

# ---------- during audio mode no rumble bit appears ----------
au = common(R.output_report(True, 3, 70, 140, True, None, None, True, (MUTE_ON, 2)))
assert au[0] == 0 and au[2:4] == bytes(2) and au[1] == 0x11, "haptics / speaker: valid_flag0 stays clear, the LEDs are safe in audio mode"
ok("audio mode: the LED flags are not rumble bits")

# ---------- Rumble.set_leds ----------
path = newpath()
dev = Dev()
rum = R.Rumble(0, dev, "hidraw", hidraw=(path, True))
assert rum.leds == (None, None) and not rum.set_leds(None, None), "nothing to say: nothing is written"
assert rum.set_leds(MUTE_ON, None) and rum.leds == (MUTE_ON, None)
assert not rum.set_leds(MUTE_ON, None), "the same value is not written again"
assert rum.set_leds(None, 0x1B) and rum.leds == (MUTE_ON, 0x1B), "a None keeps the other value"
out = reports(path)
assert len(out) == 2 and all(crc_ok(x) for x in out)
assert common(out[0])[1] == 0x01 and common(out[1])[1:2] == b"\x11" and common(out[1])[8] == MUTE_ON and common(out[1])[43] == 0x1B
assert out[0][1] >> 4 != out[1][1] >> 4, "every report has its own sequence number"
rum.set(10, 20)                           # later reports keep the LEDs (like the lightbar colour)
last = reports(path)[-1]
assert common(last)[1] & 0x11 == 0x11 and common(last)[8] == MUTE_ON and common(last)[43] == 0x1B and common(last)[3] == 20
rum.set_triggers(proto.TRIG2_OFF)
assert common(reports(path)[-1])[1] & 0x11 == 0x11
rum.close()
rum2 = R.Rumble(0, dev, "evdev")
assert not rum2.set_leds(MUTE_ON, 1) and rum2.leds == (MUTE_ON, 1), "no HID way: remembered, nothing written, no error"
ok("Rumble.set_leds: written on change only, None keeps the other LED, kept in later rumble / trigger reports, no HID way = no write")

# ---------- Ctrl.apply_leds and the whole way UDP -> sender_net -> supervisor -> Ctrl -> HID file ----------
async def scenarios():
    path = newpath()
    c = sender_ctrl.Ctrl(5, "aa:bb:cc:dd:ee:05", dev, None, None)
    c.rumble = R.Rumble(5, dev, "hidraw", hidraw=(path, True))
    state.active[c.key] = c
    net.on_leds = sup.dispatch_leds
    net.fb_filter = SeqFilter(1.0)
    net.codec = Codec()
    tx2 = Codec()
    for pkt in (tx2.pack(T_LEDS, 5, LEDS_MUTE, MUTE_ON, 0), tx2.pack(T_LEDS, 5, LEDS_PLAYER, 0, 0x0A), tx2.pack(T_LEDS, 6, LEDS_MUTE, 1, 0)):
        p = net.codec.parse(pkt)
        net._dispatch(p[0], p[1], p[2], pkt)
    assert c.leds == (MUTE_ON, 0x0A), "the flags decide what changes: a packet with only the player LEDs keeps the mute LED"
    out2 = reports(path)
    assert len(out2) == 2 and common(out2[-1])[8] == MUTE_ON and common(out2[-1])[43] == 0x0A and crc_ok(out2[-1]), "another slot is not touched"
    c.apply_leds(LEDS_MUTE, MUTE_OFF, 0)
    assert c.leds == (MUTE_OFF, 0x0A) and common(reports(path)[-1])[8] == MUTE_OFF
    del state.active[c.key]
    ok("UDP -> supervisor -> Ctrl -> HID report: flags select the LED, the other slot and the other LED are untouched")

    # a pad whose rumble backend is not ready yet: the LEDs are kept in Ctrl and written by _start_pad later (replay)
    late = sender_ctrl.Ctrl(6, "aa:bb:cc:dd:ee:06", dev, None, None)
    late.apply_leds(LEDS_MUTE | LEDS_PLAYER, MUTE_PULSE, 4)
    assert late.rumble is None and late.leds == (MUTE_PULSE, 4)
    path2 = newpath()
    late.rumble = R.Rumble(6, dev, "hidraw", hidraw=(path2, True))
    late.rumble.set_leds(*late.leds)
    assert common(reports(path2)[0])[8] == MUTE_PULSE and common(reports(path2)[0])[43] == 4
    ok("LEDs that arrive before the rumble backend exists are kept and can be replayed")

    # ---------- the mute button ----------
    sent = []
    net.emit = lambda t, idx, *vals: sent.append((t, idx, vals))
    pc = sender_ctrl.Ctrl(7, "aa:bb:cc:dd:ee:07", dev, None, None)
    pc.send_state(force=True)
    assert sent[-1][0] == T_STATE and sent[-1][2][8] == 0, "state packet: mask 0"
    pc.mask = proto.KEY_BIT[proto.BTN_SOUTH]
    pc.mute_down = True
    pc.send_state()
    assert sent[-1][2][8] == proto.KEY_BIT[proto.BTN_SOUTH] | MUTE_BIT, "MUTE_BIT sits above the real buttons"
    pc.mute_down = False
    pc.send_state()
    assert sent[-1][2][8] == proto.KEY_BIT[proto.BTN_SOUTH], "released"
    ok("state packet: MUTE_BIT while the mute button is held, the evdev mask is untouched")

    M.MUTE_POLL_S, M.MUTE_HOLD_S = 0.01, 0.05
    led = tempfile.mkdtemp()
    open(os.path.join(led, "brightness"), "w").write("0\n")
    found = []
    M.find_led = lambda dev_, pattern: found.append(pattern) or led
    mc = sender_ctrl.Ctrl(8, "aa:bb:cc:dd:ee:08", dev, None, None)
    sent.clear()
    task = asyncio.create_task(M.stream_mute(mc))
    await asyncio.sleep(0.05)
    assert found == [M.MUTE_LED_GLOB] and not mc.mute_down and not sent, "nothing happens while the LED does not change"
    open(os.path.join(led, "brightness"), "w").write("1\n")           # the kernel toggled "muted" on a press
    await asyncio.sleep(0.03)
    assert mc.mute_down and any(v[2][8] & MUTE_BIT for v in sent), "a change of the LED presses the button"
    await asyncio.sleep(0.15)
    assert not mc.mute_down and not sent[-1][2][8] & MUTE_BIT, "... and releases it after MUTE_HOLD_S"
    sent.clear()
    open(os.path.join(led, "brightness"), "w").write("0\n")           # the second press (un-mute) is a press too
    await asyncio.sleep(0.03)
    assert mc.mute_down
    await asyncio.sleep(0.15)
    open(os.path.join(led, "brightness"), "w").write("255\n")         # any non-zero value is "muted": 0 -> 255 is a press
    await asyncio.sleep(0.03)
    assert mc.mute_down
    await asyncio.sleep(0.15)
    open(os.path.join(led, "brightness"), "w").write("1\n")           # 255 -> 1: still muted, no press
    await asyncio.sleep(0.05)
    assert not mc.mute_down
    os.remove(os.path.join(led, "brightness"))                        # an unreadable LED does not press anything
    await asyncio.sleep(0.05)
    assert not mc.mute_down and not task.done()
    task.cancel()
    ok("stream_mute: every change muted <-> unmuted presses for MUTE_HOLD_S, other changes and read errors do not")

    M.find_led = lambda dev_, pattern: None
    await M.stream_mute(mc)               # no kernel LED: ends quietly, nothing raised
    ok("stream_mute: no mute LED from the kernel = the task just ends")

asyncio.run(scenarios())
print("LEDS TX OK")
