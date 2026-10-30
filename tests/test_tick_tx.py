"""Sender: TickTimer (the audio tick): a deadline in time.monotonic() seconds, on a timerfd or - without one - on loop.call_at.
Fires on time and never early, cancel, arming again replaces the deadline, a periodic tick from the callback does not drift, a deadline
in the past, a fake clock, a broken timerfd falls back, the descriptor is released."""
import asyncio, os, sys, time
sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import sender_tick as T

ok = lambda m: print("OK  ", m)
EARLY_S = 0.001                              # call_at may run a callback a clock-resolution early, a timerfd never does
LATE_S = 0.05                                # generous: a loaded CI machine, not the precision (that is measured on the pad)
HAVE_TIMERFD = T.use_timerfd


async def wait_for(cond, timeout=1.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        await asyncio.sleep(0.002)
    return cond()


async def scenarios(timerfd: bool):
    T.use_timerfd = timerfd
    name = "timerfd" if timerfd else "call_at"

    # -- fires at the deadline, not before it
    got = []
    t = T.TickTimer(lambda: got.append(time.monotonic()))
    due = time.monotonic() + 0.03
    assert t.arm(due) is t and t.precise is timerfd, (name, t.precise)
    assert await wait_for(lambda: got)
    assert due - EARLY_S <= got[0] <= due + LATE_S, (name, got[0] - due)
    await asyncio.sleep(0.05)
    assert len(got) == 1, "one-shot: it runs once"
    t.close()
    ok(f"{name}: runs once, at the deadline and not before it")

    # -- cancel
    got = []
    t = T.TickTimer(lambda: got.append(1))
    t.arm(time.monotonic() + 0.03)
    t.cancel()
    t.cancel()
    await asyncio.sleep(0.08)
    assert not got, "a cancelled timer does not run"
    t.arm(time.monotonic() + 0.01)
    assert await wait_for(lambda: got), "...and can be armed again"
    t.close()
    ok(f"{name}: cancel (twice is fine), then armed again")

    # -- arming again replaces the earlier deadline
    got = []
    t = T.TickTimer(lambda: got.append(time.monotonic()))
    t.arm(time.monotonic() + 0.3)
    near = time.monotonic() + 0.02
    t.arm(near)
    assert await wait_for(lambda: got) and got[0] < near + LATE_S
    await asyncio.sleep(0.4)
    assert len(got) == 1, "the first deadline is gone"
    t.close()
    ok(f"{name}: arming again replaces the earlier deadline")

    # -- a periodic tick (arm from the callback, due += period): absolute deadlines do not drift
    period, ticks, late = 32 / 3000, [], []
    t0 = time.monotonic()
    state = {"due": t0 + period}

    def tick():
        now = time.monotonic()
        late.append(now - state["due"])
        ticks.append(now)
        state["due"] += period
        if len(ticks) < 40:
            timer.arm(state["due"])

    timer = T.TickTimer(tick)
    timer.arm(state["due"])
    assert await wait_for(lambda: len(ticks) == 40, 3.0)
    assert min(late) >= -EARLY_S, min(late)
    assert ticks[-1] - t0 < 40 * period + LATE_S, "no drift: the 40th tick is where 40 periods put it"
    timer.close()
    ok(f"{name}: 40 periodic ticks stay on the absolute grid (worst lateness {max(late) * 1000:.2f} ms)")

    # -- a deadline in the past runs at the next turn of the loop
    got = []
    t = T.TickTimer(lambda: got.append(1))
    t.arm(time.monotonic() - 5.0)
    assert await wait_for(lambda: got, 0.5), "a deadline that has passed fires at once"
    t.arm(time.monotonic() - 1e9)
    assert await wait_for(lambda: len(got) == 2, 0.5), "...even one that is absurdly old (no negative timerfd value)"
    t.close()
    ok(f"{name}: deadlines in the past fire at once")

    # -- the caller's clock may be a fake one (the stream tests): only the distance to now counts
    real = time.monotonic
    base = [1000.0]
    time.monotonic = lambda: base[0]          # type: ignore[assignment]
    try:
        got = []
        t = T.TickTimer(lambda: got.append(real()))
        start = real()
        t.arm(base[0] + 0.04)
    finally:
        time.monotonic = real
    if timerfd:                              # call_at compares with the loop's real clock: a fake one only fires at once, as before
        assert await wait_for(lambda: got) and 0.03 <= got[0] - start <= 0.04 + LATE_S, got[0] - start
    t.close()
    ok(f"{name}: a fake clock is converted through its distance to now" if timerfd else f"{name}: a fake clock does not break it")

    # -- the descriptor is released by close, which can be called twice
    t = T.TickTimer(lambda: None)
    t.arm(time.monotonic() + 1.0)
    fd = t._fd
    assert (fd >= 0) is timerfd
    t.close()
    t.close()
    if timerfd:
        try:
            os.fstat(fd)
        except OSError:
            pass
        else:
            raise AssertionError("the timerfd is still open after close")
    assert t._fd == -1 and not t.precise
    ok(f"{name}: close releases the descriptor and is safe twice")


async def broken_timerfd():
    T.use_timerfd = HAVE_TIMERFD
    if not HAVE_TIMERFD:
        ok("no timerfd on this Python: the broken-timerfd case is covered by the call_at run")
        return
    real = T._settime
    got = []
    t = T.TickTimer(lambda: got.append(1))
    try:
        def boom(*a, **k):
            raise OSError(22, "Invalid argument")
        T._settime = boom
        t.arm(time.monotonic() + 0.02)
    finally:
        T._settime = real
    assert not t.precise and t._fd == -1, "a refused timerfd_settime drops the descriptor"
    assert await wait_for(lambda: got), "...and the tick still arrives, through call_at"
    t.close()
    ok("a timerfd that refuses falls back to call_at and the tick still arrives")


async def main():
    if HAVE_TIMERFD:
        await scenarios(True)
    await scenarios(False)
    await broken_timerfd()
    T.use_timerfd = HAVE_TIMERFD


asyncio.run(main())
print("TICK TX OK")
