"""Receiver: the Test view open in the window - shortcuts of all pads are disabled, plus an emergency exit from the pad.

The window (GUI thread) calls set_active() when the Test view is open in the active window. Then no pad runs
shortcuts: PS + button (rx_hotkeys), master actions and claim (rx_master), console mode (rx_console), PS + touchpad
click. After a state change the shortcut slots start from a clean state (sync() in the network thread), so a button held
across the test boundary triggers nothing.

The only exception: Options + Create pressed together on the master pad (with no master in the settings - on any pad; master
set but not connected - according to when_absent) ends the test at once, even when the window is hidden or blocked, with no
mouse: it lifts the output block to the game, and the buttons and D-pad held at that moment (Options, Create and the rest on
the tested pad) are delivered to the game as released until they are actually released - the first report after the test does not
carry the combination; sticks and triggers go on analogously, live. The window gets on_escape().

Without PS and without a hold: a long PS + triangle used to switch the pad off (Bluetooth). It is the edge that counts - a combination
already held on entering Test or across the test boundary has to be released and pressed again.

Touchpad in the mouse mode (the master pad): the touchpad keeps driving the cursor in the Test view - it is the only way
to operate the window from the couch - and only the "Test touchpad" button of the panel captures it. While a capture runs
(start_capture) the cursor stands, taps and clicks are not executed, and the fingers and the click are only drawn. It ends by
itself: PS on that pad, CAPTURE_IDLE_S without a touch or a click, leaving the Test view (or the window losing focus), the exit
from the pad, the button once more, a disconnected pad. Nothing here blocks the game output - that is the Test lock (S.set_blocked).
"""
import time
from collections.abc import Callable
from typing import Final

import rx_events as ev
import rx_feedback as F
import rx_hotkeys as hotkeys
import rx_master as master
import rx_mouse as mouse
import rx_settings
import rx_state as S
from rx_buttons import BIT, BUTTON_CODES, PS_BIT, TRIGGERS, pressed_inputs
from rx_i18n import _

ESCAPE_KEYS = ("options", "share")              # Options + Create (Share)
ESCAPE_MASK = BIT[BUTTON_CODES["options"]] | BIT[BUTTON_CODES["share"]]
CAPTURE_IDLE_S: Final = 8.0                     # a touchpad capture ends after this long without a touch or a click

active = False                                  # the Test view is open - pad shortcuts are disabled
escaped = False                                 # the test was ended from a pad; rearm() on the next entry into Test
on_escape: Callable[[], None] | None = None     # window: leave the Test view (called from the network thread)
capture_slot: int | None = None                 # the pad whose touchpad is captured by the test (the cursor stands), or None
_capture_t = 0.0                                # monotonic time of the last touch / click of the captured pad
_gen = 0                                        # changes of `active` - the shortcut slots start afresh (sync)
_seen: list[int] = []
_held: list[bool] = []                          # the slot holds the combination in the last known state


def init(n: int) -> None:
    """Start with the test closed, no touchpad captured and no combination held for `n` pads."""
    global active, escaped, capture_slot
    active = escaped = False
    capture_slot = None
    _seen[:] = [_gen] * n
    _held[:] = [False] * n


def _slot(i: int) -> int:
    """Slot index; without init() (e.g. the module used before the window started) the lists grow to the number of slots."""
    if i >= len(_held):
        n = max(i + 1, len(S.states or ()))
        _seen.extend([_gen] * (n - len(_seen)))
        _held.extend(_now_held(k) for k in range(len(_held), n))
    return i


def _now_held(i: int) -> bool:
    """Whether the combination is pressed in the last state of slot i (before the first packet: no)."""
    states = S.states or ()
    return i < len(states) and states[i]["raw"][0] & ESCAPE_MASK == ESCAPE_MASK


def set_active(on: bool) -> bool:
    """Window thread: the Test view was opened / closed -> True when the value changed."""
    global active, _gen
    on = bool(on)
    if on == active:
        return False
    active = on
    if not on:
        stop_capture()                                 # a captured touchpad returns to the mouse with the Test view
    _gen += 1
    _held[:] = map(_now_held, range(len(_held)))   # held across the test boundary - only a new press exits
    return True


def rearm() -> None:
    """Window thread: entering the Test view again after leaving it from a pad."""
    global escaped
    escaped = False


def sync(i: int) -> None:
    """Network thread, before the shortcuts.

    After the test state changes, the pad shortcut slots start from a clean state (no combinations in progress, no hold
    timers, no PS waiting for a pulse) - computed from the inputs held before the change (S.states[i]["raw"] still has
    the previous state), so a new press works at once.
    """
    if _seen[_slot(i)] != _gen:
        _seen[i] = _gen
        hotkeys.reset(i, keep=True)
        master.reset(i, keep=True)
        import rx_console                 # rx_console imports rx_master - import here
        rx_console.resync(i)


def shortcuts_on() -> bool:
    """Whether the pad shortcuts work (they are off while the Test view is open)."""
    return not active


def start_capture(i: int) -> bool:
    """Window thread: capture the touchpad of pad i for the test - the cursor stands, the fingers and the click are only drawn.

    Only in the Test view, for a connected pad whose touchpad is the mouse (in the DS4 touch mode the touch is visible anyway).
    A click held at that moment is released, so the button does not stay down while the cursor is frozen.

    Returns:
        True when the capture runs.
    """
    global capture_slot, _capture_t
    if not active or not S.alive[i] or not S.mouse_mode[i]:
        return False
    capture_slot, _capture_t = i, time.monotonic()
    mouse.reset(i)
    return True


def stop_capture() -> None:
    """End the touchpad capture (any thread): the touchpad drives the cursor again."""
    global capture_slot
    capture_slot = None


def capturing(i: int) -> bool:
    """Network thread (every mouse packet): whether the test holds the touchpad of pad i.

    A capture that saw no touch for CAPTURE_IDLE_S ends here, so it also ends with the window hidden.
    """
    if capture_slot != i:
        return False
    if time.monotonic() - _capture_t > CAPTURE_IDLE_S:
        stop_capture()
        return False
    return True


def touched(i: int) -> None:
    """Network thread: a finger moved or the click changed on pad i - the capture idle timer starts again."""
    global _capture_t
    if capture_slot == i:
        _capture_t = time.monotonic()


def capture_left(i: int) -> float:
    """Seconds until the capture of pad i ends by itself, 0.0 when it is not running (window: the countdown on the button)."""
    if not capturing(i):
        return 0.0
    return max(0.0, CAPTURE_IDLE_S - (time.monotonic() - _capture_t))


def may_escape(i: int) -> bool:
    """The master pad; with no master in the settings any pad; master not connected - according to when_absent (like rx_master)."""
    m = rx_settings.cur.master
    if not m.pad:
        return True
    ms = master.master_slot()
    return m.when_absent == "all" if ms is None else i == ms


def on_state(i: int, raw: tuple) -> bool:
    """Raw buttons of slot i changed (network thread), raw = (mask, hx, hy, lt, rt) before they are stored in the state.

    Detects the rising edge of Options + Create on a pad that may escape. -> True when the exit has just been performed.
    A new PS press on the captured pad only ends the touchpad capture (the previous state is still in S.states[i]["raw"]).
    """
    if not active:
        return False
    if capture_slot == i and raw[0] & PS_BIT and not S.states[i]["raw"][0] & PS_BIT:
        stop_capture()
    _slot(i)
    held = raw[0] & ESCAPE_MASK == ESCAPE_MASK
    pressed, _held[i] = held and not _held[i], held
    if not pressed or not may_escape(i):
        return False
    escape(i, raw)
    return True


def escape(i: int, raw: tuple | None = None) -> None:
    """Forced exit from the Test view through pad i: end the block, neutral inputs until released, the window leaves.

    raw: the state of pad i from the packet that triggered the exit - stored at once because the combination is in it
    for the first time (earlier the state in S.states was from before the press and Options + Create would not be
    swallowed).
    """
    global active, escaped, _gen
    if raw is not None:
        with S.lock:
            S.states[i]["raw"] = raw
    stop_capture()
    active, escaped = False, True
    _gen += 1                           # _held: rebuilt in set_active() on the next entry into Test
    for s in range(len(S.states)):
        if s == i or S.states[s]["blocked"]:
            _release(s)
    _seen[i] = _gen                     # slot i is already cleaned in _release - sync() will not lose the swallowing
    print(f"Controller {i}: Options + Create - test view closed, output to games restored")
    ev.info(_("{pad}: wyjście z widoku Test (Options + Create)").format(pad=ev.pad_name(i)))
    F.pulse(i, "action")
    if (cb := on_escape) is not None:
        cb()


def _release(s: int) -> None:
    """The slot returns to the game with a neutral report.

    The inputs held now are consumed (the game gets them as released until they are released), only then is the block
    lifted - no report to the virtual DS4 carries Options + Create.
    """
    import rx_console                   # rx_console imports rx_master - import here
    hotkeys.reset(s, keep=True)
    master.reset(s, keep=True)
    rx_console.resync(s)                # a mode button held at exit is not a new click
    with S.lock:
        st = S.states[s]
        master.slots[s].sup = frozenset(pressed_inputs(*st["raw"])) - set(TRIGGERS)   # triggers: analog, live
        st["fwd"] = hotkeys.forward(s, *st["raw"][:3])
    _seen[s] = _gen
    if not S.set_blocked(s, False):     # it was not blocked (the master next to the tested pad) - just recompute the output
        with S.lock:
            st = S.states[s]
            S.recompute(st, S.remap_maps[s])
            st["lat"] = st["blat"] = None
            st["ver"] += 1
        S.notify(s)
