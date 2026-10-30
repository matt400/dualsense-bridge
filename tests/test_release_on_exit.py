"""Receiver: regresje z szóstego przeglądu - zwolnienie spustów przy zamykaniu, wynik aktualizacji po restarcie
nadajnika, budzenie wątku DSU tylko przy klientach, ostrzeżenie o innym kluczu HMAC."""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_net as N
import rx_state as S
import rx_events as ev
import rx_feedback as F
import rx_update as U
from proto import T_PING, T_STATE, TRIG2_OFF, UPD_DONE, UPD_FAILED, UPD_NO_CHANGE, UPD_NONE, UPD_RUNNING, Codec

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)

# ---------- zwolnienie efektów spustów przy zamykaniu receivera ----------
out = []
send = lambda i, vals, n: out.append((i, tuple(vals), n))
F.trig_sent[:] = [((1, 2, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0), 0.0), (TRIG2_OFF, 0.0)]
F.release_triggers(send)
check(out == [(0, TRIG2_OFF, F.TRIG_REPEAT)] and F.trig_sent[0] is None,
      f"zamykanie: pad z efektami dostaje 'bez efektów' (pad bez efektów - nic): {out}")
out.clear()
F.trig_sent[:] = [((3, 6, 0, 0), 0.0), None]
F.release_triggers(send)
check(out == [(0, (0, 0, 0, 0), F.TRIG_REPEAT)], "starszy nadajnik (pakiet trig) - zwolnienie tym samym pakietem")
out.clear(); F.release_triggers(send)
check(out == [], "drugi raz nic (już zwolnione)")
src = open(os.path.join(RX, "receiver_controller.py"), encoding="utf-8").read()
fin = src[src.index("def _shutdown("):src.index("def main(")]
check(fin.index("release_triggers") < fin.index("rx_net.close()") and fin.index("fb_thread.join") < fin.index("release_triggers"),
      "receiver_controller: zwolnienie po zatrzymaniu wątku feedbacku, przed zamknięciem gniazda")

# ---------- wynik aktualizacji nadajnika po jego restarcie ----------
old = "a1b2c3d"
check(U.sender_state((old, 1, UPD_RUNNING), old) == UPD_RUNNING, "w trakcie: ten sam commit, RUNNING - czekamy")
check(U.sender_state(("e4f5a6b", 2, UPD_NONE), old) == UPD_DONE,
      "nowy proces nadajnika (UPD_NONE) z innym commitem = zaktualizowany")
check("zaktualizowany" in U.sender_text(("e4f5a6b", 2, UPD_NONE), "", old) and
      "nie odpowiedział" not in U.sender_text(("e4f5a6b", 2, UPD_NONE), "", old),
      "okno nie pisze 'nadajnik nie odpowiedział' po udanej aktualizacji")
check(U.sender_state((old, 1, UPD_NONE), old) == UPD_NONE and "nie odpowiedział" in U.sender_text((old, 1, UPD_NONE), "", old),
      "ten sam commit bez wyniku - dalej 'nie odpowiedział'")
check(U.sender_state((old, 1, UPD_NO_CHANGE), old) == UPD_NO_CHANGE and U.sender_state(("x", 1, UPD_FAILED), old) == UPD_FAILED,
      "wynik zgłoszony przez nadajnik ma pierwszeństwo")
check(U.sender_state(("e4f5a6b", 2, UPD_NONE)) == UPD_NONE, "bez commitu sprzed aktualizacji - jak dotąd")
qt = open(os.path.join(RX, "rx_qt.py"), encoding="utf-8").read()
check("sender_state(S.sender_build, self._upd_commit) == UPD_RUNNING" in qt, "okno przestaje czekać, gdy commit nadajnika się zmienił")

# ---------- DSU: zmiany stanu nie budzą wątku bez klientów ----------
S.push = None
S.dsu_wake.clear()
S.dsu_active = False
S.notify(0)
check(not S.dsu_wake.is_set(), "bez klientów DSU notify() nie budzi wątku DSU")
S.dsu_active = True
S.notify(0)
check(S.dsu_wake.is_set(), "z klientem DSU - budzi jak dotąd")
S.dsu_active = False
import rx_dsu
check("S.dsu_active = True" in open(os.path.join(RX, "rx_dsu.py"), encoding="utf-8").read(), "subskrypcja DSU włącza budzenie")

# ---------- inny klucz HMAC: ostrzeżenie w dzienniku ----------
N.codec = Codec()
keyed = Codec(b"sekret")
check(N.codec.parse(keyed.pack(T_PING, 255, 1)) is None, "receiver bez klucza odrzuca nadajnik z kluczem")
n0 = len(ev.since(0))
N.check_key(time.monotonic())
N.check_key(time.monotonic())
new = ev.since(0)[n0:]
check(len(new) == 1 and "klucz HMAC" in new[0].text and new[0].level == "warn", "jedno ostrzeżenie w dzienniku (nie co 100 ms)")
N._key_warned[0] = float("-inf")
N.codec = Codec()
N.check_key(time.monotonic())
check(len(ev.since(0)) == n0 + 1, "bez odrzuconych pakietów - bez ostrzeżenia")

if fails:
    print("FAIL", fails)
    sys.exit(1)
print("HUNT6 OK")
