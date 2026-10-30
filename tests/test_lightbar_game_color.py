"""Receiver: regresje z dwunastego przeglądu - kolor lightbara z gry / programu na PC (np. Steam) zasłaniał na stałe
kolor z ustawień; opcja "Gra może zmienić kolor" per pad, wpis w dzienniku przy pierwszym kolorze z PC, zapis opcji;
wibracja z gry zostawała po wyjściu z gry i w menu (np. Forza Horizon) - receiver odświeżał ją bez końca.
    python3 tests/test_lightbar_game_color.py receiver"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_events as ev
import rx_settings as R
from proto import T_IDENT

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


A = "aa:aa:aa:aa:aa:01"
feed(T_IDENT, 0, A.encode()); state(0)
now = time.monotonic

# ---------- 1. kolor z gry: domyślnie ma pierwszeństwo, "zawsze mój" go pomija ----------
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True)})))
check(F._target(0, now())[0][2:] == (255, 0, 255), "bez koloru z gry - kolor z ustawień")
S.game = "game.exe"
F.game(0, rgb=(0, 0, 255))
check(F._target(0, now())[0][2:] == (0, 0, 255), "gra ustawiła kolor - domyślnie ma pierwszeństwo")
S.game = "steam.exe"
check(F._target(0, now())[0][2:] == (255, 0, 255), "Steam / pulpit (poza grą) - kolor z ustawień mimo koloru z PC")
S.game = "game.exe"
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
S.game = "steam.exe"; F.game(0, rgb=(4, 5, 6)); F.fb[0]["note"] = True; F._note_game_color(0)
new = [e.text for e in ev.since(seq)]
check("poza grą" in new[-1], f"wpis w dzienniku poza grą: {new[-1]}")
S.game = "game.exe"
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

# ---------- 4. wyjście z gry / alt-tab zatrzymuje wibrację z gry ----------
import rx_games
FG[0] = "forzahorizon5.exe"; rx_games.poll_once()
F.game(0, 200, 100)
check(F._target(0, now())[0][:2] == (100, 200), "gra wibruje")
rx_games.poll_once()
check(F._target(0, now())[0][:2] == (100, 200), "ten sam program na pierwszym planie - wibracja zostaje")
FG[0] = None; rx_games.poll_once()
check(F._target(0, now())[0][:2] == (100, 200), "chwilowo brak okna - bez zmian")
FG[0] = "explorer.exe"; rx_games.poll_once()
check(F._target(0, now())[0][:2] == (0, 0), "wyjście z gry (pulpit) - wibracja z gry zatrzymana")
FG[0] = "forzahorizon5.exe"; rx_games.poll_once(); F.game(0, 50, 60)
check(F._target(0, now())[0][:2] == (60, 50), "powrót do gry - nowa wibracja działa")
FG[0] = "steamwebhelper.exe"; rx_games.poll_once()
check(F._target(0, now())[0][:2] == (0, 0), "powrót do Steama - zatrzymana")

# ---------- 5. gra w menu nie wysyła już raportów - wibracja wygasa po GAME_RUMBLE_STALE_S ----------
sent_fb = []
import threading
F.reset(0); F.game(0, 180, 90)
F.GAME_RUMBLE_STALE_S = 0.3
th = threading.Thread(target=F._loop, args=(lambda i, v: sent_fb.append((time.monotonic(), v)),), daemon=True); th.start()
t0 = time.monotonic()
time.sleep(0.15); F.game(0, 180, 90)        # gra wciąż wysyła - bez wygaszenia
time.sleep(0.3)
check(F.fb[0]["large"] == 180, "raporty płyną - wibracja trwa")
time.sleep(0.6)
check(F.fb[0]["large"] == 0 and sent_fb and sent_fb[-1][1][:2] == (0, 0), f"brak raportów > STALE - zatrzymana: {sent_fb[-1:]}")
S.stop.set(); F.fb_event.set(); th.join(2); S.stop.clear()
check(sent_fb[-1][0] - t0 < 0.3 + 0.15 + 0.15, f"wygaszenie punktualnie po STALE, nie po kolejnym 0.5 s: {sent_fb[-1][0] - t0:.2f}")

# ---------- 6. gra w menu wysyła raporty bez silników (kolor / spusty) - wibracja gaśnie od razu, bez STALE ----------
F.GAME_RUMBLE_STALE_S = 10.0
F.reset(0); F.game(0, 180, 90)
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "świeża wibracja trwa")
F.game(0, rgb=(1, 2, 3))
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "pojedynczy raport koloru w trakcie wibracji - bez zmian")
F.fb[0]["t"] -= 0.3; F.game(0, rgb=(1, 2, 3))
check(F.rumble_expired(F.fb[0], time.monotonic()) is not None, "raporty bez silników > GAP - wibracja wygasa")
F.game(0, 50, 60)
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "nowy raport silników - wibracja znów działa")
F.reset(0); F.fb[0]["t"] -= 5; F.game(0, rgb=(1, 2, 3))
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "bez wibracji - nic do wygaszania")

print("HUNT12", "OK" if not fails else f"BŁĘDY: {len(fails)}")
if fails:
    sys.exit(1)
