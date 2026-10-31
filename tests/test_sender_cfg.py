"""Receiver: sender settings from the Sender tab - settings.json section, cfgval handling, push on online, gating by key / v3.
    python3 tests/test_sender_cfg.py receiver"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import cfgspec
import rx_sender_cfg as SC
import rx_settings
from proto import (BODY, HEADER_LEN, CFG_ALL, CFG_GET, CFG_RESET, CFG_SET, CFGF_REMOTE, CFGR_NO_KEY, CFGR_OK, T_CFGSET, T_CFGVAL, Codec)
from chk import check, fails                                  # noqa: E402

# ---------- settings.json: sekcja "sender" ----------
path = os.path.join(TMP, "sender_settings.json")
good = rx_settings.Settings(sender=rx_settings.SenderConfig(values={"max_pads": 4, "rumble": "off", "pad_min_dt": 0.002}))
rx_settings.save(good, path)
check(rx_settings.load(path).sender.values == good.sender.values, "sekcja sender: zapis i odczyt dają to samo")
raw = json.load(open(path, encoding="utf-8"))
raw["sender"]["values"].update({"max_pads": 99, "nieznane": 1, "low_latency": "tak", "rumble": "evdev"})
json.dump(raw, open(path, "w", encoding="utf-8"))
loaded = rx_settings.load(path).sender.values
check("max_pads" not in loaded and "nieznane" not in loaded and "low_latency" not in loaded,
      "zła wartość, nieznana nazwa i zły typ są pomijane (z komunikatem)")
check(loaded.get("rumble") == "evdev" and loaded.get("pad_min_dt") == 0.002, "reszta tabeli zostaje")
check(rx_settings.load(os.path.join(TMP, "nie_ma.json")).sender.values == {}, "brak pliku: pusta sekcja")

# ---------- kodeki: nadajnik (tx) i odbiornik (N.codec) z kluczem v3 ----------
clock = [1_000_000]
def codecs(v3=True, key=b"k" * 32):
    a = Codec(key, token=lambda: clock[0], now_ms=lambda: clock[0])
    b = Codec(key, token=lambda: clock[0], now_ms=lambda: clock[0])
    a.v3 = b.v3 = v3
    return a, b
tx, N.codec = codecs()
S.peer = ("127.0.0.1", 5005)
wire: list[bytes] = []
N.send_peer = lambda pkt: wire.append(pkt)
N.send_repeat = lambda pkt, n: [wire.append(pkt) for _ in range(n)]

def requests():
    out = []
    for pkt in wire:
        p = codecs()[0].parse(pkt)          # nowy kodek na każdą kopię: filtr powtórzeń nie odrzuci kopii
        check(p is not None and p[0] == T_CFGSET, "pakiet na drucie to cfgset, podpisany i świeży")
        out.append(BODY[T_CFGSET].unpack_from(pkt, HEADER_LEN))
    wire.clear()
    return out

def cfgval(name, value, base, remote=False, result=CFGR_OK):
    spec = cfgspec.SPECS[cfgspec.BY_KEY[name]]
    data = tx.pack(T_CFGVAL, 255, cfgspec.BY_KEY[name], result, 7, CFGF_REMOTE if remote else 0,
                   cfgspec.to_wire(spec, value), cfgspec.to_wire(spec, base))
    p = N.codec.parse(data)
    N._on_control(p[0], p[1], p[2], data, S.peer, time.monotonic(), 0)

check(SC.can_send(), "klucz i v3: można wysyłać")
SC.set_value("max_pads", 3)
req = requests()
check(len(req) == 3 and req[0][0] == CFG_SET and req[0][1] == cfgspec.BY_KEY["max_pads"] and req[0][3] == 3.0,
      "zmiana = 3 kopie tego samego cfgset z wartością")
check(len({r[2] for r in req}) == 1, "kopie mają to samo odniesienie (nadajnik wykona raz)")
SC.set_value("rumble", "hidraw")
check(requests()[0][3] == float(cfgspec.RUMBLE_CHOICES.index("hidraw")), "wybór idzie jako indeks")
try:
    SC.set_value("max_pads", 99)
    check(False, "zła wartość odrzucona przed wysłaniem")
except ValueError:
    check(not wire, "zła wartość odrzucona przed wysłaniem, nic nie poszło")
SC.reset("max_pads")
check(requests()[0][:2] == (CFG_RESET, cfgspec.BY_KEY["max_pads"]), "powrót do pliku = cfgset RESET z numerem ustawienia")
SC.reset()
check(requests()[0][:2] == (CFG_RESET, CFG_ALL), "powrót wszystkiego = RESET dla CFG_ALL")
SC.request_all()
check(requests()[0][:2] == (CFG_GET, CFG_ALL), "odczyt = GET dla CFG_ALL")

# ---------- bramka: bez klucza albo bez v3 nic nie wychodzi ----------
for label, (v3, key) in {"bez v3": (False, b"k" * 32), "bez klucza": (False, b"")}.items():
    _t, N.codec = codecs(v3, key)
    check(not SC.can_send(), f"{label}: can_send = False")
    SC.set_value("max_pads", 3); SC.request_all(); SC.reset("max_pads")
    check(not wire, f"{label}: nic nie wysłano")
tx, N.codec = codecs()

# ---------- cfgval trafia do tabeli ----------
S.sender_cfg.clear(); S.sender_cfg_result = None
gen = S.sender_cfg_gen
cfgval("max_pads", 3, 2, remote=True)
row = S.sender_cfg["max_pads"]
check((row.value, row.base, row.remote) == (3, 2, True) and S.sender_cfg_gen == gen + 1, "cfgval: wartość, plik, flaga zmiany")
check(S.sender_cfg_result == CFGR_OK, "odpowiedź dla ustawienia = nadajnik słucha")
cfgval("speaker_opus_mode", "celt", "hybrid")
check(S.sender_cfg["speaker_opus_mode"].value == "celt" and not S.sender_cfg["speaker_opus_mode"].remote, "wybór wraca jako tekst")
data = tx.pack(T_CFGVAL, 255, CFG_ALL, CFGR_NO_KEY, 7, 0, 0.0, 0.0)
p = N.codec.parse(data)
N._on_control(p[0], p[1], p[2], data, S.peer, time.monotonic(), 0)
check(S.sender_cfg_result == CFGR_NO_KEY, "odmowa całego żądania zapamiętana (okno pokaże powód)")

# ---------- pełny odczyt, potem jedno uzgodnienie ----------
S.sender_cfg.clear(); S.sender_cfg_result = None
rx_settings.apply(rx_settings.Settings(sender=rx_settings.SenderConfig(values={"max_pads": 4, "tap_click": False})))
wire.clear()
watch = SC.Watch()
watch.online()
watch.tick(100.0)
check(requests()[0][:2] == (CFG_GET, CFG_ALL), "nadajnik online: odczyt wszystkich ustawień")
for k, spec in enumerate(cfgspec.SPECS):
    base = spec.choices[0] if spec.kind == cfgspec.KIND_CHOICE else (spec.lo if spec.kind != cfgspec.KIND_BOOL else True)
    cfgval(spec.key, base, base)
check(SC.complete(), "po odpowiedziach na wszystkie ustawienia odczyt jest kompletny")
watch.tick(100.1)
sent_now = requests()
names = sorted(cfgspec.SPECS[r[1]].key for r in sent_now[::3])
check(len(sent_now) == 6 and names == ["max_pads", "tap_click"] and all(r[0] == CFG_SET for r in sent_now),
      "uzgodnienie: tylko to, czego nadajnik nie ma (2 ustawienia x 3 kopie)")
watch.tick(200.0)
check(not wire, "uzgodnienie tylko raz")

# ---------- ponowienie niepełnego odczytu i brak ponowień po odmowie ----------
S.sender_cfg.clear(); S.sender_cfg_result = None
watch.online(); watch.tick(300.0); requests()
cfgval("max_pads", 2, 2)
watch.tick(300.5)
check(not wire, "przed upływem RETRY_S nie pytamy ponownie")
watch.tick(300.0 + SC.RETRY_S + 0.1)
check(requests()[0][:2] == (CFG_GET, CFG_ALL), "niekompletny odczyt: pytamy ponownie")
for n in range(SC.MAX_TRIES + 2):
    watch.tick(400.0 + n * 10)
wire.clear()
watch.tick(900.0)
check(not wire, "po MAX_TRIES koniec ponawiania")
S.sender_cfg.clear(); S.sender_cfg_result = None
watch.online(); watch.tick(1000.0); wire.clear()
S.sender_cfg_result = CFGR_NO_KEY
watch.tick(1100.0)
check(not wire and not watch.pending, "po odmowie nadajnika nie ponawiamy")

# ---------- zmiana w ustawieniach idzie do nadajnika (listener rx_settings) ----------
rx_settings.apply(rx_settings.Settings())
wire.clear()
rx_settings.apply(rx_settings.Settings(sender=rx_settings.SenderConfig(values={"gestures": False})))
r = requests()
check(len(r) == 3 and r[0][:2] == (CFG_SET, cfgspec.BY_KEY["gestures"]) and r[0][3] == 0.0, "zapis ustawień z nową wartością = SET")
rx_settings.apply(rx_settings.Settings(sender=rx_settings.SenderConfig(values={"gestures": False})))
check(not wire, "ta sama sekcja drugi raz: nic nie idzie")
rx_settings.apply(rx_settings.Settings())
check(requests()[0][:2] == (CFG_RESET, cfgspec.BY_KEY["gestures"]), "usunięty wpis = RESET tego ustawienia")
rx_settings.apply(rx_settings.Settings(mouse=rx_settings.MouseConfig(sens=0.7)))
check(not wire, "zmiana innej sekcji nie rusza nadajnika")

sys.exit(1 if fails else 0)
