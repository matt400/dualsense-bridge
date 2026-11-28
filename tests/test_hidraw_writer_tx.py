"""Sender: hidraw writes from a thread (sender_hidwrite) - a slow write does not stall the loop, order, copies, errors, close."""
import asyncio, errno, os, sys, tempfile, threading, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_config; sender_config.init(["127.0.0.1"])
import sender_hidwrite as HW
import sender_report as RP
import sender_audiomode as A
import sender_rumble as R
from sender_haptics import REPORT_LEN, Report32

TMP = tempfile.mkdtemp()
from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


class Dev:
    def capabilities(self):
        return {}


def reports(path, size):
    data = open(path, "rb").read()
    return [data[k:k + size] for k in range(0, len(data), size)]


async def settle(writer, timeout=2.0):
    """Wait until the writer thread has emptied its queue, finished the write it took last, and the loop ran its callbacks.

    An empty queue alone is not enough: the thread may still be inside os.write for the last report (on a loaded CI runner
    for longer than a fixed grace), so the write counters must also stay unchanged for a moment.
    """
    end = time.monotonic() + timeout
    while writer._q.qsize() and time.monotonic() < end:
        await asyncio.sleep(0.002)
    st = writer.stats
    last = (st.n, st.errors)
    while time.monotonic() < end:
        await asyncio.sleep(0.02)
        now = (st.n, st.errors)
        if now == last and not writer._q.qsize():
            break
        last = now


async def main():
    loop = asyncio.get_running_loop()
    real_write = os.write

    # ---------- order, copies of a reused buffer, close drains and closes the descriptor ----------
    path = os.path.join(TMP, "hidraw0")
    open(path, "wb").close()
    errs, oks = [], []
    fd = os.open(path, os.O_WRONLY)
    w = HW.HidWriter(fd, "t0", lambda k, e: errs.append((k, e)), oks.append, loop)
    buf = bytearray(8)
    for n in range(3):
        buf[:] = bytes([n]) * 8
        w.write(buf, "audio", reused=True)                  # the caller overwrites buf right after: the writer must have copied it
        w.write(bytes([0xA0 + n]) * 4, "hid")
    w.close(wait=True)
    data = open(path, "rb").read()
    check(data == b"".join(bytes([n]) * 8 + bytes([0xA0 + n]) * 4 for n in range(3)), "order kept, a reused buffer is copied")
    try:
        os.fstat(fd)
        closed = False
    except OSError:
        closed = True
    check(closed and not errs, "close() writes what is queued, then the thread closes the descriptor")
    try:
        w.write(b"x", "hid")
        check(False, "a closed writer refuses writes")
    except OSError as e:
        check(e.errno == errno.EBADF, "a closed writer raises EBADF (the callers treat it as a failed write)")

    # ---------- a stuck write: the loop keeps running, the queue is bounded ----------
    gate = threading.Event()

    def stuck(fd, data):
        gate.wait(2.0)
        return real_write(fd, data)
    path = os.path.join(TMP, "hidraw1")
    open(path, "wb").close()
    fd = os.open(path, os.O_WRONLY)
    w = HW.HidWriter(fd, "t1", lambda k, e: errs.append((k, e)), oks.append, loop)
    HW.os.write = stuck
    try:
        t0 = time.monotonic()
        refused = 0
        for n in range(HW.AUDIO_PENDING + 4):
            try:
                w.write(bytes([n]), "audio", limit=HW.AUDIO_PENDING)
            except BlockingIOError:
                refused += 1
        took = time.monotonic() - t0
        ticks = 0
        for _ in range(5):                                   # the loop is free while the thread waits in os.write
            await asyncio.sleep(0.005)
            ticks += 1
        check(took < 0.05 and ticks == 5, f"the loop does not wait for a stuck hidraw write ({took * 1000:.1f} ms to queue)")
        check(refused >= 3, f"a full queue refuses reports instead of building a late burst ({refused} refused)")
    finally:
        gate.set()
        await settle(w)
        HW.os.write = real_write
    w.close(wait=True)
    check(len(open(path, "rb").read()) == HW.AUDIO_PENDING + 4 - refused, "everything queued is written once the write returns")

    # ---------- Rumble with a writer: reports go through the thread, errors come back to the loop ----------
    path = os.path.join(TMP, "hidraw2")
    open(path, "wb").close()
    r = R.Rumble(2, Dev(), "hidraw", hidraw=(path, True), writer_loop=loop)
    check(r._writer is not None, "a Rumble with writer_loop writes through a writer thread")
    r.set(10, 20)
    await settle(r._writer)
    got = reports(path, 78)
    check(got and got[0][0] == RP.BT_REPORT_ID and got[0][3 + RP.MOTOR_LEFT_AT] == 20, "the rumble report reaches hidraw")

    check(r.haptics_begin(), "haptics start on a Bluetooth pad")
    await settle(r._writer)
    rep = Report32()
    for n in range(3):
        rep.payload[:] = bytes([n + 1]) * len(rep.payload)
        check(r.write_haptics(rep), "a haptics report is queued")
    await settle(r._writer)
    # split the file by report: a rumble refresh (0x31, 78 B) can land between or after the haptics reports on a slow runner
    data, i, hapt = open(path, "rb").read(), 0, []
    while i < len(data):
        if data[i] == 0x32:
            hapt.append(data[i + 13])
        i += REPORT_LEN if data[i] == 0x32 else 78
    check(i == len(data) and hapt[-3:] == [1, 2, 3], f"each haptics report keeps its own samples (copied before the next tick) {hapt}")

    # stop the motors first: while they run, the rumble watchdog (WATCHDOG_S) and the repeat send HID reports, and on a slow
    # runner one could fall inside the failing window below and count as an error of the HID way
    r.set(0, 0)
    await asyncio.sleep(3 * R.REPEAT_S)
    await settle(r._writer)
    calls = []

    def failing(fd, data):
        calls.append(len(data))
        raise OSError(errno.EIO, "io")
    HW.os.write = failing
    try:
        for _ in range(A.HAPTICS_FAIL_MAX + 5):
            r.write_haptics(rep)
            await asyncio.sleep(0.001)
        await settle(r._writer)
    finally:
        HW.os.write = real_write
    check(not r.haptics and R.WAY_HIDRAW in r.methods,
          "a run of failed audio writes ends the stream, not the HID way (errors counted from the thread)")

    r.set(0, 0)
    HW.os.write = lambda fd, data: (_ for _ in ()).throw(OSError(errno.ENODEV, "gone"))
    try:
        r.set(50, 50)
        await asyncio.sleep(0.05)
    finally:
        HW.os.write = real_write
    check(R.WAY_HIDRAW not in r.methods and r._writer is None and r.fd is None,
          "a pad that is gone (ENODEV in the thread) switches the HID way off and drops the writer")
    r.close()

    # ---------- close: the final stop report goes out before the descriptor closes ----------
    path = os.path.join(TMP, "hidraw3")
    open(path, "wb").close()
    r = R.Rumble(3, Dev(), "hidraw", hidraw=(path, True), writer_loop=loop)
    r.set(100, 100)
    w = r._writer
    r.close()
    got = reports(path, 78)
    check(got and got[-1][3 + RP.MOTOR_LEFT_AT] == 0 and got[-1][3 + RP.MOTOR_RIGHT_AT] == 0 and not w._thread.is_alive(),
          "close() waits for the stop report, then the writer ends")


asyncio.run(main())
sys.exit(1 if fails else 0)
