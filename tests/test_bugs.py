"""Reproductions of bugs from the review - every test should pass AFTER the fixes."""
import sys, os, time, threading, json
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                              # noqa
import rx_games
PS = BIT[316]
results = {}
def check(name, cond):
    results[name] = bool(cond)
    print(("PASS " if cond else "FAIL ") + name)

# B7: ps_hold_action = none -> a long PS should give PS to the game on release (not nothing)
C.PS_HOLD_ACTION = "none"
state(0)                         # pad already connected (PS in the first state after connecting is the pad power-on, not a shortcut)
state(0, mask=PS); time.sleep(C.PS_HOLD_S + 0.1); state(0, mask=0)
check("a long PS with ps_hold_action=none reaches the game", S.states[0]["mask"] & PS)
time.sleep(0.15); state(0); C.PS_HOLD_ACTION = "gamebar"

# B3: two quick PS presses - the second pulse must not be cut by the first one's timer
state(0, mask=PS); state(0, mask=0); time.sleep(0.05)
state(0, mask=PS); state(0, mask=0); time.sleep(0.05)       # 0.1 s after the first: the first one's timer has already expired
check("the second quick PS pulse lasts the full 80 ms", S.states[0]["mask"] & PS)
time.sleep(0.15); state(0)

# B2: releasing PS and pressing X in the same packet - no "PS without X" report
seen = []
orig = S.notify
S.notify = lambda i: (seen.append(S.states[i]["mask"]), orig(i))
state(0, mask=PS); seen.clear(); state(0, mask=BIT[304])
S.notify = orig
check("no transient report with the old state", all(m & BIT[304] for m in seen if m & PS))
time.sleep(0.15); state(0)

# B67: an automatic profile change (alt-tab) without rumble
import rx_feedback as F
base = S.profile_key(0); store.save(base + "@g.exe", {}); S.alive[0] = True
W.foreground_exe = lambda: "g.exe"; rx_games.poll_once()
p = F.pulses[0]
check("automatic profile change: colour only, no rumble", p is not None and p[1] == 0 and p[2] == 0)
W.foreground_exe = lambda: "msedge.exe"; rx_games.poll_once()

# B66: a game change prints no messages for disconnected pads
import io, contextlib
S.alive[1] = False; store.save(S.profile_key(1) + "@g.exe", {})
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    W.foreground_exe = lambda: "g.exe"; rx_games.poll_once()
check("no profile messages for a disconnected pad", "Controller 1" not in buf.getvalue())
W.foreground_exe = lambda: "msedge.exe"; rx_games.poll_once()

# B26: the game profile chosen in the window must be active (the "Wynik" preview and the pad test) when the game window is not active
import rx_profiles as P
check("choosing a game profile in the window makes it active", P.use(0, base + "@g.exe") and S.active[0] == base + "@g.exe")
check("a non-existent profile rejected", not P.use(0, base + "@nope.exe"))
W.foreground_exe = lambda: "g.exe"; rx_games.poll_once(); W.foreground_exe = lambda: "explorer.exe"; rx_games.poll_once()
check("back to the game / a window change clears the choice from the window", S.active[0] == base)

# B24: switching the profile in parallel from several threads - the final state consistent with active_key()
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
check("a consistent profile after parallel changes", ok)
S.set_game(None)

# log rotation: a file held by another process (Windows: the old receiver after a restart) must not turn logging off
import rx_config
log = os.path.join(TMP, "rx.log")
open(log, "w").write("x" * 1_100_000)
real_replace, real_stdout, real_stderr = os.replace, sys.stdout, sys.stderr
def busy(*a):
    raise PermissionError(32, "file in use by another process")
log_cfg = os.path.join(TMP, "log.json")
with open(log_cfg, "w") as f:
    json.dump({"log_file": log}, f)
os.replace = busy
try:
    handle = rx_config.init(["--config", log_cfg]).log_handle
finally:
    os.replace = real_replace
    sys.stdout, sys.stderr = real_stdout, real_stderr
check("a failed log rotation does not turn file logging off", handle is not None and handle.name == log)
if handle is not None:
    handle.close()
# receiver.toml next to the receiver loads without --config (it used to be silently ignored); --config "" = no file
cfg_dir = os.path.join(TMP, "rxdir")
os.makedirs(cfg_dir, exist_ok=True)
with open(os.path.join(cfg_dir, "receiver.toml"), "w") as f:
    f.write("pads = 3\n")
real_here = rx_config.HERE
rx_config.HERE = cfg_dir
try:
    check("receiver.toml next to the receiver loaded without --config", rx_config.init([]).NUM_PADS == 3)
    check('--config "" pomija receiver.toml', rx_config.init(["--config", ""]).NUM_PADS == 4)
    rx_config.HERE = TMP
    check("no receiver.toml - default values", rx_config.default_config() is None and rx_config.init([]).NUM_PADS == 4)
finally:
    rx_config.HERE = real_here
rx_config.init(["--pads", "2", "--remap-file", C.REMAP_PATH])           # restore the test configuration
print("ALL PASS" if all(results.values()) else f"FAILED: {[k for k, v in results.items() if not v]}")
