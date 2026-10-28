"""Receiver: głośnik pada (rx_speaker) - miks do mono, miękkie obcinanie, pakiety T_SPK, drugi odbiorca bufora audio (rx_haptics.Link),
łącze (START / END, cisza = zero ruchu, tempo, kolejka, stall), bramki (CAP_SPEAKER, master, ustawienie), composite dla samego
głośnika, przycisk testu, ustawienia per pad.
    python3 tests/test_speaker.py receiver"""
import math, os, struct, sys, tempfile
from array import array
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_audio, rx_haptics as H, rx_master, rx_net, rx_settings, rx_speaker as K
import rx_state as S
from proto import CAP_HAPTICS, CAP_SPEAKER, SPK_BYTES, SPK_END, SPK_FRAME, SPK_START, T_CMD

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


# ---------- miks ----------
m = K.mix([1000, -1000, 0], [3000, -3000, 0], 1.0)
check(list(m) == [2000, -2000, 0], "miks = średnia lewego i prawego")
check(list(K.mix([1000, -1000], [3000, -3000], 0.5)) == [1000, -1000], "siła 50 % = połowa średniej")
check(list(K.mix([1000, 1000], [1000, 1000], 0.0)) == [0, 0], "siła 0 % = cisza")
big = K.mix([32767, -32768, 20000, 24576, 24577], [32767, -32768, 20000, 24576, 24577], 2.0)
check(all(-32768 <= v <= 32767 for v in big) and big[0] > 24576 and big[1] < -24576, "200 % na pełnej skali: miękkie obcięcie, bez zawinięcia")
ramp = K.mix(list(range(0, 32768, 64)), list(range(0, 32768, 64)), 2.0)
check(all(b >= a for a, b in zip(ramp, ramp[1:], strict=False)), "obcinanie jest monotoniczne (żadnych skoków)")
check(list(K.mix([3000], [1000], 1.0)) == [2000] and list(K.mix([20000], [20000], 1.0)) == [20000], "poniżej kolana sygnał bez zmian")
check(K.pcm_bytes(array("h", [1, -2])) == struct.pack("<hh", 1, -2), "bajty pakietu: int16 little endian")

# ---------- pakiet ----------
got = []
rx_net.send_peer = lambda pkt: got.append(pkt)
frame = struct.pack("<480h", *range(480))
rx_net.send_speaker(1, 2 ** 32 + 7, SPK_START, frame)
pkt = got[0]
check(len(pkt) == 4 + 965 and pkt[0] & 0x0F == T_CMD and pkt[1] == 1, "pakiet spk: typ komendy, pad 1, 965 B ciała")
check(pkt[4:] == struct.pack("!IB", 7, SPK_START) + frame, "ciało: numer ramki modulo 2**32, flagi, 480 próbek")

# ---------- łącze ----------
A = "aa:aa:aa:aa:aa:09"
S.pad_id[0] = A
S.alive[0] = True
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(True, 100)})))
ROLES = ["speakerLeft", "speakerRight", "hapticLeft", "hapticRight"]
sent_pk = []
now = [100.0]
lk = H.Link(0, clock=lambda: now[0])
check(lk.configure(4, 48000, 16, ROLES) is None and lk.speaker_channels == (0, 1), "kanały głośnika z ról: 0 i 1")
check(lk.configure(6, 48000, 16, ["hapticLeft", "hapticRight", "speakerLeft", "speakerRight", "x", "y"]) is None
      and lk.speaker_channels == (2, 3), "role w innej kolejności i 6 kanałów: kanały głośnika 2 i 3")
lk.configure(4, 48000, 16, ROLES)
sp = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((now[0], t, n, f, c)), clock=lambda: now[0])
check(sp.attach(lk) is None and lk.on_write == sp.poke, "głośnik podpięty pod bufor łącza")
lk44 = H.Link(0)
check(K.SpeakerLink(0).attach(lk44) is None, "domyślny format łącza (48 kHz) jest przyjmowany")
lk44.rate = 44100
check(K.SpeakerLink(0).attach(lk44) is not None, "inna częstotliwość niż 48 kHz: głośnik nieużywany")


def pcm(left, right, n, ch=4, other=9999):
    """n ramek: kanały 0 i 1 niosą left / right (funkcje k), pozostałe - stała, która nie może przeciec."""
    a = array("h", [other]) * (n * ch)
    for k in range(n):
        a[k * ch], a[k * ch + 1] = left(k), right(k)
    return a.tobytes()


def run_link(sec, make, step=0.0025, link=lk, speaker=sp):
    """Podaje audio w oknach po 10 ms i krokuje głośnik na sztucznym zegarze."""
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
check(not sent_pk, "cisza przed dźwiękiem = zero pakietów")
run_link(0.5, dc)
n_tone = len(sent_pk)
check(n_tone >= 45, f"dźwięk = strumień pakietów ({n_tone})")
check(sent_pk[0][3] == SPK_START and all(p[3] == 0 for p in sent_pk[1:n_tone]), "START tylko na pierwszym")
nums = [p[2] for p in sent_pk]
check(all(b - a == 1 for a, b in zip(nums, nums[1:], strict=False)), "numeracja ciągła, +1 na ramkę, bez powtórek")
gaps = [b[0] - a[0] for a, b in zip(sent_pk[10:], sent_pk[11:n_tone], strict=False)]
check(gaps and 0.0095 < sum(gaps) / len(gaps) < 0.0105 and max(gaps) < 0.03, f"tempo zegara audio (średnio {1000 * sum(gaps) / len(gaps):.1f} ms)")
check(all(p[1] == 0 and len(p[4]) == SPK_BYTES for p in sent_pk), "pad fizyczny 0, ramka 960 B")
body = [p[4] for p in sent_pk[:n_tone]]
check(all(struct.unpack("<480h", b) == (2000,) * 480 for b in body), "każda próbka = średnia kanałów 0 i 1 (kanały haptyki nie przeciekają)")
run_link(0.6, lambda k: pcm(lambda i: 0, lambda i: 0, 480))
end_pk = [p for p in sent_pk if p[3] & SPK_END]
check(len(end_pk) == 1 and sent_pk[-1] is end_pk[0], "po ciszy dokładnie jeden SPK_END, na końcu")
check(end_pk[0][4] == K.ZERO_FRAME, "SPK_END niesie ciszę")
quiet_from = len(sent_pk)
run_link(1.0, lambda k: pcm(lambda i: 0, lambda i: 0, 480))
check(len(sent_pk) == quiet_from, "w ciszy zero ruchu")
check((end_pk[0][0] - sent_pk[n_tone - 1][0]) < 0.4, "END po krótkiej ciszy (poniżej 400 ms)")
before = len(sent_pk)
run_link(0.1, dc)
check(sent_pk[before][3] == SPK_START and sent_pk[before][2] == end_pk[0][2] + 1, "nowy strumień: znów START, numeracja idzie dalej")
n0 = len(sent_pk)
for _ in range(200):                                   # gra przestała renderować w trakcie strumienia
    now[0] += 0.005
    sp.step(now[0])
check(sent_pk[-1][3] & SPK_END and len(sent_pk) > n0, "brak callbacków w strumieniu kończy go (END)")

# siła i ramki niecałe: audio w oknach po 7 ms (nie wielokrotność 480) składa się w ramki bez zgubionych próbek
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
samples = [v for _n, _f, c in sent_pk for v in struct.unpack("<480h", c)]
sent_n = len(sp2._pend) // 2
check(samples[:200] == [(4000 + i) // 2 for i in range(200)] and len(samples) + sent_n >= 20000,
      "siła 50 %, okna po 336 ramek: ciągłość próbek między ramkami (bez luk i powtórek)")
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(True, 100)})))

# kolejka: duża porcja naraz nie robi serii, nadmiar jest odrzucany jako luka
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
check(sp3.dropped > 0, f"zbyt długa porcja: najstarsze ramki odrzucone ({sp3.dropped}), numeracja ma lukę")
check(all(b[0] - a[0] > 0.0085 for a, b in zip(sent_pk, sent_pk[1:], strict=False)), "bez serii pakietów")
check(sent_pk and sent_pk[0][2] == SPK_START, "START na pierwszym wysłanym")

# przepełnienie bufora (pisarz wyprzedził czytnika): czytnik wraca na granicę ramki, kanały się nie zamieniają
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
check(sp6.overruns == 1 and sp6._r % 12 == 0, "przepełnienie: czytnik głośnika wraca na granicę ramki (6 kanałów)")
for _ in range(40):
    now[0] += 0.0107
    sp6.step(now[0])
rows = [c for _n, _f, c in sent_pk if c != K.ZERO_FRAME]
check(bool(rows) and set(struct.unpack("<480h", rows[-1])) == {6000}, "po przepełnieniu kanały głośnika nadal są kanałami 2 i 3")

# ---------- bramki ----------
def streams(setup=lambda: None, sec=0.2):
    """Czy głośnik wysyła cokolwiek przy danym ustawieniu bramek?"""
    out = []
    link = H.Link(0, clock=lambda: now[0])
    link.configure(4, 48000, 16, ROLES)
    s = K.SpeakerLink(0, send=lambda t, n, f, c: out.append(n), clock=lambda: now[0])
    s.attach(link)
    setup()
    run_link(sec, dc, link=link, speaker=s)
    return bool(out)

check(streams(), "bazowo: v14 + CAP_SPEAKER + włączone = strumień")
S.sender_info[0] = (13, 3 | CAP_HAPTICS, 0)
check(not streams() and not K.sender_speaker(0), "stary nadajnik (v13): brak strumienia")
S.sender_info[0] = (14, 3, 0)
check(not streams() and not K.sender_speaker(0), "v14 bez bitu CAP_SPEAKER: brak strumienia")
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={A: rx_settings.PadSpeaker(False, 100)})))
check(not streams(), "wyłączone w ustawieniach (domyślnie): brak strumienia")
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 100)})))
check(streams(), "ustawienie domyślne * obejmuje pada bez własnego")
S.pad_id[1] = "bb:bb:bb:bb:bb:09"
S.alive[1] = True
S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
mst = replace(rx_settings.cur.master, pad=A, only_master=("speaker",), when_absent="nobody")
rx_settings.apply(replace(rx_settings.cur, master=mst))
check(streams(), "master dostaje głośnik")
S.pad_id[0], S.pad_id[1] = S.pad_id[1], S.pad_id[0]
check(not streams(), "pad, który nie jest masterem, nie dostaje głośnika")
S.pad_id[0], S.pad_id[1] = S.pad_id[1], S.pad_id[0]
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad="")))

# ---------- composite dla samego głośnika ----------
H._composite_off_until = 0.0
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={}),
                          speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 100)})))
check(H.composite_wanted(0), "samo włączone głośnik (bez haptyki) = pad composite")
S.sender_info[0] = (13, 3 | CAP_HAPTICS, 0)
check(not H.composite_wanted(0), "głośnik włączony, ale stary nadajnik: zwykły pad")
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={})))
check(not H.composite_wanted(0), "oba wyłączone: zwykły pad")
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={"*": rx_settings.PadHaptics(True, 100)})))
S.sender_info[0] = (14, 3 | CAP_SPEAKER | CAP_HAPTICS, 0)
check(H.composite_wanted(0), "samo włączone haptyka działa jak dawniej")
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={}),
                          speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 100)})))
mst = replace(rx_settings.cur.master, pad=A, only_master=("speaker",), when_absent="nobody")
rx_settings.apply(replace(rx_settings.cur, master=mst))
S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
check(H.composite_wanted(0) and not H.composite_wanted(1), "tylko master dostaje composite dla głośnika")
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad="")))


class Pad:
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
      "create_composite podpina głośnik pod bufor audio łącza")
H.detach(0)
check(K.speaker(0).link is None and H.link(0).on_write is None, "detach odpina głośnik")
K.speaker(0)._sent_started = True
sent_pk.clear()
K.speaker(0)._send = lambda t, n, f, c: sent_pk.append((n, f, c))
K.detach(0)
check(len(sent_pk) == 1 and sent_pk[0][1] & SPK_END, "detach w trakcie strumienia wysyła SPK_END od razu")

# ---------- przycisk testu: te same bramki, powód zamiast "wysłano" ----------
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad=A)))
S.sender_info[0] = S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
K.speakers[:] = []
why = K.test(1)
check(why is not None and "master" in why and not K.speaker(1)._inject, f"test głośnika: pad nie-master dostaje powód, nic nie czeka w kolejce ({why!r})")
check(K.test(0) is None and K.speaker(0)._inject, "test głośnika: master dostaje ton")
K.speaker(0)._inject.clear()
S.sender_info[1] = (13, 3, 0)
why = K.test(1)
check(why is not None and "starszej" in why, "test głośnika: stary nadajnik = powód")
check(K.test(5) is not None and K.test(-1) is not None, "test głośnika: nieistniejący pad = powód")
S.sender_info[1] = (14, 3 | CAP_SPEAKER, 0)
# ton zakolejkowany, zanim zabroniono: kolejka jest czyszczona
sent_pk.clear()
sp7 = K.SpeakerLink(1, send=lambda t, n, f, c: sent_pk.append(n), clock=lambda: now[0])
sp7._inject.extend([b"\x05\x00" * SPK_FRAME] * 8)
for _ in range(100):
    now[0] += 0.005
    sp7.step(now[0])
check(not sent_pk and not sp7._inject, "pad bez uprawnień: zakolejkowany ton jest odrzucany")
rx_settings.apply(replace(rx_settings.cur, master=replace(mst, pad="")))
for _ in range(100):
    now[0] += 0.005
    sp7.step(now[0])
check(not sent_pk, "po przyznaniu uprawnień stary ton nie gra")
# ton przechodzi tą samą drogą: START, ramki z sinusem 440 Hz, cisza, END
sent_pk.clear()
sp8 = K.SpeakerLink(0, send=lambda t, n, f, c: sent_pk.append((n, f, c)), clock=lambda: now[0])
sp8.test(1.0)
sp8._thread = types.SimpleNamespace(is_alive=lambda: True)
for _ in range(400):
    now[0] += 0.005
    sp8.step(now[0])
tone_frames = [c for _n, _f, c in sent_pk if c != K.ZERO_FRAME]
check(sent_pk[0][1] == SPK_START and sent_pk[-1][1] & SPK_END and len(tone_frames) == round(K.TEST_S / K.FRAME_S),
      f"ton próbny: START, {len(tone_frames)} ramek dźwięku, END")
wave = [v for c in tone_frames for v in struct.unpack("<480h", c)]
cross = sum(1 for a, b in zip(wave, wave[1:], strict=False) if a < 0 <= b)
check(abs(cross / K.TEST_S - K.TEST_HZ) < 8 and max(wave) <= 32767 * K.TEST_LEVEL + 2, f"ton próbny 440 Hz ({cross / K.TEST_S:.0f} Hz) bez przesterowania")

# ---------- ustawienia ----------
path = os.path.join(tempfile.mkdtemp(), "settings.json")
s = replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(pads={"*": rx_settings.PadSpeaker(True, 80), A: rx_settings.PadSpeaker(False, 150)}))
rx_settings.save(s, path)
back = rx_settings.load(path)
check(back.speaker == s.speaker and back.speaker.get(A).gain == 150 and back.speaker.get("zz").gain == 80, "ustawienia głośnika: zapis i odczyt, domyślne * dla nieznanego pada")
import json
raw = json.load(open(path))
raw["speaker"]["pads"]["bad"] = "x"
raw["speaker"]["pads"]["loud"] = {"enabled": True, "gain": 100000}
json.dump(raw, open(path, "w"))
back = rx_settings.load(path)
check("bad" not in back.speaker.pads and "loud" not in back.speaker.pads and back.speaker.get(A).gain == 150, "złe wpisy pomijane, reszta zostaje")
check("speaker" in rx_settings.MASTER_FEATURES, "głośnik jest funkcją zastrzeganą dla mastera")
old = rx_settings.load(os.path.join(tempfile.mkdtemp(), "none.json"))
check(old.speaker == rx_settings.SpeakerConfig() and not old.speaker.get(A).enabled, "brak sekcji w pliku: głośnik wyłączony")

print("FAILS:" if fails else "SPEAKER OK", fails or "")
sys.exit(1 if fails else 0)
