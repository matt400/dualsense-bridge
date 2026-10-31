"""Receiver: korektor głośnika pada (rx_eq) - charakterystyka pasm, ciągłość między blokami, reset w ciszy, miękkie obcinanie po podbiciu,
ustawienia per pad (zapis, walidacja, domyślne *) i działanie w łączu głośnika (zmiana bez restartu, przycisk testu).
    python3 tests/test_eq.py receiver"""
import json, math, os, sys, tempfile, time
from array import array
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_eq as E, rx_haptics as H, rx_settings, rx_speaker as K
import rx_state as S
from proto import CAP_SPEAKER, SPK_BYTES

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

RATE = 48000


def sine(hz, n=24000, amp=3000.0):
    return [amp * math.sin(2 * math.pi * hz * i / RATE) for i in range(n)]


def rms(x):
    return math.sqrt(sum(v * v for v in x) / len(x))


def gain_db(gains, hz):
    """Wzmocnienie sinusa hz po przejściu przez korektor (po ustaleniu się filtra), w dB."""
    eq = E.Equalizer()
    eq.set_gains(gains)
    x = sine(hz)
    y = eq.process(list(x))
    return 20 * math.log10(rms(y[8000:]) / rms(x[8000:]))


def band(i, db):
    g = [0] * E.NUM_BANDS
    g[i] = db
    return tuple(g)


# ---------- płaski ----------
flat = E.Equalizer()
x0 = sine(1000, 480)
check(flat.flat and flat.process(list(x0)) == x0, "płaski korektor nie zmienia próbek")
flat.set_gains(E.FLAT)
check(flat.flat, "zera po zerach: nadal płaski")

# ---------- charakterystyka ----------
for i, (hz, kind) in enumerate(E.BANDS):
    if kind == "peak":
        up, down = gain_db(band(i, 12), hz), gain_db(band(i, -12), hz)
        check(abs(up - 12) < 0.3 and abs(down + 12) < 0.3, f"pasmo {hz} Hz: +12 / -12 dB w środku ({up:+.2f} / {down:+.2f})")
    elif kind == "low":
        far, mid = gain_db(band(i, 12), 20), gain_db(band(i, 12), hz)
        check(abs(far - 12) < 1.0 and abs(mid - 6) < 0.5, f"półka dolna {hz} Hz: +12 dB w basie, +6 dB na zboczu ({far:+.2f} / {mid:+.2f})")
    else:
        far, mid = gain_db(band(i, 12), 20000), gain_db(band(i, 12), hz)
        check(abs(far - 12) < 1.0 and abs(mid - 6) < 0.5, f"półka górna {hz} Hz: +12 dB w górze, +6 dB na zboczu ({far:+.2f} / {mid:+.2f})")
leak = max(abs(gain_db(band(2, 12), hz)) for hz in (100, 8000))
check(leak < 1.0, f"podbicie 1 kHz prawie nie rusza 100 Hz i 8 kHz ({leak:.2f} dB)")
check(abs(gain_db(E.PRESETS["voice"], 1000)) > 1.0 and gain_db(E.PRESETS["voice"], 3000) > 3.0, "preset Głos podbija średnie tony")

# ---------- ciągłość i stan ----------
g = (4, -3, 6, 2, -5)
whole = E.Equalizer()
whole.set_gains(g)
chunked = E.Equalizer()
chunked.set_gains(g)
sig = sine(700, 4800, 8000.0)
a = whole.process(list(sig))
b = []
for k in range(0, len(sig), 333):                                     # okna o dziwnej długości
    b += chunked.process(sig[k:k + 333])
check(a == b, "podział na bloki nie zmienia wyniku (stan filtra przechodzi między blokami)")

ring = E.Equalizer()
ring.set_gains(band(2, 12))
ring.process(sine(1000, 2000))
tail = ring.process([0.0] * 100)
check(max(abs(v) for v in tail) > 1.0, "po dźwięku filtr z podbiciem dzwoni (stan żyje)")
ring.reset()
check(all(v == 0.0 for v in ring.process([0.0] * 100)), "reset kasuje pamięć filtra")
keep = E.Equalizer()
keep.set_gains(band(2, 12))
keep.process(sine(1000, 2000))
keep.set_gains((0, 0, 12, 0, 6))                                      # zmiana innego pasma nie ucina dzwonienia pasma 3
check(max(abs(v) for v in keep.process([0.0] * 50)) > 1.0, "zmiana innego pasma nie kasuje stanu działającego")

# ---------- ustawienia: przycinanie, presety ----------
check(E.clamp((20, -20, 1.4, 2.6, 0)) == (12, -12, 1, 3, 0), "clamp: zakres ±12 dB, zaokrąglenie")
for bad in ((1, 2, 3), (1, 2, 3, 4, "x"), (1, 2, 3, 4, True), (1, 2, 3, 4, 5, 6)):
    try:
        E.clamp(bad)
        check(False, f"clamp odrzuca {bad!r}")
    except ValueError:
        check(True, f"clamp odrzuca {bad!r}")
check(all(E.preset_of(v) == k and len(v) == E.NUM_BANDS and all(E.GAIN_DB[0] <= d <= E.GAIN_DB[1] for d in v) for k, v in E.PRESETS.items()),
      "presety: poprawne i rozpoznawane po wartościach")
check(E.preset_of((1, 0, 0, 0, 0)) == "custom", "inne wartości = własny")

# ---------- miks z korektorem ----------
eqs = E.Equalizer()
eqs.set_gains(band(2, 12))
loud = [round(30000 * math.sin(2 * math.pi * 1000 * i / RATE)) for i in range(4800)]
out = K.mix_eq(loud, loud, 1.0, eqs)
ref = E.Equalizer()
ref.set_gains(band(2, 12))
ideal = ref.process([float(v) for v in loud])
check(max(abs(v) for v in out) <= 32767 and max(abs(v) for v in out) > K.KNEE, "podbicie: miękkie obcinanie, w zakresie int16, mocny sygnał")
check(all((v > 0) == (w > 0) for v, w in zip(out, ideal, strict=True) if abs(w) > 1), "podbicie: bez przewijania (znak zgodny z ideałem)")
check(list(K.mix_eq(loud[:500], loud[:500], 0.5, E.Equalizer())) == list(K.mix(loud[:500], loud[:500], 0.5)),
      "mix_eq z płaskim korektorem = mix")
t0 = time.perf_counter()
cost = E.Equalizer()
cost.set_gains((3, 3, 3, 3, 3))
for _ in range(10):
    cost.process(sine(1000, 4800))
ms = (time.perf_counter() - t0) * 1000 / 10 * 10                    # 10 bloków po 0,1 s = 1 s dźwięku, uśrednione
check(ms < 500, f"koszt 5 pasm: {ms:.0f} ms CPU na 1 s dźwięku (info: {ms / 240:.2f} us na próbkę i pasmo)")

# ---------- ustawienia pada: plik ----------
raw = {"speaker": {"pads": {"*": {"enabled": True, "gain": 120, "eq": [3, 0, -2, 20, 1]},
                            "AA:BB": {"enabled": True, "gain": 90, "eq": [1, 2, 3, 4, -20]},
                            "CC": {"enabled": True, "gain": 80, "eq": "zle"},
                            "DD": {"enabled": True, "gain": 70, "eq": [1, 2, 3]},
                            "EE": {"enabled": True, "gain": 60}}}}
path = os.path.join(tempfile.mkdtemp(), "settings.json")
with open(path, "w") as f:
    json.dump(raw, f)
loaded = rx_settings.load(path)
pads = loaded.speaker.pads
check(pads["*"].eq == (3, 0, -2, 12, 1) and pads["aa:bb"].eq == (1, 2, 3, 4, -12), "wczytanie: wartości przycięte do ±12 dB")
check(pads["cc"].eq == E.FLAT and pads["cc"].gain == 80 and pads["cc"].enabled, "zły eq: płasko, reszta wpisu zostaje")
check(pads["dd"].eq == E.FLAT and pads["dd"].gain == 70, "zła liczba pasm: płasko, reszta wpisu zostaje")
check(pads["ee"].eq == E.FLAT and pads["ee"].gain == 60, "stary plik bez eq: płasko")
rx_settings.save(loaded, path)
again = rx_settings.load(path)
check(again.speaker == loaded.speaker, "zapis i odczyt: te same ustawienia korektora")
check(json.load(open(path))["speaker"]["pads"]["aa:bb"]["eq"] == [1, 2, 3, 4, -12], "w pliku eq jest listą pięciu liczb")
check(loaded.speaker.get("nieznany").eq == (3, 0, -2, 12, 1) and loaded.speaker.get("aa:bb").eq == (1, 2, 3, 4, -12),
      "pad bez wpisu dostaje domyślne *, pad z własnym - własne")
check(rx_settings.SpeakerConfig().get("x").eq == E.FLAT, "bez niczego: korektor płaski")

# ---------- łącze ----------
A = "aa:aa:aa:aa:aa:09"
S.pad_id[0] = A
S.alive[0] = True
S.sender_info[0] = (14, 3 | CAP_SPEAKER, 0)
ROLES = ["speakerLeft", "speakerRight", "hapticLeft", "hapticRight"]


def apply_eq(gains):
    rx_settings.apply(replace(rx_settings.cur, speaker=rx_settings.SpeakerConfig(
        pads={A: rx_settings.PadSpeaker(True, 100, tuple(gains))})))


def pcm(hz, k0, n, amp=3000):
    a = array("h", [9999]) * (n * 4)
    for k in range(n):
        v = round(amp * math.sin(2 * math.pi * hz * (k0 + k) / RATE))
        a[k * 4], a[k * 4 + 1] = v, v
    return a.tobytes()


def level(gains, hz=1000, sec=0.6, change=None):
    """RMS ramek wysłanych przez łącze (po ustaleniu się filtra); `change` = (po ilu s, nowe wzmocnienia) zmienia ustawienie w locie."""
    sent = []
    now = [100.0]
    apply_eq(gains)
    link = H.Link(0, clock=lambda: now[0])
    link.configure(4, 48000, 16, ROLES)
    sp = K.SpeakerLink(0, send=lambda t, n, f, c: sent.append((now[0], c)), clock=lambda: now[0])
    sp.attach(link)
    k = 0
    start = now[0]
    nxt = now[0]
    changed = False
    while now[0] < start + sec:
        if now[0] >= nxt:
            w = pcm(hz, k, 480)
            link.write(memoryview(w), len(w))
            nxt += 0.01
            k += 480
        if change and not changed and now[0] - start >= change[0]:
            apply_eq(change[1])
            changed = True
        sp.step(now[0])
        now[0] += 0.0025
    return sp, sent, start


def rms_of(frames):
    samples = [v for c in frames for v in array("h", c)]
    return rms(samples)


sp_flat, sent_flat, _t = level(E.FLAT)
sp_eq, sent_eq, _t = level(band(2, 12))
r_flat, r_eq = rms_of([c for _t, c in sent_flat[10:]]), rms_of([c for _t, c in sent_eq[10:]])
db = 20 * math.log10(r_eq / r_flat)
check(abs(db - 12) < 1.0, f"łącze: +12 dB na 1 kHz podbija wysłany dźwięk o ok. 12 dB ({db:+.2f})")
check(all(len(c) == SPK_BYTES for _t, c in sent_eq), "ramki z korektorem mają tę samą długość (960 B)")

sp_ch, sent_ch, t0c = level(band(2, 12), change=(0.3, E.FLAT))
early = rms_of([c for t, c in sent_ch if 0.1 < t - t0c < 0.28])
late = rms_of([c for t, c in sent_ch if t - t0c > 0.42])
check(early > 2.5 * late and abs(20 * math.log10(late / r_flat)) < 1.0,
      f"zmiana ustawienia w locie działa od razu, bez restartu ({20 * math.log10(early / late):+.1f} dB różnicy)")
check(sp_ch._eq.flat, "po zmianie na płaski link nie filtruje")

# cisza zeruje filtr
sp_s, _sent, _t = level(band(2, 12), sec=0.3)
sp_s._eq.process(sine(1000, 480))
check(any(z != [0.0, 0.0] for z in sp_s._eq._z), "filtr ma stan po dźwięku")
silent_clock = [200.0]
w = array("h", [0]) * (480 * 4)
sp_s.link.write(memoryview(w.tobytes()), len(w) * 2)
sp_s.step(silent_clock[0])
check(all(z == [0.0, 0.0] for z in sp_s._eq._z), "blok cyfrowej ciszy zeruje stan filtra (bez kliku po wznowieniu)")

# ---------- przycisk testu ----------
apply_eq(band(2, 12))
plain = K.SpeakerLink(0)
plain.ensure_thread = lambda: None
boost = K.SpeakerLink(0)
boost.ensure_thread = lambda: None
plain.test(1.0)
boost.test(1.0, band(2, 12))
check(len(plain._inject) == len(boost._inject) and list(plain._inject)[0] != list(boost._inject)[0],
      "test głośnika gra z podanym korektorem (inny dźwięk niż płaski)")
check(max(abs(v) for fr in boost._inject for v in fr) <= 32767, "jingiel z podbiciem mieści się w int16")
rx_state_calls = []
real = K.speaker
K.speaker = lambda t: type("L", (), {"test": lambda self, gain, eq: rx_state_calls.append((gain, eq))})()
check(K.test(0) is None and rx_state_calls[-1] == (1.0, band(2, 12)), "K.test domyślnie bierze zapisany korektor pada")
check(K.test(0, 0.5, E.FLAT) is None and rx_state_calls[-1] == (0.5, E.FLAT), "K.test z oknem: pokazane wartości, nie zapisane")
K.speaker = real

sys.exit(1 if fails else 0)
