"""Receiver: the PS button as a shortcut modifier on the pad.

  PS short press           -> PS for the game (a pulse on release - with PS alone it is not known earlier whether it is a shortcut)
  PS held                  -> ps_hold_action (Game Bar / Steam Big Picture)
  PS + Share               -> screenshot (ps_share_action)
  PS + D-pad up / down     -> touchpad mouse sensitivity +/- 15 %
  PS + D-pad left / right  -> previous / next remap profile of this pad
  PS + touchpad click      -> mouse / DS4 touch (handled in rx_net, only mark_used here)

Every shortcut can be restricted to the master pad (rx_master.allowed). Inputs used in a shortcut do not reach the game
until they are released. It works on RAW inputs, before remapping, so remapping PS does not disable the shortcuts.
ps_modifier = False disables all of it (PS then goes to the game at once, as it used to).
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
TAP_S = 0.08                 # length of the PS pulse sent to the game after a short press
SENS_STEP, SENS_MIN, SENS_MAX = 1.15, 0.05, 5.0


class Slot:
    """Per-pad PS shortcut state: whether PS is down / consumed, the PS pulse for the game, suppressed Share / D-pad, the previous raw
    inputs and the hold timer."""
    __slots__ = ("down", "used", "out", "gen", "tap", "sup_share", "sup_dpad", "prev_mask", "prev_hat", "timer", "fresh")

    def __init__(self):
        self.down = self.used = self.out = self.sup_share = self.sup_dpad = False
        self.gen = 0                   # PS press number - a late timer of an old press does nothing
        self.tap = 0                   # PS pulse number - the end of an old pulse does not cut a new one short
        self.prev_mask, self.prev_hat = 0, (0, 0)
        self.timer = None
        self.fresh = True              # first state after connecting: a PS held since the pad was switched on is not a new press


slots = []


def init(n):
    """Create `n` fresh slots."""
    slots[:] = [Slot() for _ in range(n)]


def reset(i, keep=False):
    """Slot disconnect / reset: ends all shortcuts in progress.

    keep: the pad stays connected (Test view boundary, output block) - the state is built from the inputs held right now
    (S.states[i]["raw"]), not as after a pad connects. A PS held across the boundary reaches neither the game nor
    Game Bar, but it is a shortcut with a new button; a fresh PS after the boundary is an ordinary PS for the game. Earlier, a reset
    "as after connecting" took the first PS after leaving the test for one held since the pad was switched on (the game did not get it),
    and a PS held across the boundary + Share gave Share in the game instead of a screenshot."""
    if slots[i].timer is not None:
        slots[i].timer.cancel()
    h = slots[i] = Slot()
    if keep and S.states is not None:
        mask, hx, hy = S.states[i]["raw"][:3]
        h.fresh = False
        h.prev_mask, h.prev_hat = mask, (hx, hy)
        if mask & PS_BIT:              # PS + Share / D-pad held = a shortcut in progress - goes to the game only after release
            h.down = h.used = True
            h.sup_share = bool(mask & SHARE_BIT)
            h.sup_dpad = (hx, hy) != (0, 0)


def forward(i, mask, hx, hy):
    """Raw inputs -> inputs for the game (without PS and inputs consumed by PS shortcuts and master combinations)."""
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
    """PS served as a modifier (e.g. PS + touchpad click) - do not send PS to the game after it is released."""
    slots[i].used = True


def on_state(i, mask, hx, hy):
    """Called from the network thread on every raw state change, BEFORE the output is recomputed."""
    if not C.PS_MODIFIER:
        return
    h = slots[i]
    ps = bool(mask & PS_BIT)
    pm, (phx, phy) = h.prev_mask, h.prev_hat
    h.prev_mask, h.prev_hat = mask, (hx, hy)
    if h.fresh:
        h.fresh = False
        if ps:                         # pad switched on by holding PS: no Game Bar (long PS) and no PS for the game
            h.down = h.used = True
            return
    if ps and not h.down:
        h.down, h.used = True, False
        h.gen += 1
        h.timer = threading.Timer(C.PS_HOLD_S, _hold, (i, h.gen))
        h.timer.daemon = True
        h.timer.start()
    elif not ps and h.down:
        h.down = False
        if h.timer is not None:
            h.timer.cancel()
        if not h.used and master.check(i, "ps_game"):
            _tap(i, refresh=False)     # the caller (rx_net) recomputes the state right after this anyway - already with the new inputs
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
    """A PS + D-pad combination: mark PS as used, suppress the D-pad for the game and run the action in a thread."""
    h.used = h.sup_dpad = True
    _run(fn, *args)


def _run(fn, *args):
    """Run actions in a separate thread: launching programs / SendInput must not stop packet reception."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _refresh(i):
    """Recompute a slot's output after the shortcut state changed (the PS pulse)."""
    with S.lock:
        s = S.states[i]
        s["fwd"] = forward(i, *s["raw"][:3])
        S.recompute(s, S.remap_maps[i])
        s["ver"] += 1
    S.notify(i)


def _tap(i, refresh=True):
    """Send a short PS pulse to the game (TAP_S long): set the output bit, recompute (optionally) and schedule its end."""
    h = slots[i]
    h.tap += 1
    h.out = True
    if refresh:
        _refresh(i)
    t = threading.Timer(TAP_S, _untap, (i, h, h.tap))
    t.daemon = True
    t.start()


def _untap(i, h, tap):
    """End of the PS pulse - ignored when a newer pulse started or the slot was reset in the meantime."""
    if slots[i] is not h or h.tap != tap:
        return                         # in the meantime a new pulse or a slot reset
    h.out = False
    _refresh(i)


def _hold(i, gen):
    """Hold timer fired: run ps_hold_action (Game Bar via Win+G, or Steam Big Picture) unless PS was released, used or the action is
    none."""
    h = slots[i]
    if h.gen != gen or not h.down or h.used or C.PS_HOLD_ACTION == "none":
        return                         # "none": a long PS stays an ordinary PS for the game (a pulse on release)
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
    """PS + Share: send the screenshot keys (Win+PrtScr, or Win+Alt+PrtScr for Game Bar capture)."""
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
    """PS + D-pad up / down: multiply the touchpad mouse sensitivity of this pad by `factor` within limits (not saved - Settings -> Mouse
    keeps it)."""
    if not master.check(i, "sens"):
        return
    old = rx_mouse.sens[i]
    rx_mouse.sens[i] = new = round(min(SENS_MAX, max(SENS_MIN, old * factor)), 3)
    print(f"Controller {i}: touchpad mouse sensitivity {old:g} -> {new:g} (Settings → Mouse to keep it)")
    F.pulse(i, "up" if new > old else "down")


def _profile(i, step):
    """PS + D-pad left / right: switch to the previous / next remap profile of this pad (until the game changes)."""
    if not master.check(i, "profile"):
        return
    key = S.cycle_profile(i, step)
    label = S.profile_label(key)
    print(f"Controller {i}: profile -> {label} (manual, until the game changes)")
    F.pulse(i, "default" if label == "default" else "profile")
