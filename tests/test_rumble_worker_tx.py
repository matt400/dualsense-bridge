"""Sender: the hidraw rumble fallback is opened in a worker thread.
    python3 tests/test_rumble_worker_tx.py sender"""
import asyncio
import os
import sys
import tempfile
import threading

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from evdev import ecodes as ec

import sender_ctrl
import sender_net as net
import sender_rumble as R

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- 5. rumble: the hidraw fallback is opened in a worker thread ----------
class FFBad:
    path = "/dev/input/event0"
    def upload_effect(self, e): raise OSError(5, "EIO")
    def write(self, *a): raise OSError(5, "EIO")
    def erase_effect(self, i): pass
    def capabilities(self): return {ec.EV_FF: [ec.FF_RUMBLE]}

async def t_rumble():
    path = os.path.join(TMP, "hidraw"); open(path, "wb").close()
    where = []
    r = R.Rumble(0, FFBad(), "auto")
    def fake_open(given=None):
        where.append(threading.current_thread() is threading.main_thread())
        r.fd = os.open(path, os.O_RDWR); r.bt = False; r.v2 = False
        return True
    r._open_hidraw = fake_open
    r.methods = ["evdev"]
    r.fails["evdev"] = R.FAIL_MAX - 1
    r.set(10, 20)
    check("hidraw" not in r.methods, "rumble: set() does not wait for the node to be opened")
    await asyncio.sleep(0.1)
    check(where == [False] and "hidraw" in r.methods, f"rumble: the node is opened in a worker ({where}, {r.methods})")
    r.close()
    # closed while the worker was opening: the descriptor must not leak
    r2 = R.Rumble(1, FFBad(), "auto")
    gate = threading.Event()
    def slow_open(given=None):
        gate.wait(2); r2.fd = os.open(path, os.O_RDWR); return True
    r2._open_hidraw = slow_open
    r2.methods = ["evdev"]; r2.fails["evdev"] = R.FAIL_MAX - 1
    r2.set(10, 20)
    await asyncio.sleep(0.02)
    r2.close(); gate.set()
    await asyncio.sleep(0.1)
    check(r2.fd is None and not r2.methods, "rumble: closing during the open leaves no descriptor and no way")
asyncio.run(t_rumble())

sys.exit(1 if fails else 0)
