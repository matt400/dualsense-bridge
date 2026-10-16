"""Reprodukcje bugów ścieżki krytycznej receivera (przegląd 2026-10-16) - każdy test przechodzi PO poprawkach."""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_ds4, rx_master, rx_profiles as P, rx_testmode as TM
from proto import T_BYE
from rx_buttons import BUTTON_CODES
fails = []
def check(cond, name):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)

TRI = BIT[BUTTON_CODES["triangle"]]
PS = BIT[BUTTON_CODES["ps"]]

# ---- 1. wyjście z widoku Test (PS + △ 3 s) z wciśniętym spustem ----
# było: KeyError('r2') w rx_master.filter_inputs -> wyjście przerwane w połowie: widok zamknięty, a slot dalej
# zablokowany (gra dostawała neutralny stan do ponownego otwarcia widoku Test)
state(0)
TM.set_active(True); P.block(0, True)
state(0, mask=PS | TRI, rt=200)
try:
    TM.tick(TM._since[0] + TM.ESCAPE_S)
    err = None
except Exception as e:
    err = e
check(err is None, f"wyjście z widoku Test z wciśniętym R2 bez wyjątku: {err!r}")
check(not TM.active and not S.states[0]["blocked"], "po wyjściu z pada slot nie jest zablokowany (gra dostaje wejścia)")
state(0, mask=PS | TRI, rt=210, sticks=(20, 128, 128, 128))
s = S.states[0]
check(s["rt"] == 210 and s["lx"] == 20, "spust i gałka idą do gry na żywo po wyjściu")
check(not s["mask"] & (PS | TRI), "PS + △ z kombinacji nie trafia do gry do puszczenia")
state(0, mask=TRI)                                   # PS puszczone, △ dalej trzymany - nadal zużyty
check(not S.states[0]["mask"] & TRI, "△ zużyty do puszczenia")
state(0); state(0, mask=TRI)
check(S.states[0]["mask"] & TRI, "nowe naciśnięcie △ po puszczeniu trafia do gry")
rx_master.slots[0].sup = frozenset({"l2", "dpad_up"})  # filtr odporny na spusty w zestawie zużytych wejść
check(rx_master.filter_inputs(0, TRI, 0, -1) == (TRI, 0, 0), "filter_inputs: spust w sup nie rzuca wyjątku")
rx_master.reset(0)
state(0)

# ---- 2. wibracja / kolor z gry nie przechodzą na następny pad w slocie ----
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
cbs[-1](None, None, 200, 100, LB(), None)            # gra: wibracja 200/100, czerwony lightbar
check(F.fb[0]["set"] and F.fb[0]["large"] == 200, "feedback z gry zapisany")
S.ignore_until[0] = 0.0
feed(T_BYE, 0)                                       # pad rozłączony -> wirtualny DS4 odłączony
t.join(2)
check(not t.is_alive() or rx_ds4.outs[0] is None, "wirtualny DS4 odłączony")
check(not F.fb[0]["set"] and F.fb[0]["large"] == F.fb[0]["small"] == 0,
      f"po odłączeniu DS4 slot nie trzyma wibracji gry: {F.fb[0]}")
S.ignore_until[0] = 0.0
state(0)                                             # następny pad w slocie, zanim powstanie jego DS4
val, ours, _blink = F._target(0, time.monotonic())
check(val[:2] == (0, 0), f"nowy pad w slocie nie dostaje starej wibracji gry: {val}")
S.want[0].clear(); S.wake[0].set()
S.push = None

print("WSZYSTKO OK" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
