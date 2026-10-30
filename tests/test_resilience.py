"""Jeden nieoczekiwany wyjątek w wątku w tle nie może na stałe wyłączyć funkcji."""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import rx_games, rx_gyro, rx_feedback as F
threading.excepthook = lambda a: None            # nie zaśmiecaj wyjścia - liczy się zachowanie
run = getattr(S, "run_forever", None)
start = (lambda name, fn, *a: threading.Thread(target=run, args=(name, fn, *a), daemon=True).start()) if run else \
        (lambda name, fn, *a: threading.Thread(target=fn, args=a, daemon=True).start())
S.alive[0] = True
res = {}

# 1) wykrywanie gry: jeden błąd odczytu okna
calls = {"n": 0}
def fg():
    calls["n"] += 1
    if calls["n"] == 1:
        raise ValueError("ctypes: chwilowy błąd")
    return "game.exe"
W.foreground_exe = fg
rx_games.POLL_S = 0.05
start("games", rx_games.watch_thread); time.sleep(0.8)
res["wykrywanie gry działa po błędzie"] = S.game == "game.exe"

# 2) feedback: jeden błąd wysyłki
sent = []
def send(i, v):
    if not sent:
        sent.append("boom"); raise RuntimeError("chwilowy błąd")
    sent.append(v)
start("feedback", F.feedback_thread, send)
F.pulse(0, "up"); time.sleep(0.8); F.pulse(0, "mouse"); time.sleep(0.4)
res["feedback działa po błędzie"] = any(v != "boom" and v[2:] == (0, 90, 255) for v in sent)

# 3) mysz z gyro: jeden błąd SendInput
moves = []
def sm(f, dx=0, dy=0, data=0):
    if not moves:
        moves.append("boom"); raise OSError("chwilowy błąd")
    moves.append(dx)
W.send_mouse = sm
store.save(S.profile_key(0), {"gyro": {"mode": "mouse"}}); S.reload_profile(0)
start("gyro", rx_gyro.mouse_thread)
state(0, gyro=(0, -1024 * 100, 0)); time.sleep(0.9); state(0, gyro=(0, -1024 * 100, 1)); time.sleep(0.3)
res["mysz z gyro działa po błędzie"] = len(moves) > 5
for k, v in res.items():
    print(("OK   " if v else "FAIL ") + k)
S.stop.set()
sys.exit(0 if all(res.values()) else 1)
