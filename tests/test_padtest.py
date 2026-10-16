"""Receiver, widok Test bez okna: pomiar dryfu (rx_drift), korekcja dryfu per pad we wszystkich profilach
(rx_padcal), blokada wyjścia do gry (neutralny raport DS4 / DSU, bez myszy i skrótów) i wynik kalibracji gyro."""
import json, math, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_drift as D
import rx_padcal
import rx_profiles as P
import rx_status as ST
from proto import (BODY, CAL_MOVED, CAL_OK, CALF_STORED, CAP_CALIB, CMD_GYRO_CAL, CMD_RAW_STICKS, RUMBLE_EVDEV, T_CALRES,
                   T_CLICK, T_CMD, T_IDENT, T_MOUSE, T_TOUCH)
from rx_buttons import normalize_profile
ok = lambda m: print("OK  ", m)
A = "aa:aa:aa:aa:aa:01"

# ---------- propozycja strefy martwej ----------
assert D.propose(0.0) == D.MIN_DZ and D.propose(0.04) == 0.05 and D.propose(0.048) == 0.06 and D.propose(0.9) == 0.5
assert D.unit(0) == -1 and D.unit(255) == 1 and D.unit(128) == 0
r = D.analyze([(128, 128, 128, 128), (133, 128, 128, 124), (131, 126, 128, 125)])
assert r.left.peak == round(5 / 127, 4) and r.right.peak == round(4 / 128, 4) and not r.moved and r.samples == 3
assert r.left.dz == D.propose(5 / 127) == 0.05, r
assert D.analyze([(128, 128, 128, 128), (200, 128, 128, 128)]).moved, "wychylenie ponad 30% = ruszana gałka"
ok("dryf: szczyt w spoczynku -> strefa martwa ×1.25 w górę do 0.01, min. 3%, max 50%; ruch wykryty")

# ---------- przebieg pomiaru: polecenia nadajnika i próbki z sieci ----------
feed(T_IDENT, 0, A.encode()); state(0)
calls = []
run = D.DriftRun(0, 10.0, lambda s, secs: calls.append((s, secs)))
assert calls == [(0, D.RAW_S)] and run.tick(10.1) is None and S.probe[0] is None, "najpierw nadajnik, potem próbki"
state(0, sticks=(131, 128, 128, 128))
assert run.tick(10.0 + D.SETTLE_S) is None and S.probe[0] == [(131, 128, 128, 128)], "stan z chwili startu się liczy"
for lx in (133, 130, 128, 134):
    state(0, sticks=(lx, 127, 128, 129))
assert run.tick(10.0 + D.SETTLE_S + 1) is None
res = run.tick(10.0 + D.SETTLE_S + D.MEASURE_S)
assert res.samples == 5 and res.left.peak == round(math.hypot(6 / 127, 1 / 128), 4), res
assert res.left.dz == 0.06 and res.right.dz == D.MIN_DZ and calls[-1] == (0, 0) and S.probe[0] is None
state(0, sticks=(140, 128, 128, 128))
assert S.probe[0] is None, "po pomiarze próbki już nie są zbierane"
run = D.DriftRun(0, 20.0, lambda s, secs: calls.append((s, secs)))
run.tick(20.0 + D.SETTLE_S)
run.cancel()
assert calls[-1] == (0, 0) and S.probe[0] is None and run.tick(30.0) is None, "przerwanie oddaje strefę martwą nadajnikowi"
t0 = 10000 / 7                                    # (t0 + 0.3) - t0 < 0.3 w arytmetyce float - granica musi się liczyć
assert (t0 + D.SETTLE_S) - t0 < D.SETTLE_S and (t0 + D.SETTLE_S + D.MEASURE_S) - t0 < D.SETTLE_S + D.MEASURE_S
run = D.DriftRun(0, t0)
assert run.tick(t0 + D.SETTLE_S) is None and run.phase == "measure", "dokładnie po SETTLE_S - pomiar trwa"
assert run.tick(t0 + D.SETTLE_S + D.MEASURE_S) is not None and run.phase == "done", "dokładnie po czasie - wynik"
state(0)
ok("pomiar: gałki bez strefy nadajnika -> próbki z każdego pakietu przez 3 s -> strefa wraca; przerwanie sprząta; "
   "granice czasu odporne na zaokrąglenia float")

# ---------- korekcja dryfu: per pad, każdy profil, gałka fizyczna ----------
rx_padcal.reload()
ok_, msg = P.set_drift(0, {"left": 0.07, "right": 0.0})
assert ok_ and json.load(open(rx_padcal.path()))["pads"] == {A: {"drift": {"left": 0.07}}}
assert S.remap_maps[0]["tuning"]["left"]["dz"] == 0.07 and "right" not in S.remap_maps[0]["tuning"]
state(0, sticks=(134, 128, 128, 128))
assert (S.states[0]["lx"], S.states[0]["ly"]) == (128, 128), "dryf 5% pod strefą 7% - gra widzi środek"
state(0, sticks=(255, 128, 128, 128))
assert S.states[0]["lx"] == 255, "pełne wychylenie dalej pełne"
assert store.get(A)["tuning"] == {}, "profil pada w remap.json bez zmian - korekcja jest osobno"
store.save(f"{A}@game.exe", {"tuning": {"left": {"dz": 0.12}}, "sticks": {"right": {"src": "left"}}})
prof = S.effective_profile(0, f"{A}@game.exe")
assert prof["tuning"]["left"]["dz"] == 0.12, "większa strefa z profilu gry zostaje"
assert prof["tuning"]["right"]["dz"] == 0.07 and prof["tuning"]["right"]["outer"] == 1.0, "prawe wyjście z lewej gałki"
store.save(f"{A}@other.exe", {"buttons": {"cross": "circle"}})
assert S.effective_profile(0, f"{A}@other.exe")["tuning"]["left"]["dz"] == 0.07, "profil gry bez strojenia też"
p = rx_padcal.apply_drift(normalize_profile({"tuning": {"left": {"dz": 0.0, "outer": 0.5}}}), {"left": 0.48})
assert p["tuning"]["left"]["outer"] == 0.53, "zasięg zawsze za strefą martwą"
assert rx_padcal.apply_drift(normalize_profile({"sticks": {"left": {"src": "none"}}}), {"left": 0.1})["tuning"] == {}
rx_padcal.reload()
assert P.drift(0) == {"left": 0.07}, "korekcja wczytana z pliku po restarcie"
P.set_drift(0, {"left": 0})
assert P.drift(0) == {} and json.load(open(rx_padcal.path()))["pads"] == {} and S.remap_maps[0]["tuning"] == {}
open(rx_padcal.path(), "w").write("[zły")
rx_padcal.reload()
assert P.drift(0) == {}, "zepsuty plik = bez korekcji, bez wyjątku"
os.remove(rx_padcal.path()); rx_padcal.reload()
store.delete(f"{A}@game.exe"); store.delete(f"{A}@other.exe")
ok("korekcja dryfu: max(profil, dryf) we wszystkich profilach pada, za zamianą gałek, zapis od razu, czyszczenie")

# ---------- blokada wyjścia do gry ----------
H.init(C.NUM_PADS)
state(0, mask=BIT[304], lt=200, sticks=(10, 20, 30, 40), gyro=(500, 600, 700))
feed(T_TOUCH, 0, 1, 3, 900, 400, 0, 0, 0, 0)
assert P.block(0, True) and not P.block(0, True)
with S.lock:
    out = S.output(0)
assert out["mask"] == 0 and out["lt"] == 0 and (out["lx"], out["ly"], out["rx"], out["ry"]) == (128,) * 4
assert out["gyro"] == (0, 0, 0) and out["touch"] == ((0, 0, 0, 0), (0, 0, 0, 0)) and out["batt_ds4"] == S.states[0]["batt_ds4"]
assert S.states[0]["rawst"] == (10, 20, 30, 40) and S.states[0]["touch"][0][2] == 900, "okno widzi pada fizycznie"
store.set_live(A, {"gyro": {"mode": "mouse"}}); S.reload_profile(0)
state(0, gyro=(0, 9000, 0))
assert S.states[0]["gyro_vel"] is None and not S.states[0]["gyro_on"], "gyro-mysz nie rusza kursorem"
S.mouse_mode[0] = True
calls.clear()
feed(T_MOUSE, 0, 30, 5, 0); feed(T_CLICK, 0, 1)
assert not [c for c in calls if c[0] in ("m", "c")] and S.states[0]["rtp"] == 1, "mysz touchpada stoi, klik widać"
feed(T_CLICK, 0, 0)
def ps_right():                                                     # PS + d-pad → = następny profil (skrót)
    state(0, mask=BIT[316]); state(0, mask=BIT[316], hat=(1, 0)); state(0, mask=BIT[316]); state(0)
    time.sleep(0.1)                                                  # skróty działają w osobnym wątku
ps_right()
assert S.override[0] is None, "skróty pada nie działają"
store.set_live(A, {}); S.reload_profile(0)
S.mouse_mode[0] = False
assert P.block(0, False)
state(0, mask=BIT[304], sticks=(10, 20, 30, 40))
with S.lock:
    out = S.output(0)
assert out is S.states[0] and out["mask"] and out["lx"] == 10, "po teście gra znowu dostaje pada"
state(0)
ps_right()
assert S.override[0] == A, "bez blokady ten sam skrót działa (test nie jest pusty)"
S.override[0] = None; S.reload_profile(0)
ok("blokada: DS4 / DSU neutralne (bateria prawdziwa), mysz, gyro-mysz i skróty stoją, okno widzi stan fizyczny")

# ---------- kalibracja żyroskopu: polecenia i wynik ----------
sent.clear()
S.sender_info[0] = (8, 3, 0)
assert not P.calibrate_gyro(0, 3) and not sent, "stary nadajnik - bez polecenia"
assert ST.rumble_status(0)[0].startswith("wibracja: ") and "stary" not in ST.rumble_status(0)[0]
S.sender_info[0] = (9, CAP_CALIB, 0)
assert ST.rumble_status(0)[1] == "bad", "bit CAP_CALIB to nie sposób wibracji"
S.sender_info[0] = (9, RUMBLE_EVDEV | CAP_CALIB, 0)
assert ST.rumble_status(0)[1] == "" and P.sender_calib(0)
assert P.calibrate_gyro(0, 30)
cmds = [BODY[T_CMD].unpack_from(pkt, 4) for t, i, _sq, pkt in sent if t == T_CMD]
assert cmds == [(CMD_GYRO_CAL, 10)] * 3, cmds
sent.clear(); P.raw_sticks(0, 6); P.query_gyro_cal(0)
assert [BODY[T_CMD].unpack_from(p, 4) for *_x, p in sent] == [(CMD_RAW_STICKS, 6)] * 3 + [(CMD_GYRO_CAL, 0)] * 3
import rx_events as ev
ev.clear()
for _n in range(3):
    feed(T_CALRES, 0, CAL_OK, CALF_STORED, 10, 20, 30, 10, 20, 30, 1_700_000_000)
g = S.gyro_cal[0]
assert g[:5] == (CAL_OK, CALF_STORED, (10, 20, 30), (10, 20, 30), 1_700_000_000)
assert sum("skalibrowany" in e.text for e in ev.since(0)) == 1, "powtórki odpowiedzi - jeden wpis w dzienniku"
feed(T_CALRES, 0, CAL_MOVED, 0, 0, 0, 0, 0, 0, 0, 0)
assert any("ruszał" in e.text for e in ev.since(0))
N.clear_slot(0, forget_id=True)
assert S.gyro_cal[0] is None, "inny pad w slocie - stan kalibracji od nowa"
ok("kalibracja gyro: polecenie tylko do nadajnika v9+, limit 10 s, wynik w stanie slotu i dzienniku (bez powtórek)")

# ---------- korekcja dryfu nie jest zmianą profilu ----------
feed(T_IDENT, 0, A.encode()); state(0)
P.set_drift(0, {"left": 0.08})
assert S.profile_changes(0) == 0 and S.remap_maps[0]["tuning"]["left"]["dz"] == 0.08
P.set_drift(0, {"left": 0})
ok("karta pada: korekcja dryfu nie liczy się jako zmiana profilu")

# ---------- widok Test: skróty padów stoją, PS + △ mastera przez 3 s wychodzi ----------
import rx_master, rx_settings, rx_testmode as TM
from rx_settings import MasterConfig, MouseConfig, Settings
B_ = "bb:bb:bb:bb:bb:02"
PS, TRI, RIGHT = BIT[316], BIT[307], (1, 0)
settings = lambda **m: rx_settings.apply(Settings(MouseConfig(), MasterConfig(**m)))
def held(i, mask):                                   # przytrzymanie: zmiana przycisków, potem tylko upływ czasu
    state(i, mask=mask)
    return time.monotonic()
def out(i):
    with S.lock:
        return S.output(i)["mask"]
escapes = []
TM.on_escape = lambda: escapes.append(1)
feed(T_IDENT, 0, A.encode()); feed(T_IDENT, 1, B_.encode()); state(0); state(1)
settings(pad=A)                                      # slot 0 = master, slot 1 = testowany pad
store.save(f"{A}@game.exe", {"buttons": {"cross": "circle"}})
TM.set_active(True); P.block(1, True)
state(0, mask=PS); state(0, mask=PS, hat=RIGHT); state(0, mask=PS); state(0); time.sleep(0.1)
assert S.override[0] is None, "widok Test: PS + → mastera nie zmienia profilu"
S.mouse_mode[0] = False
state(0, mask=PS); feed(T_CLICK, 0, 1); feed(T_CLICK, 0, 0); state(0)
assert not S.mouse_mode[0], "widok Test: PS + klik touchpada nie przełącza myszy"
t0 = held(1, PS | TRI)
assert not TM.tick(t0 + TM.ESCAPE_S + 1) and TM.active and S.states[1]["blocked"], "PS + △ nie-mastera nic nie robi"
state(1)
t0 = held(0, PS | TRI)
assert TM.held_s(0, t0 + 1) >= 1 and not TM.tick(t0 + TM.ESCAPE_S - 0.1) and TM.active, "przed 3 s - dalej test"
state(0, mask=PS)                                    # puszczony △ - licznik od zera
t0 = held(0, PS | TRI)
assert TM.held_s(0, t0) < 0.5 and not TM.tick(t0 + TM.ESCAPE_S - 0.5), "nowe przytrzymanie liczone od nowa"
assert TM.tick(t0 + TM.ESCAPE_S) and not TM.active and TM.escaped and escapes == [1], "3 s bez ruchu pada - wyjście"
assert not S.states[1]["blocked"], "blokada testowanego pada zdjęta"
assert out(0) == 0, "neutralny raport: PS + △ trzymane przy wyjściu nie trafiają do gry"
state(0, mask=PS | TRI, sticks=(130, 128, 128, 128))
assert out(0) == 0, "dalej trzymane - dalej połknięte"
state(0, mask=PS); state(0); state(0, mask=TRI)
assert out(0) == TRI, "po puszczeniu △ znowu idzie do gry"
state(0); time.sleep(0.15)
assert out(0) == 0 and not [c for c in calls if c[0] == "k"], "puszczone PS z wyjścia nie daje impulsu PS ani Game Bar"
state(0, mask=PS); state(0, mask=PS, hat=RIGHT); state(0, mask=PS); state(0); time.sleep(0.1)
assert S.override[0] == f"{A}@game.exe", "po teście skróty znowu działają"
S.override[0] = None; S.reload_profile(0)
ok("Test: skróty żadnego pada nie działają; PS + △ mastera 3 s (licznik od zera po puszczeniu, bez ruchu pada) "
   "wychodzi, zdejmuje blokadę i połyka trzymane wejścia; PS + △ innego pada nic nie robi")

# testowany pad jest masterem: wyjście z niego, przez wątek sieci (zmiana stanu po 3 s)
escapes.clear(); TM.rearm(); TM.set_active(True); P.block(0, True)
state(0, mask=PS | TRI)
TM._since[0] -= TM.ESCAPE_S                          # przytrzymanie zaczęte 3 s temu
state(0, mask=PS | TRI, lt=200)                      # dowolna zmiana na padzie kończy pomiar w wątku sieci
assert escapes == [1] and not S.states[0]["blocked"] and not TM.active
assert out(0) == 0, "przyciski trzymane na testowanym padzie połknięte (gałki i spusty analogowo na żywo)"
state(0)
ok("Test: master testowany sam siebie - wyjście z zablokowanego pada, wejścia neutralne do puszczenia")

# bez mastera w ustawieniach - każdy pad; set_active czyści licznik; ponowne wejście w test
settings(pad="")
escapes.clear(); TM.rearm(); TM.set_active(True)
t0 = held(1, PS | TRI)
TM.set_active(False); TM.set_active(True)
assert not TM.tick(t0 + 10) and TM.active, "zamknięcie widoku przerywa przytrzymanie"
state(1); t0 = held(1, PS | TRI)
assert TM.tick(t0 + TM.ESCAPE_S) and escapes == [1], "bez mastera wychodzi dowolny pad"
state(1)
settings(pad="cc:cc:cc:cc:cc:03", when_absent="nobody")
TM.rearm(); TM.set_active(True); t0 = held(0, PS | TRI)
assert not TM.tick(t0 + 10), "master ustawiony, ale nieobecny + nobody - nikt"
TM.set_active(False); state(0); settings(pad="")
store.delete(f"{A}@game.exe")
ok("Test: bez mastera każdy pad może wyjść, nieobecny master wg when_absent, zamknięcie widoku zeruje licznik")
