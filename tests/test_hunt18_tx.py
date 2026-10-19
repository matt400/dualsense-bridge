"""Bug hunt 18 (nadajnik):
  - pad ponad max_pads: wpis w _first_seen zostawał na zawsze, next_scan_s() zwracało 0,05 s i supervisor budził się
    ~17 razy na sekundę bez końca (także po odejściu pada); pad znikał bez śladu w logu i w dzienniku okna
  - przy pełnych slotach nowy pad nie był w ogóle zauważany - skan po zmianie w /dev/input daje komunikat
  - adres broadcast do Wake-on-LAN (`ip addr`) liczony w pętli asyncio zatrzymywał ją, także odczyt pada
python3 tests/test_hunt18_tx.py sender"""
import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
import sender_events as events
import sender_supervisor as sup
import sender_wol as wol
from proto import E_NO_SLOT

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


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
    check(len(started) == 2, f"dwa pady dostały sloty ({started})")
    check(w.wakes <= 6, f"pad bez slotu nie budzi supervisora co 50 ms ({w.wakes} wybudzeń w 1,6 s)")
    check(not sup._first_seen, "pad bez slotu nie zostaje w _first_seen")
    evs = [e for e in events._queue if e[3] == E_NO_SLOT]
    check(len(evs) == 1 and evs[0][5] == b"aa:bb:cc:dd:ee:02", "komunikat o pominiętym padzie - raz")
    # pad odchodzi i wraca przy pełnych slotach: zmiana w /dev/input -> skan -> komunikat znowu
    present.discard("aa:bb:cc:dd:ee:02"); w.change = True
    await asyncio.sleep(0.6)
    present.add("aa:bb:cc:dd:ee:02"); w.change = True
    await asyncio.sleep(0.6)
    evs = [e for e in events._queue if e[3] == E_NO_SLOT]
    check(len(evs) == 2, f"pad ponownie połączony przy pełnych slotach - komunikat ponownie ({len(evs)})")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

asyncio.run(supervise())


# ---------- broadcast do Wake-on-LAN poza pętlą asyncio ----------
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
    check(blocked < 0.1, f"auto_bcast nie blokuje pętli ({blocked * 1000:.0f} ms)")
    check(first is None, "przed odczytem: brak broadcastu (wysyłka i tak idzie na 255.255.255.255 i IP komputera)")
    check(wol.auto_bcast() is None and wol._bcast_pending == "10.0.0.7", "drugi odczyt w trakcie nie startuje drugiego")
    await asyncio.sleep(0.6)
    check(wol.auto_bcast() == "10.0.0.255", "po odczycie w wątku - broadcast z pamięci")
    check("10.0.0.255" in wol.targets(), "magic packet idzie na broadcast sieci")

asyncio.run(bcast())

print("WSZYSTKO OK" if not fails else f"FAIL {len(fails)}")
sys.exit(1 if fails else 0)
