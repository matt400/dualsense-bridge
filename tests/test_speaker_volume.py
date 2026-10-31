"""Receiver: the Windows volume / mute of the pad's speaker endpoint (HIDMaestro ControlChanged -> rx_speaker.host_gain).
Windows does not scale the PCM of an endpoint with a hardware volume, it writes a UAC control: the receiver must apply it itself.
    python3 tests/test_speaker_volume.py receiver"""
import os, struct, sys
from array import array
from dataclasses import replace
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, sent)
import rx_haptics as H, rx_hm, rx_settings, rx_speaker as K
import rx_state as S
from proto import CAP_SPEAKER

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

# ---------- the factor ----------
sp = K.SpeakerLink(0)
check(sp.host_gain == 1.0, "before any write the level is full (the endpoint's initial -100 dB must not silence the speaker)")
sp.set_host_volume("speaker", False, False, 0.0)
check(sp.host_gain == 1.0, "0 dB = full level")
sp.set_host_volume("speaker", False, False, -50.0)
check(abs(sp.host_gain - 0.1) < 1e-9, "the slider's middle (-50 dB of the -100 .. 0 range) = -20 dB, not -50 dB: a curve over the audible span")
sp.set_host_volume("speaker", False, False, -92.0)
check(0.012 < sp.host_gain < 0.017, f"8 % of the slider (-92 dB) is audible (x{sp.host_gain:.4f}), it used to be silence")
sp.set_host_volume("speaker", True, True, 0.0)
check(sp.host_gain == 0.0, "mute = silence")
sp.set_host_volume("speaker", False, False, -6.0)
check(sp.host_gain == 0.0, "a volume write while muted keeps it muted (Windows keeps both controls apart)")
sp.set_host_volume("speaker", True, False, 0.0)
want = K.host_factor(-6.0)
check(abs(want - 10 ** (-2.4 / 20)) < 1e-9 and sp.host_gain == want, "unmute brings back the last volume (-6 dB = -2.4 dB on the curve)")
sp.set_host_volume("microphone", True, True, 0.0)
sp.set_host_volume("microphone", False, False, -50.0)
check(sp.host_gain == want, "the microphone unit does not touch the speaker")
sp.set_host_volume("speaker", False, False, -100.0)
check(sp.host_gain == 0.0, "-100 dB (the slider at 0 %) = silence")
sp.set_host_volume("speaker", False, False, -99.0)
check(0.0 < sp.host_gain < 0.012, f"1 % of the slider is the quietest sound, not silence (x{sp.host_gain:.4f})")
curve = [K.host_factor(-100.0 + k) for k in range(101)]
check(curve[0] == 0.0 and curve[100] == 1.0 and all(b > a for a, b in zip(curve[1:], curve[2:], strict=False)),
      "the curve rises strictly from the quietest step to 0 dB (every slider step is a change)")
check(K.host_factor(5.0) == 1.0, "a value above 0 dB cannot amplify")
sp.detach()
check(sp.host_gain == 1.0, "a new pad starts at full level again")

# ---------- the sound ----------
A = "aa:aa:aa:aa:aa:0b"
S.pad_id[0] = A
S.alive[0] = True
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(True, 100)})))
ROLES = ["speakerLeft", "speakerRight", "hapticLeft", "hapticRight"]
now = [100.0]
sent_pk = []
lk = H.Link(0, clock=lambda: now[0])
lk.configure(4, 48000, 16, ROLES)
sp = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((n, f, c)), clock=lambda: now[0])
sp.attach(lk)


def window() -> bytes:
    """10 ms of constant sound: left 1000, right 3000 (mono 2000), the haptics channels carry a value that must not leak."""
    a = array("h", [9999]) * (480 * 4)
    for k in range(480):
        a[k * 4], a[k * 4 + 1] = 1000, 3000
    return a.tobytes()


def play(sec: float) -> None:
    """Feed windows of that sound and step the speaker on the fake clock."""
    end, nxt = now[0] + sec, now[0]
    while now[0] < end:
        if now[0] >= nxt:
            w = window()
            lk.write(memoryview(w), len(w))
            nxt += 0.01
        sp.step(now[0])
        now[0] += 0.0025


def level(start: int) -> set[int]:
    """The sample values of the frames sent from index `start` on, without the first two (the resampler's edge)."""
    return {v for _n, _f, c in sent_pk[start + 2:] for v in struct.unpack("<480h", c)}


play(0.3)
check(level(0) == {2000}, "full level: the mono average, as before")
mark = len(sent_pk)
sp.set_host_volume("speaker", False, False, -50.0)
play(0.3)
check(all(abs(v - 200) <= 1 for v in level(mark + 8)) and level(mark + 8),
      f"the slider's middle: a tenth of the level, the next frames already ({sorted(level(mark + 8))[:3]})")
sp.set_host_volume("speaker", True, True, 0.0)
play(0.6)
ends = [p for p in sent_pk if p[1] & K.SPK_END]
n_end = len(sent_pk)
check(len(ends) == 1 and sent_pk[-1] is ends[0], "mute: the stream ends like on digital silence (SPK_END, nothing after it)")
play(0.5)
check(len(sent_pk) == n_end, "muted: no packets at all (a running stream costs the pad's input reports)")
sp.set_host_volume("speaker", True, False, 0.0)
play(0.3)
check(len(sent_pk) > n_end and sent_pk[n_end][1] & K.SPK_START, "unmute: a new stream starts (START)")

# ---------- rx_hm.Pad: the subscription ----------
class Event:
    """What pythonnet gives for a .NET event: `+=` / `-=` with a callable."""
    def __init__(self) -> None:
        self.handlers: list = []
    def __iadd__(self, h):
        self.handlers.append(h)
        return self
    def __isub__(self, h):
        self.handlers.remove(h)
        return self


def bare_pad(audio) -> rx_hm.Pad:
    """A Pad without the HIDMaestro backend (only the audio half is under test)."""
    pad = rx_hm.Pad.__new__(rx_hm.Pad)
    pad.ctrl, pad._ctl_handler, pad._audio_handler = SimpleNamespace(UsbAudio=audio), None, None
    return pad


audio = SimpleNamespace(ControlChanged=Event())
pad = bare_pad(audio)
calls: list = []
check(pad.attach_audio_controls(lambda *a: calls.append(a)) is True and len(audio.ControlChanged.handlers) == 1, "subscribed to ControlChanged")
audio.ControlChanged.handlers[0](None, SimpleNamespace(Function="speaker", IsMute=False, MuteValue=False, VolumeDb=-12.5))
audio.ControlChanged.handlers[0](None, SimpleNamespace(Function="speaker", IsMute=True, MuteValue=True, VolumeDb=0.0))
check(calls == [("speaker", False, False, -12.5), ("speaker", True, True, 0.0)], "the event arrives as (function, is_mute, muted, dB)")
pad.attach_audio_controls(lambda *a: None)
check(len(audio.ControlChanged.handlers) == 1, "subscribing again replaces the old handler (no double delivery)")
audio.ControlChanged.handlers[0](None, SimpleNamespace())                    # an event without the properties
check(True, "a malformed event does not raise into HIDMaestro")
pad._detach_frames()
check(len(audio.ControlChanged.handlers) == 1, "attach_audio (frames only) leaves the volume subscription alone")
pad.detach_audio()
check(not audio.ControlChanged.handlers and pad._ctl_handler is None, "detach_audio unsubscribes")
check(bare_pad(SimpleNamespace()).attach_audio_controls(lambda *a: None) is False, "an old HIDMaestro without the event: False, no exception")
check(bare_pad(None).attach_audio_controls(lambda *a: None) is False, "a pad without USB audio: False")

# a sink that raises must not escape the .NET event thread
pad = bare_pad(SimpleNamespace(ControlChanged=Event()))
def boom(*a): raise RuntimeError("x")
pad.attach_audio_controls(boom)
pad.ctrl.UsbAudio.ControlChanged.handlers[0](None, SimpleNamespace(Function="speaker", IsMute=False, MuteValue=False, VolumeDb=0.0))
check(True, "a failing sink is swallowed")

# ---------- create_composite subscribes first ----------
order: list = []
class FakePad:
    def attach_audio_controls(self, sink):
        order.append(("controls", sink.__self__ is K.speaker(0)))
        return True
    def attach_audio(self, sink):
        order.append(("audio", None))
        return 4, 48000, 16, ROLES
    def close(self): pass
class FakeBackend:
    def create(self, idx, on_fb, composite=False): return FakePad()
H._composite_off_until = 0.0
import rx_audio
rx_audio.default_output_ids = dict
K.init()
H.init()
check(H.create_composite(FakeBackend(), 0, None) is not None and order == [("controls", True), ("audio", None)],
      "create_composite: the volume subscription comes before the audio one, bound to the slot's speaker")

sys.exit(1 if fails else 0)
