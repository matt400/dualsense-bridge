"""Receiver: the master pad - one pad that is allowed more (e.g. when children play).

The master is the pad with the identifier rx_settings.cur.master.pad (a MAC, the same key as the profile in remap.json), so it
stays the master regardless of the slot and the connection order. It is set in the window (the crown on the pad card,
Settings -> Master) or on the pad: PS + R3 held for CLAIM_S seconds (claim_hold; the pad flashes gold).

  allowed(i, feature)  whether pad i may use a feature from MASTER_FEATURES (console mode, PS, shortcuts...);
                       a feature that is not restricted, or no master in the settings = anyone may;
                       a master set but not connected -> according to when_absent (all / nobody)
  PS + button          system actions (pause, close the game, sleep, shut down the PC); dangerous ones need
                       holding for HOLD_S (a short rumble = "keep holding"); the button of the combination does not reach the game,
                       and PS does not either after release (like the PS shortcuts of rx_hotkeys). The buttons of the PS shortcuts (Share,
                       D-pad, touchpad click) and R3 (the master claim) are not available to actions.

It works on RAW inputs (before remapping), like the PS shortcuts. The network thread calls only on_state() and filter_inputs();
system actions run in a separate thread.
"""
from rx_i18n import _
import threading
from dataclasses import replace

import rx_events as ev
import rx_feedback as F
import rx_mouse
import rx_settings
import rx_state as S
import rx_winapi as W
from rx_buttons import BIT, BUTTON_CODES, DIRS, pressed_inputs
from rx_config import C

MOD = "ps"             # the master action modifier
CLAIM_BUTTON = "r3"    # PS + R3 held CLAIM_S = this pad becomes the master
CLAIM_S = 3.0
HOLD_S = 1.2           # this long PS + button has to be held for "dangerous" actions (close game, sleep, shut down)
# programs that "close game" does not close (desktop, Steam, this receiver)
NO_CLOSE = frozenset({"explorer.exe", "steam.exe", "steamwebhelper.exe", "python.exe", "pythonw.exe",
                      "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe", "lockapp.exe"})


class Slot:
    """Per-pad master state: PS held, previous inputs, inputs consumed in a combination, the pending combination button and its timer."""
    __slots__ = ("mod", "gen", "prev", "sup", "combo", "timer", "fresh")

    def __init__(self) -> None:
        self.mod = False          # PS held
        self.fresh = True         # first state after connecting: inputs already held are not new presses
        self.gen = 0              # PS press / combination number - a late timer does nothing
        self.prev = frozenset()   # inputs pressed in the previous state
        self.sup = frozenset()    # inputs consumed in a combination (they do not reach the game until released)
        self.combo = None         # the button of a held combination waiting for HOLD_S / CLAIM_S
        self.timer = None


slots = []


def init(n) -> None:
    """Create `n` fresh slots."""
    slots[:] = [Slot() for _ in range(n)]


def reset(i, keep=False) -> None:
    """End the combinations and timers of a slot. keep: the pad stays connected (Test view boundary, block) - inputs held
    right now are the "previous state" (they will not fire an action), and a new press after the boundary works at once (like
    rx_hotkeys.reset); without keep - as after the pad connects (the first state is the initial state).
    """
    if slots[i].timer is not None:
        slots[i].timer.cancel()
    h = slots[i] = Slot()
    if keep and S.states is not None:
        h.fresh = False
        h.prev = frozenset(pressed_inputs(*S.states[i]["raw"]))
        h.mod = MOD in h.prev


# ---------------- who is the master ----------------
def master_slot():
    """The slot of the connected master, or None (no master in the settings or it is not connected)."""
    pad = rx_settings.cur.master.pad
    if not pad:
        return None
    return next((i for i in range(len(slots)) if (S.alive[i] or S.want[i].is_set()) and S.profile_key(i) == pad), None)


def is_master(i):
    """Whether the pad in slot i is the configured master."""
    pad = rx_settings.cur.master.pad
    return bool(pad) and S.profile_key(i) == pad


def allowed(i, feature):
    """Whether pad i may use `feature`: always when it is not restricted or there is no master; otherwise only the master (or all / nobody
    when the master is absent, per when_absent).
    """
    m = rx_settings.cur.master
    if not m.pad or feature not in m.only_master:
        return True
    ms = master_slot()
    return m.when_absent == "all" if ms is None else i == ms


def check(i, feature) -> bool:
    """allowed() + a red flash on the pad when it is not allowed (a child knows it is not a malfunction)."""
    if allowed(i, feature):
        return True
    print(f"Controller {i}: {feature} - only the master pad can do that")
    F.pulse(i, "denied")
    return False


def mouse_follows_master(force=False) -> None:
    """The touchpad mouse follows the master: the master pad = mouse, the others = DS4 touch. The master is not connected
    (or force after the master was removed) -> the mouse per mouse_pads from the configuration. With no master in the settings it changes
    nothing (a manual PS + touchpad click switch stays). Called after the master changes and after a pad connects / leaves.
    """
    if not rx_settings.cur.master.pad and not force:
        return
    ms = master_slot()
    for i in range(len(slots)):
        on = i == ms if ms is not None else i in C.MOUSE_PADS
        if rx_mouse.set_mode(i, on):
            print(f"Controller {i}: touchpad -> {'MOUSE' if on else 'DS4 touch'} mode (follows the master pad)")


def master_label(pad):
    """"Pad 2 (aa:bb:...)" for a connected master, just the key for a disconnected one, "none" without a master."""
    if not pad:
        return _("brak")
    i = next((i for i in range(len(slots)) if S.pad_id[i] and S.profile_key(i) == pad), None)
    return f"{ev.pad_name(i)} ({pad})" if i is not None else pad


def _master_changed(old, new) -> None:
    """Settings listener: when the master changes, re-apply mouse mode and log the change."""
    if old.master.pad != new.master.pad:
        mouse_follows_master(force=True)
        if slots:                      # not when settings.json is loaded at startup (before init)
            ev.info(_("Master: {pad}").format(pad=master_label(new.master.pad)))


rx_settings.on_change(_master_changed)


def set_master(i, save=True):
    """The pad in slot i becomes the master (i = None: no master). -> (ok, message)."""
    if i is not None and S.pad_id[i] is None:
        return False, _("Pad {n} nie przysłał jeszcze identyfikatora (MAC) - spróbuj za chwilę").format(n=i + 1)
    pad = "" if i is None else S.profile_key(i)
    if pad == rx_settings.cur.master.pad:
        return True, _("Bez zmian")
    ok, msg = rx_settings.update(C.SETTINGS_PATH if save else None,
                                 master=replace(rx_settings.cur.master, pad=pad))
    if ok and i is not None:
        print(f"Controller {i}: is now the master pad ({pad})")
        F.pulse(i, "master")
    return ok, msg


# ---------------- network thread ----------------
def _action_for(button):
    """The master action assigned to a button, or None."""
    return next((a for a, b in rx_settings.cur.master.actions.items() if b == button), None)


def on_state(i, mask, hx, hy, lt, rt) -> None:
    """Every change of the raw button state of a pad, before the output is recomputed (like rx_hotkeys.on_state)."""
    h = slots[i]
    now = frozenset(pressed_inputs(mask, hx, hy, lt, rt))
    if h.fresh:
        h.fresh, h.prev, h.mod = False, now, MOD in now
        return
    mod = MOD in now
    if mod != h.mod:
        h.mod = mod
        h.gen += 1                          # invalidates waiting timers (claim, a hold action)
        h.combo = None
    if h.mod:
        for b in sorted(now - h.prev - {MOD}):
            if b == CLAIM_BUTTON and rx_settings.cur.master.claim_hold:
                _consume(i, h, b)
                _timer(h, CLAIM_S, _claim, i, h.gen, b)
                continue
            action = _action_for(b)
            if action is None:
                continue
            _consume(i, h, b)
            if not check(i, "actions"):
                continue
            if rx_settings.MASTER_ACTIONS[action][1]:
                F.pulse(i, "hold")          # "keep holding"
                _timer(h, HOLD_S, _held, i, h.gen, b, action)
            else:
                _run(_do, i, action)
    if h.combo and h.combo not in now:
        h.combo = None                      # released too early - no action
        h.gen += 1
    h.sup = h.sup & now                     # a consumed input returns to the game only after it is released
    h.prev = now


def _consume(i, h, b) -> None:
    """A button consumed in a combination: it does not go to the game, and PS does not either after release (and there is no Game Bar from
    a long PS).
    """
    import rx_hotkeys                       # rx_hotkeys imports rx_master - import here
    rx_hotkeys.mark_used(i)
    h.sup |= {b}
    h.gen += 1
    h.combo = b


def filter_inputs(i, mask, hx, hy):
    """Inputs for the game without those consumed in master combinations and without PS when this pad is not allowed to have it."""
    h = slots[i]
    for b in h.sup:
        if b in BUTTON_CODES:
            mask &= ~BIT[BUTTON_CODES[b]]
        elif b in DIRS:                     # triggers (l2 / r2) are neither in the mask nor in the D-pad - they go analog
            dx, dy = DIRS[b]
            if dx and hx == dx:
                hx = 0
            if dy and hy == dy:
                hy = 0
    if mask & BIT[BUTTON_CODES["ps"]] and not allowed(i, "ps_game"):
        mask &= ~BIT[BUTTON_CODES["ps"]]
    return mask, hx, hy


def _timer(h, delay, fn, *args) -> None:
    """(Re)start the slot's timer: any running one is cancelled."""
    if h.timer is not None:
        h.timer.cancel()
    h.timer = threading.Timer(delay, fn, args)
    h.timer.daemon = True
    h.timer.start()


def _run(fn, *args) -> None:
    """Run an action in a separate thread so the network thread is never blocked."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _claim(i, gen, button) -> None:
    """The claim timer fired: if PS + R3 are still held, this pad becomes the master (or confirm with a flash when it already is)."""
    h = slots[i]
    if h.gen != gen or h.combo != button or not h.mod or not rx_settings.cur.master.claim_hold:
        return
    h.combo = None
    ok, msg = set_master(i)
    if not ok:
        print(f"Controller {i}: master not saved: {msg}")
    elif msg == _("Bez zmian"):
        F.pulse(i, "master")


def _held(i, gen, button, action) -> None:
    """The hold timer of a dangerous action fired: run it if the combination is still held."""
    h = slots[i]
    if h.gen != gen or h.combo != button or not h.mod:
        return
    h.combo = None
    _do(i, action)


# ---------------- actions ----------------
def _do(i, action) -> None:
    """Perform a master action: pause (Esc), close the foreground game window (never the desktop, Steam or this receiver),
    sleep or shut down the PC (through console mode's power-off notification). Errors are logged, never raised.
    """
    try:
        hwnd = 0
        if action == "close_game":
            # the window that is checked and the window that is closed are THE SAME window: S.game can be half a second old (rx_games), and
            # the "active window"
            # at the moment of closing could already be the desktop or Steam (alt-tab during the hold)
            hwnd, exe = W.foreground_window()
            if not hwnd or not exe or exe in NO_CLOSE:
                print(f"Controller {i}: close game - foreground is {exe or 'nothing'}, not closing")
                F.pulse(i, "denied")
                return
            print(f"Controller {i}: close game ({exe})")
        print(f"Controller {i}: master action {action}")
        F.pulse(i, "action")                 # before sleep / shut down - afterwards the pad may no longer get the packet
        if action == "pause":
            W.send_keys(W.VK_ESCAPE)
        elif action == "close_game":
            W.close_window(hwnd)
        elif action in ("sleep", "shutdown"):
            import rx_console                # rx_console imports rx_master - import here
            rx_console.power_off(_("wyłączanie komputera") if action == "shutdown" else _("usypianie komputera"))
            W.sleep_pc() if action == "sleep" else W.shutdown_pc()
    except Exception as e:
        print(f"Controller {i}: master action {action} failed: {e!r}")
        ev.error(_("{pad}: akcja mastera {action} nieudana: {err}").format(pad=ev.pad_name(i), action=action, err=repr(e)))
