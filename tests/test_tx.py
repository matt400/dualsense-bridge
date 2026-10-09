import asyncio, os, sys, time, tempfile
sys.path.insert(0, sys.argv[1])
from evdev import ecodes as ec
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_net as net, sender_streams as st, sender_ctrl, sender_supervisor as sup
from sender_ctrl import Ctrl
out = []
def emit(t, i, *v): out.append((time.monotonic(), t, v))
net.emit = sender_ctrl.net.emit = emit
class Ev:
    def __init__(s,t,c,v): s.type,s.code,s.value=t,c,v
class AI: max = 1919
class Dev:
    def __init__(s, evs): s.evs = evs
    def absinfo(s, c): return AI()
    async def async_read_loop(s):
        for dt, e in s.evs:
            if dt: await asyncio.sleep(dt)
            yield e
SYN = Ev(ec.EV_SYN, ec.SYN_REPORT, 0)
def frame(dt, *pairs):
    r = [(dt, Ev(ec.EV_ABS, *pairs[0]))] + [(0, Ev(ec.EV_ABS, *p)) for p in pairs[1:]]
    return r + [(0, SYN)]
F = lambda s, tid, x, y: [(ec.ABS_MT_SLOT, s), (ec.ABS_MT_TRACKING_ID, tid), (ec.ABS_MT_POSITION_X, x), (ec.ABS_MT_POSITION_Y, y)]
M = lambda s, x, y: [(ec.ABS_MT_SLOT, s), (ec.ABS_MT_POSITION_X, x), (ec.ABS_MT_POSITION_Y, y)]
L = lambda s: [(ec.ABS_MT_SLOT, s), (ec.ABS_MT_TRACKING_ID, -1)]
def run(evs):
    out.clear()
    async def go():
        c = Ctrl(0, "aa", None, None, Dev(evs)); await st.stream_touch(c); await asyncio.sleep(0.03); return c
    return asyncio.run(go())
mouse = lambda: [v for _, t, v in out if t == 4]
clicks = lambda: [v[0] for _, t, v in out if t == 3]

# tap 1 palcem: 3 pakiety, ten sam numer, bit0
run(frame(0, *F(0, 1, 900, 500)) + frame(0.004, *M(0, 902, 501)) + frame(0.05, *L(0)))
taps = [m for m in mouse() if m[2] & 5]
assert len(taps) == 3 and len({m[2] for m in taps}) == 1 and taps[0][2] & 1 and taps[0][2] >> 4 == 1, taps
assert not [m for m in mouse() if not m[2] & 5], "tap nie może ruszać kursorem"
print("tap 1 palcem:", taps)
# tap 2 palcami -> bit2
run(frame(0, *F(0, 2, 500, 500), *F(1, 3, 700, 500)) + frame(0.05, *L(0), *L(1)))
assert all(m[2] & 4 for m in mouse()) and len(mouse()) == 3, mouse()
print("tap 2 palcami OK")
# klik fizyczny z 2 palcami -> 2, z 1 palcem -> 1
def click_seq(two):
    evs = frame(0, *F(0, 4, 500, 500), *(F(1, 5, 700, 500) if two else []))
    evs += [(0.01, Ev(ec.EV_KEY, ec.BTN_LEFT, 1)), (0, SYN), (0.03, Ev(ec.EV_KEY, ec.BTN_LEFT, 0)), (0, SYN)]
    evs += frame(0.01, *L(0), *(L(1) if two else []))
    run(evs); return clicks()
c2, c1 = click_seq(True), click_seq(False)
assert 2 in c2 and 1 not in c2 and c2[-1] == 0 and 1 in c1 and c1[-1] == 0, (c2, c1)
assert not [m for m in mouse() if m[2] & 5], "klik fizyczny nie może dawać tapu"
print("klik fizyczny: 2 palce ->", sorted(set(c2)), " 1 palec ->", sorted(set(c1)))
# przewijanie 2 palcami
run(frame(0, *F(0, 6, 500, 500), *F(1, 7, 700, 500)) + sum((frame(0.008, *M(0, 500, 500 + i*10), *M(1, 700, 500 + i*10)) for i in range(1, 8)), []) + frame(0.008, *L(0), *L(1)))
assert mouse() and all(m[2] == 2 for m in mouse()), mouse()
print("scroll OK:", len(mouse()), "pakietów")

# recykling ścieżki eventN: nie-pad zapamiętany, potem pad na tej samej ścieżce
d = tempfile.mkdtemp(); p = os.path.join(d, "event5"); open(p, "w").close()
nid = sup._node_id(p); sup._not_pads[p] = nid
os.remove(p); time.sleep(0.01); open(p, "w").close()
assert sup._node_id(p) != nid, "nowy węzeł musi mieć inny identyfikator"
print("recykling ścieżki: nowy węzeł rozpoznany")
print("SENDER OK")
