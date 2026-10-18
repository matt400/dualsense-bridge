"""Receiver: wirtualne pady - backend HIDMaestro (DualSense) / ViGEmBus (DS4), stała kolejność graczy (pad zastępczy),
raport DualSense, feedback z raportu wyjściowego, zmiana backendu w ustawieniach.
    python3 tests/test_vpad.py receiver"""
import os, sys, threading, time, types
from dataclasses import replace
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_ds4, rx_events as ev, rx_feedback as F, rx_hm, rx_report as R, rx_settings
import rx_state as S

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


def vpad(**kw):
    rx_settings.apply(replace(rx_settings.cur, vpad=replace(rx_settings.VpadConfig(), **kw)))


def wait(cond, t=3.0):
    end = time.monotonic() + t
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


# ---------- ViGEm (zaślepka): kolejność tworzenia ----------
created = []                                       # (numer pada, slot) w kolejności tworzenia
alive_pads = set()
class VPad:
    n = 0
    def __init__(self):
        VPad.n += 1
        self.id = VPad.n
        time.sleep(0.05)                           # tworzenie trwa - wyższy slot musiałby poczekać
        created.append(self.id)
        alive_pads.add(self.id)
    def register_notification(self, cb): pass
    def unregister_notification(self): alive_pads.discard(self.id)
    def update_extended_report(self, rep): pass

rx_ds4.vg.VDS4Gamepad = VPad
rx_ds4.init()
vpad(backend="vigem")
threads = [threading.Thread(target=rx_ds4.slot_thread, args=(i,), daemon=True) for i in range(2)]
for t in threads:
    t.start()


def pad_id(i):
    o = rx_ds4.outs[i]
    return o.pad.id if o is not None and o.pad is not None else None


# pad 2 włączony pierwszy: slot 0 dostaje pad zastępczy PRZED padem slotu 1
S.want[1].set(); S.wake[1].set()
check(wait(lambda: S.plugged[0] and S.plugged[1]), "pad 2 sam: oba sloty mają wirtualny pad (0 zastępczy)")
first0, first1 = pad_id(0), pad_id(1)
check(created.index(first0) < created.index(first1), f"zastępczy slot 0 powstał przed slotem 1: {created}")
check(rx_ds4.kinds[0] == rx_ds4.kinds[1] == "vigem", "rodzaj pada w kinds (okno)")
# pad 1 dołącza: slot 0 to ten sam wirtualny pad (gracz 1 bez ponownego podłączania)
S.want[0].set(); S.wake[0].set()
time.sleep(0.3)
check(pad_id(0) == first0 and pad_id(1) == first1, "pad 1 dołącza do istniejącego zastępczego - bez przełączania")
# pad 2 wychodzi: slot 1 znika, slot 0 zostaje (to pad 1)
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[1]) and S.plugged[0] and pad_id(0) == first0, "pad 2 rozłączony: slot 0 zostaje")
# pad 2 wraca, pad 1 wychodzi: slot 0 staje się zastępczym, nie znika (pad 2 zostaje graczem 2)
S.want[1].set(); S.wake[1].set()
check(wait(lambda: S.plugged[1]), "pad 2 wraca")
S.want[0].clear(); S.wake[0].set()
time.sleep(0.3)
check(S.plugged[0] and pad_id(0) == first0, "pad 1 rozłączony przy podłączonym 2: slot 0 trzyma miejsce")
# pad 2 też wychodzi: nic nie zostaje
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[0] and not S.plugged[1]), "oba rozłączone: brak wirtualnych padów")
check(wait(lambda: not alive_pads), f"wszystkie pady odłączone od ViGEm: {alive_pads}")

# bez stałej kolejności: pad 2 sam = jeden wirtualny pad
vpad(backend="vigem", fixed_slots=False)
S.want[1].set(); S.wake[1].set()
check(wait(lambda: S.plugged[1]) and not S.plugged[0], "fixed_slots=false: tylko slot 1 (stare zachowanie)")
# włączenie stałej kolejności w trakcie: slot 0 dostaje pad zastępczy od razu (wątek slotu sprawdza needed)
vpad(backend="vigem", fixed_slots=True)
check(wait(lambda: S.plugged[0]), "fixed_slots włączone w trakcie: zastępczy slot 0")
S.want[1].clear(); S.wake[1].set()
check(wait(lambda: not S.plugged[0] and not S.plugged[1]), "sprzątanie")


# ---------- HIDMaestro (zaślepka backendu): raport DualSense, feedback, zmiana backendu ----------
class HMPad:
    kind = "hm"
    build = staticmethod(R.build_ds5_report)
    def __init__(self, slot, on_fb):
        self.slot, self.on_fb = slot, on_fb
        self.buf = bytearray(63)
        self.sent = []
        self.closed = False
    def submit(self):
        self.sent.append(bytes(self.buf))
    def close(self):
        self.closed = True

class HMBackend:
    def __init__(self):
        self.pads = []
    def create(self, slot, on_fb):
        p = HMPad(slot, on_fb)
        self.pads.append(p)
        return p

hm = HMBackend()
real_get = rx_hm.get
rx_hm.get = lambda path="": hm
vpad(backend="auto")
S.want[0].set(); S.wake[0].set()
check(wait(lambda: S.plugged[0]) and rx_ds4.kinds[0] == "hm", "auto + HIDMaestro dostępny: DualSense")
with S.lock:
    S.states[0].update(lx=10, ly=20, rx=30, ry=40, lt=200, rt=0, mask=BIT[304])
S.notify(0)
p = hm.pads[-1]
check(wait(lambda: p.sent and p.sent[-1][0] == 10), "raport idzie przez submit() pada (bufor pada, bez kopii)")
r = p.sent[-1]
check(r[:6] == bytes((10, 20, 30, 40, 200, 0)) and r[7] & 0x20 and r[8] & 0x04, f"układ raportu DualSense: {r[:10].hex()}")

# feedback z gry: raport wyjściowy 0x02 (silniki + lightbar z flagami ważności)
data = bytearray(47)
data[0], data[1], data[2], data[3] = 0x03, 0x04, 40, 200
data[44:47] = bytes((1, 2, 3))
motors, rgb = rx_hm.parse_output(0x02, data)
check(motors == (200, 40) and rgb == (1, 2, 3), f"parse_output: lewy = duży, prawy = mały, kolor: {motors} {rgb}")
check(rx_hm.parse_output(0x02, bytearray(47)) == (None, None), "bez flag ważności nic nie zmienia")
check(rx_hm.parse_output(0x05, data) == (None, None), "inny raport ignorowany")
class NetArray(list):              # tablica .NET: każdy odczyt elementu to wywołanie przez pythonnet
    reads = 0
    def __getitem__(self, k):
        NetArray.reads += 1
        return list.__getitem__(self, k)
class OutPkt:
    Source, ReportId = rx_hm.HID_OUTPUT, rx_hm.OUT_REPORT
    def __init__(self, raw):
        self.Data = types.SimpleNamespace(ToArray=lambda: NetArray(raw))
hm_pad, got_fb = object.__new__(rx_hm.Pad), []
hm_pad.on_fb = lambda m, c: got_fb.append((m, c))
hm_pad._on_output(None, OutPkt(list(data) + [0] * 16))
check(got_fb == [((200, 40), (1, 2, 3))] and NetArray.reads <= len(rx_hm.OUT_USED),
      f"wyjście HIDMaestro: tylko potrzebne bajty ({NetArray.reads} odczytów) - {got_fb}")
got_fb.clear()
hm_pad._on_output(None, OutPkt(list(data[:10])))
check(got_fb == [], "za krótki raport wyjściowy - bez feedbacku i bez wyjątku")
p.on_fb(motors, rgb)
f = F.fb[0]
check((f["large"], f["small"], f["r"], f["g"], f["b"], f["rgb"], f["set"]) == (200, 40, 1, 2, 3, True, True), f"F.game: {f}")
p.on_fb(None, (9, 9, 9))
check((f["large"], f["small"], f["r"]) == (200, 40, 9), "sam kolor nie zeruje wibracji")

# zmiana backendu w oknie: pady odtwarzane w nowym (bez restartu)
vpad(backend="vigem")
check(wait(lambda: p.closed and S.plugged[0] and rx_ds4.kinds[0] == "vigem"), "zmiana na ViGEm: DualSense zamknięty, DS4 podłączony")
vpad(backend="auto")
check(wait(lambda: rx_ds4.kinds[0] == "hm" and len(hm.pads) == 2), "i z powrotem na HIDMaestro")

# błąd tworzenia w HIDMaestro: ten pad przez ViGEm, ostrzeżenie w dzienniku
S.want[0].clear(); S.wake[0].set()
check(wait(lambda: not S.plugged[0]), "odłączony")
class BadHM:
    def create(self, slot, on_fb):
        raise OSError("PnP timeout")
rx_hm.get = lambda path="": BadHM()
seq = ev.since(0)[-1].seq if ev.since(0) else 0
S.want[0].set(); S.wake[0].set()
check(wait(lambda: S.plugged[0]) and rx_ds4.kinds[0] == "vigem", "błąd HIDMaestro przy tworzeniu: zapas ViGEm")
check(any(e.level == "warn" and "HIDMaestro" in e.text for e in ev.since(seq)), "ostrzeżenie w dzienniku")
S.want[0].clear(); S.wake[0].set()
check(wait(lambda: not S.plugged[0]), "odłączony")
rx_hm.get = real_get

# brak DLL: auto = tylko informacja, hidmaestro = ostrzeżenie
rx_ds4._hm_warned = None
seq = ev.since(0)[-1].seq if ev.since(0) else 0
vpad(backend="auto", hm_dll="/nie/ma/tego")
check(rx_ds4.hm_backend() is None and rx_ds4.hm_note, "auto bez DLL: ViGEm, powód w hm_note")
check([e.level for e in ev.since(seq) if "HIDMaestro" in e.text] == ["info"], "auto bez DLL: informacja, nie ostrzeżenie")
seq = ev.since(0)[-1].seq if ev.since(0) else 0
vpad(backend="hidmaestro", hm_dll="/nie/ma/tego")
rx_ds4.hm_backend()
rx_ds4.hm_backend()
check([e.level for e in ev.since(seq) if "HIDMaestro" in e.text] == ["warn"], "wybrany HIDMaestro bez DLL: jedno ostrzeżenie")
vpad(backend="vigem")
check(rx_ds4.hm_backend() is None and rx_ds4.hm_note is None, "vigem: bez powodu w oknie")

# folder w hm_dll = DLL w tym folderze
import tempfile
d = tempfile.mkdtemp()
open(os.path.join(d, rx_hm.DLL), "wb").close()
check(rx_hm.find_dll(d) == os.path.join(d, rx_hm.DLL), "hm_dll może wskazywać folder")


# ---------- raport DualSense ----------
s = S.new_state()
s.update(lx=1, ly=2, rx=3, ry=4, lt=5, rt=255, hx=1, hy=0, mask=BIT[304] | BIT[310] | R.PS_BIT,
         gyro=(0, 0, 0), acc=(1, 2, 3), touch=((1, 7, 1919, 942), (0, 0, 0, 0)), tp=True, batt_ds4=0x10 | 5)
b = R.build_ds5_report(s, 300, False)
check(len(b) == 63 and b[:7] == bytes((1, 2, 3, 4, 5, 255, 300 & 0xFF)), f"gałki, spusty, licznik: {b[:7].hex()}")
check(b[7] == (2 | 0x20) and b[8] == (0x01 | 0x04 | 0x08) and b[9] == 0x03, f"D-pad + krzyżyk, L1 + L2 i R2 cyfrowe, PS + klik: {b[7:10].hex()}")
check(b[21:27] == bytes((1, 0, 2, 0, 3, 0)), "akcelerometr w bajtach 21..26")
check(b[32] == 7 and b[33] == 0x7F and (b[34] & 0x0F) == 0x07 and ((b[34] >> 4) | (b[35] << 4)) == 1079,
      "dotyk w skali DualSense (1920x1080)")
check(b[36] & 0x80, "drugi palec nieaktywny")
check(b[52] == 0x15, f"bateria: poziom 5, ładowanie: {b[52]:#x}")
check(R.ds5_battery(0x10 | 11) == 0x2A, "pełna przy kablu")
import rx_net as N
check([R.ds5_battery(N.batt_codes(p, 0)[1]) for p in (100, 50, 0)] == [10, 5, 0],
      "bez kabla skala DS4 0..8 -> DualSense 0..10 (100 % = poziom 10, nie 8 = 85 % w grze)")
b = R.build_ds5_report(s, 1, True)
check(b[9] == 0x01 and b[32] & 0x80, "tryb myszy: bez kliku touchpada i dotyku w grze")


# ---------- settings.json ----------
path = os.path.join(TMP, "s_vpad.json")
cur = replace(rx_settings.defaults(), vpad=rx_settings.VpadConfig(backend="hidmaestro", fixed_slots=False, hm_dll="C:\\HM"))
rx_settings.save(cur, path)
check(rx_settings.load(path).vpad == cur.vpad, "vpad zapis / odczyt (ścieżka bez zmiany wielkości liter)")
import json
json.dump({"vpad": {"backend": "xinput", "fixed_slots": "tak"}}, open(path, "w"))
check(rx_settings.load(path).vpad == rx_settings.VpadConfig(), "złe wartości -> domyślne")

S.stop.set()
for t in threads:
    t.join(3)
print("VPAD", "OK" if not fails else f"BŁĘDY: {len(fails)}")
if fails:
    sys.exit(1)
