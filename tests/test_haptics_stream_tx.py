"""Sender: the haptics jitter buffer (HapticsStream) driven by a fake clock and a fake pad: start, ordering, loss, late and repeated
chunks, underrun and re-priming, overrun, clock drift (drop / repeat of one frame), the 200 ms timeout, the end tail and a restart."""
import asyncio, contextlib, io, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_haptics as H
from proto import HAPT_CHUNK, HAPT_END, HAPT_FRAMES, HAPT_RATE, HAPT_START

ok = lambda m: print("OK  ", m)
F = HAPT_FRAMES
PERIOD = F / HAPT_RATE


class Clock:
    t = 1000.0


H.time.monotonic = lambda: Clock.t         # type: ignore[assignment]


class FakePad:
    """The part of Rumble the stream uses."""
    def __init__(self, can=True):
        self.can, self.haptics, self.begun, self.ended, self.out, self.refuse = can, False, 0, 0, [], False

    def haptics_begin(self):
        if not self.can:
            return False
        self.haptics = True
        self.begun += 1
        return True

    def haptics_end(self):
        self.haptics = False
        self.ended += 1

    def write_haptics(self, report):
        if self.refuse:
            return False
        self.out.append(bytes(report.payload))
        return True


def chunk(n):
    return bytes([n % 100 + 1]) * HAPT_CHUNK       # a whole chunk of one value: easy to tell apart


def mk(buffer_ms=20, drift=False):
    """A stream on a fake pad; the drift correction (drop / repeat one frame) is off unless a test is about it."""
    pad = FakePad()
    s = H.HapticsStream(0, pad, buffer_ms)           # type: ignore[arg-type]
    if not drift:
        s.low, s.high = 0, 10**9
    return pad, s


def feed(s, n, flags=0):
    s.feed(n * F, flags, memoryview(chunk(n)))


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


def vals(pad):
    return [p[0] if p[0] == p[-1] else None for p in pad.out]      # one value per report; None = mixed (drift / tail)


async def main():
    # -- the buffer: 20 ms = 60 frames; a report plays 32 frames (10.67 ms); drift limits: low 44, high 92
    pad, s = mk(20)
    assert s.target == 60 and s.frames == F and abs(s.period - 0.010667) < 1e-5 and s.limit == s.target + 3 * F
    assert (mk(20, True)[1].low, mk(20, True)[1].high) == (44, 92)
    quiet(feed, s, 0, HAPT_START)
    assert s.active and pad.haptics and pad.begun == 1 and abs((s._due - Clock.t) - 0.02) < 1e-9, "playout starts target ms after the first chunk"
    feed(s, 1)
    tick(s, 0.02)
    feed(s, 2)
    tick(s)
    tick(s)
    assert vals(pad) == [chunk(0)[0], chunk(1)[0], chunk(2)[0]] and s.stats.underruns == 0 and s.stats.played == 3
    assert all(p == bytes([p[0]]) * HAPT_CHUNK for p in pad.out), "no sample touched in steady state"
    ok("start: pad enters audio mode, first tick after the buffer time, chunks played in order and unchanged")

    # -- an underrun plays silence, refills to target, then continues
    pad, s = mk(20)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)
    tick(s, 0.02)
    tick(s)
    tick(s)                                         # empty: silence + priming
    assert s.stats.underruns == 1 and s.priming and pad.out[-1] == bytes(HAPT_CHUNK), "silence on underrun"
    feed(s, 2)
    tick(s)                                         # 32 frames < target 60: still silence
    assert pad.out[-1] == bytes(HAPT_CHUNK) and s.priming
    feed(s, 3)
    tick(s)
    assert pad.out[-1][0] == chunk(2)[0] and not s.priming, "refilled to target, audio resumes with the oldest chunk, order kept"
    tick(s)
    assert pad.out[-1][0] == chunk(3)[0]
    pad, s = mk(20)                                 # a source that stays too slow: priming gives up after PRIME_MAX_TICKS
    quiet(feed, s, 0, HAPT_START)
    tick(s, 0.02)
    tick(s)                                         # underrun, priming
    feed(s, 1)
    for _ in range(H.PRIME_MAX_TICKS + 1):
        tick(s)
    assert not s.priming and any(p[0] == chunk(1)[0] for p in pad.out), "does not wait forever"
    ok("underrun: silence, re-priming to the buffer depth, gives up after a few ticks")

    # -- loss: a missing chunk is replaced with silence of the same length (later chunks keep their place in time)
    pad, s = mk(40)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)
    feed(s, 3)                                      # chunk 2 lost
    assert s.stats.lost == F and s.ring.depth == 4 * F
    tick(s, 0.04)
    out = [pad.out[-1][0]]
    for _ in range(3):
        tick(s)
        out.append(pad.out[-1][0])
    assert out == [chunk(0)[0], chunk(1)[0], 0, chunk(3)[0]], out
    pad, s = mk(40)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)
    feed(s, 2)
    depth = s.ring.depth
    feed(s, 2)                                      # a repeated chunk
    feed(s, 1)                                      # a late one
    assert s.stats.stale == 2 and s.ring.depth == depth, "stale chunks do not enter the buffer"
    pad, s = mk(20)                                 # the frame counter wraps at 2**32
    quiet(s.feed, 2**32 - F, HAPT_START, memoryview(chunk(1)))
    s.feed(0, 0, memoryview(chunk(2)))
    s.feed(F, 0, memoryview(chunk(3)))
    assert s.stats.lost == 0 and s.stats.stale == 0 and s.stats.resyncs == 0 and s.ring.depth == 3 * F, "wrap-around is not a loss"
    ok("loss filled with silence, late / repeated chunks refused, frame counter wraps")

    # -- a jump of the counter (restarted receiver) restarts the buffer instead of filling seconds of silence
    pad, s = mk(20)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)
    s.feed(10**7, 0, memoryview(chunk(5)))
    assert s.stats.resyncs == 1 and s.ring.depth == F and s.stats.lost == 0 and s.priming
    quiet(s.feed, 5, HAPT_START, memoryview(chunk(6)))          # START in the middle of a running stream = a new stream
    assert s.stats.resyncs == 2 and s.ring.depth == F
    ok("a jump of the counter or a second START restarts the buffer")

    # -- overrun: a stall, then a burst: depth is bounded and the newest audio stays
    pad, s = mk(20)
    quiet(feed, s, 0, HAPT_START)
    for n in range(1, 30):
        feed(s, n)
    assert s.ring.depth <= s.limit and s.stats.overruns >= 1 and s.stats.dropped > 0
    tick(s, 0.02)
    assert pad.out[-1][0] >= chunk(20)[0], "the oldest audio is gone, a late echo is not played"
    ok("overrun: depth bounded, the oldest audio dropped")

    # -- drift: faster source -> one frame dropped per tick while deeper than `high`; slower -> one frame repeated
    pad, s = mk(20, True)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)
    feed(s, 2)                                      # 96 > 92
    quiet(tick, s, 0.02)
    assert s.stats.dropped == 1 and s.ring.depth == 96 - 1 - F and s.stats.repeated == 0
    pad, s = mk(20, True)
    quiet(feed, s, 0, HAPT_START)                   # 32 < 44, still a whole report
    feed(s, 1)
    quiet(tick, s, 0.02)
    quiet(tick, s)
    assert s.stats.repeated == 1 and pad.out[0][-4:-2] == pad.out[0][-2:], "the last frame is repeated"
    pad, s = mk(20, True)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)                                      # 64 frames: between low (44) and high (92)
    quiet(tick, s, 0.02)
    assert s.stats.dropped == 0 and s.stats.repeated == 0, "depth between low and high: untouched"
    ok("drift: a faster source drops a frame per tick, a slower one repeats a frame; in between nothing is touched")

    # -- timeout: no packet for 200 ms plays out the tail and ends the stream (the pad gets a normal report)
    pad, s = mk(20)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)
    tick(s, 0.02)
    Clock.t += H.TIMEOUT_S + 0.01
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for _ in range(4):
            if s.active:
                tick(s, 0.0)
    assert not s.active and pad.ended == 1 and not pad.haptics and s._timer is None, "timeout ends the stream"
    assert "haptics end (timeout)" in buf.getvalue() and s.stats.played == 2, buf.getvalue()
    n_out = len(pad.out)
    Clock.t += 1
    assert len(pad.out) == n_out, "nothing is written after the end"
    ok("timeout: tail played, stream ended once, pad returned to rumble, no more writes")

    # -- HAPT_END: the tail is played and the stream ends without waiting for the timeout
    pad, s = mk(20)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1, HAPT_END)
    tick(s, 0.02)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        while s.active:
            tick(s)
    assert pad.ended == 1 and vals(pad) == [chunk(0)[0], chunk(1)[0]] and "end of stream" in buf.getvalue()
    quiet(feed, s, 50, HAPT_START)                  # a new stream after the end starts cleanly
    assert s.active and pad.begun == 2 and s.stats.chunks == 1 and s.stats.lost == 0
    s.close()
    assert not s.active and pad.ended == 2 and s._timer is None
    s.close()
    assert pad.ended == 2
    pad, s = mk(20)                                 # the tail shorter than a report is padded with silence
    quiet(s.feed, 0, HAPT_START | HAPT_END, memoryview(chunk(1)))
    s.ring.drop(8)
    tick(s, 0.02)
    assert pad.out[0] == chunk(1)[:48] + bytes(16)
    ok("HAPT_END plays the tail (padded) and ends at once; a new stream starts with clean counters; close() is idempotent")

    # -- a pad that cannot play haptics (USB): ignored
    pad = FakePad(can=False)
    s = H.HapticsStream(0, pad, 20)                  # type: ignore[arg-type]
    quiet(feed, s, 0, HAPT_START)
    quiet(feed, s, 1)
    assert not s.active and pad.begun == 0 and s._timer is None
    ok("a pad without haptics: no stream, no timer")

    # -- a full queue is counted, not fatal; the stream ends when Rumble leaves audio mode
    pad, s = mk(20)
    quiet(feed, s, 0, HAPT_START)
    feed(s, 1)
    pad.refuse = True
    tick(s, 0.02)
    assert s.stats.unwritten == 1 and s.active
    pad.haptics = False
    quiet(tick, s)
    assert not s.active and s._timer is None
    ok("unwritten reports counted; the stream ends when the pad leaves audio mode")

    # -- a stalled loop does not produce a burst of catch-up reports
    pad, s = mk(20)
    quiet(feed, s, 0, HAPT_START)
    for n in range(1, 4):
        feed(s, n)
    tick(s, 0.02)
    Clock.t += 0.5
    s._rx = Clock.t
    tick(s, 0.0)
    assert s.stats.late >= 1 and s._due > Clock.t, "restarts the timeline instead of catching up"
    quiet(s.close)
    ok("a stalled event loop restarts the timeline")

    # -- the larger-report seam: more chunks per report means a longer period and a bigger unit
    class Big:
        chunks = 2
        payload = memoryview(bytearray(2 * HAPT_CHUNK))
        def finish(self, seq):
            return self.payload
    s = H.HapticsStream(0, FakePad(), 20, Big())     # type: ignore[arg-type]
    assert s.frames == 2 * F and abs(s.period - 2 * PERIOD) < 1e-12 and s.target >= s.frames
    ok("ChunkReport seam: chunks per report sets the period and the unit of the buffer")


asyncio.run(main())
print("HAPTICS STREAM TX OK")
