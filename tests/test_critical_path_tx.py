"""Reprodukcje bugów ścieżki krytycznej nadajnika (przegląd 2026-10-16) - każdy test przechodzi PO poprawkach."""
import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
from evdev import ecodes as ec
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_net as net, sender_streams as st, sender_ctrl, sender_wol as wol
from sender_ctrl import Ctrl
from proto import KEY_BIT, PAD_KEYS, T_MOUSE, T_TOUCH
out = []
def emit(t, i, *v): out.append((t, v))
net.emit = sender_ctrl.net.emit = emit
fails = []
def check(cond, name):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)

class Ev:
    def __init__(s, t, c, v): s.type, s.code, s.value = t, c, v
SYN = Ev(ec.EV_SYN, ec.SYN_REPORT, 0)
DROP = Ev(ec.EV_SYN, ec.SYN_DROPPED, 0)
class AI:
    max, value = 1919, 0
class TouchDev:
    def __init__(s, evs): s.evs = evs
    def absinfo(s, c): return AI()
    async def async_read_loop(s):
        for dt, e in s.evs:
            if dt: await asyncio.sleep(dt)
            yield e
def abs_(dt, *pairs): return [(dt if k == 0 else 0, Ev(ec.EV_ABS, c, v)) for k, (c, v) in enumerate(pairs)] + [(0, SYN)]

# ---- 1. nowy palec w tym samym x albo y co poprzedni: jądro nie wysyła tej osi (wartość slotu bez zmian) ----
# jak hid-playstation + rdzeń input: drugi dotyk przy krawędzi (y = 0) ma tylko ABS_MT_POSITION_X
evs = (abs_(0, (ec.ABS_MT_SLOT, 0), (ec.ABS_MT_TRACKING_ID, 1), (ec.ABS_MT_POSITION_X, 900), (ec.ABS_MT_POSITION_Y, 0))
       + abs_(0.05, (ec.ABS_MT_TRACKING_ID, -1))
       + abs_(0.05, (ec.ABS_MT_TRACKING_ID, 2), (ec.ABS_MT_POSITION_X, 300))       # y = 0 jak poprzednio - pominięte
       + abs_(0.05, (ec.ABS_MT_TRACKING_ID, -1)))
async def touch_run():
    c = Ctrl(0, "aa", None, None, TouchDev(evs))
    await st.stream_touch(c)
    await asyncio.sleep(0.03)
asyncio.run(touch_run())
taps = {v[2] >> 4 for t, v in out if t == T_MOUSE and v[2] & 1}
check(len(taps) == 2, f"tap drugim palcem w tym samym y co pierwszy (krawędź) nie ginie: numery tapów {sorted(taps)}")
touch_on = [v for t, v in out if t == T_TOUCH and v[0] == 1]
check(any(v[2] == 300 and v[3] == 0 for v in touch_on), f"dotyk DS4 drugiego palca (300, 0) dochodzi: {touch_on}")

# ---- 2. SYN_DROPPED w trakcie kombinacji Wake-on-LAN: puszczenie zgubione, stan z jądra -> odliczanie anulowane ----
PS, OPT = KEY_BIT[316], KEY_BIT[315]
wol.cfg = wol.WolCfg(enabled=True, button=PAD_KEYS.index(315), hold_ms=300)
fired = []
wol.fire = lambda c, now=None: fired.append(c.idx)
class PadDev:
    keys = []
    def __init__(s, evs): s.evs = evs
    def absinfo(s, c):
        a = AI(); a.value = 128 if c in (0, 1, 3, 4) else 0
        return a
    def active_keys(s): return list(s.keys)
    async def async_read_loop(s):
        for dt, e in s.evs:
            if dt: await asyncio.sleep(dt)
            if e is None:
                s.keys = []                  # puszczenie przycisków w zgubionych zdarzeniach (jądro już je zna)
                continue
            yield e
pad_evs = [(0, Ev(ec.EV_KEY, 316, 1)), (0, Ev(ec.EV_KEY, 315, 1)), (0, SYN),
           (0.05, DROP), (0, None), (0, Ev(ec.EV_KEY, 316, 0)), (0, SYN)]
async def pad_run():
    c = Ctrl(0, "aa:bb:cc:dd:ee:ff", PadDev(pad_evs), None, None)
    c.pad_dev.keys = [316, 315]
    await st.stream_pad(c)
    await asyncio.sleep(0.4)                 # dłużej niż hold_ms
    return c
c = asyncio.run(pad_run())
check(not fired and c.wol_timer is None, f"kombinacja puszczona w trakcie SYN_DROPPED nie wysyła Wake-on-LAN: {fired}")
check(c.mask == 0, "stan przycisków odtworzony z jądra po SYN_DROPPED")

print("WSZYSTKO OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
