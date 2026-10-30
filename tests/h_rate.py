import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
from evdev import ecodes as ec
import sender_config
sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_net as net, sender_streams as st, sender_ctrl
from sender_ctrl import Ctrl
out = []
def emit(t, idx, *v): out.append((time.monotonic(), t, v))
net.emit = emit; sender_ctrl.net.emit = emit
class Ev:
    def __init__(s,t,c,v): s.type,s.code,s.value=t,c,v
class Dev:
    def __init__(s, evs): s.evs = evs
    async def async_read_loop(s):
        for dt, e in s.evs:
            if dt: await asyncio.sleep(dt)
            yield e
syn = lambda: Ev(ec.EV_SYN, ec.SYN_REPORT, 0)
def pad_script(n, step):      # drążek zmienia się co 1 ms
    r = []
    for i in range(n):
        r += [(0.001, Ev(ec.EV_ABS, 0, 100 + (i % 50))), (0, syn())]
    return r
def motion_script(n):
    r = []
    for i in range(n):
        r += [(0.001, Ev(ec.EV_ABS, 3, 1000 + (i % 7))), (0, Ev(ec.EV_ABS, 0, 50)), (0, syn())]
    return r
async def main():
    c = Ctrl(0, "aa:bb", Dev(pad_script(500, 1)), Dev(motion_script(500)), None)
    C.CAL_S = 0; C.AUTOBIAS = False
    t0 = time.monotonic()
    await asyncio.gather(st.stream_pad(c), st.stream_motion(c))
    await asyncio.sleep(0.02)
    dur = time.monotonic() - t0
    states = [o for o in out if o[1] == 1]
    gaps = [ (b[0]-a[0])*1000 for a, b in zip(states, states[1:])]
    print(f"{len(states)} pakietów stanu w {dur*1000:.0f} ms = {len(states)/dur:.0f} pkt/s; min przerwa {min(gaps):.1f} ms")
    last_ev_pad = 100 + (499 % 50)
    print("ostatni stan lx =", states[-1][2][0], "(oczekiwane", last_ev_pad, ")  |  gyro x =", states[-1][2][14])
    assert states[-1][2][0] == last_ev_pad, "trailing flush zgubił końcową wartość"
asyncio.run(main())
