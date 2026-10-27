"""Receiver: haptyka gry (HD haptics) - dziesiątkowanie 48 kHz -> 3 kHz (Goertzel, DC, clipping), pakiety T_HAPT, łącze (START / END,
cisza = zero ruchu, tempo, kolejka), bramka CAP_HAPTICS, master, fallback composite, ustawienie per pad, domyślne wyjście audio.
    python3 tests/test_haptics.py receiver"""
import math, os, struct, sys, types
from array import array
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_audio, rx_haptics as H, rx_master, rx_settings
import rx_state as S
from proto import CAP_HAPTICS, HAPT_CHUNK, HAPT_END, HAPT_FRAMES, HAPT_START, T_CMD

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


def pcm(fn, n, rate=48000, ch=4, amp=16000):
    """n ramek: kanały 2 i 3 niosą fn(k) * amp, 0 i 1 - szum, który nie może przeciec."""
    a = array("h", [0]) * (n * ch)
    for k in range(n):
        v = int(amp * fn(k))
        a[k * ch], a[k * ch + 1], a[k * ch + 2], a[k * ch + 3] = 9999, -9999, v, -v
    return a.tobytes()


def goertzel(x, f, fs):
    w = 2 * math.pi * f / fs
    c = 2 * math.cos(w)
    s1 = s2 = 0.0
    for v in x:
        s1, s2 = v + c * s1 - s2, s1
    return math.sqrt(max(0.0, s1 * s1 + s2 * s2 - c * s1 * s2)) * 2 / len(x)


# ---------- dziesiątkowanie ----------
def amp_at(f, rate=48000):
    d = H.Decimator(rate)
    sec = 0.6
    out, _ = d.feed(pcm(lambda k: math.sin(2 * math.pi * f * k / rate), int(sec * rate), rate, amp=10000))
    out = out[len(out) // 3:]                       # po ustaleniu filtra
    return goertzel(out, f, 3000) / 10000

for f in (50, 160, 500, 900):
    g = 20 * math.log10(amp_at(f))
    check(abs(g) < 0.5, f"pasmo przepustowe {f} Hz: {g:+.2f} dB")
for f in (1550, 2000, 3000, 6000):
    g = 20 * math.log10(max(amp_at(f), 1e-9))
    check(g < -40, f"pasmo zaporowe {f} Hz: {g:.1f} dB")
d = H.Decimator()
out, outr = d.feed(pcm(lambda k: 1.0, 48000 // 2))
check(abs(out[-1] - 16000) < 1 and abs(outr[-1] + 16000) < 1, "DC: wzmocnienie 1, kanały lewy / prawy rozdzielone")
# blokami o dowolnej długości = jedno wywołanie (stan filtra między wywołaniami)
data = pcm(lambda k: math.sin(2 * math.pi * 200 * k / 48000), 4800)
one = H.Decimator().feed(data)
d = H.Decimator()
parts = ([], [])
fb = d.frame_bytes
for a, b in ((0, 7), (7, 1000), (1000, 1001), (1001, 4800)):
    l, r = d.feed(data[a * fb:b * fb])
    parts[0].extend(l)
    parts[1].extend(r)
check(len(one[0]) == len(parts[0]) and max(abs(x - y) for x, y in zip(one[0], parts[0])) < 1e-6, "stan filtra zachowany między wywołaniami")
# cisza po dźwięku: ta sama faza i wynik co pełny FIR
d1, d2 = H.Decimator(), H.Decimator()
sig = pcm(lambda k: math.sin(0.05 * k), 2000) + bytes(8 * 6000)
l1, _r = d1.feed(sig)
l2 = []
for k in range(0, 8000, 400):
    l2 += d2.feed(sig[k * 8:(k + 400) * 8])[0]
check(len(l1) == len(l2) and max(abs(x - y) for x, y in zip(l1, l2)) < 1e-6 and l1[-1] == 0.0, "szybka ścieżka ciszy nie zmienia wyniku ani fazy")

# clipping
big = H.quantize([32767.0, -32768.0, 20000.0, 100.0, 0.0], 2.0)
check(big[0] == 127 and big[1] == -127 and big[4] == 0 and -127 <= big[2] <= 127, f"miękki clip, bez zawinięcia: {big}")
check(H.quantize([256.0 * 50], 1.0) == [50] and H.quantize([1e9, -1e9], 2.0) == [127, -127], "liniowo do kolana, ekstremum nasyca")
check(H.quantize([30000.0], 0.0) == [0], "siła 0 % = zero")

# ---------- pakiet ----------
ch = H.make_chunk(list(range(32)), [-v for v in range(32)])
check(len(ch) == HAPT_CHUNK == 64 and ch[0] == 0 and ch[1] == 0 and ch[2] == 1 and ch[3] == 255, "chunk: 32 ramki stereo s8, lewy pierwszy")
import rx_net
got = []
rx_net.send_peer = lambda pkt: got.append(pkt)
rx_net.send_haptics(0, 2 ** 32 + 5, HAPT_START, ch)
pkt = got[-1]
check(pkt is not None and len(pkt) > 64, "pakiet T_HAPT zbudowany")
body = struct.pack("!IB", 5, HAPT_START) + ch
check(body in pkt, "ciało pakietu: !IB64s, numer ramki modulo 2**32")

# ---------- łącze ----------
A = "aa:aa:aa:aa:aa:09"
S.pad_id[0] = A
S.alive[0] = True
S.sender_info[0] = (13, 3 | CAP_HAPTICS, 0)
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={A: rx_settings.PadHaptics(True, 100)})))
sent_pk = []
now = [100.0]
lk = H.Link(0, send=lambda t, n, f, c: sent_pk.append((now[0], t, n, f, c)), clock=lambda: now[0])
check(lk.configure(4, 48000, 16, ["speakerLeft", "speakerRight", "hapticLeft", "hapticRight"]) is None, "format 4 kanały 48 kHz przyjęty")
check(lk.configure(2, 48000, 16, ["a", "b"]) is not None and lk.configure(4, 44100, 16, []) is not None, "inny format odrzucony")
lk.configure(4, 48000, 16, ["speakerLeft", "speakerRight", "hapticLeft", "hapticRight"])

def run_link(sec, make, step=0.0025):
    """Podaje audio w oknach po 10 ms i krokuje łącze na sztucznym zegarze."""
    end = now[0] + sec
    nxt = now[0]
    k = 0
    while now[0] < end:
        if now[0] >= nxt:
            w = make(k)
            lk.write(memoryview(w), len(w))
            nxt += 0.01
            k += 480
        lk.step(now[0])
        now[0] += step

def tone(k0):
    return pcm(lambda k: math.sin(2 * math.pi * 160 * (k + k0) / 48000), 480)

run_link(0.05, lambda k: bytes(480 * 8))
check(not sent_pk, "cisza przed dźwiękiem = zero pakietów")
run_link(0.5, tone)
n_tone = len(sent_pk)
check(n_tone >= 40, f"dźwięk = strumień pakietów ({n_tone})")
check(sent_pk[0][3] == HAPT_START and all(p[3] == 0 for p in sent_pk[1:n_tone]), "START tylko na pierwszym")
nums = [p[2] for p in sent_pk]
check(all(b - a == HAPT_FRAMES for a, b in zip(nums, nums[1:])), "numeracja ciągła, +32, bez powtórek")
gaps = [b[0] - a[0] for a, b in zip(sent_pk[10:], sent_pk[11:n_tone])]
check(gaps and sum(gaps) / len(gaps) > 0.0095 and max(gaps) < 0.03, f"tempo zegara audio (średnio {1000 * sum(gaps) / len(gaps):.1f} ms)")
check(all(p[1] == 0 and len(p[4]) == 64 for p in sent_pk), "pad fizyczny 0, chunk 64 B")
check(any(p[4][2:] != bytes(62) for p in sent_pk[:n_tone]), "w chunkach jest sygnał")
run_link(0.6, lambda k: bytes(480 * 8))
end_pk = [p for p in sent_pk if p[3] & HAPT_END]
check(len(end_pk) == 1 and sent_pk[-1] is end_pk[0], "po ciszy dokładnie jeden HAPT_END, na końcu")
quiet_from = len(sent_pk)
run_link(1.0, lambda k: bytes(480 * 8))
check(len(sent_pk) == quiet_from, "w ciszy zero ruchu")
tail = [p for p in sent_pk if p[3] & HAPT_END][0]
check((tail[0] - sent_pk[n_tone - 1][0]) < 0.4, "END po krótkiej ciszy (poniżej 400 ms)")
before = len(sent_pk)
run_link(0.1, tone)
check(sent_pk[before][3] == HAPT_START, "nowy strumień znów zaczyna się od START")
# brak audio w trakcie strumienia (gra przestała renderować) = END
run_link(0.0, tone)
n0 = len(sent_pk)
for _ in range(200):
    now[0] += 0.005
    lk.step(now[0])
check(sent_pk[-1][3] & HAPT_END and len(sent_pk) > n0 - 1, "brak callbacków w strumieniu kończy go (END)")

# kolejka: duża porcja naraz nie robi serii, nadmiar jest odrzucany jako luka
sent_pk.clear()
lk2 = H.Link(0, send=lambda t, n, f, c: sent_pk.append((now[0], n, f)), clock=lambda: now[0])
lk2.configure(4, 48000, 16, ["a", "b", "hapticLeft", "hapticRight"])
big = pcm(lambda k: math.sin(2 * math.pi * 160 * k / 48000), 48000 // 2)
lk2.write(memoryview(big), len(big))
for _ in range(400):
    now[0] += 0.0025
    lk2.step(now[0])
check(lk2.dropped > 0, f"zbyt długa porcja: najstarsze chunki odrzucone ({lk2.dropped}), numeracja ma lukę")
check(all(b[0] - a[0] > 0.008 for a, b in zip(sent_pk, sent_pk[1:] if len(sent_pk) > 1 else [])), "bez serii pakietów")
check(sent_pk and sent_pk[0][2] == HAPT_START, "START na pierwszym wysłanym")

# ---------- bramki ----------
S.sender_info[0] = (12, 3, 0)
check(not H.sender_haptics(0) and not H.composite_wanted(0), "stary nadajnik: brak composite")
S.sender_info[0] = (13, 3, 0)
check(not H.sender_haptics(0), "v13 bez bitu CAP_HAPTICS: brak haptyki")
S.sender_info[0] = (13, 3 | CAP_HAPTICS, 0)
check(H.sender_haptics(0) and H.composite_wanted(0), "v13 + CAP_HAPTICS + włączone = composite")
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={A: rx_settings.PadHaptics(False, 100)})))
check(not H.composite_wanted(0), "wyłączone w ustawieniach (domyślnie): zwykły pad")
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={"*": rx_settings.PadHaptics(True, 100)})))
check(H.composite_wanted(0), "ustawienie domyślne * obejmuje pada bez własnego")
# master-only
S.pad_id[1] = "bb:bb:bb:bb:bb:09"
S.alive[1] = True
S.sender_info[1] = (13, 3 | CAP_HAPTICS, 0)
m = replace(rx_settings.cur.master, pad=A, only_master=("haptics",), when_absent="none")
rx_settings.apply(replace(rx_settings.cur, master=m))
check(H.composite_wanted(0) and not H.composite_wanted(1), "tylko master dostaje haptykę")
rx_settings.apply(replace(rx_settings.cur, master=replace(m, pad="")))

# ---------- fallback przy błędzie utworzenia ----------
class Pad:
    def __init__(self, fail=False): self.fail, self.closed = fail, False
    def attach_audio(self, sink):
        if self.fail: raise RuntimeError("no endpoint")
        return 4, 48000, 16, ["speakerLeft", "speakerRight", "hapticLeft", "hapticRight"]
    def close(self): self.closed = True
class Backend:
    def __init__(self, exc=None, pad=None): self.exc, self.pad, self.calls = exc, pad, []
    def create(self, idx, on_fb, composite=False):
        self.calls.append(composite)
        if self.exc: raise self.exc
        return self.pad
rx_audio.default_output_ids = dict
H._composite_off_until = 0.0
good = Pad()
check(H.create_composite(Backend(pad=good), 0, None) is good, "composite utworzony")
H.detach(0)
bad = Backend(exc=RuntimeError("usbip refused"))
check(H.create_composite(bad, 0, None) is None, "błąd tworzenia composite = None (zwykły pad)")
check(not H.composite_wanted(0), "po błędzie przerwa w próbach")
half = Pad(fail=True)
H._composite_off_until = 0.0
check(H.create_composite(Backend(pad=half), 0, None) is None and half.closed, "błąd podłączenia audio: pad composite zamknięty, fallback")
# zmiana ustawień kasuje przerwę
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={"*": rx_settings.PadHaptics(True, 80)})))
check(H.composite_wanted(0), "zmiana ustawień kasuje przerwę po błędzie")

# ---------- ustawienie per pad nie rusza pozostałych ----------
H.init()
before_links = list(H.links)
rx_settings.apply(replace(rx_settings.cur, haptics=rx_settings.HapticsConfig(pads={"*": rx_settings.PadHaptics(True, 80), A: rx_settings.PadHaptics(False, 100)})))
check(list(H.links) == before_links, "zmiana ustawienia jednego pada nie odtwarza łączy innych")
check(not H.composite_wanted(0) and H.composite_wanted(1), "pad A wyłączony, pad B z domyślnego")
check(rx_settings.cur.haptics.get(A).gain == 100 and rx_settings.cur.haptics.get("zz").gain == 80, "siła per pad z domyślnym awaryjnym")

# ---------- domyślne wyjście audio ----------
state = {"eConsole": "ctl", "eMultimedia": "ctl", "eCommunications": "spk"}
names = [("spk", "Speakers (Realtek)"), ("ctl", "Speakers (Wireless Controller)"), ("tv", "TV")]
setcalls = []
rx_audio.default_output_ids = lambda: dict(state)
rx_audio.list_outputs = lambda controllers=False: [x for x in names if controllers or not rx_audio.is_controller_endpoint(x[1])]
rx_audio.set_default_id = lambda dev, roles=rx_audio.ROLES: ([state.__setitem__(r, dev) for r in roles], setcalls.append((dev, tuple(roles))))
before = {"eConsole": "spk", "eMultimedia": "spk", "eCommunications": "spk"}
check(sorted(H.restore_defaults(before)) == ["eConsole", "eMultimedia"], "przywrócone tylko role przełączone na pada")
check(state["eConsole"] == "spk" and state["eMultimedia"] == "spk", "domyślne wyjście z powrotem na głośniki")
state["eConsole"] = "tv"
check(H.restore_defaults(before) == [] and state["eConsole"] == "tv", "zmiana na inne urządzenie (użytkownik, tryb konsoli) zostaje")
check(rx_audio.is_controller_endpoint("Speakers (Wireless Controller)") and not rx_audio.is_controller_endpoint("TV"), "rozpoznanie endpointu pada")
check(all("Wireless" not in n for _i, n in rx_audio.list_outputs()), "list_outputs ukrywa endpointy padów")

print("FAILS:" if fails else "HAPTICS OK", fails or "")
sys.exit(1 if fails else 0)
