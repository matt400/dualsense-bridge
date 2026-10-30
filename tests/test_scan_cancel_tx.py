"""Sender: a scan cancelled in its worker thread closes the devices it opened.
    python3 tests/test_scan_cancel_tx.py sender"""
import asyncio, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_supervisor as sup

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- a cancelled scan closes the devices it opened ----------
class Opened:
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


async def cancelled_scan():
    devs = [Opened(), Opened(), Opened()]
    def slow_discover():
        time.sleep(0.15)
        return {"k": {"pad": devs[0], "motion": devs[1], "touch": devs[2]}}
    real = sup.discover
    sup.discover = slow_discover
    try:
        task = asyncio.create_task(sup._scan_once())
        await asyncio.sleep(0.03)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.3)               # the worker thread finishes and the callback runs
    finally:
        sup.discover = real
    check(all(d.closed for d in devs), "a scan cancelled in its thread: every device it opened is closed")

asyncio.run(cancelled_scan())

sys.exit(1 if fails else 0)
