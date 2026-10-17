"""Receiver: regresje z dwunastego przeglądu - kolor lightbara z gry / programu na PC (np. Steam) zasłaniał na stałe
kolor z ustawień; opcja "Gra może zmienić kolor" per pad, wpis w dzienniku przy pierwszym kolorze z PC, zapis opcji.
    python3 tests/test_hunt12.py receiver"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_events as ev
import rx_settings as R
from proto import T_IDENT

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


A = "aa:aa:aa:aa:aa:01"
feed(T_IDENT, 0, A.encode()); state(0)
now = time.monotonic

# ---------- 1. kolor z gry: domyślnie ma pierwszeństwo, "zawsze mój" go pomija ----------
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True)})))
check(F._target(0, now())[0][2:] == (255, 0, 255), "bez koloru z gry - kolor z ustawień")
F.game(0, rgb=(0, 0, 255))
check(F._target(0, now())[0][2:] == (0, 0, 255), "gra ustawiła kolor - domyślnie ma pierwszeństwo")
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True, game=False)})))
val, ours, _b = F._target(0, now())
check(val[2:] == (255, 0, 255) and ours, "game = False - zawsze kolor z ustawień, mimo koloru z gry")
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {"*": R.PadLight("battery", "", True, game=False)})))
check(F._target(0, now())[0][2:] == (0, 220, 60), "game = False z wpisu domyślnego (*) - kolor wg baterii")
F.reset(0)

# ---------- 2. pierwszy kolor z PC trafia do dziennika (raz na wirtualny pad) ----------
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True)})))
seq = ev.since(0)[-1].seq if ev.since(0) else 0
F.game(0, rgb=(1, 2, 3)); F.game(0, rgb=(4, 5, 6))
check(F.fb[0]["note"], "kolor z gry oznaczony do wpisu")
F._note_game_color(0)
new = [e.text for e in ev.since(seq)]
check(len(new) == 1 and "#040506" in new[0] and "Gra może zmienić kolor" in new[0], f"wpis w dzienniku: {new}")
F.game(0, rgb=(7, 8, 9))
check(not F.fb[0]["note"], "kolejne kolory z gry bez nowych wpisów")
F.reset(0)
check(not F.fb[0]["note"] and not F.fb[0]["rgb"], "nowy wirtualny pad - od nowa")
cb = F.make_cb(0)
cb(None, None, 0, 0, type("Led", (), {"Red": 9, "Green": 9, "Blue": 9})(), None)
check(F.fb[0]["note"], "ViGEm: kolor z gry też oznaczony")
F.reset(0)

# ---------- 3. zapis / odczyt opcji ----------
path = os.path.join(TMP, "s12.json")
R.save(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True, game=False)})), path)
check(json.load(open(path))["lightbar"]["pads"][A]["game"] is False, "game zapisane w settings.json")
check(R.load(path).lightbar.pads[A].game is False, "game odczytane")
json.dump({"lightbar": {"pads": {A: {"mode": "solid", "color": "#ff00ff"}}}}, open(path, "w"))
check(R.load(path).lightbar.pads[A].game is True, "stary plik bez game - gra może zmienić kolor (jak dotąd)")

print("HUNT12", "OK" if not fails else f"BŁĘDY: {len(fails)}")
if fails:
    sys.exit(1)
