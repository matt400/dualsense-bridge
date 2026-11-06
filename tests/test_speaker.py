"""Receiver: the pad speaker (rx_speaker) - mono mix, soft clipping, T_SPK packets, the second reader of the audio buffer (rx_haptics.Link),
the link (START / END, silence = no traffic, pacing, queue, stall), gates (CAP_SPEAKER, master, setting), composite for the speaker
alone, the test button, per-pad settings.
    python3 tests/test_speaker.py receiver"""
import math, os, struct, sys, tempfile
from array import array
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, sent)
import rx_audio, rx_haptics as H, rx_master, rx_net, rx_settings, rx_speaker as K
import rx_state as S
from proto import CAP_HAPTICS, CAP_SPEAKER, SPK_BYTES, SPK_END, SPK_FRAME, SPK_START, T_CMD

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- miks ----------
m = K.mix([1000, -1000, 0], [3000, -3000, 0], 1.0)
check(list(m) == [2000, -2000, 0], "mix = mean of left and right")
check(list(K.mix([1000, -1000], [3000, -3000], 0.5)) == [1000, -1000], "strength 50 % = half the mean")
check(list(K.mix([1000, 1000], [1000, 1000], 0.0)) == [0, 0], "strength 0 % = silence")
big = K.mix([32767, -32768, 20000, 24576, 24577], [32767, -32768, 20000, 24576, 24577], 2.0)
check(all(-32768 <= v <= 32767 for v in big) and big[0] > 24576 and big[1] < -24576, "200 % at full scale: soft clipping, no wrap-around")
ramp = K.mix(list(range(0, 32768, 64)), list(range(0, 32768, 64)), 2.0)
check(all(b >= a for a, b in zip(ramp, ramp[1:], strict=False)), "clipping is monotonic (no jumps)")
check(list(K.mix([3000], [1000], 1.0)) == [2000] and list(K.mix([20000], [20000], 1.0)) == [20000], "below the knee the signal is unchanged")
check(K.pcm_bytes(array("h", [1, -2])) == struct.pack("<hh", 1, -2), "packet bytes: int16 little endian")

# ---------- resampling 48 -> 45 kHz ----------
def resample_all(src, chunks=None):
    """All full frames from the source samples, given at once or in pieces (-> list of output samples)."""
    rs = K.Resampler()
    out = []
    pos = 0
    for size in chunks or [len(src)]:
        rs.push(array("h", src[pos:pos + size]))
        pos += size
        for fr in iter(rs.frame, None):
            out += struct.unpack("<480h", fr)
    return out, rs


sine1k = [round(12000 * math.sin(2 * math.pi * 1000 * i / 48000)) for i in range(20000)]
y, rs0 = resample_all(sine1k)
want = [12000 * math.sin(2 * math.pi * 1000 * k * 16 / 15 / 48000) for k in range(len(y))]
check(len(y) == (20000 - 2) // 512 * 480, f"512 source samples = one 480-sample frame ({len(y) // 480} frames)")
check(max(abs(a - b) for a, b in zip(y, want, strict=True)) < 2, "the result lies at source positions k * 16 / 15 (a 1 kHz tone is still 1 kHz after 45 kHz)")
y_uneven, _ = resample_all(sine1k, [1, 700, 3, 511, 5000, 20000])
check(y_uneven == y, "pieces of any length give the same samples as one chunk")
dc, _ = resample_all([2000] * 4000)
check(set(dc[1:]) == {2000}, "a constant stays constant (the weights sum to 1)")
fs, _ = resample_all([32767, -32768] * 2500)
check(-32768 <= min(fs) and max(fs) <= 32767, "full scale: the cubic is clipped, not wrapped")
rsz = K.Resampler()
rsz.push_zeros(2000)
check(all(f == K.ZERO_FRAME for f in iter(rsz.frame, None)), "silence at the input = zero frames without computing")
check(len(rs0.buf) < K.SRC_NEED, "after the frames less than needed for the next one stays in the buffer")

# ---------- packet ----------
got = []
rx_net.send_peer = lambda pkt: got.append(pkt)
frame = struct.pack("<480h", *range(480))
rx_net.send_speaker(1, 2 ** 32 + 7, SPK_START, frame)
pkt = got[0]
check(len(pkt) == 4 + 965 and pkt[0] & 0x0F == T_CMD and pkt[1] == 1, "spk packet: command type, pad 1, 965 B body")
check(pkt[4:] == struct.pack("!IB", 7, SPK_START) + frame, "body: frame number modulo 2**32, flags, 480 samples")

# ---------- link ----------
A = "aa:aa:aa:aa:aa:09"
S.pad_id[0] = A
S.alive[0] = True
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(True, 100)})))
ROLES = ["speakerLeft", "speakerRight", "hapticLeft", "hapticRight"]
sent_pk = []
now = [100.0]
lk = H.Link(0, clock=lambda: now[0])
check(lk.configure(4, 48000, 16, ROLES) is None and lk.speaker_channels == (0, 1), "speaker channels from the roles: 0 and 1")
check(lk.configure(6, 48000, 16, ["hapticLeft", "hapticRight", "speakerLeft", "speakerRight", "x", "y"]) is None
      and lk.speaker_channels == (2, 3), "roles in another order and 6 channels: speaker channels 2 and 3")
lk.configure(4, 48000, 16, ROLES)
sp = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((now[0], t, n, f, c)), clock=lambda: now[0])
check(sp.attach(lk) is None and lk.on_write == sp.poke, "the speaker hooked to the link buffer")
lk44 = H.Link(0)
check(K.SpeakerLink(0).attach(lk44) is None, "the default link format (48 kHz) is accepted")
lk44.rate = 44100
check(K.SpeakerLink(0).attach(lk44) is not None, "a rate other than 48 kHz: the speaker not used")


def pcm(left, right, n, ch=4, other=9999):
    """n frames: channels 0 and 1 carry left / right (functions of k), the others - a constant that must not leak."""
    a = array("h", [other]) * (n * ch)
    for k in range(n):
        a[k * ch], a[k * ch + 1] = left(k), right(k)
    return a.tobytes()


def run_link(sec, make, step=0.0025, link=lk, speaker=sp):
    """Feeds audio in 10 ms windows and steps the speaker on a fake clock."""
    end = now[0] + sec
    nxt = now[0]
    k = 0
    while now[0] < end:
        if now[0] >= nxt:
            w = make(k)
            link.write(memoryview(w), len(w))
            nxt += 0.01
            k += 480
        speaker.step(now[0])
        now[0] += step


dc = lambda k: pcm(lambda i: 1000, lambda i: 3000, 480)
run_link(0.05, lambda k: pcm(lambda i: 0, lambda i: 0, 480))
check(not sent_pk, "silence before sound = zero packets")
run_link(0.5, dc)
n_tone = len(sent_pk)
check(n_tone >= 45, f"sound = a packet stream ({n_tone})")
check(sent_pk[0][3] == SPK_START and all(p[3] == 0 for p in sent_pk[1:n_tone]), "START only on the first")
nums = [p[2] for p in sent_pk]
check(all(b - a == 1 for a, b in zip(nums, nums[1:], strict=False)), "continuous numbering, +1 per frame, no repeats")
gaps = [b[0] - a[0] for a, b in zip(sent_pk[10:], sent_pk[11:n_tone], strict=False)]
check(gaps and 0.0102 < sum(gaps) / len(gaps) < 0.0112 and max(gaps) < 0.03,
      f"pad clock pacing: a frame every 10.667 ms report period (mean {1000 * sum(gaps) / len(gaps):.2f} ms)")
check(all(p[1] == 0 and len(p[4]) == SPK_BYTES for p in sent_pk), "pad fizyczny 0, ramka 960 B")
body = [p[4] for p in sent_pk[:n_tone]]
check(all(struct.unpack("<480h", b) == (2000,) * 480 for b in body[2:]), "every sample = mean of channels 0 and 1 (haptics channels do not leak)")
check(all(-400 <= v <= 2400 for b in body[:2] for v in struct.unpack("<480h", b)), "the first frames (a jump from silence): the cubic rings by at most a dozen or so %")
run_link(0.6, lambda k: pcm(lambda i: 0, lambda i: 0, 480))
end_pk = [p for p in sent_pk if p[3] & SPK_END]
check(len(end_pk) == 1 and sent_pk[-1] is end_pk[0], "after silence exactly one SPK_END, at the end")
check(end_pk[0][4] == K.ZERO_FRAME, "SPK_END carries silence")
quiet_from = len(sent_pk)
run_link(1.0, lambda k: pcm(lambda i: 0, lambda i: 0, 480))
check(len(sent_pk) == quiet_from, "no traffic in silence")
check((end_pk[0][0] - sent_pk[n_tone - 1][0]) < 0.4, "END after a short silence (under 400 ms)")
before = len(sent_pk)
run_link(0.1, dc)
check(sent_pk[before][3] == SPK_START and sent_pk[before][2] == end_pk[0][2] + 1, "a new stream: START again, the numbering continues")
n0 = len(sent_pk)
for _ in range(200):                                   # the game stopped rendering during a stream
    now[0] += 0.005
    sp.step(now[0])
check(sent_pk[-1][3] & SPK_END and len(sent_pk) > n0, "no callbacks during a stream ends it (END)")

# strength and partial frames: audio in 7 ms windows (not a multiple of 480) assembles into frames without lost samples
sent_pk.clear()
sp2 = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((n, f, c)), clock=lambda: now[0])
lk5 = H.Link(0, clock=lambda: now[0])
lk5.configure(4, 48000, 16, ROLES)
sp2.attach(lk5)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(True, 50)})))
total = 0
for _ in range(60):
    w = pcm(lambda i: 4000 + total + i, lambda i: 4000 + total + i, 336)
    total += 336
    lk5.write(memoryview(w), len(w))
    now[0] += 0.007
    sp2.step(now[0])
for _ in range(100):
    now[0] += 0.005
    sp2.step(now[0])
samples = [v for _n, f, c in sent_pk if not f & SPK_END for v in struct.unpack("<480h", c)]
# a rising ramp: after the 16 : 15 resampling sample k lies at source position k * 16 / 15; a cubic is exact on a straight line
ramp_err = max(abs(v - (4000 + k * 16 / 15) / 2) for k, v in enumerate(samples))
check(len(samples) >= 18000 and ramp_err <= 1.0,
      f"strength 50 %, windows of 336 frames: sample continuity between frames after resampling (no gaps or repeats, error {ramp_err:.2f})")
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(True, 100)})))

# queue: a big chunk at once makes no burst, the excess is dropped as a gap
sent_pk.clear()
lk2 = H.Link(0, clock=lambda: now[0])
lk2.configure(4, 48000, 16, ROLES)
sp3 = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((now[0], n, f)), clock=lambda: now[0])
sp3.attach(lk2)
bigaudio = pcm(lambda i: 5000, lambda i: 5000, 48000 // 2)
lk2.write(memoryview(bigaudio), len(bigaudio))
for _ in range(400):
    now[0] += 0.0025
    sp3.step(now[0])
check(sp3.dropped > 0, f"too long a chunk: the oldest frames dropped ({sp3.dropped}), the numbering has a gap")
check(all(b[0] - a[0] > 0.0085 for a, b in zip(sent_pk, sent_pk[1:], strict=False)), "no packet burst")
check(sent_pk and sent_pk[0][2] == SPK_START, "START on the first one sent")

# buffer overflow (the writer overtook the reader): the reader goes back to a frame boundary, the channels do not swap
lk6 = H.Link(0, clock=lambda: now[0])
lk6.configure(6, 48000, 16, ["hapticLeft", "hapticRight", "speakerLeft", "speakerRight", "x", "y"])
sent_pk.clear()
sp6 = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((n, f, c)), clock=lambda: now[0])
sp6.attach(lk6)
a6 = array("h", [0]) * (6 * 4000)
for k in range(4000):
    a6[k * 6 + 2], a6[k * 6 + 3] = 6000, 6000
blob = a6.tobytes() * 12
for off in range(0, len(blob) - 60000, 60000):
    lk6.write(memoryview(blob)[off:off + 60000], 60000)
sp6.step(now[0])
check(sp6.overruns == 1 and sp6._r % 12 == 0, "overflow: the speaker reader goes back to a frame boundary (6 channels)")
for _ in range(40):
    now[0] += 0.0107
    sp6.step(now[0])
rows = [c for _n, _f, c in sent_pk if c != K.ZERO_FRAME]
check(len(rows) > 2 and set(struct.unpack("<480h", rows[len(rows) // 2])) == {6000}, "after the overflow the speaker channels are still channels 2 and 3")

# ---------- bramki ----------
def streams(setup=lambda: None, sec=0.2):
    """Does the speaker send anything with the given gate settings?"""
    out = []
    link = H.Link(0, clock=lambda: now[0])
    link.configure(4, 48000, 16, ROLES)
    s = K.SpeakerLink(0, send=lambda t, n, f, c: out.append(n), clock=lambda: now[0])
    s.attach(link)
    setup()
    run_link(sec, dc, link=link, speaker=s)
    return bool(out)

check(streams(), "baseline: v14 + CAP_SPEAKER + enabled = a stream")
S.sender_info[0] = (13, 3 | CAP_HAPTICS, 0)
check(not streams() and not K.sender_speaker(0), "old sender (v13): no stream")
S.sender_info[0] = (14, 3, 0)
check(not streams() and not K.sender_speaker(0), "v14 without the CAP_SPEAKER bit: no stream")
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(False, 100)})))
check(not streams(), "off in the settings (default): no stream")
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 100)})))
check(streams(), "the default setting * covers a pad without its own")
S.pad_id[1] = "bb:bb:bb:bb:bb:09"
S.alive[1] = True
S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
mst = replace(rx_settings.cur.master, pad=A, only_master=("speaker",), when_absent="nobody")
rx_settings.apply(replace(rx_settings.cur, master=mst))
check(streams(), "the master gets the speaker")
S.pad_id[0], S.pad_id[1] = S.pad_id[1], S.pad_id[0]
check(not streams(), "a pad that is not the master does not get the speaker")
S.pad_id[0], S.pad_id[1] = S.pad_id[1], S.pad_id[0]
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad="")))

# ---------- composite for the speaker alone ----------
H._composite_off_until = 0.0
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={}),
                          speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 100)})))
check(H.composite_wanted(0), "speaker alone enabled (no haptics) = composite pad")
S.sender_info[0] = (13, 3 | CAP_HAPTICS, 0)
check(not H.composite_wanted(0), "speaker enabled, but an old sender: a plain pad")
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={})))
check(not H.composite_wanted(0), "both off: a plain pad")
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={"*": rx_settings.PadHaptics(True, 100)})))
S.sender_info[0] = (14, 3 | CAP_SPEAKER | CAP_HAPTICS, 0)
check(H.composite_wanted(0), "haptics alone enabled works as before")
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={}),
                          speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 100)})))
mst = replace(rx_settings.cur.master, pad=A, only_master=("speaker",), when_absent="nobody")
rx_settings.apply(replace(rx_settings.cur, master=mst))
S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
check(H.composite_wanted(0) and not H.composite_wanted(1), "only the master gets the composite for the speaker")
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad="")))


class Pad:
    def attach_audio_controls(self, sink): return True
    def attach_audio(self, sink):
        return 4, 48000, 16, ROLES
    def close(self):
        pass
class Backend:
    def create(self, idx, on_fb, composite=False):
        return Pad()
rx_audio.default_output_ids = dict
K.init()
H.init()
pad = H.create_composite(Backend(), 0, None)
check(pad is not None and K.speaker(0).link is H.link(0) and H.link(0).on_write == K.speaker(0).poke,
      "create_composite hooks the speaker to the link audio buffer")
H.detach(0)
check(K.speaker(0).link is None and H.link(0).on_write is None, "detach unhooks the speaker")
K.speaker(0)._sent_started = True
sent_pk.clear()
K.speaker(0)._send = lambda t, n, f, c: sent_pk.append((n, f, c))
K.detach(0)
check(len(sent_pk) == 1 and sent_pk[0][1] & SPK_END, "detach during a stream sends SPK_END at once")

# ---------- test button: the same gates, a reason instead of "sent" ----------
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad=A)))
S.sender_info[0] = S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
K.speakers[:] = []
why = K.test(1)
check(why is not None and "master" in why and not K.speaker(1)._inject, f"speaker test: a non-master pad gets a reason, nothing waits in the queue ({why!r})")
check(K.test(0) is None and K.speaker(0)._inject, "speaker test: the master gets the tone")
K.speaker(0)._inject.clear()
S.sender_info[1] = (13, 3, 0)
why = K.test(1)
check(why is not None and "starszej" in why, "speaker test: old sender = a reason")
check(K.test(5) is not None and K.test(-1) is not None, "speaker test: non-existent pad = a reason")
S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
# a tone queued before it was forbidden: the queue is cleared
sent_pk.clear()
sp7 = K.SpeakerLink(1, send=lambda t, n, f, c: sent_pk.append(n), clock=lambda: now[0])
sp7._inject.extend([array("h", [5]) * K.SRC_FRAME] * 8)
for _ in range(100):
    now[0] += 0.005
    sp7.step(now[0])
check(not sent_pk and not sp7._inject, "a pad without permission: the queued tone is dropped")
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad="")))
for _ in range(100):
    now[0] += 0.005
    sp7.step(now[0])
check(not sent_pk, "after permission is granted the old tone does not play")
# the jingle goes the same way: START, frames with the melody, silence, END
sent_pk.clear()
sp8 = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((n, f, c)), clock=lambda: now[0])
sp8.test(1.0)
sp8._thread = types.SimpleNamespace(is_alive=lambda: True)
for _ in range(400):
    now[0] += 0.005
    sp8.step(now[0])
tone_frames = [c for _n, _f, c in sent_pk if c != K.ZERO_FRAME]
check(sent_pk[0][1] == SPK_START and sent_pk[-1][1] & SPK_END and len(tone_frames) == K.TEST_FRAMES,
      f"test jingle: START, {len(tone_frames)} sound frames, END")
wave = [v for c in tone_frames for v in struct.unpack("<480h", c)]
check(len(wave) == K.TEST_FRAMES * SPK_FRAME and max(abs(v) for v in wave) <= 32767 * K.TEST_LEVEL * 1.02, "test jingle: no overdrive")
at = 0
PLAY_RATE = K.SPK_RATE * K.BLOCK_OUT / K.BLOCK_IN                # the pad plays a 480-sample frame over 10.667 ms: 45 kHz
for hz, secs in K.JINGLE:                                        # the middle half of each note has its pitch (zero crossings)
    n = round(round(secs * K.SPK_RATE) * K.BLOCK_OUT / K.BLOCK_IN)
    mid = wave[at + n // 4: at + 3 * n // 4]
    cross = sum(1 for a, b in zip(mid, mid[1:], strict=False) if a < 0 <= b)
    got = cross / (len(mid) / PLAY_RATE)
    check(abs(got - hz) < hz * 0.04, f"jingiel testowy: nuta {hz:.0f} Hz ({got:.0f} Hz)")
    at += n
check(wave[0] == 0 and all(abs(a - b) < 6000 for a, b in zip(wave, wave[1:], strict=False)), "test jingle: no clicks at the note edges")
tune = K.jingle()
check(tune is K.jingle() and len(tune) == K.TEST_FRAMES * K.SRC_FRAME, "the jingle computed once, full source frames")

# ---------- settings ----------
path = os.path.join(tempfile.mkdtemp(), "settings.json")
s = replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 80), A: rx_settings.PadSpeaker(False, 150)}))
rx_settings.save(s, path)
back = rx_settings.load(path)
check(back.speaker == s.speaker and back.speaker.get(A).gain == 150 and back.speaker.get("zz").gain == 80, "speaker settings: write and read, default * for an unknown pad")
import json
raw = json.load(open(path))
raw["speaker"]["pads"]["bad"] = "x"
raw["speaker"]["pads"]["loud"] = {"enabled": True, "gain": 100000}
json.dump(raw, open(path, "w"))
back = rx_settings.load(path)
check("bad" not in back.speaker.pads and "loud" not in back.speaker.pads and back.speaker.get(A).gain == 150, "bad entries skipped, the rest stays")
check("speaker" in rx_settings.MASTER_FEATURES, "the speaker is a master-restricted feature")
old = rx_settings.load(os.path.join(tempfile.mkdtemp(), "none.json"))
check(old.speaker == rx_settings.SpeakerConfig() and not old.speaker.get(A).enabled, "no section in the file: speaker off")

print("FAILS:" if fails else "SPEAKER OK", fails or "")
sys.exit(1 if fails else 0)
