"""Sender: the pad's speaker - the spk packet (plain, HMAC v2, v3, old parsers), the bytes of report 0x36 against independent code,
the jitter buffer (SpeakerStream) on a fake clock, one clock for haptics and speaker, the audio mode in Rumble (setup, volume,
volume 0 at the end), the flags of every other report during a stream, the settings, and the whole way from the UDP packet through
a real libopus to the pad's HID file."""
import asyncio, contextlib, ctypes, io, math, os, statistics, struct, sys, tempfile, time, zlib
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import proto
import sender_ctrl
import sender_haptics as H
import sender_net as net
import sender_opus as O
import sender_rumble as R
import sender_speaker as S
import sender_state as state
import sender_supervisor as sup
from proto import (CAP_HAPTICS, CAP_SPEAKER, HAPT_CHUNK, HAPT_START, SPK_BYTES, SPK_END, SPK_START, T_SPK, Codec, SeqFilter)

TMP = tempfile.mkdtemp()
ok = lambda m: print("OK  ", m)
KEY = b"0123456789abcdef"
REAL_MONOTONIC = time.monotonic
PERIOD = S.PERIOD_S
HAVE_OPUS = O.available()
SIZES = {0x31: 78, 0x32: 142, 0x36: 398}


def frame(n):
    return bytes([n % 100 + 1]) * SPK_BYTES            # a whole frame of one value (never 0): easy to tell apart and from silence


def sine(n_frames, hz=440.0, amp=12000):
    count = n_frames * proto.SPK_FRAME
    return struct.pack(f"<{count}h", *[round(amp * math.sin(2 * math.pi * hz * i / proto.SPK_RATE)) for i in range(count)])


_files = iter(range(1000))


def newpath():
    path = os.path.join(TMP, f"hidraw{next(_files)}")
    open(path, "wb").close()
    return path


def reports(path):
    data = open(path, "rb").read()
    out, k = [], 0
    while k < len(data):
        n = SIZES[data[k]]
        out.append(data[k:k + n])
        k += n
    return out


def common(rep):
    return rep[3:50]


def crc_ok(rep):
    return int.from_bytes(rep[-4:], "little") == zlib.crc32(b"\xa2" + rep[:-4]) & 0xFFFFFFFF


def vol_report(seq, volume):
    c = bytearray(47)
    c[0], c[5] = 0x20, volume
    r = bytearray([0x31, (seq & 15) << 4, 0x10]) + c + bytes(24)
    return bytes(r) + (zlib.crc32(b"\xa2" + bytes(r)) & 0xFFFFFFFF).to_bytes(4, "little")


def build36(seq, counter, hapt, opus, buffer=0x10):
    """Report 0x36 the way P3a verified it on the pad, built without any code of the sender."""
    r = bytearray(398)
    r[0], r[1] = 0x36, (seq & 15) << 4
    body = bytes([0x91, 0x07, 0xFE, *[buffer] * 5, counter, 0x92, 0x40]) + hapt + bytes([0x93, len(opus)]) + opus
    r[2:2 + len(body)] = body
    r[-4:] = (zlib.crc32(b"\xa2" + bytes(r[:-4])) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(r)


class Dev:
    def capabilities(self):
        return {}


# ---------- the spk packet ----------
frame_no, data = 0x01020304, bytes(i % 251 for i in range(SPK_BYTES))
plain = Codec().pack(T_SPK, 1, frame_no, SPK_START, data)
assert plain[0] == (2 << 4) | 0x0A and plain[1] == 1 and len(plain) == 4 + 965, (plain[:4].hex(), len(plain))
assert plain[4:] == struct.pack(">IB", frame_no, SPK_START) + data, "body: counter (big endian), flags, 960 B of samples"
assert Codec().parse(plain) == (T_SPK, 1, int.from_bytes(plain[2:4], "big"))
assert proto.BODY[T_SPK].unpack_from(plain, 4) == (frame_no, SPK_START, data)
assert (proto.WIRE[T_SPK], proto.CAP_SPEAKER, proto.SENDER_VERSION) == (proto.T_CMD, 128, 15) and CAP_SPEAKER not in (CAP_HAPTICS, 1, 2, 4, 8, 16, 32)
v2 = Codec(KEY).pack(T_SPK, 1, frame_no, 0, data, v3=False)
assert len(v2) == 4 + 965 + 8 and Codec(KEY).parse(v2)[0] == T_SPK
v3 = Codec(KEY, token=lambda: 1234).pack(T_SPK, 1, frame_no, 0, data, v3=True)
assert len(v3) == 4 + 965 + 12 and v3[0] >> 4 == 3 and Codec(KEY, now_ms=lambda: 1234).parse(v3)[0] == T_SPK
assert len(v3) <= net.RECV_SIZE, "the socket read is large enough for the largest packet"
assert Codec(KEY).parse(v2[:-1]) is None and Codec(b"another key 1234").parse(v2) is None
ok("spk packet: 965 B body, plain / HMAC v2 / v3 round trip, fits the receive buffer")

got = []
net.on_speaker = lambda idx, frame, flags, view: got.append((idx, frame, flags, bytes(view)))
net.fb_filter = SeqFilter(1.0)


def deliver(pkt):
    p = net.codec.parse(pkt)
    assert p is not None
    net._dispatch(p[0], p[1], p[2], pkt)


net.codec = Codec()
tx = Codec()
a, b = tx.pack(T_SPK, 2, 10, SPK_START, frame(1)), tx.pack(T_SPK, 2, 42, 0, frame(2))
deliver(b)
deliver(a)                                                  # an older packet that arrived late
deliver(b)                                                  # the same datagram twice
assert got == [(2, 42, 0, frame(2))], got[:1]
deliver(tx.pack(T_SPK, 5, 7, SPK_END, frame(4)))            # another pad has its own filter, and haptics do not share it
assert got[-1] == (5, 7, SPK_END, frame(4)) and len(got) == 2
net.fb_filter = SeqFilter(1.0)
net.codec = Codec(KEY, token=lambda: 77, now_ms=lambda: 77)
del got[:]
deliver(net.codec.pack(T_SPK, 1, 99, SPK_START, frame(9), v3=True))
assert got == [(1, 99, SPK_START, frame(9))], "the samples are found with the HMAC trailer behind them"
net.codec = Codec()
net.on_speaker = None
deliver(tx.pack(T_SPK, 1, 1, 0, frame(1)))                  # no hook: silently ignored
ok("sender_net: one call per new packet, late / repeated packets dropped, separate filter per pad")


@contextlib.contextmanager
def old_tables():
    """The parser of a peer from before this change: it does not know T_SPK."""
    saved = proto.BY_SIZE
    proto.BY_SIZE = {k: v for k, v in saved.items() if v != T_SPK}
    try:
        yield
    finally:
        proto.BY_SIZE = saved


with old_tables():
    assert Codec().parse(plain) is None, "an older sender drops the unknown length"
    keyed = Codec(KEY)
    assert keyed.parse(v2) is None and keyed.mismatch_t == float("-inf"), "...also with a key, and it is not taken for a key mismatch"
    assert Codec(KEY, now_ms=lambda: 1234).parse(v3) is None
    assert Codec().parse(Codec().pack(proto.T_HAPT, 0, 0, 0, bytes(64)))[0] == proto.T_HAPT, "other packets are not affected"
assert Codec().parse(plain)[0] == T_SPK
ok("an older peer ignores the spk packet (plain, v2, v3) without a key-mismatch warning")

# ---------- report 0x32 / 0x36 ----------
rep = S.Report3x()
rep32 = H.Report32()
for seq in (5, 0x17, 1, 15):
    rep.payload[:] = rep32.payload[:] = bytes((seq * 3 + i) % 200 + 1 for i in range(HAPT_CHUNK))
    assert bytes(rep.finish(seq)) == bytes(rep32.finish(seq)), "without a speaker stream the report is byte for byte report 0x32"
opus = bytes(range(1, 81))


def fill_with(packet):
    def fill():
        rep.buf[S.OPUS_AT:S.OPUS_AT + len(packet)] = packet
    return fill


rep = S.Report3x()
hapt = bytes(range(1, 65))
rep.payload[:] = hapt
rep.attach(fill_with(opus), 80)
first = bytes(rep.finish(5))
assert first == build36(5, 1, hapt, opus), (first.hex(), build36(5, 1, hapt, opus).hex())
assert len(first) == 398 and crc_ok(first) and first[77:79] == bytes([0x93, 80]) and not any(first[159:394]), "zero padding after the packet"
second = bytes(rep.finish(0x17))
assert second == build36(7, 2, hapt, opus), "sequence 4 bits, counter + 1"
for _ in range(254):
    third = bytes(rep.finish(1))
assert third[10] == 0 and rep.finish(1)[10] == 1, "the report counter wraps at 256"
rep.attach(fill_with(bytes(range(1, 121))), 120)
assert bytes(rep.finish(2)) == build36(2, 2, hapt, bytes(range(1, 121))), "another packet size"
rep.detach()
back = bytes(rep.finish(3))
assert len(back) == 142 and back[0] == 0x32 and not any(back[77:138]) and crc_ok(back), "back to a clean 0x32, no leftovers of the packet"
ok("report 0x36: layout, speaker sub-packet 0x93, counter, CRC32 (seed 0xA2) against an independent computation; 0x32 unchanged")

# the pad's own audio buffer length: five bytes of the control sub-packet, set while the speaker plays, the old 0x10 otherwise
rep = S.Report3x()
rep.payload[:] = hapt
rep.attach(fill_with(opus), 80, 48)
big = bytes(rep.finish(5))
assert big == build36(5, 1, hapt, opus, buffer=48), "the five buffer bytes carry the setting, everything else stays"
assert big[5:10] == bytes([48]) * 5 and big[4] == 0xFE and crc_ok(big), "bytes 5-9 only; the CRC covers them"
rep.detach()
back = bytes(rep.finish(6))
assert back[5:10] == bytes([0x10]) * 5 and back[0] == 0x32 and crc_ok(back), "haptics alone keeps the old buffer length"
rep.attach(fill_with(opus), 80)
assert bytes(rep.finish(7))[5:10] == bytes([0x10]) * 5, "no buffer given: the old default"
ok("report 0x36: the pad's buffer length is set per stream (bytes 5-9, CRC32 valid), 0x32 and the default stay at 0x10")

# ---------- the jitter buffer storage ----------
ring = S.PcmRing(4)
for n in range(3):
    assert ring.push(frame(n)) == 0
dst = memoryview(bytearray(SPK_BYTES))
ring.pop_into(dst)
assert bytes(dst) == frame(0) and ring.depth == 2
for n in range(3, 7):
    dropped = ring.push(frame(n))
assert dropped == 1 and ring.depth == 4, "a full ring makes room by dropping the oldest frame"
out = []
while ring.depth:
    ring.pop_into(dst)
    out.append(dst[0])
assert out == [frame(n)[0] for n in (3, 4, 5, 6)] and ring.drop(3) == 0, "frames wrap around the end of the storage in order"
for n in range(3):
    ring.push(frame(n))
assert ring.drop(2) == 2 and ring.depth == 1
ring.pop_into(dst)
assert bytes(dst) == frame(2)
ok("PcmRing: whole frames, wrap-around, overwrite when full, drop")


# ---- crossfade: the drift correction removes one frame without a jump
def seam(frames):
    """The biggest sample-to-sample step inside a stream of frames."""
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return max(abs(b - a) for a, b in zip(samples, samples[1:], strict=False))


wave = sine(4)
f0, fa, fb, fc = (wave[k * SPK_BYTES:(k + 1) * SPK_BYTES] for k in range(4))
natural = seam(wave)                                  # a plain sine's own steepest step
assert seam(fa + fc) > 3 * natural, "test premise: cutting a whole frame out of a sine leaves a jump"
mix_a = bytearray(fa)
S.crossfade(memoryview(mix_a), memoryview(fb))
sa, sb, sm = (struct.unpack(f"<{proto.SPK_FRAME}h", f) for f in (fa, fb, mix_a))
assert sm[0] == sa[0] and sm[-1] == sb[-1], "the first sample is the old frame's, the last one the next frame's"
assert seam(bytes(mix_a) + fc) <= 1.5 * natural, "faded frame + the frame after it: no jump at the seam"
assert seam(f0 + bytes(mix_a)) <= 1.5 * natural, "the previous frame + the faded one: no jump at the start either"
assert max(map(abs, sm)) <= max(map(abs, sa + sb)), "a convex blend never overshoots (no int16 overflow)"
flat = bytearray(frame(7))
S.crossfade(memoryview(flat), memoryview(frame(7)))
assert bytes(flat) == frame(7), "two equal frames stay equal"
ok("crossfade: first / last sample kept, no jump at either seam (a hard cut jumps), no overshoot")


# ---------- the stream on a fake clock and a fake pad ----------
class Clock:
    t = 1000.0


class FakeEnc:
    """Stands in for libopus: the 'packet' is the first byte of the frame repeated, so the played frame can be read from the report."""

    def __init__(self, size, mode="hybrid", complexity=-1):
        self.size, self.mode, self.fail, self.closed, self.complexity = size, mode, False, False, complexity
        self._pcm = bytearray(SPK_BYTES)
        self.pcm = memoryview(self._pcm)

    def bind(self, out, at):
        self.out, self.at = out, at

    def encode(self):
        if self.fail:
            return False
        self.out[self.at:self.at + self.size] = bytes([self._pcm[0]]) * self.size
        return True

    def close(self):
        self.closed = True


class FakePad:
    """The part of Rumble the streams use."""

    def __init__(self, can=True):
        self.can, self.haptics, self.speaker, self.t_audio = can, False, False, 0.0
        self.begun, self.ended, self.out, self.refuse, self.volumes = 0, 0, [], False, []
        self.hbegun = self.hended = 0

    def speaker_begin(self, volume):
        if not self.can:
            return False
        self.speaker = True
        self.begun += 1
        self.volumes.append(volume)
        return True

    def speaker_end(self):
        self.speaker = False
        self.ended += 1

    def haptics_begin(self):
        self.haptics = True
        self.hbegun += 1
        return True

    def haptics_end(self):
        self.haptics = False
        self.hended += 1

    def write_haptics(self, report):
        if self.refuse:
            return False
        self.out.append(bytes(report.finish(len(self.out) + 1)))
        self.t_audio = Clock.t
        return True


def mk(buffer_ms=30, can=True):
    pad = FakePad(can)
    report = S.Report3x()
    s = S.SpeakerStream(0, pad, report, lambda: False, buffer_ms, 100, 80)       # type: ignore[arg-type]
    return pad, report, s


def feed(s, n, flags=0):
    s.feed(n, flags, memoryview(frame(n)))


def tick(s, dt=PERIOD):
    """Advance the clock by one period (or dt) and run the pending tick by hand."""
    if s._timer is not None:
        s._timer.cancel()
        s._timer = None
    Clock.t = max(Clock.t + dt, s._due) if dt else Clock.t
    s._tick()


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def played(pad):
    """The value of the frame each report carried (0 = silence)."""
    return [r[S.OPUS_AT] for r in pad.out]


async def fake_clock_scenarios():
    S.OpusEncoder = FakeEnc
    time.monotonic = lambda: Clock.t         # type: ignore[assignment]
    try:
        # -- start: 30 ms = 3 frames; the first tick comes after the buffer time
        pad, report, s = mk(30)
        assert s.target == 3 and (s.high, s.limit) == (5, 8) and abs(S.PERIOD_S - 0.0106667) < 1e-6
        quiet(feed, s, 0, SPK_START)
        assert s.active and pad.speaker and pad.volumes == [100] and abs((s._due - Clock.t) - 0.03) < 1e-9, "the volume is set at the start"
        assert report.buf[0] == 0x36 and report.buf[77:79] == bytes([0x93, 80]), "the report became a 0x36"
        feed(s, 1)
        feed(s, 2)
        tick(s, 0.03)
        tick(s)
        tick(s)
        assert played(pad) == [1, 2, 3] and s.stats.underruns == 0 and s.stats.played == 3
        assert all(r[0] == 0x36 and crc_ok(r) and r[S.OPUS_AT:S.OPUS_AT + 80] == bytes([r[S.OPUS_AT]]) * 80 for r in pad.out)
        assert all(not any(r[13:77]) for r in pad.out), "no haptics: the haptics part of the report is silent"
        assert [r[1] >> 4 for r in pad.out] == [1, 2, 3], "one report per tick, sequence numbers from Rumble"
        ok("start: audio mode and volume, first tick after the buffer time, frames played in order, 0x36 reports with a silent haptics part")

        # -- the pad's own audio buffer length goes out with the stream and the default comes back at its end
        pad, report, s = mk(30)
        assert report.buf[5:10] == bytes([0x10]) * 5, "idle: the old default"
        quiet(feed, s, 0, SPK_START)
        feed(s, 1)
        feed(s, 2)
        tick(s, 0.03)
        assert pad.out[0][5:10] == bytes([S.PAD_BUFFER]) * 5 and 16 <= S.PAD_BUFFER <= 128 and crc_ok(pad.out[0]), "the stream's reports carry it"
        quiet(s.close)
        assert report.buf[5:10] == bytes([0x10]) * 5 and report.buf[0] == 0x32, "the end of the stream gives the default back"
        ok("speaker stream: the pad's audio buffer length (bytes 5-9) is set while it plays and back to 0x10 afterwards")

        # -- an underrun plays a silent frame (never a skipped report), refills to the target, then continues
        pad, report, s = mk(30)
        quiet(feed, s, 0, SPK_START)
        feed(s, 1)
        tick(s, 0.03)
        tick(s)
        tick(s)                                     # empty
        assert s.stats.underruns == 1 and s.priming and played(pad) == [1, 2, 0], "silence on underrun, the report still goes out"
        feed(s, 2)
        tick(s)                                     # one frame < target 3: still silence
        assert played(pad)[-1] == 0 and s.priming
        feed(s, 3)
        feed(s, 4)
        tick(s)
        assert played(pad)[-1] == 3 and not s.priming, "refilled to the target, audio resumes with the oldest frame, order kept"
        tick(s)
        assert played(pad)[-1] == 4
        pad, report, s = mk(30)                     # a source that stays too slow: priming gives up after PRIME_MAX_TICKS
        quiet(feed, s, 0, SPK_START)
        tick(s, 0.03)
        tick(s)                                     # underrun, priming
        feed(s, 1)
        for _ in range(S.PRIME_MAX_TICKS + 1):
            tick(s)
        assert not s.priming and 2 in played(pad), "does not wait forever"
        ok("underrun: a silent frame in the report, re-priming to the buffer depth, gives up after a few ticks")

        # -- loss, late and repeated frames, a jump, and the wrap of the frame counter
        pad, report, s = mk(50)
        quiet(feed, s, 0, SPK_START)
        feed(s, 1)
        feed(s, 3)                                  # frame 2 lost
        assert s.stats.lost == 1 and s.ring.depth == 4
        tick(s, 0.05)
        for _ in range(3):
            tick(s)
        assert played(pad) == [1, 2, 0, 4], played(pad)
        pad, report, s = mk(50)
        quiet(feed, s, 0, SPK_START)
        feed(s, 1)
        feed(s, 2)
        depth = s.ring.depth
        feed(s, 2)                                  # a repeated frame
        feed(s, 1)                                  # a late one
        assert s.stats.stale == 2 and s.ring.depth == depth, "stale frames do not enter the buffer"
        quiet(feed, s, 1000)                        # a jump too large to be a loss
        assert s.stats.resyncs == 1 and s.ring.depth == 1 and s.priming and s._expected == 1001
        quiet(feed, s, 0, SPK_START)                # the PC began a new stream without ending the old one
        assert s.stats.resyncs == 2 and s.ring.depth == 1
        pad, report, s = mk(50)
        quiet(s.feed, 2**32 - 2, SPK_START, memoryview(frame(0)))
        for n in (2**32 - 1, 0, 1):
            s.feed(n, 0, memoryview(frame(1)))
        assert s.stats.lost == 0 and s.stats.stale == 0 and s.ring.depth == 4 and s._expected == 2, "the counter wraps at 2**32"
        ok("loss filled with a silent frame, late / repeated frames refused, a jump restarts, the frame counter wraps")
        # -- a pause in the sound: END, then a new START with the next number while the old stream still plays out: no restart
        pad, report, s = mk(50)
        quiet(feed, s, 0, SPK_START)
        feed(s, 1)
        feed(s, 2, SPK_END)
        assert s.ending and s.ring.depth == 3
        feed(s, 3, SPK_START)
        assert not s.ending and s.stats.resyncs == 0 and s.stats.lost == 0 and s.ring.depth == 4, "the buffered audio stays"
        assert s.stats.dropped == 0 and not s.priming
        ok("a new START right after an END keeps the buffer (no gap, no resync)")

        # -- clock drift: the PC sends 100 frames per second, the pad takes 93.75
        pad, report, s = mk(30)
        t0, fed = Clock.t, 300
        for k in range(fed):
            Clock.t = t0 + k * 0.010
            s.feed(k, SPK_START if k == 0 else 0, memoryview(frame(k)))
            while s.active and s._due <= Clock.t + 1e-9:
                s._timer = None
                s._tick()
        st = s.stats
        assert st.underruns == 0 and 275 <= st.played <= 281, (st.underruns, st.played)
        assert st.dropped == fed - st.played - s.ring.depth, "every frame is either played, dropped or still buffered"
        assert 12 <= st.dropped <= 25 and s.ring.depth <= s.high, (st.dropped, s.ring.depth)
        vals = played(pad)
        steps = [(b - a) % 100 for a, b in zip(vals, vals[1:], strict=False)]
        assert set(steps) <= {1, 2}, "frames are dropped one at a time and never reordered"
        ok(f"drift: {st.dropped} frames crossfaded away in 3 s (about 1 in 16), no underrun, the buffer stays shallow")

        # -- a burst after a stall keeps the newest audio
        pad, report, s = mk(30)
        quiet(feed, s, 0, SPK_START)
        for n in range(1, 30):
            feed(s, n)
        assert s.ring.depth <= s.limit and s.stats.dropped >= 20, (s.ring.depth, s.stats.dropped)
        tick(s, 0.03)
        assert played(pad)[-1] > 20, "the newest audio plays, not a late echo"
        ok("a burst after a stall: the oldest frames are dropped at once")

        # -- the end: a timeout, and SPK_END; both play a silent tail, set the volume to 0 and give the pad back
        for how in ("timeout", "flag"):
            pad, report, s = mk(30)
            quiet(feed, s, 0, SPK_START)
            feed(s, 1)
            feed(s, 2, SPK_END if how == "flag" else 0)
            tick(s, 0.03)
            tick(s)
            tick(s)
            assert played(pad) == [1, 2, 3] and s.active
            if how == "timeout":
                Clock.t += 0.25
            n = len(pad.out)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                for _ in range(40):
                    if not s.active:
                        break
                    tick(s)
            assert not s.active and s._timer is None and pad.ended == 1 and not pad.speaker, "the pad gets speaker_end exactly once"
            assert played(pad)[n:] == [0] * S.TAIL_TICKS, "a silent tail of about 100 ms after the last frame"
            assert report.buf[0] == 0x32 and not any(report.buf[77:394]), "the report is a plain 0x32 again"
            assert f"speaker end ({'timeout' if how == 'timeout' else 'end of stream'})" in buf.getvalue(), buf.getvalue()
            assert s.stats.underruns == 0, "the end is not an underrun"
        ok("end of stream (SPK_END or 200 ms timeout): buffered audio, ~100 ms of silence, then speaker_end once")

        # -- a pad that cannot play, a second stream, an encoder error, closing
        pad, report, s = mk(30, can=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            feed(s, 0, SPK_START)
            feed(s, 1)
            Clock.t += 0.5
            feed(s, 2)
        assert not s.active and buf.getvalue().count("cannot play audio") == 1, "refused: logged once, retried only after a second"
        Clock.t += 1.0
        pad.can = True
        quiet(feed, s, 3, SPK_START)
        assert s.active and pad.begun == 1
        s.encoder.fail = True
        feed(s, 4)
        feed(s, 5)
        tick(s, 0.03)
        assert s.stats.encoder_errors == 1 and len(pad.out) == 1 and crc_ok(pad.out[0]), "an encoder error still sends a valid report"
        pad.refuse = True
        tick(s)
        assert s.stats.unwritten == 1, "a report that was not written is counted"
        quiet(s.close)
        assert not s.active and pad.ended == 1 and s.encoder.closed and s._timer is None
        quiet(s.close)
        ok("a USB pad refuses once and retries later, encoder errors and unwritten reports are counted, close ends the stream")

        # -- one clock for haptics and speaker
        pad = FakePad()
        report = S.Report3x()
        hs = H.HapticsStream(0, pad, 20, report)             # type: ignore[arg-type]
        hs.low, hs.high = 0, 10**9
        sp = S.SpeakerStream(0, pad, report, lambda: hs.writing, 30, 100, 80)     # type: ignore[arg-type]
        quiet(feed, sp, 0, SPK_START)
        for n in range(1, 5):
            feed(sp, n)
        tick(sp, 0.03)
        tick(sp)
        assert len(pad.out) == 2 and all(r[0] == 0x36 for r in pad.out) and not hs.writing, "speaker alone: its own clock"
        hch = [bytes([n + 10]) * HAPT_CHUNK for n in range(4)]
        for n, hc in enumerate(hch):
            quiet(hs.feed, n * proto.HAPT_FRAMES, HAPT_START if n == 0 else 0, memoryview(hc))
        assert hs.active and pad.haptics and sp.drives() is False, "haptics started but has not written yet: the speaker keeps going"
        tick(sp)
        assert len(pad.out) == 3 and not any(pad.out[-1][13:77]), "the haptics part stays silent until the haptics tick writes"
        feed(sp, 5)
        feed(sp, 6)
        Clock.t = max(Clock.t + 0.001, hs._due)
        hs._timer = None
        hs._tick()                                           # the haptics tick: one 0x36 with both sounds
        assert hs.writing and len(pad.out) == 4
        last = pad.out[-1]
        assert last[0] == 0x36 and last[13:77] == hch[0] and last[S.OPUS_AT] == 4 and crc_ok(last), (last[13], last[S.OPUS_AT])
        n_before = len(pad.out)
        sp._timer = None
        Clock.t = max(Clock.t + 0.002, sp._due)
        sp._tick()
        assert len(pad.out) == n_before, "the speaker writes nothing while the haptics tick writes"
        assert abs(sp._due - (pad.t_audio + S.PERIOD_S + S.FOLLOW_S)) < 1e-9, "...and keeps its timer in phase with it"
        for _ in range(2):
            Clock.t = max(Clock.t + 0.001, hs._due)
            hs._timer = None
            hs._tick()
        assert [r[S.OPUS_AT] for r in pad.out[-3:]] == [4, 5, 6], "every haptics report plays the next speaker frame"
        assert [r[1] >> 4 for r in pad.out] == [(i + 1) & 15 for i in range(len(pad.out))], "one sequence for all reports"
        quiet(hs._finish, "test")
        assert not hs.writing and not pad.haptics and pad.speaker, "the haptics ended, the pad stays in audio mode for the speaker"
        n_before = len(pad.out)
        sp._timer = None
        Clock.t = max(Clock.t + 0.002, sp._due)
        sp._tick()
        assert len(pad.out) == n_before + 1 and not any(pad.out[-1][13:77]) and pad.out[-1][S.OPUS_AT] == 7, "the speaker takes the clock back"
        quiet(sp.close)
        ok("haptics and speaker share one report and one clock; the speaker follows the haptics tick and takes over when it ends")
    finally:
        time.monotonic = REAL_MONOTONIC
        S.OpusEncoder = O.OpusEncoder


asyncio.run(fake_clock_scenarios())


# ---------- Rumble: audio mode with a speaker ----------
async def rumble_scenarios():
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    assert r.bits & CAP_HAPTICS and (r.bits & CAP_SPEAKER == (CAP_SPEAKER if HAVE_OPUS else 0))
    assert not R.Rumble(0, Dev(), "hidraw", hidraw=(newpath(), False)).bits & CAP_SPEAKER, "a USB pad has no report 0x36"
    assert not R.Rumble(0, Dev(), "evdev").bits & CAP_SPEAKER and not R.Rumble(0, Dev(), "off").bits & CAP_SPEAKER
    saved = O._lib, O._tried
    O._lib, O._tried = None, True
    try:
        assert not O.available() and not r.bits & CAP_SPEAKER and r.bits & CAP_HAPTICS, "no libopus: no speaker, haptics unaffected"
    finally:
        O._lib, O._tried = saved
    ok("CAP_SPEAKER only for a Bluetooth pad with a HID report and an installed libopus")

    # speaker alone: setup, volume, reports during the stream, volume 0, normal report
    assert r.speaker_begin(120) and r.speaker and not r.haptics and r.audio
    out = reports(path)
    assert len(out) == 2 and out[0][1] >> 4 == 1 and common(out[0])[:2] == bytes([0, 0x62]), "the haptics setup report first"
    assert out[1] == vol_report(2, 120), "then a report that only sets the volume (valid_flag0 0x20, byte 5)"
    r.set(70, 140, (1, 2, 3))
    assert common(reports(path)[-1])[0] & 0x03 == 0 and common(reports(path)[-1])[2:4] == b"\0\0", "no rumble bits while the speaker plays"
    assert r.speaker_begin(50) and len(reports(path)) == 3, "a second begin changes nothing"
    n = len(reports(path))
    r.haptics_end()
    r.haptics_end()
    assert len(reports(path)) == n and r.audio, "haptics_end without a haptics stream does not touch the speaker's audio mode"
    r.speaker_end()
    out = reports(path)
    assert out[-2] == vol_report(out[-2][1] >> 4, 0) and out[-1][0] == 0x31 and common(out[-1])[0] & 0x03 == 0x03, "volume 0, then a normal report"
    assert not r.speaker and not r.audio and common(out[-1])[2:4] == bytes([70, 140]), "the game's rumble is back"
    n = len(reports(path))
    r.speaker_end()
    assert len(reports(path)) == n, "ending twice sends nothing"
    assert all(crc_ok(x) for x in reports(path))
    r.close()

    # haptics first, then the speaker, then the haptics end first: audio mode stays until the last stream
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    assert r.haptics_begin() and len(reports(path)) == 1
    assert r.speaker_begin(100) and len(reports(path)) == 2 and reports(path)[1] == vol_report(2, 100), "audio mode is on: only the volume"
    r.haptics_end()
    assert not r.haptics and r.speaker and r.audio and len(reports(path)) == 2, "the haptics ended, the speaker still plays: no normal report"
    assert r.haptics_begin() and len(reports(path)) == 2, "haptics again: no second setup"
    r.speaker_end()
    out = reports(path)
    assert len(out) == 3 and out[2] == vol_report(3, 0) and r.haptics and r.audio, "the speaker ended, the haptics play: volume 0 only"
    r.haptics_end()
    assert common(reports(path)[-1])[0] & 0x03 == 0x03 and not r.audio
    r.close()

    # close() and write errors with a speaker stream
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    r.speaker_begin(100)
    r.close()
    assert not r.speaker and common(reports(path)[-1])[0] & 0x03 == 0x03, "closing the pad leaves audio mode"
    real = os.write
    try:
        r = R.Rumble(0, Dev(), "hidraw", hidraw=(newpath(), True))
        r.speaker_begin(100)
        rep = S.Report3x()
        os.write = lambda fd, buf: (_ for _ in ()).throw(OSError(5, "io")) if buf[0] == 0x36 else real(fd, buf)
        rep.attach(lambda: None, 80)
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(R.HAPTICS_FAIL_MAX):
                r.write_haptics(rep)
        assert not r.speaker and not r.haptics and R.WAY_HIDRAW in r.methods, "a run of errors ends the audio mode, not the HID way"
    finally:
        os.write = real
    ok("Rumble: setup + volume at the start, no rumble bits while it plays, volume 0 + normal report at the end, shared audio mode")

    # the volume byte is clamped and a report for a vanished pad does not raise
    path = newpath()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    r.speaker_begin(1000)
    assert reports(path)[1][3 + 5] == 255
    r.close()
    ok("speaker volume is clamped to a byte")


asyncio.run(rumble_scenarios())


# ---------- Ctrl, supervisor, settings ----------
async def ctrl_scenarios():
    dev = Dev()
    c = sender_ctrl.Ctrl(3, "aa:bb:cc:dd:ee:03", dev, None, None)
    c.on_speaker(0, SPK_START, memoryview(frame(0)))
    assert c.speaker is None, "no rumble backend: ignored"
    c.rumble = R.Rumble(3, dev, "hidraw", hidraw=(newpath(), False))
    c.on_speaker(0, SPK_START, memoryview(frame(0)))
    assert c.speaker is None, "a USB pad: ignored"
    ok("a pad without a Bluetooth HID report ignores the speaker frames")
    if not HAVE_OPUS:
        print("SKIP libopus is not installed: the encoder and the end-to-end checks")
        return
    # the encoder: exactly the requested size, valid Opus that decodes to the sine we put in
    enc = O.OpusEncoder(80)
    buf = bytearray(398)
    enc.bind(buf, S.OPUS_AT)
    enc.pcm[:] = sine(1)
    assert enc.encode() and any(buf[S.OPUS_AT:S.OPUS_AT + 80]) and not any(buf[S.OPUS_AT + 80:]) and buf[S.OPUS_AT] >> 3 == 14, buf[79:83].hex()   # hybrid fullband 10 ms (TOC 0x70)
    lib = O.library()
    lib.opus_decoder_create.restype = ctypes.c_void_p
    err = ctypes.c_int(0)
    dec = lib.opus_decoder_create(48000, 1, ctypes.byref(err))
    assert err.value == 0
    for size in (30, 80, 200):
        e = O.OpusEncoder(size)
        b = bytearray(400)
        e.bind(b, 10)
        e.pcm[:] = sine(1)
        assert e.encode() and e.size == size and not any(b[10 + size:]), f"CBR gives exactly {size} bytes"
        e.close()
    assert O.OpusEncoder(5).size == O.OPUS_BYTES_MIN and O.OpusEncoder(5000).size == O.OPUS_BYTES_MAX, "the size is clamped"
    try:
        enc.bind(bytearray(10), 0)
        raise AssertionError("a buffer that is too small must be refused")
    except ValueError:
        pass
    enc.close()
    enc.close()
    ok("libopus: CBR packets of exactly the requested size, written in place into the report buffer")

    # the Opus mode: hybrid by default (a clean tone on the pad), celt on request; both decode to 480 samples of the sine we put in
    lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    for mode, want in (("hybrid", range(12, 16)), ("celt", range(28, 32)), ("anything", range(12, 16))):
        e = O.OpusEncoder(80) if mode == "hybrid" else O.OpusEncoder(80, mode)
        b = bytearray(400)
        e.bind(b, 10)
        got = set()
        pcm_out = (ctypes.c_int16 * 480)()
        for k in range(20):
            e.pcm[:] = sine(1, hz=1234.0) if k else bytes(960)
            assert e.encode() and not any(b[10 + 80:]), mode
            got.add(b[10] >> 3)
            assert lib.opus_decode(dec, bytes(b[10:90]), 80, ctypes.addressof(pcm_out), 480, 0) == 480
        assert got <= set(want), (mode, got)
        assert e.mode == ("celt" if mode == "celt" else "hybrid"), (mode, e.mode)
        e.close()
    assert O.OpusEncoder(80).mode == "hybrid" and O.DEFAULT_MODE == "hybrid", "hybrid is the default"
    ok("libopus: hybrid by default (TOC config 12-15, CBR, decodes), celt on request, an unknown mode counts as hybrid")

    # the complexity: automatic = 3 for hybrid and 5 for celt, a number sets it, a larger one counts as 10, the packets stay CBR
    def complexity_of(enc):
        value = ctypes.c_int32(-1)
        assert lib.opus_encoder_ctl(enc._state, ctypes.c_int(4011), ctypes.byref(value)) == 0     # OPUS_GET_COMPLEXITY
        return value.value
    for mode, given, want in (("hybrid", O.COMPLEXITY_AUTO, 3), ("celt", O.COMPLEXITY_AUTO, 5), ("hybrid", 0, 0), ("hybrid", 1, 1),
                              ("celt", 10, 10), ("hybrid", 99, 10), ("celt", -7, 5)):
        e = O.OpusEncoder(80, mode, given)
        b = bytearray(400)
        e.bind(b, 0)
        e.pcm[:] = sine(1, hz=1234.0)
        assert complexity_of(e) == want and e.mode == mode and e.encode(), (mode, given, complexity_of(e))
        e.close()
    ok("libopus: complexity automatic (3 hybrid / 5 celt) or set, 0-10, larger values count as 10")

    # end to end: UDP packets -> sender_net -> supervisor -> Ctrl -> stream -> real libopus -> 0x36 in the HID file
    path = newpath()
    c = sender_ctrl.Ctrl(3, "aa:bb:cc:dd:ee:03", dev, None, None)
    c.rumble = R.Rumble(3, dev, "hidraw", hidraw=(path, True))
    state.active[c.key] = c
    net.on_speaker = sup.dispatch_speaker
    net.fb_filter = SeqFilter(1.0)
    net.codec = Codec()
    tx = Codec()
    sender_config.C.SPEAKER_OPUS_COMPLEXITY = 1                     # from the settings, through Ctrl, into the encoder
    count = 150
    pcm = sine(count)
    stamps = []
    real = os.write

    def timed_write(fd, buf):
        if buf[0] == 0x36:
            stamps.append(REAL_MONOTONIC())
        return real(fd, buf)

    os.write = timed_write
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    done = asyncio.Event()

    def send(n):
        flags = SPK_START if n == 0 else SPK_END if n == count - 1 else 0
        deliver(tx.pack(T_SPK, 3, n, flags, pcm[n * SPK_BYTES:(n + 1) * SPK_BYTES]))
        if n == count - 1:
            done.set()

    for n in range(count):
        loop.call_at(t0 + n * 0.010, send, n)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            await asyncio.wait_for(done.wait(), 5)
            await asyncio.sleep(0.4)
    finally:
        os.write = real
    out = reports(path)
    r36 = [x for x in out if x[0] == 0x36]
    assert 125 <= len(r36) <= 165, f"{len(r36)} audio reports for 1.5 s of sound"
    assert out[0][0] == 0x31 and common(out[0])[:2] == bytes([0, 0x62]) and out[1] == vol_report(2, sender_config.C.SPEAKER_VOLUME), "setup, then the volume"
    k = max(i for i, x in enumerate(out) if x[0] == 0x31 and common(x)[0] == 0x20)       # the volume report that ends the stream
    assert out[k] == vol_report(out[k][1] >> 4, 0) and k > 2 and all(x[0] == 0x36 for x in out[k - 1:k]), "volume 0 right after the last frame"
    assert out[k + 1:] and all(x[0] == 0x31 and common(x)[0] & 0x03 == 0x03 for x in out[k + 1:]), "then only normal reports (and their repeat)"
    mid = [x for x in out[2:k] if x[0] == 0x31]
    assert all(common(x)[:2] == bytes([0, 0x62]) for x in mid) and len(mid) <= 1, "the only other 0x31 in the stream is the repeated setup"
    assert all(crc_ok(x) for x in out) and all(x[77:79] == bytes([0x93, 80]) and not any(x[13:77]) for x in r36)
    assert all((b[1] >> 4) - (a[1] >> 4) in (1, -15) for a, b in zip(out, out[1:], strict=False)), "one sequence for every report"
    assert not c.rumble.speaker and not c.speaker.active and c.speaker._timer is None and c.speaker._ticker is None, "timer released"
    assert complexity_of(c.speaker.encoder) == 1, "speaker_opus_complexity reaches the real encoder"
    sender_config.C.SPEAKER_OPUS_COMPLEXITY = O.COMPLEXITY_AUTO
    gaps = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:], strict=False)]
    mean = (stamps[-1] - stamps[0]) * 1000 / (len(stamps) - 1)
    assert 10.55 < mean < 10.78 and 9.5 < statistics.median(gaps) < 11.5, (mean, statistics.median(gaps))   # the pad's clock is 10.667 ms
    assert "speaker start" in buf.getvalue() and "speaker end (end of stream)" in buf.getvalue(), buf.getvalue()
    # the packets are valid Opus and carry our 440 Hz sine
    pcm_out = bytearray()
    lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    frames_out = (ctypes.c_int16 * 480)()
    for x in r36:
        n = lib.opus_decode(dec, bytes(x[S.OPUS_AT:S.OPUS_AT + 80]), 80, frames_out, 480, 0)
        assert n == 480
        pcm_out += bytes(frames_out)
    samples = struct.unpack(f"<{len(pcm_out) // 2}h", bytes(pcm_out))[480 * 20:480 * 100]
    rms = (sum(v * v for v in samples) / len(samples)) ** 0.5
    crossings = sum(1 for a, b in zip(samples, samples[1:], strict=False) if a < 0 <= b)
    hz = crossings / (len(samples) / 48000)
    assert 6000 < rms < 10000 and 420 < hz < 460, (rms, hz)
    n = len(out)
    await asyncio.sleep(0.15)
    assert len(reports(path)) == n, "nothing is sent between streams"
    ok(f"end to end: spk packets -> real Opus in 0x36 reports ({len(r36)}), decodes to the 440 Hz sine, mean interval "
       f"{mean:.3f} ms, volume and mode restored at the end")
    lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_decoder_destroy(dec)

    # a second stream, and stop_feedback in the middle of it
    with contextlib.redirect_stdout(io.StringIO()):
        deliver(tx.pack(T_SPK, 3, 5000, SPK_START, frame(1)))
        assert c.speaker.active and c.rumble.speaker
        c.stop_feedback()
    assert c.speaker is None and c.rumble is None and reports(path)[-1][0] == 0x31, "closing the pad ends the stream with a normal report"
    assert common(reports(path)[-1])[0] & 0x03 == 0x03 and reports(path)[-2] == vol_report(reports(path)[-2][1] >> 4, 0)
    del state.active[c.key]
    # an encoder that cannot be created is logged once, not for every packet
    c2 = sender_ctrl.Ctrl(4, "aa:bb:cc:dd:ee:04", dev, None, None)
    c2.rumble = R.Rumble(4, dev, "hidraw", hidraw=(newpath(), True))
    real_enc = S.OpusEncoder

    def broken(size, mode="hybrid", complexity=-1):
        raise OSError("no encoder")

    S.OpusEncoder = broken
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            for n in range(5):
                c2.on_speaker(n, 0, memoryview(frame(n)))
    finally:
        S.OpusEncoder = real_enc
    assert c2.speaker is None and buf.getvalue().count("speaker unavailable") == 1, buf.getvalue()
    ok("closing a pad ends its stream (volume 0, normal report); a broken encoder is logged once")


asyncio.run(ctrl_scenarios())

# ---------- settings ----------
cfg = dict(sender_config.DEFAULTS)
assert (cfg["speaker_buffer_ms"], cfg["speaker_volume"], cfg["speaker_opus_bytes"], cfg["speaker_opus_mode"]) == (30, 100, 80, "hybrid")
sender_config._apply_runtime(cfg)
C = sender_config.C
assert (C.SPEAKER_BUFFER_MS, C.SPEAKER_VOLUME, C.SPEAKER_OPUS_BYTES) == (30.0, 100, 80)
for given, want in (((5000, 999, 5000), (200.0, 255, O.OPUS_BYTES_MAX)), ((1, -4, 1), (20.0, 0, O.OPUS_BYTES_MIN)), ((45, 60, 120), (45.0, 60, 120))):
    cfg.update(speaker_buffer_ms=given[0], speaker_volume=given[1], speaker_opus_bytes=given[2])
    sender_config._apply_runtime(cfg)
    assert (C.SPEAKER_BUFFER_MS, C.SPEAKER_VOLUME, C.SPEAKER_OPUS_BYTES) == want, given
assert C.SPEAKER_OPUS_MODE == "hybrid"
for given, want in (("celt", "celt"), (" CELT ", "celt"), ("hybrid", "hybrid"), ("nonsense", "hybrid")):
    cfg.update(speaker_opus_mode=given)
    sender_config._apply_runtime(cfg)
    assert C.SPEAKER_OPUS_MODE == want, given
assert cfg["speaker_opus_complexity"] == -1 and C.SPEAKER_OPUS_COMPLEXITY == -1, "automatic by default"
for given, want in ((0, 0), (3, 3), (10, 10), (11, -1), (-1, -1), (-9, -1), ("7", 7)):
    cfg.update(speaker_opus_complexity=given)
    sender_config._apply_runtime(cfg)
    assert C.SPEAKER_OPUS_COMPLEXITY == want, given
sender_config._apply_runtime(dict(sender_config.DEFAULTS))
ok("settings: speaker_buffer_ms clamped to 20-200 ms, speaker_volume to a byte, speaker_opus_bytes to 30-200, speaker_opus_mode hybrid | celt")
ok("settings: speaker_opus_complexity 0-10, automatic (-1) by default and for anything out of range")
print("SPEAKER TX OK")
