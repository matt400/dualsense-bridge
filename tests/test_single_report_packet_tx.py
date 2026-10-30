"""Sender: one pad report (stick + motion from two evdev devices) gives one packet at once, not two.
    python3 tests/test_single_report_packet_tx.py sender"""
import asyncio, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from evdev import ecodes as ec

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# jeden raport pada (gałka + ruch, dwa urządzenia evdev naraz) -> jeden pakiet od razu, nie dwa (drugi po limiterze)
import types
import sender_ctrl, sender_net, sender_supervisor as sup
from proto import T_STATE
sender_config.C.CAL_S, sender_config.C.MOTION_MIN_DT = 0, 0
sender_net.init()
sent = []
sender_net.emit = sender_ctrl.net.emit = lambda t, i, *v: sent.append((t, v))
sup.emit_repeat = lambda t, i, *v, **k: sent.append((t, v))
E = lambda t, c, v: types.SimpleNamespace(type=t, code=c, value=v)

import time as _time
import sender_streams


class ReportDev:
    """Jak jądro: jeden raport HID pada trafia naraz do urządzeń przycisków / gałek i ruchu (oba gotowe do odczytu
    w tej samej iteracji pętli)."""
    path = "/dev/input/event98"
    report = None
    def __init__(self, events):
        self.events = events
    def absinfo(self, c):
        raise OSError
    async def async_read_loop(self):
        await ReportDev.report
        for e in self.events:
            yield e
        await asyncio.sleep(1)


async def same_report():
    ReportDev.report = asyncio.get_running_loop().create_future()
    c = sender_ctrl.Ctrl(1, "aa:bb:cc:dd:ee:02", ReportDev([E(ec.EV_ABS, 0, 10), E(ec.EV_SYN, ec.SYN_REPORT, 0)]),
                         ReportDev([E(ec.EV_ABS, 4, 9000), E(ec.EV_SYN, ec.SYN_REPORT, 0)]), None)
    sender_config.C.PAD_MIN_DT = sender_config.C.MOTION_MIN_DT = 0.004
    c.t_state = 0.0
    sent.clear()
    tasks = [asyncio.create_task(sender_streams.stream_pad(c)), asyncio.create_task(sender_streams.stream_motion(c))]
    await asyncio.sleep(0.01)
    t0 = _time.monotonic()
    ReportDev.report.set_result(None)                      # raport pada przyszedł
    while not any(t == T_STATE for t, v in sent) and _time.monotonic() - t0 < 1:
        await asyncio.sleep(0)
    first = _time.monotonic() - t0
    await asyncio.sleep(0.02)
    states = [v for t, v in sent if t == T_STATE]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    c.cancel_timers()
    check(len(states) == 1 and states[0][0] == 10 and states[0][15] == 9000,
          f"gałka i ruch z jednego raportu w jednym pakiecie: {[(v[0], v[15]) for v in states]}")
    check(first < 0.002, f"pakiet od razu (bez czekania na okno limitera): {first * 1000:.2f} ms")

asyncio.run(same_report())

sys.exit(1 if fails else 0)
