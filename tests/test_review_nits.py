"""Receiver: drobne poprawki z przeglądu - osobny plik .tmp dla każdego zapisującego, SeqFilter.forget odporny na
równoległe fresh(), strażnik restartu anulowany po zamknięciu, sprawdzanie efektów spustów, stare pakiety build / updmsg.
    python3 tests/test_review_nits.py receiver"""
import json, os, sys, threading
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import proto
import rx_feedback as F
from proto import T_BUILD, T_BUILD3, T_STATE, T_UPDMSG, Codec, SeqFilter

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


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


# ---------- spusty: puste bajty to nie efekt ----------
check(not F._has_effect((0, 0, bytes(F.TRIG_RAW_LEN), bytes(F.TRIG_RAW_LEN), 0)), "same zera (też surowe bajty) - brak efektu")
check(F._has_effect((0, 3, 0)) and F._has_effect((0, bytes(3) + b"\1")), "liczba lub bajt różny od zera - jest efekt")


# ---------- strażnik restartu: anulowany po zakończeniu sprzątania ----------
src = open(os.path.join(RX, "receiver_controller.py"), encoding="utf-8").read()
fin = src[src.index("def _shutdown("):src.index("def main(")]
check("guard.cancel()" in fin and fin.index("rx_winapi.end_timer()") < fin.index("guard.cancel()") < fin.index("_restart_now(console_on)"),
      "receiver_controller: strażnik anulowany po sprzątaniu, przed startem nowego procesu")


# ---------- pakiety build / updmsg z klucza v3 podlegają sprawdzeniu świeżości ----------
clock = [1_000_000]
key = b"k" * 32
def pair():
    """Nowa para kodeków v3 na wspólnym zegarze (osobny odbiorca na każdy przypadek: bez wpływu filtra powtórzeń)."""
    a = Codec(key, token=lambda: clock[0], now_ms=lambda: clock[0])
    b = Codec(key, token=lambda: clock[0], now_ms=lambda: clock[0])
    a.v3 = b.v3 = True
    return a, b
for ptype, args in ((T_UPDMSG, (b"x",)), (T_BUILD, (b"abc1234", 1, 0)), (T_BUILD3, (b"abc1234", 1, 0, b"\1" * 6, b"\0" * 4, 5))):
    tx, rx = pair()
    clock[0] = 1_000_000
    check(rx.parse(tx.pack(ptype, 255, *args)) is not None, f"typ {ptype}: świeży pakiet przechodzi")
    tx, rx = pair()
    pkt = tx.pack(ptype, 255, *args)
    clock[0] += 10_000
    check(rx.parse(pkt) is None and rx.stale == 1, f"typ {ptype}: pakiet sprzed 10 s odrzucony jako stary (nie jako powtórzenie)")

if fails:
    print("FAIL", fails)
    sys.exit(1)
print("WSZYSTKO OK")
