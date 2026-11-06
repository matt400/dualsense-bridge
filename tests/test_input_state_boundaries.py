"""Receiver: regressions from the ninth review - the state of shortcuts, master actions and the console mode button at the boundary of the
Test view / output lock counted from the inputs held at that moment (not "as after the pad connected").
    python3 tests/test_input_state_boundaries.py receiver"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_console
import rx_hotkeys as Hk
import rx_master
import rx_profiles as P
import rx_testmode as TM
import rx_winapi as Wapi
from rx_buttons import BUTTON_CODES
from rx_console import Console, ConsoleConfig

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

PS, SHARE, CROSS, OPTIONS = (BIT[BUTTON_CODES[n]] for n in ("ps", "share", "cross", "options"))

def game_mask():
    with S.lock:
        return S.output(0)["mask"]

def keys():
    return [c[1:] for c in calls if c[0] == "k"]

def neutral():
    """Pad connected, nothing held, Test view closed, shortcut slots after the first state."""
    TM.set_active(False); P.block(0, False)
    state(0, mask=CROSS); state(0); time.sleep(Hk.TAP_S + 0.05)
    calls.clear()


# ---------- the first PS after leaving the Test view reaches the game ----------
neutral()
TM.set_active(True); TM.set_active(False)          # e.g. the Test window lost focus (a click into the game)
state(0, mask=PS)
check(not Hk.slots[0].used, "a fresh PS after the test is not 'PS held since pad power-on'")
state(0)
check(game_mask() & PS, "PS released -> a PS pulse for the game (swallowed before)")
time.sleep(Hk.TAP_S + 0.05)
check(not game_mask() & PS, "the PS pulse ends")

# ---------- the same after the output lock is removed (P.block, window thread) ----------
neutral()
P.block(0, True); P.block(0, False)
state(0, mask=PS); state(0)
check(game_mask() & PS, "the first PS after the lock is removed reaches the game")
time.sleep(Hk.TAP_S + 0.05)

# ---------- PS held across the test boundary + Share = screenshot, no Share and no PS in the game ----------
neutral()
TM.set_active(True)
state(0, mask=PS)                                  # PS pressed in the test (shortcuts paused)
TM.set_active(False)                               # leaving the test, PS still held
state(0, mask=PS | SHARE)
time.sleep(0.1)                                    # the shortcut action in a separate thread
check((Wapi.VK_LWIN, Wapi.VK_SNAPSHOT) in keys(), "PS (held across the boundary) + Share -> screenshot")
check(not game_mask() & SHARE, "Share from the combo does not reach the game (before: Share in the game, no screenshot)")
state(0, mask=PS); state(0)
check(not game_mask() & PS, "PS held across the boundary and used in a shortcut - no PS pulse for the game")
time.sleep(Hk.TAP_S + 0.05)

# ---------- PS held across the boundary and released alone - no pulse and no Game Bar ----------
neutral()
TM.set_active(True); state(0, mask=PS); TM.set_active(False)
state(0, mask=PS, lt=200); state(0)
time.sleep(0.05)
check(not game_mask() & PS and not keys(), "PS held across the test boundary - nothing reaches the game or Windows")

# ---------- a pad connection still "as after connecting": PS held since power-on does not reach the game ----------
neutral()
Hk.reset(0); rx_master.reset(0); N._digital[0] = None
state(0, mask=PS); state(0)
check(not game_mask() & PS, "reset without keep (new pad): PS held since power-on gives no pulse")

# ---------- master: PS held across the boundary + Options = action (pause) at once ----------
neutral()
TM.set_active(True); state(0, mask=PS); TM.set_active(False)
state(0, mask=PS | OPTIONS)
time.sleep(0.1)
check((Wapi.VK_ESCAPE,) in keys(), "PS + Options after the test -> master pause")
check(not game_mask() & OPTIONS, "Options from the combo does not reach the game")
state(0, mask=PS); state(0); time.sleep(Hk.TAP_S + 0.05)

# ---------- console mode: R2 released in the test does not lose the first click after the test ----------
log = []
rx_console._display = lambda mode: log.append(("display", mode))
rx_console._audio = lambda name: log.append(("audio", name))
rx_console._toast = lambda *a: log.append(("toast",))
rx_console._tv_mode = lambda *a: None
c = Console(ConsoleConfig(toggle_button="r2", allowed_apps=())); c.start()
done = threading.Event(); c._jobs.put(done.set); done.wait(5)
rx_console.ctl = c
neutral()
state(0, rt=255)                                   # R2 pressed before the test
c._last_click[0] = 0.0                             # this press is not part of the series below
TM.set_active(True)
state(0, rt=0)                                     # released in the test (console mode does not see it)
TM.set_active(False)
state(0, rt=255); state(0, rt=0); state(0, rt=255); state(0, rt=0)
end = time.monotonic() + 3
while not c.on and time.monotonic() < end:
    time.sleep(0.01)
check(c.on, "double R2 right after the test switches console mode (the first click was lost before)")
c.close(); rx_console.ctl = None
neutral()

if fails:
    print("FAIL", len(fails), "failures")
    sys.exit(1)
print("ALL OK")
