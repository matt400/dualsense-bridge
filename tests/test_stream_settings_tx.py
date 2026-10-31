"""Sender: audio stream settings (haptics / speaker) reach the next stream of a connected pad, and a closed Opus encoder is harmless."""
import asyncio, contextlib, io, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_ctrl
import sender_opus
import sender_rumble as R
from proto import HAPT_CHUNK, HAPT_END, HAPT_START, SPK_BYTES, SPK_END, SPK_START
from sender_config import C

ok = lambda m: print("OK  ", m)
TMP = tempfile.mkdtemp()


class Dev:
    def capabilities(self):
        return {}


def pad():
    path = os.path.join(TMP, "hidraw0")
    open(path, "wb").close()
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:00", Dev(), None, None)
    c.rumble = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    return c


async def haptics_settings():
    c = pad()
    chunk = memoryview(bytes([5]) * HAPT_CHUNK)
    with contextlib.redirect_stdout(io.StringIO()):
        C.HAPTICS_BUFFER_MS = 20.0
        c.on_haptics(0, HAPT_START | HAPT_END, chunk)
        first = c.haptics
        assert first.target == 60
        c.on_haptics(1000, HAPT_START | HAPT_END, chunk)
        assert c.haptics is first, "same settings: the stream is kept"
        C.HAPTICS_BUFFER_MS = 100.0
        c.on_haptics(0, HAPT_START, chunk)
        assert first.active and c.haptics is first, "a running stream is not rebuilt in the middle"
        c.haptics.ending = True
        c.haptics._finish("test")
        assert not first.active
        c.on_haptics(5000, HAPT_START | HAPT_END, chunk)
        assert c.haptics is not first and c.haptics.target == 300, c.haptics.target
        c.stop_feedback()
    C.HAPTICS_BUFFER_MS = 20.0
    ok("haptics_buffer_ms changed between streams: the next stream uses it")


async def speaker_settings():
    if not sender_opus.available():
        ok("speaker settings: skipped (no libopus)")
        return
    c = pad()
    pcm = memoryview(bytes(SPK_BYTES))
    with contextlib.redirect_stdout(io.StringIO()):
        C.SPEAKER_VOLUME = 100
        c.on_speaker(0, SPK_START | SPK_END, pcm)
        first = c.speaker
        assert first is not None
        c.on_speaker(10, SPK_START | SPK_END, pcm)
        assert c.speaker is first, "same settings: the stream is kept"
        C.SPEAKER_VOLUME, C.SPEAKER_OPUS_BYTES = 50, 100
        if first.active:
            first._finish("test")
        c.on_speaker(20, SPK_START | SPK_END, pcm)
        assert c.speaker is not first and c.speaker.encoder.size == 100, "new volume / Opus size reach the next stream"
        assert first.encoder._state.value in (None, 0), "the old encoder was freed"
        c.stop_feedback()
    C.SPEAKER_VOLUME, C.SPEAKER_OPUS_BYTES = 100, 80
    ok("speaker settings changed between streams: the next stream uses them")


def closed_encoder():
    if not sender_opus.available():
        ok("closed encoder: skipped (no libopus)")
        return
    e = sender_opus.OpusEncoder(80)
    e.bind(bytearray(200), 0)
    assert e.encode()
    e.close()
    assert e.encode() is False, "a closed encoder reports a failure instead of crashing libopus"
    e.close()
    ok("encode() on a closed Opus encoder returns False")


asyncio.run(haptics_settings())
asyncio.run(speaker_settings())
closed_encoder()
