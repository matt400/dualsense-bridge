"""Sender: start errors are reported; a finished teardown asks for a scan.
    python3 tests/test_start_errors_tx.py sender"""
import asyncio
import contextlib
import io
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])

import sender_ctrl
import sender_net as net
import sender_supervisor as sup

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- 7. start errors are reported; a finished teardown asks for a scan ----------
async def t_start():
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:06", None, None, None)
    async def boom(c_): raise ValueError("boom")
    buf = io.StringIO()
    evs = []
    sup.events.error = lambda *a: evs.append(a)
    sup._start_pad = boom
    sup.state.spawn = lambda coro: coro.close()
    with contextlib.redirect_stdout(buf):
        await sup.run_ctrl(c)
    check("start error" in buf.getvalue() and evs, "run_ctrl: an unexpected start error is reported")
    class W:
        event = asyncio.Event()
    sup._watch = W()
    sup.kick_scan()
    check(W.event.is_set(), "kick_scan: wakes the supervisor")
    sup._watch = None
    sup.kick_scan()
asyncio.run(t_start())

sys.exit(1 if fails else 0)
