"""Sender: HD haptics - the hapt packet (plain, HMAC v2, v3, old parsers), the bytes of report 0x32 and of the setup report checked
against independent code, the flags of every other report while a stream runs, the motors policy, write errors, the whole way from
the UDP packet to the pad's HID file, and the new settings."""
import asyncio, contextlib, errno, io, os, struct, sys, tempfile, zlib
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_ctrl
import sender_haptics as H
import sender_net as net
import sender_rumble as R
import sender_state as state
import sender_supervisor as sup
from evdev import ecodes as ec
from proto import CAP_HAPTICS, HAPT_CHUNK, HAPT_END, HAPT_START, T_HAPT, TRIG2_OFF, Codec, SeqFilter

TMP = tempfile.mkdtemp()
ok = lambda m: print("OK  ", m)
KEY = b"0123456789abcdef"


def chunk(n):
    return bytes((n * 5 + i) % 100 + 1 for i in range(HAPT_CHUNK))        # never zero: silence can be told from audio


_files = iter(range(1000))


def newpath():
    path = os.path.join(TMP, f"hidraw{next(_files)}")
    open(path, "wb").close()
    return path


def reports(path):
    """The reports written to a fake hidraw file: 0x31 is 78 B, 0x32 is 142 B."""
    data = open(path, "rb").read()
    out, k = [], 0
    while k < len(data):
        n = {0x31: 78, 0x32: 142}[data[k]]
        out.append(data[k:k + n])
        k += n
    return out


def common(rep):
    return rep[3:50]


def crc_ok(rep):
    return int.from_bytes(rep[-4:], "little") == zlib.crc32(b"\xa2" + rep[:-4]) & 0xFFFFFFFF


class Dev:
    def capabilities(self):
        return {}


class FFDev:
    def __init__(self):
        self.log = []
    def capabilities(self):
        return {ec.EV_FF: [ec.FF_RUMBLE]}
    def upload_effect(self, eff):
        self.log.append("upload")
        return 7
    def write(self, t, code, v):
        self.log.append("play")
    def erase_effect(self, i):
        self.log.append("erase")


# ---------- the hapt packet ----------
frame_no, data = 0x01020304, chunk(3)
plain = Codec().pack(T_HAPT, 1, frame_no, HAPT_START, data)
assert plain[0] == (2 << 4) | 0x0A and plain[1] == 1 and len(plain) == 4 + 69, plain[:4].hex()
assert plain[4:] == struct.pack(">IB", frame_no, HAPT_START) + data, "body: counter (big endian), flags, 64 B of samples"
assert Codec().parse(plain) == (T_HAPT, 1, int.from_bytes(plain[2:4], "big"))
assert proto.BODY[T_HAPT].unpack_from(plain, 4) == (frame_no, HAPT_START, data)
v2 = Codec(KEY).pack(T_HAPT, 1, frame_no, 0, data, v3=False)
assert len(v2) == 4 + 69 + 8 and Codec(KEY).parse(v2)[0] == T_HAPT
v3 = Codec(KEY, token=lambda: 1234).pack(T_HAPT, 1, frame_no, 0, data, v3=True)
assert len(v3) == 4 + 69 + 12 and v3[0] >> 4 == 3 and Codec(KEY, now_ms=lambda: 1234).parse(v3)[0] == T_HAPT
assert Codec(KEY).parse(v2[:-1]) is None and Codec(b"another key 1234").parse(v2) is None
ok("hapt packet: 69 B body, plain / HMAC v2 / v3 round trip")

# a received packet reaches the hook once, with the samples cut out of the datagram (also with a v3 trailer)
got = []
net.on_haptics = lambda idx, frame, flags, view: got.append((idx, frame, flags, bytes(view)))
net.fb_filter = SeqFilter(1.0)


def deliver(pkt):
    p = net.codec.parse(pkt)
    assert p is not None
    net._dispatch(p[0], p[1], p[2], pkt)


net.codec = Codec()
tx = Codec()
a, b = tx.pack(T_HAPT, 2, 10, HAPT_START, chunk(1)), tx.pack(T_HAPT, 2, 42, 0, chunk(2))
deliver(b)
deliver(a)                                                  # an older packet that arrived late
deliver(b)                                                  # the same datagram twice
assert got == [(2, 42, 0, chunk(2))], got
deliver(tx.pack(T_HAPT, 5, 7, HAPT_END, chunk(4)))          # another pad has its own stream filter
assert got[-1] == (5, 7, HAPT_END, chunk(4)) and len(got) == 2
net.fb_filter = SeqFilter(1.0)
net.codec = Codec(KEY, token=lambda: 77, now_ms=lambda: 77)
del got[:]
deliver(net.codec.pack(T_HAPT, 1, 99, HAPT_START, chunk(9), v3=True))
assert got == [(1, 99, HAPT_START, chunk(9))], "the samples are found with the HMAC trailer behind them"
net.codec = Codec()
ok("sender_net: one call per new packet, late / repeated packets dropped, separate filter per pad")


@contextlib.contextmanager
def old_tables():
    """The parser of a peer from before this change: it does not know T_HAPT."""
    saved = proto.BY_SIZE
    proto.BY_SIZE = {k: v for k, v in saved.items() if v != T_HAPT}
    try:
        yield
    finally:
        proto.BY_SIZE = saved


with old_tables():
    assert Codec().parse(plain) is None, "an older sender drops the unknown length"
    keyed = Codec(KEY)
    assert keyed.parse(v2) is None and keyed.mismatch_t == float("-inf"), "...also with a key, and it is not taken for a key mismatch"
    assert Codec(KEY, now_ms=lambda: 1234).parse(v3) is None
    assert Codec().parse(Codec().pack(proto.T_TRIG2, 0, *TRIG2_OFF))[0] == proto.T_TRIG2, "known packets are not affected"
assert Codec().parse(plain)[0] == T_HAPT
ok("an older peer ignores the hapt packet (plain, v2, v3) without a key-mismatch warning")

# ---------- report 0x32 ----------
rep32 = H.Report32()
rep32.payload[:] = bytes(range(64))
first = bytes(rep32.finish(5))
exp = bytearray(142)
exp[0], exp[1] = 0x32, 5 << 4
exp[2:5] = bytes([0x91, 0x07, 0xFE])
exp[5:10] = b"\x10" * 5
exp[10] = 1
exp[11:13] = bytes([0x92, 0x40])
exp[13:77] = bytes(range(64))
exp[138:] = (zlib.crc32(b"\xa2" + bytes(exp[:138])) & 0xFFFFFFFF).to_bytes(4, "little")
assert first == bytes(exp), (first.hex(), bytes(exp).hex())
assert not any(first[77:138]) and crc_ok(first) and len(first) == 142
second = bytes(rep32.finish(0x17))
assert second[1] == 0x70 and second[10] == 2 and second[13:77] == bytes(range(64)) and crc_ok(second), "sequence 4 bits, counter + 1"
for _ in range(254):
    third = bytes(rep32.finish(1))
assert third[10] == 0 and rep32.finish(1)[10] == 1, "the report counter wraps at 256"
rep32.payload[:] = chunk(1)
assert crc_ok(bytes(rep32.finish(2))) and bytes(rep32.finish(2))[13:77] == chunk(1), "the CRC follows the samples"
ok("report 0x32: layout, sized headers, depth 0x10 x5, counter, CRC32 (seed 0xA2) against an independent computation")

setup = R.haptics_setup_report(3)
exp = bytearray(78)
exp[0], exp[1], exp[2] = 0x31, 3 << 4, 0x10
exp[3 + 1] = 0x02 | 0x20 | 0x40                              # valid_flag1; valid_flag0 stays 0; motor power byte (3 + 36) is 0
exp[74:] = (zlib.crc32(b"\xa2" + bytes(exp[:74])) & 0xFFFFFFFF).to_bytes(4, "little")
assert setup == bytes(exp), (setup.hex(), bytes(exp).hex())
ok("setup report: valid_flag0 0, valid_flag1 0x62, motor power 0")

# ---------- the flags of an ordinary report while the haptics play ----------
trig = (R.trigger_effect(2, 5), R.weapon_effect(5, 7))
for v2_flag, f0, f2 in ((None, 0x03, 0x04), (False, 0x03, 0x00), (True, 0x02, 0x04)):
    c = common(R.output_report(True, 1, 70, 140, v2_flag, (1, 2, 3), trig))
    assert c[0] == f0 | 0x0C and c[38] == f2 and c[2:4] == bytes([70, 140]) and c[1] == 0x04, (v2_flag, c[:4].hex())
    h = common(R.output_report(True, 1, 70, 140, v2_flag, (1, 2, 3), trig, haptics=True))
    assert h[0] == 0x0C, f"trigger bits only (never 0x01 / 0x02): {h[0]:#x}"
    assert h[38] == 0 and h[2:4] == b"\0\0" and h[1] == 0x04 and h[44:47] == bytes([1, 2, 3]), "no vibration v2, no motors, lightbar kept"
    assert h[R.LEFT_TRIGGER_AT:R.LEFT_TRIGGER_AT + 11] == trig[0] and h[R.RIGHT_TRIGGER_AT:R.RIGHT_TRIGGER_AT + 11] == trig[1]
    assert common(R.output_report(True, 1, 70, 140, v2_flag, None, None, haptics=True))[0] == 0
assert crc_ok(R.output_report(True, 1, 0, 0, None, None, None, True)) and len(R.output_report(True, 1, 0, 0, None, None, None, True)) == 78
ok("output_report(haptics=True): valid_flag0 keeps only the trigger bits, valid_flag2 and the motors are zero; normal reports unchanged")

# ---------- capability ----------
assert R.Rumble(0, Dev(), "hidraw", hidraw=(newpath(), True)).bits & CAP_HAPTICS
assert not R.Rumble(0, Dev(), "hidraw", hidraw=(newpath(), False)).bits & CAP_HAPTICS, "a USB pad has no report 0x32"
assert not R.Rumble(0, Dev(), "evdev").bits & CAP_HAPTICS and not R.Rumble(0, Dev(), "off").bits & CAP_HAPTICS
assert proto.SENDER_VERSION >= 13 and CAP_HAPTICS == 64
ok("CAP_HAPTICS only for a Bluetooth pad with a HID report")


async def scenarios():
    rep = H.Report32()
    rep.payload[:] = chunk(1)

    # -- policy "haptics": the setup, the flags, the game's motors, the end
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    r.set(0, 0, (10, 20, 30))
    assert common(reports(path)[-1])[0] & 0x03 == 0x03
    assert r.haptics_begin() and r.haptics and r.audio
    out = reports(path)
    assert out[-1] == R.haptics_setup_report(out[-1][1] >> 4) and common(out[-1])[0] == 0 and common(out[-1])[1] == 0x62
    await asyncio.sleep(R.HAPTICS_RESEND_S + 0.03)
    out = reports(path)
    assert [common(x)[1] for x in out].count(0x62) == 2 and common(out[-1])[1] == 0x62 and crc_ok(out[-1]), "the setup report goes out twice"
    assert all(common(x)[0] & 0x03 == 0 for x in out[1:]), "the pending repeat of the earlier rumble set does not cut the haptics"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r.set(70, 140, (9, 8, 7))
        r.set(80, 150, (9, 8, 7))
    c = common(reports(path)[-1])
    assert c[0] & 0x03 == 0 and c[38] == 0 and c[2:4] == b"\0\0" and c[1] == 0x04 and c[44:47] == bytes([9, 8, 7]), c.hex()
    assert buf.getvalue().count("game rumble ignored") == 1, buf.getvalue()
    assert r.cur == (80, 150), "the game's value is remembered"
    fx = (proto.TFX_RES, 2, 5, 0, proto.TFX_CLICK, 5, 7, 0, 0, 0, 0, 0)
    assert r.set_triggers(fx)
    c = common(reports(path)[-1])
    assert c[0] == 0x0C and c[38] == 0 and c[R.LEFT_TRIGGER_AT:R.LEFT_TRIGGER_AT + 11] == trig[0], "trigger report: trigger bits only"
    assert r.write_haptics(rep) and not r.write_haptics.__self__.ducked
    last = reports(path)[-1]
    assert len(last) == 142 and last[13:77] == chunk(1) and crc_ok(last)
    out = reports(path)
    seqs = [x[1] >> 4 for x in out]
    assert all((b - a) & 0x0F == 1 for a, b in zip(seqs, seqs[1:])), f"one sequence counter for 0x31 and 0x32: {seqs}"
    r.haptics_end()
    c = common(reports(path)[-1])
    assert c[0] & 0x03 == 0x03 and c[2:4] == bytes([80, 150]) and c[1] == 0x04 and c[44:47] == bytes([9, 8, 7]) and not r.haptics
    assert not r.write_haptics(rep) and reports(path)[-1][0] == 0x31, "no stream, nothing written"
    r.close()
    ok("stream: setup (twice), no rumble bits in lightbar / trigger / motor reports, motors logged once, normal report at the end")

    # -- the kernel driver must not be touched while the haptics play
    path = newpath()
    d = FFDev()
    r = R.Rumble(0, d, "auto", hidraw=(path, True))
    r.set(10, 20)
    assert d.log == ["upload", "play"]
    r.haptics_begin()
    del d.log[:]
    r.set(30, 40)
    r.set(0, 0)
    assert d.log == [], "evdev FF would make the driver send a report with rumble bits"
    r.haptics_end()
    r.set(50, 60)
    assert d.log == ["upload", "play"], d.log
    r.close()
    ok("auto mode: the evdev FF path is skipped while haptics play and works again afterwards")

    # -- motors that run when the stream starts are stopped first (their stop could not arrive later)
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    r.set(50, 60)
    n = len(reports(path))
    r.haptics_begin()
    out = reports(path)
    assert common(out[n])[0] & 0x03 == 0x03 and common(out[n])[2:4] == b"\0\0", "a normal report with the motors off ..."
    assert common(out[n + 1])[0] == 0 and common(out[n + 1])[1] == 0x62, "... then the setup report"
    assert r.cur == (50, 60)
    r.haptics_end()
    assert common(reports(path)[-1])[2:4] == bytes([50, 60]), "the game's rumble comes back after the stream"
    r.close()
    ok("motors running at the start of a stream are stopped, and come back after it")

    # -- policy "rumble": the game's motors pause the haptics until they stop
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True), haptics_motors="rumble")
    r.haptics_begin()
    assert r.write_haptics(rep)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r.set(50, 60)
    c = common(reports(path)[-1])
    assert c[0] & 0x03 == 0x03 and c[2:4] == bytes([50, 60]) and r.ducked and not r.write_haptics(rep), "rumble wins, haptics paused"
    assert "haptics_motors = rumble" in buf.getvalue()
    n = len(reports(path))
    r.set(0, 0)
    out = reports(path)
    assert common(out[n])[0] & 0x03 == 0x03 and common(out[n])[2:4] == b"\0\0", "the motors are stopped with a normal report"
    assert common(out[n + 1])[0] == 0 and common(out[n + 1])[1] == 0x62 and not r.ducked, "then the setup report: audio mode again"
    assert r.write_haptics(rep)
    r.close()
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True), haptics_motors="rumble")
    r.set(50, 60)
    n = len(reports(path))
    assert r.haptics_begin() and r.haptics and r.ducked and len(reports(path)) == n, "motors running: the stream starts paused"
    r.set(0, 0)
    assert not r.ducked and common(reports(path)[-1])[1] == 0x62
    r.close()
    ok("haptics_motors = rumble: motors pause the haptics (also from the start) and the setup report follows their stop")

    # -- write errors: a bad stream must not take the HID way (rumble, lightbar, triggers) with it
    real, mode = os.write, {"err": None}

    def fake_write(fd, buf):
        if mode["err"] is not None and buf[0] == 0x32:
            raise mode["err"]
        return real(fd, buf)

    os.write = fake_write
    try:
        path = newpath()
        r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
        r.haptics_begin()
        mode["err"] = BlockingIOError(errno.EAGAIN, "full")
        assert not r.write_haptics(rep) and r.haptics and r._haptics_fails == 0, "a full queue is not an error"
        mode["err"] = OSError(errno.EIO, "io")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for _ in range(R.HAPTICS_FAIL_MAX - 1):
                assert not r.write_haptics(rep)
            assert r.haptics and R.WAY_HIDRAW in r.methods
            assert not r.write_haptics(rep)
        assert not r.haptics and R.WAY_HIDRAW in r.methods and r.fd is not None, "the stream ends, the HID way stays"
        assert common(reports(path)[-1])[0] & 0x03 == 0x03, "and a normal report brings rumble back"
        mode["err"] = None
        r.set(1, 2)
        assert reports(path)[-1][3 + 2:3 + 4] == bytes([1, 2])
        r.close()
        path = newpath()
        r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
        r.haptics_begin()
        mode["err"] = OSError(errno.ENODEV, "gone")
        with contextlib.redirect_stdout(io.StringIO()):
            assert not r.write_haptics(rep)
        assert not r.haptics and R.WAY_HIDRAW not in r.methods and r.fd is None, "a pad that is gone goes through the normal handling"
        r.close()
    finally:
        os.write = real
    ok("write errors: a full queue is ignored, a run of errors ends only the stream, a vanished pad switches the HID way off")

    # -- the whole way: UDP packet -> sender_net -> supervisor -> Ctrl -> stream -> HID file
    path = newpath()
    dev = Dev()
    c = sender_ctrl.Ctrl(3, "aa:bb:cc:dd:ee:03", dev, None, None)
    c.rumble = R.Rumble(3, dev, "hidraw", hidraw=(path, True))
    c.haptics = H.HapticsStream(3, c.rumble, sender_config.C.HAPTICS_BUFFER_MS)
    c.haptics.high, c.haptics.low = 10**9, 0                      # keep the audio exactly as sent: no drift correction here
    state.active[c.key] = c
    net.on_haptics = sup.dispatch_haptics
    net.fb_filter = SeqFilter(1.0)
    net.codec = Codec()
    tx = Codec()
    count = 12
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    done = asyncio.Event()

    def send(n):
        pkt = tx.pack(T_HAPT, 3, n * proto.HAPT_FRAMES, HAPT_START if n == 0 else HAPT_END if n == count - 1 else 0, chunk(n))
        deliver(pkt)
        if n == count - 1:
            done.set()

    for n in range(count):
        loop.call_at(t0 + n * proto.HAPT_FRAMES / proto.HAPT_RATE, send, n)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await asyncio.wait_for(done.wait(), 2)
        await asyncio.sleep(0.25)
    out = reports(path)
    audio = [x[13:77] for x in out if x[0] == 0x32]
    played = [a for a in audio if any(a)]
    assert played == [chunk(n) for n in range(count)], f"{len(played)} of {count} chunks, in order and unchanged"
    assert common(out[0])[1] == 0x62 and out[-1][0] == 0x31 and common(out[-1])[0] & 0x03 == 0x03, "setup first, a normal report last"
    assert all(crc_ok(x) for x in out) and not c.rumble.haptics and not c.haptics.active and c.haptics._timer is None
    assert "haptics start" in buf.getvalue() and "haptics end (end of stream)" in buf.getvalue(), buf.getvalue()
    n = len(out)
    await asyncio.sleep(0.1)
    assert len(reports(path)) == n, "nothing is sent between streams"
    ok("end to end: hapt packets -> 0x32 reports, in order, setup before and a normal report after, silence afterwards")

    # a second stream on the same pad, and stop_feedback in the middle of it
    del got[:]
    with contextlib.redirect_stdout(io.StringIO()):
        deliver(tx.pack(T_HAPT, 3, 5000, HAPT_START, chunk(1)))
        assert c.haptics.active and c.rumble.haptics
        c.stop_feedback()
    assert c.haptics is None and c.rumble is None and reports(path)[-1][0] == 0x31, "closing the pad ends the stream with a normal report"
    del state.active[c.key]
    c2 = sender_ctrl.Ctrl(4, "aa:bb:cc:dd:ee:04", dev, None, None)
    c2.on_haptics(0, HAPT_START, memoryview(chunk(0)))
    assert c2.haptics is None, "no rumble backend: ignored"
    c2.rumble = R.Rumble(4, dev, "hidraw", hidraw=(newpath(), False))
    c2.on_haptics(0, HAPT_START, memoryview(chunk(0)))
    assert c2.haptics is None, "a USB pad: ignored"
    ok("a pad closing ends its stream; a pad without a Bluetooth HID report ignores the chunks")


asyncio.run(scenarios())

# ---------- settings ----------
cfg = dict(sender_config.DEFAULTS)
assert cfg["haptics_buffer_ms"] == 20 and cfg["haptics_motors"] == "haptics"
sender_config._apply_runtime(cfg)
assert sender_config.C.HAPTICS_BUFFER_MS == 20.0 and sender_config.C.HAPTICS_MOTORS == "haptics"
for given, want_ms, want_motors in ((5000, 200.0, "haptics"), (1, 10.0, "haptics"), (35, 35.0, "rumble")):
    cfg.update(haptics_buffer_ms=given, haptics_motors={"rumble": "Rumble "}.get("rumble") if want_motors == "rumble" else "nonsense")
    sender_config._apply_runtime(cfg)
    assert sender_config.C.HAPTICS_BUFFER_MS == want_ms and sender_config.C.HAPTICS_MOTORS == want_motors, (given, sender_config.C.HAPTICS_BUFFER_MS)
sender_config._apply_runtime(dict(sender_config.DEFAULTS))
ok("settings: haptics_buffer_ms clamped to 10-200 ms, haptics_motors falls back to haptics")
print("HAPTICS TX OK")
