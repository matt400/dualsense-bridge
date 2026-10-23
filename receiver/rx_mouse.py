"""Receiver: touchpad as a mouse (movement with a fractional remainder, left / right tap, two-finger scrolling, button).

T_MOUSE packet flags: bit0 = tap (left button), bit1 = scrolling (dx, dy is the sum of the finger movements), bit2 = right tap,
bits 4-7 = tap number 1..15 (the sender repeats the tap packet, the number lets the click be executed only once).

Physical click stabilisation (settings in the window: rx_settings.MouseConfig). Pressing the finger down shifts its centre
on the sensor, so the cursor used to "run away" from the target:
  rewind  movement from the last click_rewind_ms before the press is undone (the same steps backwards - Windows
          acceleration treats them the same way, so the cursor returns to the same spot)
  freeze  for click_freeze_ms after the press and after the release the movement is ignored
  drag    while the click is held the cursor starts moving only after drag_threshold units (a deliberate drag)
"""
from __future__ import annotations

import time
from collections import deque
from typing import Final

import rx_settings
import rx_state as S
import rx_winapi as W
from proto import MOUSE_SCROLL, MOUSE_TAP, MOUSE_TAP_ID_SHIFT, MOUSE_TAP_RIGHT
from rx_config import C

# SendInput MOUSEEVENTF_* flags
MOVE, LDOWN, LUP, RDOWN, RUP, WHEEL, HWHEEL = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0800, 0x1000
TAP_REPEAT_S: Final = 0.5        # a packet with the same tap number inside this window is a redundant copy
BUTTON_LEFT, BUTTON_RIGHT = 1, 2
MS_PER_S: Final = 1000

mrem: list[list[float]] = []     # fractional movement remainders per slot
srem: list[list[float]] = []     # fractional scroll remainders per slot
sens: list[float] = []           # mouse sensitivity per slot (changed by the PS + d-pad up / down shortcut)
mdown: list[int] = []            # pressed button per slot: 0 = none, 1 = left, 2 = right
last_tap: list[tuple[int, float]] = []   # (number, time) of the last executed tap per slot
hist: list[deque[tuple[float, int, int]]] = []   # recently sent cursor moves (time, dx, dy) per slot - to be undone on a click
frozen: list[float] = []         # until when (monotonic) cursor movement is ignored, per slot
drag: list[float | None] = []    # movement [touchpad units] since the click was pressed; None = the cursor moves normally
BTN: Final = {BUTTON_LEFT: (LDOWN, LUP), BUTTON_RIGHT: (RDOWN, RUP)}


def init(n: int) -> None:
    """Reset all per-slot mouse state for `n` pads (called at startup)."""
    mrem[:] = [[0.0, 0.0] for _ in range(n)]
    srem[:] = [[0.0, 0.0] for _ in range(n)]
    sens[:] = [rx_settings.cur.mouse.sens] * n
    mdown[:] = [0] * n
    last_tap[:] = [(0, 0.0)] * n
    hist[:] = [deque() for _ in range(n)]
    frozen[:] = [0.0] * n
    drag[:] = [None] * n


def _settings_changed(old: rx_settings.Settings, new: rx_settings.Settings) -> None:
    """Settings listener: a changed mouse sensitivity is applied to every slot."""
    if new.mouse.sens != old.mouse.sens:
        sens[:] = [new.mouse.sens] * len(sens)     # the slider in the window sets all pads (PS + up / down changes one)


rx_settings.on_change(_settings_changed)


def reset(i: int) -> None:
    """Release the button and clear the fractional remainders (pad disconnect, mode change)."""
    mouse_button(i, 0)
    mrem[i] = [0.0, 0.0]
    srem[i] = [0.0, 0.0]
    hist[i].clear()
    frozen[i] = 0.0
    drag[i] = None


def set_mode(i: int, on: bool) -> bool:
    """Switch the touchpad of pad i between mouse (on) and DS4 touch.

    Releases the button and resets the gesture; the game receives a released touchpad click.

    Returns:
        True when the mode changed.
    """
    if S.mouse_mode[i] == on:
        return False
    S.mouse_mode[i] = on
    reset(i)
    with S.lock:
        S.states[i]["tp"] = False
        S.states[i]["ver"] += 1
    S.notify(i)
    return True


def _tap(i: int, flags: int) -> bool:
    """Execute the click of a tap packet once per tap number; return False for a redundant copy of an executed tap."""
    tid, now = flags >> MOUSE_TAP_ID_SHIFT, time.monotonic()
    if tid and tid == last_tap[i][0] and now - last_tap[i][1] < TAP_REPEAT_S:
        return False                     # a repeat of the same tap (redundancy in case a packet is lost)
    last_tap[i] = (tid, now)
    if flags & MOUSE_TAP:                # tap = left click
        W.send_click(LDOWN, LUP)
    if flags & MOUSE_TAP_RIGHT:          # two-finger tap = right click
        W.send_click(RDOWN, RUP)
    return True


def _scroll(i: int, dx: int, dy: int) -> None:
    """Two-finger scrolling: 120 = one notched wheel step, smaller values = smooth scrolling."""
    k = C.SCROLL_SENS if C.SCROLL_NATURAL else -C.SCROLL_SENS
    fx = dx * -k + srem[i][0]          # horizontal: fingers right = scroll right (reversed in "natural" mode)
    fy = dy * k + srem[i][1]
    sx, sy = int(fx), int(fy)
    srem[i][0], srem[i][1] = fx - sx, fy - sy
    if sy:
        W.send_mouse(WHEEL, data=sy)
    if sx:
        W.send_mouse(HWHEEL, data=sx)


def _move(i: int, dx: int, dy: int) -> None:
    """Cursor movement: multiplied by the sensitivity, the fractional remainder carried to the next packet.

    It is skipped right after a click (freeze) and while a held click has not yet moved past the drag threshold; sent
    steps are remembered in `hist` so that _rewind can undo them when the click is pressed.
    """
    now = time.monotonic()
    if now < frozen[i]:
        mrem[i] = [0.0, 0.0]
        return                              # right after a press / release of the click: finger pressure jitter
    held = drag[i]
    if held is not None:
        held += abs(dx) + abs(dy)
        drag[i] = held
        if held < rx_settings.cur.mouse.drag_threshold:
            return                          # click held without clear movement - the cursor stays on the target
        drag[i] = None                      # a deliberate drag - from now on ordinary movement
    fx = dx * sens[i] + mrem[i][0]
    fy = dy * sens[i] + mrem[i][1]
    sx, sy = int(fx), int(fy)
    mrem[i][0], mrem[i][1] = fx - sx, fy - sy
    if sx or sy:
        W.send_mouse(MOVE, sx, sy)
        h = hist[i]
        h.append((now, sx, sy))
        limit = now - rx_settings.cur.mouse.click_rewind_ms / MS_PER_S
        while h and h[0][0] < limit:
            h.popleft()


def mouse_rel(i: int, dx: int, dy: int, flags: int) -> None:
    """Handle a T_MOUSE packet of pad i: taps (once per tap number), two-finger scrolling or cursor movement."""
    if flags & (MOUSE_TAP | MOUSE_TAP_RIGHT) and not _tap(i, flags):
        return
    if not (dx or dy):
        return
    if flags & MOUSE_SCROLL:
        _scroll(i, dx, dy)
    else:
        _move(i, dx, dy)


def _rewind(i: int, now: float) -> None:
    """Undo the cursor movement of the last click_rewind_ms (the steps in reverse order and with the opposite sign)."""
    h, limit = hist[i], now - rx_settings.cur.mouse.click_rewind_ms / MS_PER_S
    while h:
        t, sx, sy = h.pop()
        if t >= limit:
            W.send_mouse(MOVE, -sx, -sy)
    mrem[i] = [0.0, 0.0]


def mouse_button(i: int, btn: int) -> None:
    """Physical touchpad click: 0 = release, 1 = left, 2 = right.

    Changing the button while one is held releases the previous one.
    """
    cur = mdown[i]
    if btn == cur:
        return
    now = time.monotonic()
    m = rx_settings.cur.mouse
    if btn in BTN and not cur:
        _rewind(i, now)                     # the cursor goes back to where it was before the finger pressed down
        drag[i] = 0
    if cur:
        W.send_mouse(BTN[cur][1])
    if btn in BTN:
        W.send_mouse(BTN[btn][0])
    else:
        drag[i] = None
    mdown[i] = btn if btn in BTN else 0
    frozen[i] = now + m.click_freeze_ms / MS_PER_S
