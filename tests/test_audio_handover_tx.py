"""Sender: haptics and speaker on one pad clock - a haptics stream that starts while the speaker plays joins the speaker's clock.

Regression of the sender review: the first haptics report went out at the haptics stream's own phase, a fraction of a period after
the speaker's last report. That was one report too many and one speaker frame gone from the jitter buffer for every haptics start (the
receiver ends and starts haptics at every pause), so a few starts drained the buffer into an underrun (a gap of silence). And while the
speaker followed the haptics tick, a haptics write that did not go through made it re-arm for a moment that had passed: a busy loop
until the next haptics write.

The streams run on a fake clock; a tiny scheduler fires whichever timer is due first, with both tie orders.
"""
import asyncio, contextlib, io, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_haptics as H
import sender_speaker as S
from proto import HAPT_CHUNK, HAPT_END, HAPT_FRAMES, HAPT_START, SPK_BYTES, SPK_START

ok = lambda m: print("OK  ", m)
REAL_MONOTONIC = time.monotonic
P = S.PERIOD_S


class Clock:
    t = 1000.0


class FakeEnc:
    """Stands in for libopus: the 'packet' is the first byte of the frame, so the played frame can be read from the report."""

    def __init__(self, size, mode="hybrid", complexity=-1):
        self.size, self.mode = size, mode
        self._pcm = bytearray(SPK_BYTES)
        self.pcm = memoryview(self._pcm)

    def bind(self, out, at):
        self.out, self.at = out, at

    def encode(self):
        self.out[self.at:self.at + self.size] = bytes([self._pcm[0]]) * self.size
        return True

    def close(self):
        pass


class FakePad:
    """The part of Rumble the streams use; ``refuse`` makes write_haptics fail like a full hidraw queue."""

    def __init__(self):
        self.haptics = self.speaker = False
        self.t_audio, self.out, self.refuse = 0.0, [], False

    def speaker_begin(self, volume):
        self.speaker = True
        return True

    def speaker_end(self):
        self.speaker = False

    def haptics_begin(self):
        self.haptics = True
        return True

    def haptics_end(self):
        self.haptics = False

    def write_haptics(self, report):
        if self.refuse:
            return False
        rep = report.finish(len(self.out) + 1)
        self.out.append((Clock.t, rep[S.OPUS_AT] if rep[0] == 0x36 else None, any(rep[13:77])))
        self.t_audio = Clock.t
        return True


class Pad:
    """One pad: the shared report, both streams wired like Ctrl does, and a scheduler that fires the due timers in order."""

    def __init__(self, haptics_first, haptics_ms=20):
        self.dev, self.report, self.first = FakePad(), S.Report3x(), haptics_first
        self.sp = S.SpeakerStream(0, self.dev, self.report, lambda: self.hs.drives(), 30, 100, 80,  # type: ignore[arg-type]
                                  clock=lambda: self.hs.clock_due())
        self.hs = H.HapticsStream(0, self.dev, haptics_ms, self.report, self.sp.clock_due, self.sp.take_clock)  # type: ignore[arg-type]
        self.spk_n = self.hapt_n = 0
        self.next_feed: float | None = Clock.t                # the next speaker frame, None = no sound
        self.next_hapt: float | None = None    # the next haptics chunk (their own grid, at any phase of the speaker's), None = no burst
        self.ticks = 0

    def _timers(self):
        order = (self.hs, self.sp) if self.first else (self.sp, self.hs)
        return [s for s in order if s.active and s._timer is not None]

    def run(self, until):
        """Run to ``until``: a speaker frame every period, a haptics chunk every period during a burst, the timers when due."""
        while True:
            due = min((s._due for s in self._timers()), default=None)
            feeds = [t for t in (self.next_feed, self.next_hapt) if t is not None and t <= until and (due is None or t < due)]
            if feeds:
                Clock.t = min(feeds)
                if Clock.t == self.next_feed:
                    n, self.spk_n = self.spk_n, self.spk_n + 1
                    self.sp.feed(n, SPK_START if n == 0 else 0, memoryview(bytes([n % 200 + 1]) * SPK_BYTES))
                    self.next_feed = Clock.t + P
                else:
                    k, self.hapt_n = self.hapt_n, self.hapt_n + 1
                    self.hs.feed(k * HAPT_FRAMES, 0 if self.hs.active else HAPT_START, memoryview(bytes([9]) * HAPT_CHUNK))
                    self.next_hapt = Clock.t + P
                continue
            if due is None or due > until:
                Clock.t = until
                return
            Clock.t = max(Clock.t, due)
            stream = next(s for s in self._timers() if s._due == due)
            stream._timer = None
            stream._tick()
            self.ticks += 1

    def burst(self, start, until):
        """A haptics burst: chunks from ``start`` (any phase) to ``until``, then HAPT_END."""
        self.next_hapt = start
        self.run(until)
        self.next_hapt = None
        self.stop_haptics()

    def stop_haptics(self):
        k, self.hapt_n = self.hapt_n, self.hapt_n + 1
        self.hs.feed(k * HAPT_FRAMES, HAPT_END, memoryview(bytes(HAPT_CHUNK)))


async def scenarios():
    S.OpusEncoder = FakeEnc
    time.monotonic = lambda: Clock.t         # type: ignore[assignment]
    try:
        for first in (False, True):
            for offset in (0.0, 0.0009, 0.0037, 0.0071, P - 0.0004):
                Clock.t = 1000.0
                pad = Pad(first)
                with contextlib.redirect_stdout(io.StringIO()):
                    pad.run(1000.3)
                    t = 1000.3
                    for _ in range(40):                    # forty haptics bursts while the speaker plays
                        pad.burst(t + offset, t + 0.25)
                        pad.run(t + 0.5)
                        assert not pad.hs.active, "the burst has ended"
                        t += 0.5
                out = pad.dev.out
                gaps = [b[0] - a[0] for a, b in zip(out, out[1:], strict=False)]
                assert min(gaps) > P - 1e-6, f"a report squeezed in (offset {offset}, haptics first {first}): {min(gaps) * 1000:.3f} ms"
                assert max(gaps) < P + 1e-6, f"a report late (offset {offset}, haptics first {first}): {max(gaps) * 1000:.3f} ms"
                st = pad.sp.stats
                assert st.underruns == 0 and st.dropped == 0, (offset, first, st.text())
                assert any(h for _, _, h in out), "the haptics did play"
                played = [v for _, v, _ in out if v]
                steps = {(b - a) % 200 for a, b in zip(played, played[1:], strict=False)}
                assert steps == {1}, f"every report plays the next speaker frame, none skipped: {sorted(steps)}"
                depth = pad.sp.ring.depth
                assert pad.sp.target <= depth <= pad.sp.target + 1, f"no frame is lost: the buffer stays at its depth ({depth})"
        ok("haptics starting at any phase of a playing speaker: no extra report, no lost speaker frame, no underrun (both tie orders)")

        # the other way round: sound starts while haptics play, then the haptics end (also before the speaker's own first tick)
        for first in (False, True):
            for gap in (0.005, 0.2):
                Clock.t = 5000.0
                pad = Pad(first)
                pad.next_feed = None
                with contextlib.redirect_stdout(io.StringIO()):
                    pad.next_hapt = 5000.0
                    pad.run(5000.2)
                    pad.next_feed = 5000.2037                    # the first speaker frame, at another phase than the haptics
                    pad.run(5000.2037 + gap)
                    pad.next_hapt = None
                    pad.stop_haptics()
                    pad.run(5001.0)
                out = pad.dev.out
                gaps = [b[0] - a[0] for a, b in zip(out, out[1:], strict=False)]
                assert P - 1e-6 < min(gaps) and max(gaps) < P + 1e-6, f"one clock all along ({gap}, {first}): {min(gaps)}..{max(gaps)}"
                st = pad.sp.stats
                assert st.underruns == 0 and st.dropped == 0, (gap, first, st.text())
                played = [v for _, v, _ in out if v]
                assert played[0] == 1 and {(b - a) % 200 for a, b in zip(played, played[1:], strict=False)} == {1}, played[:5]
                assert pad.sp.target <= pad.sp.ring.depth + 1, "the buffer was filled before the first frame played"
        ok("sound starting during haptics: the buffer is primed first (no underrun), and the speaker keeps the haptics clock at their end")

        # sound that starts while a haptics stream with a longer buffer still waits for its first tick: the speaker joins that clock
        for first in (False, True):
            Clock.t = 6000.0
            pad = Pad(first, haptics_ms=100)
            pad.next_feed = None
            with contextlib.redirect_stdout(io.StringIO()):
                pad.next_hapt = 6000.0
                pad.run(6000.005)
                pad.next_feed = 6000.0071
                pad.run(6000.6)
                pad.next_hapt = None
                pad.stop_haptics()
                pad.run(6001.2)
            out = pad.dev.out
            gaps = [b[0] - a[0] for a, b in zip(out, out[1:], strict=False)]
            assert P - 1e-6 < min(gaps) and max(gaps) < P + 1e-6, f"one clock ({first}): {min(gaps)}..{max(gaps)}"
            first_hapt = 6000.0 + pad.hs.target / H.HAPT_RATE
            steps = (first_hapt - out[0][0]) / P
            assert steps >= 1 and abs(steps - round(steps)) < 1e-6, "the speaker starts first, on the grid of the haptics' first tick"
            assert out[0][0] <= 6000.0071 + 0.03 + P, "...after its own playout delay, not after the haptics' longer one"
            assert pad.sp.stats.underruns == 0 and pad.sp.stats.dropped == 0, pad.sp.stats.text()
        ok("sound starting before the first tick of a long haptics buffer: the speaker plays on the haptics grid, no double report")

        # the clock the haptics stream joins, and what it does without one
        Clock.t = 1000.0
        pad = Pad(False)
        with contextlib.redirect_stdout(io.StringIO()):
            pad.run(1000.1)
            due = pad.sp.clock_due()
            assert due is not None and due > Clock.t
            pad.hs.feed(0, HAPT_START, memoryview(bytes(HAPT_CHUNK)))
            k = round((pad.hs._due - due) / P)
            assert abs(pad.hs._due - (due + k * P)) < 1e-9 and pad.hs._due >= Clock.t + pad.hs.target / H.HAPT_RATE - 1e-9
            assert not pad.hs.drives(), "not before its first deadline"
            Clock.t = pad.hs._due
            assert pad.hs.drives(), "at that deadline the speaker leaves the report to the haptics tick"
            pad.hs.close()
            pad.sp.close()
        assert pad.sp.clock_due() is None, "an idle speaker has no clock"
        alone = H.HapticsStream(0, FakePad(), 20, S.Report3x(), lambda: None)   # type: ignore[arg-type]
        with contextlib.redirect_stdout(io.StringIO()):
            Clock.t = 2000.0
            alone.feed(0, HAPT_START, memoryview(bytes(HAPT_CHUNK)))
        assert abs(alone._due - (2000.0 + alone.target / H.HAPT_RATE)) < 1e-9, "no speaker: the stream keeps its own playout delay"
        with contextlib.redirect_stdout(io.StringIO()):
            alone.close()
        ok("the first haptics deadline lies on the speaker's clock and after the playout delay; alone it keeps its own")

        # a haptics write that does not go through: the following speaker timer must not re-arm for a moment that has passed
        Clock.t = 3000.0
        pad = Pad(False)
        with contextlib.redirect_stdout(io.StringIO()):
            pad.run(3000.3)
            pad.next_hapt = 3000.3037
            pad.run(3000.4)
            assert pad.hs.writing and pad.sp._following
            pad.dev.refuse = True
            before = pad.ticks
            pad.run(3000.4 + 1.5 * P)
            assert pad.sp._due > Clock.t - 1e-9, "the speaker looks again later, not at once"
            assert pad.ticks - before <= 2 + int(1.5 * P / S.FOLLOW_S) + 2, f"no busy loop: {pad.ticks - before} ticks in 1.5 periods"
            pad.dev.refuse = False
            pad.run(3000.6)
            pad.hs.close()
            pad.sp.close()
        ok("a failed haptics write: the speaker timer re-checks every FOLLOW_S instead of spinning")
    finally:
        time.monotonic = REAL_MONOTONIC
        S.OpusEncoder = __import__("sender_opus").OpusEncoder


asyncio.run(scenarios())
print("AUDIO HANDOVER TX OK")
