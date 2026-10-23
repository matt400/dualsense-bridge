"""Receiver: regresje z osiemnastego przeglądu.
  - wskaźnik utraty pakietów: przerwa dłuższa niż ~1000 pakietów (1-4 s) liczyła się jako restart nadajnika - 0 % strat
    właśnie przy największych zanikach; restart (nowy losowy numer) dalej nie jest stratą
  - szybki restart receivera (< 3 s): pakiety v3 ze znacznikiem zegara starego procesu dawały w dzienniku fałszywe
    "przeterminowane pakiety (powtórzone nagranie)"; prawdziwe nagranie po synchronizacji zegara dalej ostrzega
    python3 tests/test_loss_indicator_restart.py receiver"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_events as ev

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


# ---------- 1. utrata pakietów przy długiej przerwie ----------
def run(gap_packets, gap_s, rate=500):
    """1 s pakietów co 1/rate, cisza gap_s, skok numeru o gap_packets, potem znowu pakiety -> link_loss [%]."""
    link = N.LinkStats(2)
    t, sq = 100.0, 1000
    link.t0 = t
    for _ in range(rate):
        link.packet(sq, t); sq += 1; t += 1 / rate
    link.tick(t + 0.01)                                  # okno 1: rate zmierzony
    t += 0.01
    t += gap_s
    sq = (sq + gap_packets) & 0xFFFF
    for _ in range(50):
        link.packet(sq, t); sq = (sq + 1) & 0xFFFF; t += 1 / rate
    link.tick(t + 1.0)
    return S.link_loss


loss = run(gap_packets=1500, gap_s=3.0)
check(loss is not None and loss > 90, f"przerwa 3 s (1500 pakietów zgubionych) = strata ~97 %, nie 0 % ({loss})")
check((run(gap_packets=20, gap_s=0.04) or 0) > 20, "krótka luka dalej liczona jak dotąd")
loss = run(gap_packets=30000, gap_s=2.0)
check(loss is None or loss < 1, f"restart nadajnika (skok o 30000 po 2 s) to nie strata ({loss})")

# ---------- 2. fałszywe ostrzeżenie o przeterminowanych pakietach po restarcie ----------
codec = N.codec
warned = []
orig_warn = ev.warn
ev.warn = lambda text, *a, **k: warned.append(text)
try:
    N._first_ping[0] = None
    N._auth_warned["stale"] = float("-inf")
    now = 1000.0
    codec.stale_t = now - 0.2                            # pakiet v3 ze starym zegarem, zanim przyszedł ping
    N.check_key(now)
    check(not warned, "pakiety ze zegarem poprzedniego procesu przed pierwszym pingiem - bez ostrzeżenia")
    N._first_ping[0] = now                               # pierwszy ping nowego procesu
    codec.stale_t = now + 0.3                            # nadajnik jeszcze nie dostał pong2
    N.check_key(now + 0.4)
    check(not warned, "tuż po pierwszym pingu (nadajnik synchronizuje zegar) - bez ostrzeżenia")
    codec.stale_t = now + 5.0                            # po synchronizacji: prawdziwe nagranie
    N.check_key(now + 5.1)
    check(len(warned) == 1, "przeterminowany pakiet po synchronizacji zegara - ostrzeżenie jak dotąd")
    N._first_ping[0] = None
    N.note_ping(123, 456)
    check(N._first_ping[0] is not None, "pierwszy ping zapamiętany")
finally:
    ev.warn = orig_warn

print("WSZYSTKO OK" if not fails else f"FAIL {len(fails)}")
sys.exit(1 if fails else 0)
