"""Sender: Bluetooth.loop clears the wake event before the work; DevWatch.start closes the inotify descriptor when add_reader fails.
    python3 tests/test_bt_loop_and_devwatch_tx.py sender"""
import asyncio, inspect, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB
import sender_supervisor as sup

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- Bluetooth.loop: wake cleared before the work ----------
src = inspect.getsource(SB.Bluetooth.loop)
check(0 <= src.index("self.wake.clear()") < src.index("self.active(now)") < src.index("self.wake.wait()"),
      "Bluetooth.loop clears the wake event at the top of an iteration, before the work and the wait")
check(src.count("self.wake.clear()") == 1, "...and only there")

# ---------- DevWatch.start: no descriptor leak ----------
def open_fds():
    return len(os.listdir("/proc/self/fd"))

before = open_fds()
w = sup.DevWatch()
ok_start = w.start(tempfile.mkdtemp().encode())           # no running loop here: add_reader cannot even be reached
check(ok_start is False and w.fd is None and open_fds() == before, f"DevWatch.start: failure after inotify_init closes the descriptor ({before} -> {open_fds()})")


async def reader_fails():
    loop = asyncio.get_running_loop()
    real = loop.add_reader
    def refuse(*_a, **_k):
        raise OSError("no reader")
    loop.add_reader = refuse
    n = open_fds()
    try:
        w = sup.DevWatch()
        res = w.start(tempfile.mkdtemp().encode())
    finally:
        loop.add_reader = real
    check(res is False and w.fd is None and open_fds() == n, "DevWatch.start: add_reader fails -> False and the descriptor is closed")

asyncio.run(reader_fails())

sys.exit(1 if fails else 0)
