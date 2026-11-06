"""Sender: pad limit and scan regressions:
  - a pad over max_pads: its _first_seen entry stayed forever, next_scan_s() returned 0.05 s and the supervisor woke
    ~17 times a second without end (also after the pad left); the pad vanished without a trace in the log and the window log
  - with full slots a new pad was not noticed at all - a scan after a change in /dev/input gives a message
  - the Wake-on-LAN broadcast address (`ip addr`) computed in the asyncio loop stalled it, the pad read too
python3 tests/test_pad_limit_tx.py sender"""
import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_events as events
import sender_supervisor as sup
import sender_wol as wol
from proto import E_NO_SLOT

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


C.SLOTS_FILE = ""
C.MAX_PADS, C.RESCAN_S = 2, 2.0
events.set_enabled(True)


class Dev:
    def __init__(s, n): s.path = f"/dev/input/event{n}"
    def close(s): pass


present = {f"aa:bb:cc:dd:ee:0{k}" for k in range(3)}
sup.discover = lambda: {k: {"pad": Dev(n * 3), "motion": Dev(n * 3 + 1), "touch": Dev(n * 3 + 2)}
                        for n, k in enumerate(sorted(present))}
started = []


async def fake_run(c):
    started.append(c.key)
    await asyncio.sleep(3600)
sup.run_ctrl = fake_run


class Watch:
    def __init__(s): s.wakes, s.change = 0, False
    async def wait(s, t):
        s.wakes += 1
        await asyncio.sleep(min(t, 0.5))
        ch, s.change = s.change, False
        return ch


async def supervise():
    w = Watch()
    task = asyncio.create_task(sup._supervise(w))
    await asyncio.sleep(1.6)
    check(len(started) == 2, f"two pads got slots ({started})")
    check(w.wakes <= 6, f"a pad without a slot does not wake the supervisor every 50 ms ({w.wakes} wakeups in 1.6 s)")
    check(not sup._first_seen, "a pad without a slot does not stay in _first_seen")
    evs = [e for e in events._queue if e[3] == E_NO_SLOT]
    check(len(evs) == 1 and evs[0][5] == b"aa:bb:cc:dd:ee:02", "message about a skipped pad - once")
    # a pad leaves and comes back with full slots: change in /dev/input -> scan -> the message again
    present.discard("aa:bb:cc:dd:ee:02"); w.change = True
    await asyncio.sleep(0.6)
    present.add("aa:bb:cc:dd:ee:02"); w.change = True
    await asyncio.sleep(0.6)
    evs = [e for e in events._queue if e[3] == E_NO_SLOT]
    check(len(evs) == 2, f"a pad reconnected with full slots - the message again ({len(evs)})")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

asyncio.run(supervise())


# ---------- Wake-on-LAN broadcast outside the asyncio loop ----------
def slow_broadcast(ip, ip_output=None):
    time.sleep(0.4)                       # `ip addr` na wolnym telefonie
    return "10.0.0.255"


async def bcast():
    wol.subnet_broadcast = slow_broadcast
    wol.local_ip = lambda: "10.0.0.7"
    wol._bcast_cache, wol._bcast_pending = (None, None), None
    t0 = time.monotonic()
    first = wol.auto_bcast()
    blocked = time.monotonic() - t0
    check(blocked < 0.1, f"auto_bcast does not block the loop ({blocked * 1000:.0f} ms)")
    check(first is None, "before the read: no broadcast (it goes to 255.255.255.255 and the PC IP anyway)")
    check(wol.auto_bcast() is None and wol._bcast_pending == "10.0.0.7", "a second read in progress does not start a second one")
    await asyncio.sleep(0.6)
    check(wol.auto_bcast() == "10.0.0.255", "after the read in the thread - broadcast from memory")
    check("10.0.0.255" in wol.targets(), "magic packet idzie na broadcast sieci")

asyncio.run(bcast())

print("ALL OK" if not fails else f"FAIL {len(fails)}")
sys.exit(1 if fails else 0)
