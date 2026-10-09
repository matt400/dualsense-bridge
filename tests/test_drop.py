import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
from evdev import ecodes as ec
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_net as net, sender_streams as st, sender_ctrl, sender_supervisor as sup, sender_state as state
from sender_ctrl import Ctrl
out = []
def emit(t, i, *v): out.append((t, v))
net.emit = sender_ctrl.net.emit = sup.emit = emit
class Ev:
    def __init__(s, t, c, v): s.type, s.code, s.value = t, c, v
class AI:
    def __init__(s, v=0, mx=1919): s.value, s.max = v, mx
SYN = Ev(ec.EV_SYN, ec.SYN_REPORT, 0); DROP = Ev(ec.EV_SYN, ec.SYN_DROPPED, 0)
A = lambda c, v: Ev(ec.EV_ABS, c, v)
class Dev:
    path = "/dev/input/event7"; name = "DualSense Wireless Controller"
    def __init__(s, evs, absv=None, keys=()):
        s.evs, s.absv, s.keys, s.closed = evs, absv or {}, list(keys), False
    def absinfo(s, c): return AI(s.absv.get(c, 0), 1 if c == ec.ABS_MT_SLOT else 1919)
    def active_keys(s): return s.keys
    def grab(s): pass
    def ungrab(s): pass
    def close(s): s.closed = True
    async def async_read_loop(s):
        for dt, e in s.evs:
            if dt: await asyncio.sleep(dt)
            if isinstance(e, Exception): raise e
            yield e

# 1) touchpad: palec leży, przepełnienie bufora, palec dalej leży i się rusza -> bez skoku, bez tapu
st.read_mt = lambda dev, n=2: [(5, 1000, 500), (-1, 0, 0)]      # jądro: slot 0 = palec id 5 na (1000, 500)
evs = [(0, A(ec.ABS_MT_SLOT, 0)), (0, A(ec.ABS_MT_TRACKING_ID, 5)), (0, A(ec.ABS_MT_POSITION_X, 500)),
       (0, A(ec.ABS_MT_POSITION_Y, 500)), (0, SYN),
       (0.01, DROP), (0, A(ec.ABS_MT_POSITION_X, 777)), (0, SYN),          # niepełna ramka - pomijana
       (0.01, A(ec.ABS_MT_POSITION_X, 1010)), (0, SYN),                    # ruch po odtworzeniu stanu
       (0.01, A(ec.ABS_MT_TRACKING_ID, -1)), (0, SYN)]
async def t1():
    c = Ctrl(0, "aa", None, None, Dev(evs)); await st.stream_touch(c); await asyncio.sleep(0.02)
asyncio.run(t1())
touch = [v for t, v in out if t == 2]; mouse = [v for t, v in out if t == 4]
assert any(tp[0] == 1 and tp[2] == 1000 for tp in touch), touch      # palec odtworzony z jądra (x=1000)
assert not any(tp[2] == 777 for tp in touch), "niepełna ramka nie może przejść"
assert mouse == [(10, 0, 0)], mouse                                   # ruch 1000->1010, bez skoku 500->1000, bez tapu
assert touch[-1][0] == 0
print("touch po SYN_DROPPED: palec odtworzony, ruch", mouse, "bez tapu")

# 2) pad: niepełna ramka pominięta, stan z urządzenia
out.clear()
pevs = [(0, Ev(ec.EV_KEY, 304, 1)), (0, SYN), (0.01, DROP), (0, Ev(ec.EV_KEY, 305, 1)), (0, SYN)]
async def t2():
    c = Ctrl(0, "aa", Dev(pevs, keys=[]), None, None); await st.stream_pad(c); await asyncio.sleep(0.02)
asyncio.run(t2())
last = [v for t, v in out if t == 1][-1]
assert last[8] == 0, last          # jądro mówi: nic nie wciśnięte (305 z niepełnej ramki zignorowane, 304 puszczony)
print("pad po SYN_DROPPED: stan z urządzenia, maska", last[8])

# 3) spóźniony touchpad dołącza się do działającego pada; jego utrata nie rozłącza pada
out.clear(); C.RESCAN_S = 0.01
async def forever():
    await asyncio.sleep(3600)
async def t3():
    pad = Dev([(0.3, OSError(19, "No such device"))])       # pad znika po 0.3 s
    c = Ctrl(0, "aa", pad, None, None); state.active["aa"] = c
    st.stream_battery = lambda c: forever()
    st.stream_motion = lambda c: forever()
    touch = Dev([(0.05, OSError(19, "gone"))])
    calls = {"n": 0}
    def disc():
        calls["n"] += 1
        return {"aa": {"touch": touch}} if calls["n"] == 1 else {}
    sup.discover = disc
    run = asyncio.create_task(sup.run_ctrl(c))
    supv = asyncio.create_task(sup.supervisor())
    await asyncio.sleep(0.02)
    assert c.touch_dev is touch, "touchpad nie dołączony"
    await asyncio.sleep(0.1)
    assert c.touch_dev is None and touch.closed and state.active.get("aa") is c, "utrata touchpada nie może rozłączyć pada"
    await run
    assert "aa" not in state.active and pad.closed and any(t == 8 for t, _ in out)
    supv.cancel()
asyncio.run(t3())
print("spóźniony touchpad: dołączony, odłączony bez rozłączania pada, sprzątanie OK")
print("ALL OK")
