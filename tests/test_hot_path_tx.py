"""Sender: the hot-path fixes - one persistent evdev reader (pump), no packets after "bye", state files written off the
event loop, lightbar writes that survive a transient error.
    python3 tests/test_hot_path_tx.py sender"""
import asyncio, builtins, contextlib, errno, io, json, os, sys, tempfile, threading, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
from sender_config import C
from evdev import InputDevice
import sender_net as net, sender_ctrl, sender_state as state, sender_streams as st, sender_supervisor as sup
from sender_ctrl import Ctrl
from proto import T_BYE, T_CLICK, T_MOUSE, T_STATE, T_STATE2

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)

TMP = tempfile.mkdtemp()


# ---------- 1. pump: one registration of the descriptor, whole batches per wake-up ----------
def make_dev(batches):
    """A real InputDevice object on a pipe: every byte written to the pipe is one wake-up that delivers the next batch."""
    r, w = os.pipe()
    os.set_blocking(r, False)
    dev = InputDevice.__new__(InputDevice)          # no open /dev/input node needed
    dev.fd = r
    queue = list(batches)
    def read():
        os.read(r, 1)                               # a wake-up byte; BlockingIOError when there is none
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        yield from item
    dev.read = read
    return dev, w

def watch_loop(loop):
    regs, unregs = [], []
    add, rem = loop.add_reader, loop.remove_reader
    loop.add_reader = lambda fd, cb, *a: (regs.append(fd), add(fd, cb, *a))[1]
    loop.remove_reader = lambda fd: (unregs.append(fd), rem(fd))[1]
    return regs, unregs

async def wake(w, n=1):
    for _ in range(n):
        os.write(w, b"x")
        await asyncio.sleep(0.02)

async def t_pump():
    loop = asyncio.get_running_loop()
    regs, unregs = watch_loop(loop)
    # events of several batches arrive in order; a spurious wake-up (BlockingIOError) does not end the loop
    dev, w = make_dev([[1, 2], BlockingIOError(), [3], OSError(errno.ENODEV, "gone")])
    got = []
    task = asyncio.create_task(st.pump(dev, got.append))
    await asyncio.sleep(0.02)
    await wake(w, 3)
    check(got == [1, 2, 3] and not task.done(), f"pump: events of all batches in order, spurious wake-up survived ({got})")
    check(regs == [dev.fd], f"pump: the descriptor is registered once for three batches ({regs})")
    await wake(w)
    try:
        await asyncio.wait_for(task, 1)
        err = None
    except OSError as e:
        err = e
    check(err is not None and err.errno == errno.ENODEV, "pump: OSError of the device (disappeared) is raised from the pump")
    check(unregs == [dev.fd], "pump: the descriptor is unregistered when the device is gone")

    # a bug in the handler ends the loop like it did with async_read_loop (the supervisor reports it)
    dev, w = make_dev([[1]])
    def bad(_e):
        raise ValueError("bug in the handler")
    task = asyncio.create_task(st.pump(dev, bad))
    await asyncio.sleep(0.02)
    await wake(w)
    try:
        await asyncio.wait_for(task, 1)
        err = None
    except ValueError as e:
        err = e
    check(err is not None and unregs[-1] == dev.fd, "pump: an exception of the handler is raised and the descriptor unregistered")

    # cancelling (pad teardown) unregisters too, before the caller closes the device
    dev, w = make_dev([[1]])
    task = asyncio.create_task(st.pump(dev, lambda e: None))
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    check(unregs[-1] == dev.fd and task.cancelled(), "pump: cancelling the task unregisters the descriptor")

    # a stand-in without a file descriptor (the other tests) is read with its own async_read_loop
    class Fake:
        async def async_read_loop(self):
            for i in (7, 8):
                yield i
    seen = []
    await st.pump(Fake(), seen.append)
    check(seen == [7, 8], "pump: a device that is not an InputDevice goes through async_read_loop")

asyncio.run(t_pump())


# ---------- 2. after "bye" no delayed repeat sends anything for the released slot ----------
out = []
net.emit = sender_ctrl.net.emit = lambda t, i, *v: out.append(t)

async def t_sealed():
    loop = asyncio.get_running_loop()
    c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
    c.mask = 1
    st._send_state_burst(c, loop)                    # one packet now, two repeats in 3 and 9 ms
    c.send_tap(1)                                    # a tap: the packet now and two repeats
    c.send_click()
    first = len(out)
    c.sealed = True
    await asyncio.sleep(0.05)
    check(len(out) == first and first >= 3, f"delayed repeats after sealing send nothing ({first} sent before, {len(out)} after)")
    out.clear()
    c.send_state(True); c.send_touch(force=True); c.send_click(); c.send_mouse(1, 1, 0)
    check(not out, "a sealed pad sends no state, touch, click or mouse packet")

    # _teardown: wait for the read loops first, close the devices after, send "bye" and seal at the end
    order = []
    class Dev:
        path = "/dev/input/event9"
        def ungrab(self): pass
        def close(self): order.append("close")
    async def stream():
        try:
            await asyncio.sleep(3600)
        finally:
            await asyncio.sleep(0.01)
            order.append("stream ended")
    c2 = Ctrl(1, "aa:bb:cc:dd:ee:01", Dev(), None, None)
    state.active[c2.key] = c2
    t = asyncio.create_task(stream())
    await asyncio.sleep(0.01)
    out.clear()
    net.emit_repeat = sup.emit_repeat = lambda t_, i, *v, **k: out.append(T_BYE)
    await sup._teardown(c2, [t])
    check(order == ["stream ended", "close"], f"_teardown waits for the read loops before closing the device ({order})")
    check(c2.sealed and T_BYE in out and c2.key not in state.active, "_teardown: bye sent, the pad is sealed and removed")

asyncio.run(t_sealed())


# ---------- 3. state files: fsync in a worker thread, in order, text made at call time ----------
async def until(cond, limit=5.0):
    """Wait for the worker thread (a slow CI disk can take longer than a fixed sleep)."""
    end = time.monotonic() + limit
    while not cond() and time.monotonic() < end:
        await asyncio.sleep(0.01)


async def t_files():
    path = os.path.join(TMP, "a.json")
    threads = []
    real = state.write_file
    def spy(p, text):
        threads.append(threading.current_thread().name)
        return real(p, text)
    state.write_file = spy
    data = {"v": 1}
    state.write_json_later(path, data)
    data["v"] = 99                                   # later changes must not reach the first write
    state.write_json_later(path, {"b": 2, "a": 1}, indent=1, sort_keys=True)
    await until(lambda: len(threads) >= 2 and os.path.exists(path))
    await asyncio.sleep(0.05)
    state.write_file = real
    check(len(threads) == 2 and all(n.startswith("state-files") for n in threads), f"files are written by the worker thread ({threads})")
    check(json.load(open(path)) == {"a": 1, "b": 2} and open(path).read().startswith('{\n "a": 1'), "the last request wins, indent and key order kept")
    p2 = os.path.join(TMP, "b.json")
    state.write_json_later(p2, data)
    await until(lambda: os.path.exists(p2))
    check(json.load(open(p2)) == {"v": 99}, "the text is made when the call is made (data as it was then)")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        state.write_json_later(os.path.join(TMP, "missing-dir", "x.json"), {})
        await until(lambda: "Cannot write" in buf.getvalue())
    check("Cannot write" in buf.getvalue(), "a failed write is only logged")

asyncio.run(t_files())
p3 = os.path.join(TMP, "c.json")
state.write_json_later(p3, {"sync": 1})
check(json.load(open(p3)) == {"sync": 1}, "without a running loop the file is written at once")
try:
    state.write_json_later(os.path.join(TMP, "missing-dir", "y.json"), {})
    err = None
except OSError as e:
    err = e
check(err is not None, "without a running loop an OSError reaches the caller (as with write_json)")
state.flush_writes()


# ---------- 4. lightbar: max_brightness read once, a transient error does not switch the lightbar off ----------
led = os.path.join(TMP, "led")
os.mkdir(led)
for name, text in (("max_brightness", "255\n"), ("multi_intensity", ""), ("brightness", "")):
    open(os.path.join(led, name), "w").write(text)
opened = []
real_open = builtins.open
def spy_open(file, *a, **k):
    opened.append(os.fspath(file))
    return real_open(file, *a, **k)
builtins.open = spy_open
c = Ctrl(0, "aa:bb:cc:dd:ee:ff", None, None, None)
c.led = led
c.apply_feedback(0, 0, 1, 2, 3)
c.apply_feedback(0, 0, 4, 5, 6)
c.apply_feedback(0, 0, 7, 8, 9)
builtins.open = real_open
check(sum(p.endswith("max_brightness") for p in opened) == 1 and c.led_color == (7, 8, 9),
      "max_brightness is read from sysfs once, not on every colour change")
check(open(os.path.join(led, "brightness")).read() == "255", "brightness gets the max value")

mi = os.path.join(led, "multi_intensity")
os.remove(mi); os.mkdir(mi)                          # opening it for writing now fails with EISDIR
c.apply_feedback(0, 0, 1, 1, 1)
c.apply_feedback(0, 0, 1, 1, 1)
check(c.led == led and c.led_color == (7, 8, 9), "two failed writes in a row: the lightbar is still there, the colour unchanged")
os.rmdir(mi); open(mi, "w").close()                  # the problem went away
c.apply_feedback(0, 0, 1, 1, 1)
check(c.led == led and c.led_color == (1, 1, 1) and c._led_fails == 0, "the next feedback retries and succeeds; the failure counter is reset")
os.remove(mi); os.mkdir(mi)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    for _ in range(3):
        c.apply_feedback(0, 0, 2, 2, 2)
check(c.led is None and "lightbar unavailable" in buf.getvalue(), "three failures in a row: the lightbar is given up until reconnect")
c2 = Ctrl(1, "aa:bb:cc:dd:ee:02", None, None, None)
c2.led = os.path.join(TMP, "no-such-led")
with contextlib.redirect_stdout(io.StringIO()):
    c2.apply_feedback(0, 0, 3, 3, 3)
check(c2.led is None, "a LED node that is gone is given up at once")

if fails:
    print("FAIL", len(fails), "errors")
    sys.exit(1)
print("ALL OK")
