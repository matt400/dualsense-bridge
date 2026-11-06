"""Receiver: regressions from the seventh review - a touchpad click released in the Test view does not stay pressed in the game,
the thread error counter reset after quiet operation, TV mode: releasing PS before the shortcut button is not navigation.
    python3 tests/test_touchpad_tv_mode.py receiver"""
import contextlib, io, os, sys, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_net as N
import rx_state as S
from proto import T_CLICK

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- touchpad click and the output lock (Test view) ----------
S.mouse_mode[0] = False                        # touchpad as DS4 touch (the click goes to the game)
state(0)
feed(T_CLICK, 0, 1)
check(S.states[0]["tp"] and S.output(0)["tp"], "touchpad click pressed - the game sees it")
S.set_blocked(0, True)
check(not S.output(0)["tp"], "Test view: the game gets a neutral state")
feed(T_CLICK, 0, 0)                            # puszczony w trakcie testu
S.set_blocked(0, False)
check(not S.output(0)["tp"], "after the test a click released meanwhile does not stay pressed in the game")
feed(T_CLICK, 0, 0)
check(not S.output(0)["tp"], "'released' repeats from the sender change nothing")
S.set_blocked(0, True)
feed(T_CLICK, 0, 1)
S.set_blocked(0, False)
check(S.output(0)["tp"], "a click held through the end of the test goes live (like buttons and sticks)")
feed(T_CLICK, 0, 0)
check(not S.output(0)["tp"], "... and releases normally")
S.mouse_mode[0] = True
S.set_blocked(0, True)
feed(T_CLICK, 0, 1)
S.set_blocked(0, False)
check(not S.states[0]["tp"], "mouse mode: the lock does not set the DS4 touch click")
feed(T_CLICK, 0, 0)
S.mouse_mode[0] = False


# ---------- receiver thread: the error counter resets after quiet operation ----------
S.STABLE_S = 0.05
calls = [0]


def flaky():
    calls[0] += 1
    if calls[0] <= 2:
        raise RuntimeError(f"error {calls[0]}")
    if calls[0] == 3:
        time.sleep(0.08)                       # longer than STABLE_S without an error
        raise RuntimeError("rare error")

buf = io.StringIO()
t0 = time.monotonic()
with contextlib.redirect_stdout(buf):
    S.run_forever("test", flaky)
log = buf.getvalue()
check(log.count("thread error - restarting:\n") == 2 and "#3" not in log,
      "an error after quiet operation counted from scratch (full trace, short pause)")
check(time.monotonic() - t0 < 1.08, f"pauses 0.2 + 0.4 + 0.2 s, not 0.6 s the third time: {time.monotonic() - t0:.2f} s")


# ---------- TV mode: PS + button is a shortcut, not navigation (also when PS is released first) ----------
try:
    import rx_qt_tv
except ImportError:
    print("SKIP  TV mode (no PySide6)")
else:
    from rx_buttons import BIT, BUTTON_CODES
    PS = BIT[BUTTON_CODES["ps"]]
    CROSS = BIT[BUTTON_CODES["cross"]]
    acts = []

    class Nav(rx_qt_tv.PadNav):
        def target(self):
            return "okno"

        def act(self, root, name):
            acts.append(name)

    nav = Nav(types.SimpleNamespace(view="out"))
    S.alive[0] = True
    t = 100.0

    def step(mask=0, hat=(0, 0)):
        global t
        state(0, mask=mask, hat=hat)
        t += 0.033
        nav.tick(t)

    step()
    step(PS); step(PS, (1, 0))                 # PS + → = next profile
    step(0, (1, 0)); step(0, (1, 0))           # PS released before the D-pad
    step()
    check(acts == [], f"PS + →, then PS released: no focus step in the window: {acts}")
    step(PS); step(PS | CROSS); step(CROSS); step()
    check(acts == [], f"PS + ✕ (master action), PS released first: ✕ does not click in the window: {acts}")
    step(0, (1, 0)); step()
    check(acts == ["dpad_right"], f"samo → dalej przesuwa fokus: {acts}")
    acts.clear()
    step(0, (1, 0))
    for _k in range(20):                        # held -> repeats
        step(0, (1, 0))
    n = len(acts)
    step(PS, (1, 0))
    for _k in range(20):
        step(PS, (1, 0))
    check(n > 1 and len(acts) == n, f"PS added to a held direction stops the repeats: {n} -> {len(acts)}")
    step()

print("ALL OK" if not fails else f"FAILURES: {len(fails)}")
sys.exit(1 if fails else 0)
