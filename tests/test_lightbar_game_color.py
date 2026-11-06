"""Receiver: regressions from the twelfth review - the lightbar colour from a game / PC program (e.g. Steam) permanently covered
the colour from the settings; the "Gra może zmienić kolor" option per pad, a log entry on the first colour from the PC, saving the option;
game rumble stayed after leaving the game and in menus (e.g. Forza Horizon) - the receiver refreshed it forever.
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

# ---------- game colour: wins by default, "always mine" skips it ----------
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True)})))
check(F._target(0, now())[0][2:] == (255, 0, 255), "no game colour - the colour from the settings")
S.game = "game.exe"
F.game(0, rgb=(0, 0, 255))
check(F._target(0, now())[0][2:] == (0, 0, 255), "the game set a colour - it wins by default")
S.game = "steam.exe"
check(F._target(0, now())[0][2:] == (255, 0, 255), "Steam / desktop (outside a game) - the colour from the settings despite a PC colour")
S.game = "game.exe"
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True, game=False)})))
val, ours, _b = F._target(0, now())
check(val[2:] == (255, 0, 255) and ours, "game = False - always the colour from the settings, despite the game colour")
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {"*": R.PadLight("battery", "", True, game=False)})))
check(F._target(0, now())[0][2:] == (0, 220, 60), "game = False from the default entry (*) - colour by battery")
F.reset(0)

# ---------- the first colour from the PC goes into the log (once per virtual pad) ----------
R.apply(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True)})))
seq = ev.since(0)[-1].seq if ev.since(0) else 0
F.game(0, rgb=(1, 2, 3)); F.game(0, rgb=(4, 5, 6))
check(F.fb[0]["note"], "game colour marked for the entry")
F._note_game_color(0)
new = [e.text for e in ev.since(seq)]
check(len(new) == 1 and "#040506" in new[0] and "Gra może zmienić kolor" in new[0], f"log entry: {new}")
S.game = "steam.exe"; F.game(0, rgb=(4, 5, 6)); F.fb[0]["note"] = True; F._note_game_color(0)
new = [e.text for e in ev.since(seq)]
check("poza grą" in new[-1], f"log entry outside a game: {new[-1]}")
S.game = "game.exe"
F.game(0, rgb=(7, 8, 9))
check(not F.fb[0]["note"], "further game colours without new entries")
F.reset(0)
check(not F.fb[0]["note"] and not F.fb[0]["rgb"], "nowy wirtualny pad - od nowa")
cb = F.make_cb(0)
cb(None, None, 0, 0, type("Led", (), {"Red": 9, "Green": 9, "Blue": 9})(), None)
check(F.fb[0]["note"], "ViGEm: the game colour is marked too")
F.reset(0)

# ---------- option write / read ----------
path = os.path.join(TMP, "s12.json")
R.save(R.Settings(lightbar=R.LightbarConfig(15, {A: R.PadLight("solid", "#ff00ff", True, game=False)})), path)
check(json.load(open(path))["lightbar"]["pads"][A]["game"] is False, "game zapisane w settings.json")
check(R.load(path).lightbar.pads[A].game is False, "game odczytane")
json.dump({"lightbar": {"pads": {A: {"mode": "solid", "color": "#ff00ff"}}}}, open(path, "w"))
check(R.load(path).lightbar.pads[A].game is True, "old file without game - the game can change the colour (as before)")

# ---------- leaving the game / alt-tab stops game rumble ----------
import rx_games
FG[0] = "forzahorizon5.exe"; rx_games.poll_once()
F.game(0, 200, 100)
check(F._target(0, now())[0][:2] == (100, 200), "the game rumbles")
rx_games.poll_once()
check(F._target(0, now())[0][:2] == (100, 200), "the same program in the foreground - rumble stays")
FG[0] = None; rx_games.poll_once()
check(F._target(0, now())[0][:2] == (100, 200), "no window for a moment - no change")
FG[0] = "explorer.exe"; rx_games.poll_once()
check(F._target(0, now())[0][:2] == (0, 0), "leaving the game (desktop) - game rumble stopped")
FG[0] = "forzahorizon5.exe"; rx_games.poll_once(); F.game(0, 50, 60)
check(F._target(0, now())[0][:2] == (60, 50), "back in the game - new rumble works")
FG[0] = "steamwebhelper.exe"; rx_games.poll_once()
check(F._target(0, now())[0][:2] == (0, 0), "back to Steam - stopped")

# ---------- a game in its menu no longer sends reports - rumble expires after GAME_RUMBLE_STALE_S ----------
sent_fb = []
import threading
F.reset(0); F.game(0, 180, 90)
F.GAME_RUMBLE_STALE_S = 0.3
th = threading.Thread(target=F.feedback_thread, args=(lambda i, v: sent_fb.append((time.monotonic(), v)),), daemon=True); th.start()
t0 = time.monotonic()
time.sleep(0.15); F.game(0, 180, 90)        # the game still sends - no expiry
time.sleep(0.3)
check(F.fb[0]["large"] == 180, "reports flow - rumble continues")
time.sleep(0.6)
check(F.fb[0]["large"] == 0 and sent_fb and sent_fb[-1][1][:2] == (0, 0), f"no reports > STALE - stopped: {sent_fb[-1:]}")
S.stop.set(); F.fb_event.set(); th.join(2); S.stop.clear()
check(sent_fb[-1][0] - t0 < 0.3 + 0.15 + 0.15, f"expiry right after STALE, not after another 0.5 s: {sent_fb[-1][0] - t0:.2f}")

# ---------- a game in its menu sends reports without motors (colour / triggers) - rumble stops at once, no STALE ----------
F.GAME_RUMBLE_STALE_S = 10.0
F.reset(0); F.game(0, 180, 90)
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "fresh rumble continues")
F.game(0, rgb=(1, 2, 3))
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "a single colour report during rumble - no change")
F.fb[0]["t"] -= 0.3; F.game(0, rgb=(1, 2, 3))
check(F.rumble_expired(F.fb[0], time.monotonic()) is not None, "reports without motors > GAP - rumble expires")
F.game(0, 50, 60)
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "a new motor report - rumble works again")
F.reset(0); F.fb[0]["t"] -= 5; F.game(0, rgb=(1, 2, 3))
check(F.rumble_expired(F.fb[0], time.monotonic()) is None, "no rumble - nothing to expire")

print("HUNT12", "OK" if not fails else f"FAILURES: {len(fails)}")
if fails:
    sys.exit(1)
