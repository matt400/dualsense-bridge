"""Sender: the pad is grabbed before the rumble backend opens; the heartbeat leaves a starting pad alone.
    python3 tests/test_start_order_tx.py sender"""
import asyncio, sys, types
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_ctrl
import sender_state
import sender_streams as streams
import sender_supervisor as sup

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)



class Dev:
    path = "/dev/input/event91"
    name = "Wireless Controller"
    def absinfo(self, code):
        return types.SimpleNamespace(value=0)
    def active_keys(self):
        return []
    def grab(self):
        pass


# ---------- grab before rumble, heartbeat skips a starting pad ----------
async def start_order():
    order = []
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:21", Dev(), None, None)
    c.starting = True
    real = (sup.grab, sup._open_rumble, streams.stream_pad, streams.stream_battery, sup.events.info)
    async def fake_rumble(_c):
        order.append("rumble")
        assert c.starting, "still starting while the rumble backend opens"
        return types.SimpleNamespace(set_triggers=lambda t: None, bits=0, why=0)
    async def idle(_c):
        await asyncio.sleep(3600)
    sup.grab = lambda dev, label: order.append("grab")
    sup._open_rumble = fake_rumble
    streams.stream_pad = streams.stream_battery = idle
    sup.events.info = lambda *a: None
    C.GRAB_PAD = True
    try:
        tasks = await sup._start_pad(c)
    finally:
        sup.grab, sup._open_rumble, streams.stream_pad, streams.stream_battery, sup.events.info = real
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    check(order == ["grab", "rumble"], f"_start_pad grabs the pad before the rumble backend opens ({order})")
    check(not c.starting, "_start_pad clears 'starting' once the pad is ready")

    # the heartbeat
    seen = []
    hb_pad = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:22", Dev(), None, None)
    hb_pad.t_state = float("-inf")
    hb_pad.send_state = lambda force=False: seen.append(("state", hb_pad.starting))
    hb_pad.send_ident = hb_pad.send_click = lambda *a, **k: seen.append(("other", hb_pad.starting))
    hb_pad.send_touch = lambda *a, **k: seen.append(("other", hb_pad.starting))
    sender_state.active.clear()
    sender_state.active[hb_pad.key] = hb_pad
    old = C.HEARTBEAT_S
    C.HEARTBEAT_S = 0.01
    try:
        hb_pad.starting = True
        task = asyncio.create_task(streams.heartbeat())
        await asyncio.sleep(0.08)
        check(not seen, "the heartbeat sends nothing for a pad that is still starting")
        hb_pad.starting = False
        await asyncio.sleep(0.08)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        C.HEARTBEAT_S = old
        sender_state.active.clear()
    check(any(k == "state" for k, _s in seen), "...and resumes once the pad is ready")

asyncio.run(start_order())


# ---------- a new pad is 'starting' at the moment it becomes visible ----------
class Active(dict):
    """state.active that records the starting flag of every pad at the moment it is inserted."""
    def __setitem__(self, key, c):
        seen_starting.append(c.starting)
        super().__setitem__(key, c)


seen_starting: list[bool] = []
spawned = []
real_active, real_spawn, real_pick = sender_state.active, sender_state.spawn, sup.pick_slot
sender_state.active = sup.state.active = Active()
sup.state.spawn = lambda coro: (spawned.append(coro), coro.close())
sup.pick_slot = lambda key: 0
try:
    sup._start_new_pad("aa:bb:cc:dd:ee:07", {"pad": Dev(), "motion": None, "touch": None}, 0.0)
finally:
    sender_state.active = sup.state.active = real_active
    sup.state.spawn, sup.pick_slot = real_spawn, real_pick
check(seen_starting == [True] and len(spawned) == 1, f"a new pad is marked 'starting' before it appears in state.active ({seen_starting})")

sys.exit(1 if fails else 0)
