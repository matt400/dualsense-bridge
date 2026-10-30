"""Receiver: parallel write_file calls use separate .tmp files; SeqFilter.forget survives a parallel fresh().
    python3 tests/test_thread_safety.py receiver"""
import json, os, sys, threading
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import proto
from proto import T_STATE, SeqFilter

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- write_file: dwa równoległe zapisy tego samego pliku nie dzielą pliku tymczasowego ----------
path = os.path.join(TMP, "shared.json")
errors = []
def writer(n):
    try:
        for k in range(50):
            proto.write_json(path, {"w": n, "k": k})
    except OSError as e:
        errors.append(e)
threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
for t in threads: t.start()
for t in threads: t.join()
check(not errors, f"równoległe zapisy tego samego pliku bez błędów: {errors[:1]}")
check(isinstance(json.load(open(path, encoding="utf-8")), dict), "po wyścigu plik jest kompletnym JSON-em")
check(not [n for n in os.listdir(TMP) if n.startswith("shared.json")and n != "shared.json"], "bez porzuconych plików .tmp")

temps = []
real_replace = os.replace
os.replace = lambda src, dst: temps.append(src) or real_replace(src, dst)
try:
    proto.write_file(path, "a"); proto.write_file(path, "b")
finally:
    os.replace = real_replace
check(len(temps) == 2 and all(t.endswith(".tmp") and t != path + ".tmp" for t in temps), "nazwa pliku tymczasowego zawiera pid i wątek")

# ---------- SeqFilter.forget: nowy klucz z innego wątku w trakcie przeglądania nie psuje słownika ----------
flt = SeqFilter()
for slot in range(4):
    flt.fresh((T_STATE, slot), 1, 0.0)
def match(k):
    flt.fresh((T_STATE, 99), 1, 0.0)              # tak zachowałby się wątek sieciowy w trakcie forget()
    return k[1] == 1
try:
    flt.forget(match)
    ok = True
except RuntimeError:
    ok = False
check(ok, "forget przy równoległym fresh() nie rzuca RuntimeError")
check(flt.fresh((T_STATE, 1), 1, 0.1) and not flt.fresh((T_STATE, 2), 1, 0.1), "zapomniany strumień przyjmuje stary numer, reszta nie")

sys.exit(1 if fails else 0)
