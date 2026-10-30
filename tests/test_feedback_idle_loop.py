"""Receiver: regresje z dziewiętnastego przeglądu.
  - wątek feedbacku: pad bez sygnału (alive = False, wirtualny DS4 jeszcze podłączony) z niezerową wibracją z gry
    liczył czas oczekiwania z ujemnej wartości -> pętla bez snu (100 % procesora, wątek sieci traci GIL) do odłączenia
    python3 tests/test_feedback_idle_loop.py receiver"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

S.alive[0] = False
F.fb[0].update(large=100, small=0, set=True, t=time.monotonic() - 5.0)     # gra trzyma wibrację, ostatni raport dawno temu
waits = []
real_wait = F.fb_event.wait
def counting_wait(timeout=None):
    waits.append(timeout)
    return real_wait(timeout)
F.fb_event.wait = counting_wait
S.stop.clear()
th = threading.Thread(target=F._loop, args=(lambda i, v: None,), daemon=True)
th.start()
time.sleep(0.4)
S.stop.set(); F.fb_event.set(); th.join(2)
check(len(waits) < 20, f"pad bez sygnału nie kręci pętli feedbacku ({len(waits)} obrotów w 0,4 s)")
sys.exit(1 if fails else 0)
