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
    """Pad bez evdev FF (albo z FF, który się psuje)."""
    def __init__(self, ff=False):
        self.ff = ff
    def capabilities(self):
        return {ec.EV_FF: [ec.FF_RUMBLE]} if self.ff else {}
    def upload_effect(self, eff):
        raise OSError(errno.ENOSYS, "Function not implemented")
    def write(self, *a):
        pass


async def main():
    # chwilowy EAGAIN przy zapisie raportu HID nie może wyłączyć wibracji na zawsze
    path = os.path.join(TMP, "hidraw")
    open(path, "wb").close()
    r = R.Rumble(0, Dev(), "hidraw", hidraw=(path, True))
    real = r._send_hidraw
    def busy(small, large, audio=False):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
    r._send_hidraw = busy
    r.set(100, 200)
    check(r.methods == ["hidraw"], f"EAGAIN przy zapisie: HID zostaje w użyciu ({r.methods})")
    r._send_hidraw = real
    await asyncio.sleep(0.5)
    r.set(100, 200)
    check(os.path.getsize(path) > 0, "po chwilowym błędzie kolejne odświeżenie dochodzi do pada")
    r.close()

    # evdev FF psuje się, zapas przez HID też rzuca - wyjątek nie może wyjść z set() (wywalał callback pętli asyncio)
    path2 = os.path.join(TMP, "hidraw2")
    open(path2, "wb").close()
    r = R.Rumble(1, Dev(ff=True), "auto")           # zapas przez HID tylko w trybie auto (w "evdev" wybór z konfiguracji zostaje)
    check(r.methods == ["evdev"], "start: tylko evdev FF")
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
    check(raised is None, f"błąd evdev i zapasowego HID nie wychodzi z set(): {raised!r}")
    check(r.methods == ["hidraw"], f"pojedynczy EIO nie wyłącza raportu HID: {r.methods}")
    for k in range(R.FAIL_MAX):
        r.set(50 + k + 1, 60)                       # zmiana = zapis (bez niej set() nic nie wysyła)
    check(r.methods == [] and r.fd is None, f"stały EIO: oba zepsute sposoby wyłączone, deskryptor zamknięty: {r.methods}")
    r.close()

asyncio.run(main())

sys.exit(1 if fails else 0)
