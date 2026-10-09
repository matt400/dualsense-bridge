import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import rx_editor, rx_profiles as P, rx_status as ST
from proto import T_CMD

ok = lambda msg: print("OK  ", msg)
base = S.profile_key(0)
ed = rx_editor.Editor(0)
assert ed.key == base and not ed.modified() and ed.status()[0] == "ok"

# ---------- przyciski, gałki, gyro: zmiany i wartości domyślne znikają ----------
ed.set_button("cross", "circle"); ed.set_button("l3", None)
assert ed.prof["buttons"] == {"cross": "circle", "l3": None} and ed.target_of("cross") == "circle" and ed.target_of("l3") is None
ed.set_button("cross", "cross"); assert "cross" not in ed.prof["buttons"] and ed.target_of("cross") == "cross"
ed.set_stick("right", src="left", inv_x=True); assert ed.prof["sticks"]["right"]["src"] == "left"
ed.set_stick("right", src="right", inv_x=False); assert ed.prof["sticks"] == {}
ed.set_tuning("left", dz=0.12, curve=1.5); ed.set_tuning("r2", dz=30)
assert ed.tuning("left") == {"dz": 0.12, "outer": 1.0, "curve": 1.5, "anti": 0.0} and ed.tuning("r2") == {"dz": 30, "max": 255}
ed.set_tuning("r2", dz=0); assert "r2" not in ed.prof["tuning"]
ed.set_gyro(mode="stick", hold="l2", sens=1.75); assert ed.gyro()["hold"] == "l2"
ed.set_gyro(mode="off", hold="always", sens=1.0); assert ed.prof["gyro"] == {}
ed.set_gyro(mode="mouse")
texts = [t for t, _ in ed.changes()]
assert texts == ["L3 (stick) → disabled", "Left stick response: deadzone 12%, curve 1.50", "Gyro: mouse, always, sensitivity 1×"], texts
for _, undo in ed.changes()[:2]: undo()
assert [t for t, _ in ed.changes()] == ["Gyro: mouse, always, sensitivity 1×"]
ed.reset_all(); assert ed.changes() == []
ok("edytor: zmiany przycisków / gałek / spustów / gyro, domyślne znikają, lista zmian i cofanie")

# ---------- status, zastosowanie na żywo, zapis, przywracanie ----------
ed.set_button("cross", "circle")
assert ed.modified() and ed.status()[0] == "warn" and "not saved" in ed.status()[1]
assert ed.apply(False) == (True, "Applied (not saved)") and S.remap_maps[0]["buttons"] == {"cross": "circle"}
assert not store.in_file(base) and ed.status()[0] == "warn"
okk, msg = ed.apply(True); assert okk and "Saved" in msg and store.in_file(base)
assert json.load(open(C.REMAP_PATH))["profiles"][base]["buttons"] == {"cross": "circle"}
assert not ed.modified() and ed.status() == ("ok", "✓ Matches saved profile")
ed.set_button("square", "triangle"); ed.apply(False); ed.revert()
assert ed.prof["buttons"] == {"cross": "circle"} and not ed.modified()
ed.apply(False); assert S.remap_maps[0]["buttons"] == {"cross": "circle"}
ok("edytor: status, apply na żywo, zapis do pliku, revert")

# ---------- profile gier ----------
assert ed.new_game("nonsense name with / slash")[0] is False
okk, msg = ed.new_game("EldenRing"); key = base + "@eldenring.exe"
assert okk and ed.key == key and "save it" in msg and store.has(key) and not store.in_file(key)
assert ed.prof["buttons"] == {"cross": "circle"}, "kopia bieżących ustawień"
assert ed.status()[0] == "warn" and "not saved" in ed.status()[1]
assert S.active[0] == key, "wybrany profil działa od razu na padzie"
ed.set_gyro(mode="stick"); ed.apply(True); assert store.in_file(key) and ed.status()[0] == "ok"
okk, _ = ed.new_game("doom.exe", copy_current=False); assert okk and ed.prof["buttons"] == {}
assert ed.status() == ("warn", "● Game profile not saved yet"), "pusty profil gry też trzeba zapisać"
assert [n for _, n, _ in ed.profiles()] == ["Default (all games)", "doom.exe", "eldenring.exe"]
assert [u for _, _, u in ed.profiles()] == [False, True, False]
ed.select(base); assert ed.key == base and ed.prof["buttons"] == {"cross": "circle"} and S.active[0] == base
assert ed.info().startswith("In use right now.")
S.set_game("eldenring.exe")                                           # gra na pierwszym planie przejmuje profil
assert S.active[0] == key and ed.info().startswith("Used when no game profile matches") and "eldenring.exe" in ed.info()
ed.select(key); assert ed.info().startswith("In use right now.")
S.set_game(None); assert S.active[0] == base, "zmiana okna kasuje ręczny wybór profilu"
assert ed.info().startswith("Used automatically while eldenring.exe is the active window"), ed.info()
assert ed.delete_current() == (True, "Profile deleted") and not store.has(key) and ed.key == base
assert ed.delete_current()[0] is False, "profilu pada nie usuwamy"
ed.select("doom.exe"); assert ed.key == base, "nieistniejący klucz -> profil w użyciu"
ok("edytor: nowy profil gry (kopia / pusty), lista, wybór, usuwanie")

# ---------- zmiana klucza pada (nadeszło id) ----------
ed.set_button("square", "triangle")
S.pad_id[0] = "aa:bb:cc:dd:ee:01"
assert ed.sync() is True and ed.base == "aa:bb:cc:dd:ee:01" and ed.key == "aa:bb:cc:dd:ee:01" and ed.prof["buttons"] == {}
assert ed.sync() is False
S.pad_id[0] = None
ok("edytor: zmiana klucza pada po nadejściu id")

# ---------- rx_profiles ----------
assert P.key_for(0, None) == S.profile_key(0) and P.key_for(0, S.profile_key(0) + "@a.exe") == S.profile_key(0) + "@a.exe"
assert P.key_for(0, "inny@a.exe") is None and P.key_for(0, S.profile_key(0) + "@../x.exe") is None
assert P.game_key(0, "  Doom ") == S.profile_key(0) + "@doom.exe" and P.game_key(0, "a/b") is None and P.game_key(0, "") is None
assert P.slot_ok(0) and not P.slot_ok(True) and not P.slot_ok(99) and not P.slot_ok("0")
S.alive[0] = False; assert P.power_off(0) is False
S.alive[0] = True; sent.clear(); assert P.power_off(0) is True and sum(1 for x in sent if x[0] == T_CMD) == 3
state(0, mask=BIT[304], batt=50); lv = P.live(0)
assert lv["pressed"] == ["cross"] and lv["connected"] and lv["active"] == S.active[0]
ok("rx_profiles: klucze, nazwy gier, power, stan na żywo")

# ---------- rx_status ----------
now = time.monotonic(); S.alive[0] = True; S.alive[1] = False
S.info[0] = (1.2, 540, 500, now); S.link_loss = 0.0; S.pps[0] = 250.0
assert ST.link_text(0, now) == "ping 1.2 ms  ·  pad 540 Hz  ·  loss 0.0%  ·  250 pkt/s"
assert ST.link_lines(0, now) == ("ping 1.2 ms  ·  loss 0.0%", "pad 540 Hz  ·  250 pkt/s") and ST.link_level(0, now) == "ok"
assert ST.link_text(1, now) == "" and ST.link_lines(1, now) == ("", "")
S.info[0] = (7.0, 540, 500, now); assert ST.link_level(0, now) == "warn"
S.info[0] = (25.0, 540, 500, now); assert ST.link_level(0, now) == "bad"
S.info[0] = (1.0, 540, 500, now - 10); assert ST.link_level(0, now) == "ok" and "ping" not in ST.link_text(0, now)
S.link_loss = 2.0; assert ST.link_level(0, now) == "warn"; S.link_loss = None
assert "loss" not in ST.link_text(0, now)
assert [ST.battery_level(*a) for a in ((50, True, 15), (10, False, 15), (25, False, 15), (80, False, 15))] == ["charge", "bad", "warn", "ok"]
S.batt_info[0] = (10, 0); assert ST.is_low(0, 15)
S.batt_info[0] = (10, 1); assert not ST.is_low(0, 15)           # ładuje się
S.batt_info[0] = (255, 0); assert not ST.is_low(0, 15)          # nieznany
S.alive[1] = False; S.batt_info[1] = (5, 0); assert not ST.is_low(1, 15)
ok("rx_status: linie łącza, poziomy, bateria")
