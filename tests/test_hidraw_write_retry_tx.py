"""Sender: a transient EAGAIN on a HID report write does not turn rumble off for good.
    python3 tests/test_hidraw_write_retry_tx.py sender"""
import asyncio, errno, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_rumble as R
from evdev import ecodes as ec

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


class Dev:
    """A pad without evdev FF (or with an FF that breaks)."""
    path = "/dev/input/event-test"
    def __init__(self, ff=False):
        self.ff = ff
    def capabilities(self):
        return {ec.EV_FF: [ec.FF_RUMBLE]} if self.ff else {}
    def upload_effect(self, eff):
        raise OSError(errno.ENOSYS, "Function not implemented")
    def write(self, *a):
        pass


async def main():
    # a transient EAGAIN while writing the HID report must not turn rumble off for good
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    real = r._send_hidraw
    def busy(small, large, audio=False):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
    r._send_hidraw = busy
    r.set(100, 200)
    check(r.methods == ["hidraw"], f"EAGAIN on write: HID stays in use ({r.methods})")
    r._send_hidraw = real
    await asyncio.sleep(0.5)
    r.set(100, 200)
    check(os.path.getsize(path) > 0, "after a transient error the next refresh reaches the pad")
    r.close()

    # evdev FF breaks, the HID fallback raises too - the exception must not leave set() (it crashed the asyncio loop callback)
    path2 = os.path.join(TMP, "hidraw2")
    open(path2, "wb").close()
    r = R.Rumble(1, Dev(ff=True), "auto")           # HID fallback only in auto mode (in "evdev" the configured choice stays)
    check(r.methods == ["evdev"], "start: evdev FF only")
    r._open_hidraw = lambda given=None: (setattr(r, "fd", os.open(path2, os.O_RDWR)) or True)
    def broken(small, large, audio=False):
        raise OSError(errno.EIO, "Input/output error")
    r._send_hidraw = broken
    try:
        r.set(50, 60)
        await asyncio.sleep(0.1)                    # the fallback is opened in a worker thread
        raised = None
    except OSError as e:
        raised = e
    check(raised is None, f"an evdev and HID fallback error does not leave set(): {raised!r}")
    check(r.methods == ["hidraw"], f"a single EIO does not turn the HID report off: {r.methods}")
    for k in range(R.FAIL_MAX):
        r.set(50 + k + 1, 60)                       # a change = a write (without one set() sends nothing)
    check(r.methods == [] and r.fd is None, f"permanent EIO: both broken methods turned off, descriptor closed: {r.methods}")
    r.close()

asyncio.run(main())

sys.exit(1 if fails else 0)
