"""Receiver: regresje z siódmego przeglądu - klik touchpada puszczony w widoku Test nie zostaje wciśnięty w grze,
licznik błędów wątku zerowany po spokojnej pracy, tryb TV: puszczenie PS przed przyciskiem skrótu to nie nawigacja.
    python3 tests/test_touchpad_tv_mode.py receiver"""
import contextlib, io, os, sys, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_net as N
import rx_state as S
from proto import T_CLICK

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- klik touchpada a blokada wyjścia (widok Test) ----------
S.mouse_mode[0] = False                        # touchpad jako dotyk DS4 (klik idzie do gry)
state(0)
feed(T_CLICK, 0, 1)
check(S.states[0]["tp"] and S.output(0)["tp"], "klik touchpada wciśnięty - gra go widzi")
S.set_blocked(0, True)
check(not S.output(0)["tp"], "widok Test: gra dostaje neutralny stan")
feed(T_CLICK, 0, 0)                            # puszczony w trakcie testu
S.set_blocked(0, False)
check(not S.output(0)["tp"], "po teście klik puszczony w trakcie nie zostaje wciśnięty w grze")
feed(T_CLICK, 0, 0)
check(not S.output(0)["tp"], "powtórki 'puszczony' z nadajnika niczego nie zmieniają")
S.set_blocked(0, True)
feed(T_CLICK, 0, 1)
S.set_blocked(0, False)
check(S.output(0)["tp"], "klik trzymany przez koniec testu idzie na żywo (jak przyciski i gałki)")
feed(T_CLICK, 0, 0)
check(not S.output(0)["tp"], "... i puszcza się normalnie")
S.mouse_mode[0] = True
S.set_blocked(0, True)
feed(T_CLICK, 0, 1)
S.set_blocked(0, False)
check(not S.states[0]["tp"], "tryb myszy: blokada nie ustawia kliku dotyku DS4")
feed(T_CLICK, 0, 0)
S.mouse_mode[0] = False


# ---------- wątek receivera: licznik błędów zeruje się po spokojnej pracy ----------
S.STABLE_S = 0.05
calls = [0]


def flaky():
    calls[0] += 1
    if calls[0] <= 2:
        raise RuntimeError(f"błąd {calls[0]}")
    if calls[0] == 3:
        time.sleep(0.08)                       # dłużej niż STABLE_S bez błędu
        raise RuntimeError("rzadki błąd")

buf = io.StringIO()
t0 = time.monotonic()
with contextlib.redirect_stdout(buf):
    S.run_forever("test", flaky)
log = buf.getvalue()
check(log.count("thread error - restarting:\n") == 2 and "#3" not in log,
      "błąd po spokojnej pracy liczony od nowa (pełny ślad, krótka przerwa)")
check(time.monotonic() - t0 < 1.08, f"przerwy 0.2 + 0.4 + 0.2 s, nie 0.6 s za trzecim razem: {time.monotonic() - t0:.2f} s")


# ---------- tryb TV: PS + przycisk to skrót, nie nawigacja (także po puszczeniu PS jako pierwszego) ----------
try:
    import rx_qt_tv
except ImportError:
    print("SKIP  tryb TV (brak PySide6)")
else:
    from rx_buttons import BIT, BUTTON_CODES
    PS = BIT[BUTTON_CODES["ps"]]
    CROSS = BIT[BUTTON_CODES["cross"]]
    acts = []

    class Nav(rx_qt_tv.PadNav):
        def target(self):
            return "okno"

        def act(self, root, name):
            acts.append(name)

    nav = Nav(types.SimpleNamespace(view="out"))
    S.alive[0] = True
    t = 100.0

    def step(mask=0, hat=(0, 0)):
        global t
        state(0, mask=mask, hat=hat)
        t += 0.033
        nav.tick(t)

    step()
    step(PS); step(PS, (1, 0))                 # PS + → = następny profil
    step(0, (1, 0)); step(0, (1, 0))           # PS puszczone przed D-padem
    step()
    check(acts == [], f"PS + →, potem puszczone PS: bez kroku fokusu w oknie: {acts}")
    step(PS); step(PS | CROSS); step(CROSS); step()
    check(acts == [], f"PS + ✕ (akcja mastera), PS puszczone pierwsze: ✕ nie klika w oknie: {acts}")
    step(0, (1, 0)); step()
    check(acts == ["dpad_right"], f"samo → dalej przesuwa fokus: {acts}")
    acts.clear()
    step(0, (1, 0))
    for _k in range(20):                        # przytrzymane -> powtórki
        step(0, (1, 0))
    n = len(acts)
    step(PS, (1, 0))
    for _k in range(20):
        step(PS, (1, 0))
    check(n > 1 and len(acts) == n, f"PS dołożone do trzymanego kierunku zatrzymuje powtórki: {n} -> {len(acts)}")
    step()

print("WSZYSTKO OK" if not fails else f"BŁĘDY: {len(fails)}")
sys.exit(1 if fails else 0)
