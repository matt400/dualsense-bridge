"""Sender: regressions of the sender review (2026-10-31) outside the audio clock.

* WHEN_STREAM settings (haptics_buffer_ms, speaker_*) changed from the receiver window count from the next audio stream, as the window
  says - not only after the pad reconnects (Ctrl kept the stream object it made for the first stream of the connection).
* OpusEncoder.encode() on a closed (or never bound) encoder returns False instead of handing libopus a null pointer (a segfault).
* The motion average sends the same values as before without allocating per packet, and reads the deadbands live.
"""
import asyncio, contextlib, io, sys, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_ctrl
import sender_opus as O
import sender_speaker as S
import sender_streams as streams
from proto import CAP_HAPTICS, CAP_SPEAKER, HAPT_CHUNK, HAPT_END, HAPT_START, SPK_BYTES, SPK_END, SPK_START
from sender_config import C

ok = lambda m: print("OK  ", m)


class FakeEnc:
    instances: list = []

    def __init__(self, size, mode="hybrid", complexity=-1):
        self.size, self.mode, self.complexity, self.closed = size, mode, complexity, False
        self._pcm = bytearray(SPK_BYTES)
        self.pcm = memoryview(self._pcm)
        FakeEnc.instances.append(self)

    def bind(self, out, at):
        pass

    def encode(self):
        return True

    def close(self):
        self.closed = True


class FakeRumble:
    bits = CAP_HAPTICS | CAP_SPEAKER

    def __init__(self):
        self.haptics = self.speaker = False
        self.t_audio = 0.0

    def haptics_begin(self):
        self.haptics = True
        return True

    def haptics_end(self):
        self.haptics = False

    def speaker_begin(self, volume):
        self.speaker = True
        return True

    def speaker_end(self):
        self.speaker = False

    def write_haptics(self, report):
        report.finish(0)
        return True

    def close(self):
        pass


class Dev:
    def capabilities(self):
        return {}


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


async def settings_scenarios():
    S.OpusEncoder = FakeEnc
    saved = {k: getattr(C, k) for k in ("HAPTICS_BUFFER_MS", "SPEAKER_BUFFER_MS", "SPEAKER_VOLUME", "SPEAKER_OPUS_BYTES",
                                        "SPEAKER_OPUS_MODE", "SPEAKER_OPUS_COMPLEXITY")}
    try:
        c = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:01", Dev(), None, None)
        c.rumble = FakeRumble()                                          # type: ignore[assignment]
        hap = memoryview(bytes(HAPT_CHUNK))
        quiet(c.on_haptics, 0, HAPT_START, hap)
        first = c.haptics
        assert first is not None and first.active and first.buffer_ms == C.HAPTICS_BUFFER_MS
        C.HAPTICS_BUFFER_MS = 100.0                                       # changed in the window while the stream plays
        quiet(c.on_haptics, 32, 0, hap)
        assert c.haptics is first and first.target == round(sender_config.DEFAULTS["haptics_buffer_ms"] * 3), "not in the middle of a stream"
        quiet(first._finish, "test")
        quiet(c.on_haptics, 0, HAPT_START, hap)
        assert c.haptics is not first and c.haptics.target == 300 and c.haptics.active, "the next stream has the new buffer"
        same = c.haptics
        quiet(same._finish, "test")
        quiet(c.on_haptics, 0, HAPT_START, hap)
        assert c.haptics is same, "an unchanged setting keeps the stream object"
        quiet(c.on_haptics, 32, HAPT_END, hap)
        ok("haptics_buffer_ms from the window: the next haptics stream uses it, the running one is left alone")

        spk = memoryview(bytes(SPK_BYTES))
        quiet(c.on_speaker, 0, SPK_START, spk)
        first_sp = c.speaker
        assert first_sp is not None and first_sp.active and first_sp.settings[1] == C.SPEAKER_VOLUME
        for name, value in (("SPEAKER_VOLUME", 33), ("SPEAKER_BUFFER_MS", 80.0), ("SPEAKER_OPUS_BYTES", 120),
                            ("SPEAKER_OPUS_MODE", "celt"), ("SPEAKER_OPUS_COMPLEXITY", 1)):
            setattr(C, name, value)
            quiet(c.on_speaker, 1, 0, spk)
            assert c.speaker is first_sp, f"{name}: not in the middle of a stream"
        quiet(first_sp._finish, "test")
        quiet(c.on_speaker, 2, SPK_START, spk)
        sp = c.speaker
        assert sp is not first_sp and first_sp.encoder.closed, "the old stream (and its encoder) is closed"
        assert (sp.volume, sp.target, sp.encoder.size, sp.encoder.mode, sp.encoder.complexity) == (33, 8, 120, "celt", 1), sp.settings
        assert c.rumble.speaker and sp.active
        quiet(sp._finish, "test")
        quiet(c.on_speaker, 3, SPK_START, spk)
        assert c.speaker is sp, "unchanged settings keep the stream"
        quiet(c.on_speaker, 4, SPK_END, spk)
        quiet(c.stop_feedback)
        ok("speaker_* settings from the window: the next speaker stream uses them, the running one is left alone")
    finally:
        for k, v in saved.items():
            setattr(C, k, v)
        S.OpusEncoder = O.OpusEncoder


asyncio.run(settings_scenarios())


# ---------- the encoder after close ----------
if O.available():
    enc = O.OpusEncoder(80)
    assert not enc.encode(), "not bound yet: no packet, and no write through a null pointer"
    buf = bytearray(400)
    enc.bind(buf, 10)
    assert enc.encode()
    enc.close()
    assert not enc.encode(), "closed: False, not a crash"
    ok("OpusEncoder.encode before bind / after close returns False")
else:
    print("SKIP libopus is not installed: the encoder checks")


# ---------- the motion average ----------
def reference(sums, n, bias):
    mean = [total / n for total in sums]
    return [round(mean[k]) for k in range(3)] + [round(mean[3 + k] - bias[k]) for k in range(3)]


sent = []
c = types.SimpleNamespace(motion=[0] * 6, pending=False, gyro_bias=[10.4, -2.6, 0.5], mark_event=lambda e: None,
                          request_send=lambda: sent.append(list(c.motion)))
st = streams._MotionStream.__new__(streams._MotionStream)
st.c, st.sums, st.count, st._next = c, [0] * 6, 0, [0] * 6
lists = set()
for k, (sums, n) in enumerate(((([500, -500, 1000, 3000, -3000, 600]), 2), (([501, -500, 1000, 3000, -3000, 600]), 2),
                              (([4000, 0, 0, 0, 0, 0]), 1), (([4000, 0, 0, 900, 0, 0]), 1))):
    st.sums[:] = sums
    st.count = n
    before = len(sent)
    st._send_average(None)
    assert st.sums == [0] * 6 and st.count == 0
    if len(sent) > before:
        assert sent[-1] == reference(sums, n, c.gyro_bias), (k, sent[-1])
    lists.add(id(c.motion))
assert len(sent) == 3, "the second packet stays inside the deadband"
assert len(lists) == 2 and id(st._next) in lists, "two lists take turns: nothing is allocated per packet"
C.DB_ACC, saved_db = 0, C.DB_ACC
st.sums[:] = [4001, 0, 0, 900, 0, 0]
st.count = 1
st._send_average(None)
assert len(sent) == 4, "a deadband changed in the window counts at once"
C.DB_ACC = saved_db
ok("motion average: the same values, two lists taking turns instead of new ones, deadbands read live")
print("STREAM SETTINGS TX OK")
