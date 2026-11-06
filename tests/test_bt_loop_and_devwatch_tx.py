"""Sender: Bluetooth.loop clears the wake event before the work; DevWatch.start closes the inotify descriptor when add_reader fails.
    python3 tests/test_bt_loop_and_devwatch_tx.py sender"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_bt as SB
import sender_supervisor as sup

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- Bluetooth.loop: wake cleared before the work ----------
async def poke_during_work():
    """A poke that arrives while an iteration works must end that iteration's wait at once (not after IDLE_S / 5 = 1 s)."""
    b = SB.Bluetooth(run=None, emit=lambda *a: None)
    turns: list[float] = []

    def active(now):
        turns.append(time.monotonic())
        if len(turns) == 1:
            b.poke()                        # e.g. a command from the window during the first iteration
        return False

    async def no_adapters():
        pass

    b.active, b.check_adapters, b.kill_scan = active, no_adapters, lambda: None
    task = asyncio.create_task(b.loop())
    while len(turns) < 2 and (not turns or time.monotonic() - turns[0] < 0.5):
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return turns


real_new = SB.net.new_receiver
SB.net.new_receiver = lambda now: False
turns = asyncio.run(poke_during_work())
SB.net.new_receiver = real_new
check(len(turns) >= 2 and turns[1] - turns[0] < 0.2, f"Bluetooth.loop: a poke during the work is not lost ({len(turns)} turns)")

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
