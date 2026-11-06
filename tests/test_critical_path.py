"""Reproductions of receiver critical-path bugs (review 2026-10-16) - every test passes AFTER the fixes."""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_ds4, rx_master, rx_profiles as P, rx_testmode as TM
from proto import T_BYE
from rx_buttons import BUTTON_CODES
from chk import check, fails

TRI = BIT[BUTTON_CODES["triangle"]]
PS = BIT[BUTTON_CODES["ps"]]

# ---- leaving the Test view (Options + Create) with a trigger pressed ----
# was: KeyError('r2') in rx_master.filter_inputs -> the exit stopped halfway: the view closed, but the slot still
# locked (the game got a neutral state until the Test view was opened again)
state(0)
TM.set_active(True); P.block(0, True)
try:
    state(0, mask=TM.ESCAPE_MASK, rt=200)
    err = None
except Exception as e:
    err = e
check(err is None, f"leaving the Test view with R2 pressed raises nothing: {err!r}")
check(not TM.active and not S.states[0]["blocked"], "after the pad exit the slot is not locked (the game gets the inputs)")
state(0, mask=TM.ESCAPE_MASK, rt=210, sticks=(20, 128, 128, 128))
s = S.states[0]
check(s["rt"] == 210 and s["lx"] == 20, "trigger and stick reach the game live after the exit")
OPT = BIT[BUTTON_CODES["options"]]
check(not s["mask"] & TM.ESCAPE_MASK, "Options + Create from the combo do not reach the game until released")
state(0, mask=OPT)                                   # Create released, Options still held - still consumed
check(not S.states[0]["mask"] & OPT, "Options consumed until released")
state(0); state(0, mask=OPT)
check(S.states[0]["mask"] & OPT, "a new Options press after the release reaches the game")
rx_master.slots[0].sup = frozenset({"l2", "dpad_up"})  # the filter copes with triggers in the set of consumed inputs
check(rx_master.filter_inputs(0, TRI, 0, -1) == (TRI, 0, 0), "filter_inputs: a trigger in sup does not raise")
rx_master.reset(0)
state(0)

# ---- game rumble / colour do not carry over to the next pad in the slot ----
cbs = []
class Pad:
    def register_notification(self, cb): cbs.append(cb)
    def unregister_notification(self): pass
    def update_extended_report(self, rep): pass
rx_ds4.vg.VDS4Gamepad = Pad
rx_ds4.init()
S.want[0].set()
t = threading.Thread(target=rx_ds4.slot_thread, args=(0,), daemon=True); t.start()
end = time.monotonic() + 2
while rx_ds4.outs[0] is None and time.monotonic() < end:
    time.sleep(0.01)
class LB: Red, Green, Blue = 255, 0, 0
cbs[-1](None, None, 200, 100, LB(), None)            # game: rumble 200/100, red lightbar
check(F.fb[0]["set"] and F.fb[0]["large"] == 200, "game feedback stored")
S.ignore_until[0] = 0.0
feed(T_BYE, 0)                                       # pad disconnected -> virtual DS4 detached
t.join(2)
check(not t.is_alive() or rx_ds4.outs[0] is None, "virtual DS4 detached")
check(not F.fb[0]["set"] and F.fb[0]["large"] == F.fb[0]["small"] == 0,
      f"after the DS4 is detached the slot keeps no game rumble: {F.fb[0]}")
S.ignore_until[0] = 0.0
state(0)                                             # the next pad in the slot, before its DS4 is created
val, ours, _blink = F._target(0, time.monotonic())
check(val[:2] == (0, 0), f"a new pad in the slot does not get the old game rumble: {val}")
S.want[0].clear(); S.wake[0].set()
S.push = None

print("ALL OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
