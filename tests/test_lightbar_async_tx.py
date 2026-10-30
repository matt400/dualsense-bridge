"""Sender: the lightbar is written off the event loop and the latest colour wins.
    python3 tests/test_lightbar_async_tx.py sender"""
import asyncio
import os
import sys
import tempfile
import threading

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])

import sender_ctrl
import sender_net as net

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append((t, v))
TMP = tempfile.mkdtemp()


# ---------- 4. lightbar: off the loop, latest colour wins ----------
async def t_led():
    led = os.path.join(TMP, "led"); os.mkdir(led)
    for n, t in (("max_brightness", "255"), ("multi_intensity", ""), ("brightness", "")):
        open(os.path.join(led, n), "w").write(t)
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:02", None, None, None)
    c.led = led
    threads, gate, started = [], threading.Event(), threading.Event()
    real = c._led_io
    def slow(l, rgb):
        threads.append((threading.current_thread() is threading.main_thread(), rgb))
        started.set(); gate.wait(2)
        return real(l, rgb)
    c._led_io = slow
    c._write_led((1, 1, 1))
    await asyncio.sleep(0.05)
    check(started.is_set(), "lightbar: the write runs while the loop is free")
    for rgb in ((2, 2, 2), (3, 3, 3), (4, 4, 4)):
        c._write_led(rgb)
    gate.set()
    await asyncio.sleep(0.2)
    check([t[1] for t in threads] == [(1, 1, 1), (4, 4, 4)] and not any(t[0] for t in threads),
          f"lightbar: one write at a time in a worker, only the newest colour follows ({threads})")
    check(c.led_color == (4, 4, 4) and not c._led_busy, "lightbar: the final colour is recorded")
asyncio.run(t_led())

# ---------- 4b. lightbar: going back to the old colour while a write runs is not skipped ----------
async def t_led_back():
    led = os.path.join(TMP, "led_back"); os.mkdir(led)
    for n, t in (("max_brightness", "255"), ("multi_intensity", ""), ("brightness", "")):
        open(os.path.join(led, n), "w").write(t)
    c = sender_ctrl.Ctrl(0, "aa:bb:cc:dd:ee:03", None, None, None)
    c.led = led
    c._led_done((0, 0, 255), None)                       # the pad is blue
    gate, started, written = threading.Event(), threading.Event(), []
    real = c._led_io
    def slow(l, rgb):
        written.append(rgb)
        started.set(); gate.wait(2)
        return real(l, rgb)
    c._led_io = slow
    c.apply_feedback(0, 0, 255, 0, 0)                    # red is being written
    await asyncio.sleep(0.05)
    c.apply_feedback(0, 0, 0, 0, 255)                    # the PC wants blue again, before red has finished
    gate.set()
    await asyncio.sleep(0.2)
    check(written == [(255, 0, 0), (0, 0, 255)] and c.led_color == (0, 0, 255),
          f"lightbar: back to the old colour during a write is written ({written}, {c.led_color})")
asyncio.run(t_led_back())

sys.exit(1 if fails else 0)
