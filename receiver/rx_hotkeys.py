"""Receiver: przycisk PS jako modyfikator skrótów na padzie.

  PS krótko               -> PS dla gry (impuls po puszczeniu - przy samym PS nie wiadomo wcześniej, czy to skrót)
  PS przytrzymany          -> ps_hold_action (Game Bar / Steam Big Picture)
  PS + Share               -> zrzut ekranu (ps_share_action)
  PS + D-pad góra / dół    -> czułość myszy touchpada +/- 15 %
  PS + D-pad lewo / prawo  -> poprzedni / następny profil remapowania tego pada
  PS + klik touchpada      -> mysz / dotyk DS4 (obsługa w rx_net, tu tylko mark_used)

Każdy skrót może być zastrzeżony dla pada master (rx_master.allowed). Wejścia użyte w skrócie nie trafiają do gry,
dopóki nie zostaną puszczone. Działa na SUROWYCH wejściach,
przed remapowaniem, więc zremapowanie PS nie wyłącza skrótów. ps_modifier = False wyłącza całość
(PS idzie wtedy do gry od razu, jak wcześniej).
"""
import os
import threading

import rx_feedback as F
import rx_master as master
import rx_mouse
import rx_state as S
import rx_winapi as W
from rx_buttons import BIT, PS_BIT
from rx_config import C

SHARE_BIT = BIT[314]
TAP_S = 0.08                 # długość impulsu PS wysyłanego do gry po krótkim naciśnięciu
SENS_STEP, SENS_MIN, SENS_MAX = 1.15, 0.05, 5.0


class Slot:
    __slots__ = ("down", "used", "out", "gen", "tap", "sup_share", "sup_dpad", "prev_mask", "prev_hat", "timer")

    def __init__(self):
        self.down = self.used = self.out = self.sup_share = self.sup_dpad = False
        self.gen = 0                   # numer naciśnięcia PS - spóźniony timer starego naciśnięcia nic nie zrobi
        self.tap = 0                   # numer impulsu PS - koniec starego impulsu nie ucina nowego
        self.prev_mask, self.prev_hat = 0, (0, 0)
        self.timer = None


slots = []


def init(n):
    slots[:] = [Slot() for _ in range(n)]


def reset(i):
    """Rozłączenie / reset slotu: koniec wszystkich trwających skrótów."""
    if slots[i].timer is not None:
        slots[i].timer.cancel()
    slots[i] = Slot()


def forward(i, mask, hx, hy):
    """Surowe wejścia -> wejścia dla gry (bez PS i wejść zużytych w skrótach PS i kombinacjach mastera)."""
    if not C.PS_MODIFIER:
        return master.filter_inputs(i, mask, hx, hy)
    h = slots[i]
    mask = (mask & ~PS_BIT) | (PS_BIT if h.out else 0)
    if h.sup_share:
        mask &= ~SHARE_BIT
    if h.sup_dpad:
        hx = hy = 0
    return master.filter_inputs(i, mask, hx, hy)


def mark_used(i):
    """PS posłużył za modyfikator (np. PS + klik touchpada) - po puszczeniu nie wysyłaj PS do gry."""
    slots[i].used = True


def on_state(i, mask, hx, hy):
    """Wołane z wątku sieci przy każdej zmianie surowego stanu, PRZED przeliczeniem wyjścia."""
    if not C.PS_MODIFIER:
        return
    h = slots[i]
    ps = bool(mask & PS_BIT)
    pm, (phx, phy) = h.prev_mask, h.prev_hat
    h.prev_mask, h.prev_hat = mask, (hx, hy)
    if ps and not h.down:
        h.down, h.used = True, False
        h.gen += 1
        h.timer = threading.Timer(C.PS_HOLD_S, _hold, (i, h.gen))
        h.timer.daemon = True
        h.timer.start()
    elif not ps and h.down:
        h.down = False
        h.timer.cancel()
        if not h.used and master.check(i, "ps_game"):
            _tap(i, refresh=False)     # wołający (rx_net) i tak zaraz przelicza stan - już z nowymi wejściami
    if h.down:
        if mask & SHARE_BIT and not pm & SHARE_BIT:
            h.used = h.sup_share = True
            _run(_share, i)
        if (hx, hy) != (0, 0):
            if hy == -1 and phy != -1:
                _combo(h, _sens, i, SENS_STEP)
            elif hy == 1 and phy != 1:
                _combo(h, _sens, i, 1 / SENS_STEP)
            elif hx == -1 and phx != -1:
                _combo(h, _profile, i, -1)
            elif hx == 1 and phx != 1:
                _combo(h, _profile, i, 1)
    if h.sup_share and not mask & SHARE_BIT:
        h.sup_share = False
    if h.sup_dpad and (hx, hy) == (0, 0):
        h.sup_dpad = False


def _combo(h, fn, *args):
    h.used = h.sup_dpad = True
    _run(fn, *args)


def _run(fn, *args):
    """Akcje w osobnym wątku: otwieranie programów / SendInput nie mogą zatrzymać odbioru pakietów."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _refresh(i):
    """Przelicza wyjście slotu po zmianie stanu skrótów (impuls PS)."""
    with S.lock:
        s = S.states[i]
        s["fwd"] = forward(i, *s["raw"][:3])
        S.recompute(s, S.remap_maps[i])
        s["ver"] += 1
    S.notify(i)


def _tap(i, refresh=True):
    h = slots[i]
    h.tap += 1
    h.out = True
    if refresh:
        _refresh(i)
    t = threading.Timer(TAP_S, _untap, (i, h, h.tap))
    t.daemon = True
    t.start()


def _untap(i, h, tap):
    if slots[i] is not h or h.tap != tap:
        return                         # w międzyczasie nowy impuls albo reset slotu
    h.out = False
    _refresh(i)


def _hold(i, gen):
    h = slots[i]
    if h.gen != gen or not h.down or h.used or C.PS_HOLD_ACTION == "none":
        return                         # "none": długie PS zostaje zwykłym PS dla gry (impuls po puszczeniu)
    h.used = True
    if not master.check(i, "ps_hold"):
        return
    if C.PS_HOLD_ACTION == "gamebar":
        W.send_keys(W.VK_LWIN, W.VK_G)
    elif C.PS_HOLD_ACTION == "bigpicture":
        try:
            os.startfile("steam://open/bigpicture")
        except OSError as e:
            print(f"Big Picture: cannot start Steam ({e})")
    print(f"Controller {i}: PS hold -> {C.PS_HOLD_ACTION}")
    F.pulse(i, "action")


def _share(i):
    if not master.check(i, "screenshot"):
        return
    if C.PS_SHARE_ACTION == "screenshot":
        W.send_keys(W.VK_LWIN, W.VK_SNAPSHOT)
    elif C.PS_SHARE_ACTION == "capture":
        W.send_keys(W.VK_LWIN, W.VK_MENU, W.VK_SNAPSHOT)
    else:
        return
    print(f"Controller {i}: screenshot ({C.PS_SHARE_ACTION})")
    F.pulse(i, "action")


def _sens(i, factor):
    if not master.check(i, "sens"):
        return
    old = rx_mouse.sens[i]
    rx_mouse.sens[i] = new = round(min(SENS_MAX, max(SENS_MIN, old * factor)), 3)
    print(f"Controller {i}: touchpad mouse sensitivity {old:g} -> {new:g} (Settings → Mouse to keep it)")
    F.pulse(i, "up" if new > old else "down")


def _profile(i, step):
    if not master.check(i, "profile"):
        return
    key = S.cycle_profile(i, step)
    label = S.profile_label(key)
    print(f"Controller {i}: profile -> {label} (manual, until the game changes)")
    F.pulse(i, "default" if label == "default" else "profile")
