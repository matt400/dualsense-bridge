import asyncio, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
from evdev import ecodes as ec
from fakedev import DROP, SYN, Dev, Ev
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_net as net, sender_streams as st, sender_ctrl, sender_supervisor as sup, sender_state as state
from sender_ctrl import Ctrl
out = []
def emit(t, i, *v): out.append((t, v))
net.emit = sender_ctrl.net.emit = emit
sup.emit_repeat = lambda t, i, *v, **k: emit(t, i, *v)
A = lambda c, v: Ev(ec.EV_ABS, c, v)

# touchpad: finger resting, buffer overflow, the finger still resting and moving -> no jump, no tap
st.read_mt = lambda dev, n=2: [(5, 1000, 500), (-1, 0, 0)]      # kernel: slot 0 = finger id 5 at (1000, 500)
evs = [(0, A(ec.ABS_MT_SLOT, 0)), (0, A(ec.ABS_MT_TRACKING_ID, 5)), (0, A(ec.ABS_MT_POSITION_X, 500)),
       (0, A(ec.ABS_MT_POSITION_Y, 500)), (0, SYN),
       (0.01, DROP), (0, A(ec.ABS_MT_POSITION_X, 777)), (0, SYN),          # incomplete frame - skipped
       (0.01, A(ec.ABS_MT_POSITION_X, 1010)), (0, SYN),                    # ruch po odtworzeniu stanu
       (0.01, A(ec.ABS_MT_TRACKING_ID, -1)), (0, SYN)]
async def t1():
    c = Ctrl(0, "aa", None, None, Dev(evs)); await st.stream_touch(c); await asyncio.sleep(0.02)
asyncio.run(t1())
touch = [v for t, v in out if t == 2]; mouse = [v for t, v in out if t == 4]
assert any(tp[0] == 1 and tp[2] == 1000 for tp in touch), touch      # finger restored from the kernel (x=1000)
assert not any(tp[2] == 777 for tp in touch), "an incomplete frame must not get through"
assert mouse == [(10, 0, 0)], mouse                                   # move 1000->1010, no jump 500->1000, no tap
assert touch[-1][0] == 0
print("touch po SYN_DROPPED: palec odtworzony, ruch", mouse, "no tap")

# pad: incomplete frame skipped, state from the device
out.clear()
pevs = [(0, Ev(ec.EV_KEY, 304, 1)), (0, SYN), (0.01, DROP), (0, Ev(ec.EV_KEY, 305, 1)), (0, SYN)]
async def t2():
    c = Ctrl(0, "aa", Dev(pevs, keys=[]), None, None); await st.stream_pad(c); await asyncio.sleep(0.02)
asyncio.run(t2())
last = [v for t, v in out if t == 1][-1]
assert last[8] == 0, last          # the kernel says: nothing pressed (305 from the incomplete frame ignored, 304 released)
print("pad after SYN_DROPPED: state from the device, mask", last[8])

# a late touchpad joins a running pad; losing it does not disconnect the pad
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
    assert c.touch_dev is touch, "touchpad not attached"
    await asyncio.sleep(0.1)
    assert c.touch_dev is None and touch.closed and state.active.get("aa") is c, "losing the touchpad must not disconnect the pad"
    await run
    assert "aa" not in state.active and pad.closed and any(t == 8 for t, _ in out)
    supv.cancel()
asyncio.run(t3())
print("late touchpad: attached, detached without disconnecting the pad, cleanup OK")
print("ALL OK")
