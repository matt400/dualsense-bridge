"""Receiver: efekty spustów - tryby w profilu (opór, klik, drgania), wibracja z gry na spustach, dziedziczenie
z profilu pada w profilu gry, trig2 / trig wg wersji nadajnika, funkcja mastera "triggers", edytor."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (zaślepki Windows, TMP, store, sent)
import rx_net
import rx_settings
import rx_status as ST
from proto import (BODY, CAP_TRIG_FX, RFX_ON, T_TRIG, T_TRIG2, TFX_CLICK, TFX_OFF, TFX_RES, TFX_VIB, TRIG2_OFF)
from rx_buttons import normalize_profile
from rx_editor import Editor
from rx_settings import MasterConfig, MouseConfig, Settings
from rx_shape import legacy_values, needs_fx, normalize_triggers, trigger_values

ok = lambda m: print("OK  ", m)

# ---------- normalizacja ----------
t = normalize_triggers({"l2": {"start": 3, "force": 6}, "r2": {"mode": "click", "pos": 9, "force": 0, "start": 4},
                        "rumble": {"on": True, "gain": 999, "freq": 0}, "x": {"mode": "res"}})
assert t == {"l2": {"mode": "res", "start": 3, "force": 6}, "r2": {"mode": "click", "pos": 7, "force": 1},
             "rumble": {"on": True, "gain": 200, "freq": 1}}, t
assert normalize_triggers({"l2": {"mode": "vib"}, "r2": {"mode": "boom"}}) == \
    {"l2": {"mode": "vib", "start": 0, "amp": 4, "freq": 30}}, "domyślne parametry trybu, nieznany tryb odrzucony"
assert normalize_triggers({"l2": {"mode": "off", "force": 5}, "r2": {"force": 0}}) == {"l2": {"mode": "off"}}
assert normalize_triggers({"rumble": {"on": "yes"}})["rumble"]["on"] is False, "on tylko jako true"
ok("normalizacja: stary zapis oporu, tryby i ich parametry w zakresach, wibracja z gry, jawne off")

v = trigger_values(t)
assert v == (TFX_RES, 3, 6, 0, TFX_CLICK, 7, 1, 0, RFX_ON, 200, 1, 0), v
assert trigger_values({"r2": {"mode": "vib", "start": 2, "amp": 5, "freq": 90}, "rumble": {"on": False, "gain": 50, "freq": 9}}) \
    == (TFX_OFF, 0, 0, 0, TFX_VIB, 2, 5, 90, 0, 0, 0, 0), "wibracja z gry wyłączona = zera (bez odświeżania)"
assert trigger_values({}) == TRIG2_OFF and legacy_values(v) == (3, 6, 0, 0) and legacy_values(TRIG2_OFF) == (0, 0, 0, 0)
assert needs_fx(t) and not needs_fx({"l2": {"mode": "res", "start": 0, "force": 3}}) and not needs_fx({})
assert needs_fx({"rumble": {"on": True, "gain": 100, "freq": 40}})
ok("wartości trig2, stary trig (sam opór), czy potrzeba nowego nadajnika")

# ---------- profil gry dziedziczy efekty spustów z profilu pada ----------
A = "aa:aa:aa:aa:aa:01"
S.pad_id[0] = A
store.save(A, normalize_profile({"buttons": {"cross": "circle"},
                                 "triggers": {"l2": {"mode": "res", "start": 2, "force": 5},
                                              "rumble": {"on": True, "gain": 80, "freq": 40}}}))
store.save(f"{A}@race.exe", normalize_profile({"triggers": {"r2": {"mode": "click", "pos": 4, "force": 7}}}))
store.save(f"{A}@quiet.exe", normalize_profile({"triggers": {"l2": {"mode": "off"}, "rumble": {"on": False}}}))
S.reload_profile(0)
assert S.remap_maps[0]["triggers"]["l2"]["mode"] == "res"
S.set_game("race.exe")
tr = S.remap_maps[0]["triggers"]
assert S.active[0] == f"{A}@race.exe" and tr["l2"]["mode"] == "res" and tr["r2"]["mode"] == "click" and tr["rumble"]["on"]
assert S.remap_maps[0]["buttons"] == {}, "reszta profilu gry dalej zastępuje profil pada"
S.set_game("quiet.exe")
assert trigger_values(S.remap_maps[0]["triggers"]) == TRIG2_OFF, "jawne off w profilu gry wygrywa"
S.set_game(None)
ok("profil gry: efekty spustów z profilu pada, gdy ich nie ustawia; off w grze wyłącza; przyciski bez dziedziczenia")

# ---------- wysyłka z pętli feedbacku: trig2 dla v8, trig dla starszego ----------
trig = []
send = lambda i, vals, n: trig.append((i, vals, n))
S.alive[0] = True
F.trig_sent[0] = None
S.sender_info[0] = (8, 15, 0)
F.triggers_tick(0, 100.0, send)
assert trig == [(0, trigger_values(S.remap_maps[0]["triggers"]), F.TRIG_REPEAT)] and len(trig[0][1]) == 12, trig
S.sender_info[0] = (7, 7, 0)
F.triggers_tick(0, 100.5, send)
assert trig[-1] == (0, (2, 5, 0, 0), F.TRIG_REPEAT), "starszy nadajnik: sam opór starym pakietem"
S.sender_info[0] = (8, 7, 0)
assert not F.sender_fx(0), "v8 bez raportu HID (bez CAP_TRIG_FX) - stary pakiet"
S.sender_info[0] = (8, 3 | CAP_TRIG_FX, 0)
assert F.sender_fx(0)
S.sender_info[0] = (8, 15, 0)
ok("feedback: trig2 do nadajnika v8 z CAP_TRIG_FX, trig (sam opór) do starszego")

# ---------- tylko master ----------
B = "bb:bb:bb:bb:bb:02"
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=B, when_absent="nobody", only_master=("triggers",))))
F.triggers_tick(0, 101.0, send)
assert trig[-1] == (0, TRIG2_OFF, F.TRIG_REPEAT), "nie-master: efekty zdjęte"
assert "master" in ST.triggers_status(0)[0]
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=A, only_master=("triggers",))))
F.triggers_tick(0, 101.5, send)
assert trig[-1][1][0] == TFX_RES, "master ma efekty"
rx_settings.apply(Settings(MouseConfig(), MasterConfig(pad=B, only_master=("console",))))
F.triggers_tick(0, 102.0, send)
assert trig[-1][1][0] == TFX_RES, "efekty spustów niezastrzeżone - każdy pad"
rx_settings.apply(Settings())
ok("funkcja mastera: efekty spustów tylko na padzie master (pozostałe = Off)")

S.sender_info[0] = (7, 7, 0)
assert "newer" in ST.triggers_status(0, fx=True)[0] or "nowszego" in ST.triggers_status(0, fx=True)[0]
assert ST.triggers_status(0, fx=False) == ("", "")
S.sender_info[0] = (8, 15, 0)
assert ST.triggers_status(0, fx=True) == ("", "")
ok("status: klik / drgania / wibracja z gry wymagają nadajnika v8")

# ---------- rx_net: trig2 / trig ----------
sent.clear()
vals = (TFX_CLICK, 5, 7, 0, TFX_VIB, 0, 3, 30, RFX_ON, 120, 40, 0)
rx_net.send_triggers(1, vals, 2)
rx_net.send_triggers(0, (0, 0, 3, 6))
assert [(x[0], x[1]) for x in sent] == [(T_TRIG2, 1), (T_TRIG2, 1), (T_TRIG, 0)], sent
assert BODY[T_TRIG2].unpack_from(sent[0][3], 4) == vals
ok("rx_net.send_triggers: 12 wartości = trig2, 4 = trig")

# ---------- edytor: tryby, dziedziczenie, pamięć suwaków ----------
ed = Editor(0)
ed.select(A)
assert ed.trigger_fx("l2")["mode"] == "res" and ed.trigger_fx("r2")["mode"] == "off" and ed.rumble_fx()["on"] is True
ed.set_trigger_fx("r2", mode="vib")
ed.set_trigger_fx("r2", amp=7, freq=120)
assert ed.prof["triggers"]["r2"] == {"mode": "vib", "start": 0, "amp": 7, "freq": 120}
ed.set_trigger_fx("r2", mode="off")
assert "r2" not in ed.prof["triggers"], "profil pada: off = brak wpisu"
ed.set_trigger_fx("r2", mode="vib")
assert ed.prof["triggers"]["r2"]["amp"] == 7, "zmiana trybu nie gubi wartości suwaków"
ed.set_rumble_fx(on=False)
assert "rumble" not in ed.prof["triggers"]
ed.set_rumble_fx(on=True, gain=150)
assert ed.prof["triggers"]["rumble"] == {"on": True, "gain": 150, "freq": 40}
ed.select(f"{A}@race.exe")
assert ed.trigger_fx("l2")["mode"] == "inherit" and ed.rumble_fx()["on"] is None
ed.set_trigger_fx("l2", mode="off"); ed.set_rumble_fx(on=False)
assert ed.prof["triggers"]["l2"] == {"mode": "off"} and ed.prof["triggers"]["rumble"]["on"] is False
ed.set_trigger_fx("l2", mode="inherit"); ed.set_rumble_fx(on=None)
assert "l2" not in ed.prof["triggers"] and "rumble" not in ed.prof["triggers"]
assert normalize_profile(ed.prof) == ed.prof, "edytor tworzy znormalizowane wpisy"
names = [t for t, _u in ed.changes()]
assert any(n.startswith(("Click R2", "Klik R2")) for n in names), names
ok("edytor: tryby i parametry, off / jak w profilu pada w profilu gry, pamięć suwaków, lista zmian")
print("TRIGFX OK")
