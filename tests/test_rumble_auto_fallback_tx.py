"""Sender: the HID rumble fallback is used only in "auto" mode.
    python3 tests/test_rumble_auto_fallback_tx.py sender"""
import asyncio
import errno
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_rumble as R
from evdev import ecodes as ec

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- 9: the HID fallback only in "auto" ----------
class Dev:
    def capabilities(self):
        return {ec.EV_FF: [ec.FF_RUMBLE]}
    def upload_effect(self, eff):
        raise OSError(errno.ENODEV, "No such device")
    def write(self, *a):
        pass


async def fallback():
    for mode, expect_open in (("evdev", False), ("auto", True)):
        r = R.Rumble(1, Dev(), mode)
        opened = []
        r._open_hidraw = lambda given=None, o=opened: (o.append(1) or False)
        opened.clear()
        r.set(50, 60)
        check(bool(opened) == expect_open, f"mode {mode!r}: evdev failure {'opens' if expect_open else 'does not open'} hidraw (opened={bool(opened)})")
        r.close()

asyncio.run(fallback())

sys.exit(1 if fails else 0)
