"""Receiver: ViGEm pad creation is retried; DS4 reports are pushed from the network thread without loss or duplicates.
    python3 tests/test_ds4_push.py receiver"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_console, rx_settings
from proto import T_BYE

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.01)
    return pred()


connected = []
real_pc = rx_console.pad_connected

# ---------- błąd ViGEm nie udaje ponownego połączenia pada ----------
import threading, rx_ds4
attempts = []
class BrokenPad:
    def __init__(self):
        attempts.append(1)
        raise OSError("ViGEmBus not installed")
rx_ds4.vg.VDS4Gamepad = BrokenPad
connected.clear()
rx_console.pad_connected = lambda i, ident=False: connected.append(i)
S.ignore_until[1] = 0.0
S.want[0].clear()                                   # slot 0 nie czeka w kolejce (wait_turn)
from dataclasses import replace as _replace
rx_settings.apply(_replace(rx_settings.cur, vpad=rx_settings.VpadConfig(fixed_slots=False)))   # bez zastępczego slotu 0
t = threading.Thread(target=rx_ds4.slot_thread, args=(1,), daemon=True)
t.start()
end = time.monotonic() + 2.4
while time.monotonic() < end:
    state(1); time.sleep(0.05)
rx_console.pad_connected = real_pc
check(len(attempts) >= 2, f"ViGEm: kolejne próby utworzenia pada ({len(attempts)})")
check(connected.count(1) == 1, f"błąd ViGEm nie jest nowym połączeniem pada (pad_connected {connected.count(1)}x)")
S.want[1].clear(); S.stop.set(); S.wake[1].set(); t.join(3); S.stop.clear()

# ---------- raport DS4 od razu w wątku sieci (bez przełączania na wątek slotu), seria zlana bez utraty ----------
import threading, rx_ds4
rx_settings.apply(_replace(rx_settings.cur, vpad=rx_settings.VpadConfig(fixed_slots=False)))   # mechanika jednego slotu
reports = []
class FastPad:
    def register_notification(self, cb): pass
    def unregister_notification(self): pass
    def update_extended_report(self, rep):
        reports.append((threading.get_ident(), time.perf_counter(), bytes(rep)[0]))
rx_ds4.vg.VDS4Gamepad = FastPad
rx_ds4.init()
S.ignore_until[0] = 0.0
feed(T_BYE, 0); S.ignore_until[0] = 0.0
state(0)
S.want[0].set()
t = threading.Thread(target=rx_ds4.slot_thread, args=(0,), daemon=True); t.start()
end = time.monotonic() + 2
while rx_ds4.outs[0] is None and time.monotonic() < end:
    time.sleep(0.01)
time.sleep(0.05); reports.clear()
me = threading.get_ident()
t0 = time.perf_counter()
state(0, sticks=(10, 128, 128, 128))
check(reports and reports[0][0] == me and reports[0][2] == 10,
      f"raport z wątku sieci, zanim obsługa pakietu wróciła: {[(r[0] == me, r[2]) for r in reports]}")
for x in (20, 30, 40):                                    # seria szybsza niż pad_hz (1 ms)
    state(0, sticks=(x, 128, 128, 128))
time.sleep(0.05)
check(reports[-1][2] == 40, f"seria szybsza niż pad_hz: ostatni stan dosłany przez wątek slotu: {[r[2] for r in reports]}")
check(len(reports) <= 4, "seria zlana (nie więcej raportów niż zmian)")
import random as _r
rng = _r.Random(5); reports.clear(); seq = []
for k in range(300):                                      # zmiany w nieregularnych odstępach wokół limitu pad_hz
    v = 1 + k % 250
    state(0, sticks=(v, 128, 128, 128)); seq.append(v)
    time.sleep(rng.choice((0, 0, 0.0004, 0.0009, 0.0011, 0.002)))
time.sleep(0.05)
vals = [r[2] for r in reports]
dups = sum(1 for a, b in zip(vals, vals[1:]) if a == b)
check(dups == 0, f"bez zdublowanych raportów (push + wątek slotu): {dups} duplikatów na {len(vals)}")
check(vals[-1] == seq[-1], "ostatni stan zawsze dochodzi")
S.want[0].clear(); S.wake[0].set(); t.join(2)
check(rx_ds4.outs[0] is None and not S.plugged[0], "odłączenie: push nie używa już odłączonego pada")
state(0, sticks=(50, 128, 128, 128))                      # po odłączeniu tylko budzi wątek slotu, bez wyjątku
S.push = None

sys.exit(1 if fails else 0)
