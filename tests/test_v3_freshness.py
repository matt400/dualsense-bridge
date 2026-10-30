"""Receiver: build / updmsg packets of a v3 key are checked for freshness.
    python3 tests/test_v3_freshness.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
from proto import T_BUILD, T_BUILD3, T_UPDMSG, Codec

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


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

sys.exit(1 if fails else 0)
