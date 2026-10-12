"""Reprodukcje bugów z przeglądu - każdy test powinien przechodzić PO poprawkach."""
import sys, os, time, threading, json
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import rx_games
PS = BIT[316]
results = {}
def check(name, cond):
    results[name] = bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

# B7: ps_hold_action = none -> długie PS ma dać PS dla gry po puszczeniu (a nie nic)
C.PS_HOLD_ACTION = "none"
state(0)                         # pad już połączony (PS w pierwszym stanie po połączeniu to włączanie pada, nie skrót)
state(0, mask=PS); time.sleep(C.PS_HOLD_S + 0.1); state(0, mask=0)
check("długie PS przy ps_hold_action=none trafia do gry", S.states[0]["mask"] & PS)
time.sleep(0.15); state(0); C.PS_HOLD_ACTION = "gamebar"

# B3: dwa szybkie PS - drugi impuls nie może zostać ucięty przez timer pierwszego
state(0, mask=PS); state(0, mask=0); time.sleep(0.05)
state(0, mask=PS); state(0, mask=0); time.sleep(0.05)       # 0.1 s po pierwszym: timer pierwszego już wygasł
check("drugi szybki impuls PS trwa pełne 80 ms", S.states[0]["mask"] & PS)
time.sleep(0.15); state(0)

# B2: puszczenie PS i wciśnięcie X w tym samym pakiecie - żadnego raportu "PS bez X"
seen = []
orig = S.notify
S.notify = lambda i: (seen.append(S.states[i]["mask"]), orig(i))
state(0, mask=PS); seen.clear(); state(0, mask=BIT[304])
S.notify = orig
check("brak przejściowego raportu ze starym stanem", all(m & BIT[304] for m in seen if m & PS))
time.sleep(0.15); state(0)

# B67: automatyczna zmiana profilu (alt-tab) bez wibracji
import rx_feedback as F
base = S.profile_key(0); store.save(base + "@g.exe", {}); S.alive[0] = True
W.foreground_exe = lambda: "g.exe"; rx_games.poll_once()
p = F.pulses[0]
check("automatyczna zmiana profilu: tylko kolor, bez wibracji", p is not None and p[1] == 0 and p[2] == 0)
W.foreground_exe = lambda: "msedge.exe"; rx_games.poll_once()

# B66: zmiana gry nie wypisuje komunikatów dla niepodłączonych padów
import io, contextlib
S.alive[1] = False; store.save(S.profile_key(1) + "@g.exe", {})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    W.foreground_exe = lambda: "g.exe"; rx_games.poll_once()
check("brak komunikatów o profilu dla niepodłączonego pada", "Controller 1" not in buf.getvalue())
W.foreground_exe = lambda: "msedge.exe"; rx_games.poll_once()

# B26: wybrany w oknie profil gry ma być aktywny (podgląd "Wynik" i test na padzie), gdy okno gry nie jest aktywne
import rx_profiles as P
check("wybór profilu gry w oknie ustawia go jako aktywny", P.use(0, base + "@g.exe") and S.active[0] == base + "@g.exe")
check("nieistniejący profil odrzucony", not P.use(0, base + "@nope.exe"))
W.foreground_exe = lambda: "g.exe"; rx_games.poll_once(); W.foreground_exe = lambda: "explorer.exe"; rx_games.poll_once()
check("powrót do gry / zmiana okna kasuje wybór z okna", S.active[0] == base)

# B24: równoległe przełączanie profilu z kilku wątków - końcowy stan spójny z active_key()
store.save(base + "@h.exe", {"buttons": {"cross": "square"}})
def flip():
    for k in range(300):
        S.set_game("h.exe" if k % 2 else "x.exe")
def web():
    for _ in range(300):
        S.reload_profile(0)
ts = [threading.Thread(target=flip), threading.Thread(target=web), threading.Thread(target=web)]
[t.start() for t in ts]; [t.join() for t in ts]
ok = S.active[0] == S.active_key(0) and S.remap_maps[0] == store.get(S.active[0])
check("spójny profil po równoległych zmianach", ok)
S.set_game(None)

# rotacja logu: plik trzymany przez inny proces (Windows: stary receiver po restarcie) nie może wyłączyć logowania
import rx_config
log = os.path.join(TMP, "rx.log")
open(log, "w").write("x" * 1_100_000)
real_replace, real_stdout, real_stderr = os.replace, sys.stdout, sys.stderr
def busy(*a):
    raise PermissionError(32, "plik używany przez inny proces")
log_cfg = os.path.join(TMP, "log.json")
with open(log_cfg, "w") as f:
    json.dump({"log_file": log}, f)
os.replace = busy
try:
    handle = rx_config.init(["--config", log_cfg]).log_handle
finally:
    os.replace = real_replace
    sys.stdout, sys.stderr = real_stdout, real_stderr
check("nieudana rotacja logu nie wyłącza logowania do pliku", handle is not None and handle.name == log)
if handle is not None:
    handle.close()
rx_config.init(["--remap-file", C.REMAP_PATH])           # przywróć konfigurację testów
print("ALL PASS" if all(results.values()) else f"FAILED: {[k for k, v in results.items() if not v]}")
