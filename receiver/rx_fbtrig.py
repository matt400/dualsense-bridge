"""Receiver: trigger effects and game LEDs sent to the pad (see rx_feedback).

What goes to the sender for the triggers: the profile's resistance / click / vibration, the effects the game set through the
virtual DualSense (trig3), the master-only restriction, and the game's mute / player LEDs (leds packet). Which packet a
sender understands is decided by its version and CAP_* bits. The per-slot state lives in rx_fbstate; the loop that calls
these ticks is rx_feedback.feedback_thread.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import rx_state as S
from proto import (
    CAP_TRIG_FX, CAP_TRIG_RAW, LEDS_MUTE, LEDS_PLAYER, LEDS_SENDER, TRG3_L2, TRG3_R2, TRIG2_OFF, TRIG_RAW_LEN,
)
from rx_fbstate import (
    FX_SENDER, IDLE_REFRESH_S, NO_GAME_TRIG, RAW_SENDER, SendLeds, SendTriggers, TRIG_OFF_MODES, TRIG_REFRESH_S,
    TRIG_REPEAT, Trig, fb, leds_sent, trig_sent,
)
from rx_shape import legacy_values, trigger_values

def sender_leds(i: int) -> bool:
    """Tell whether this pad's sender understands the leds packet (v15+; the ways byte has no free bit left, so the version decides)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= LEDS_SENDER


def leds_target(i: int) -> tuple[int, int, int] | None:
    """Return the leds packet values for what the game set (flags, mute LED, player LEDs), or None when it set neither."""
    mute, player = fb[i]["leds"]
    if mute is None and player is None:
        return None
    return ((LEDS_MUTE if mute is not None else 0) | (LEDS_PLAYER if player is not None else 0), mute or 0, player or 0)


def leds_tick(i: int, now: float, send_leds: SendLeds) -> None:
    """Send the LEDs the game set: a change at once (several times), the same values again every IDLE_REFRESH_S.

    Nothing is sent while the game has set no LED (the pad's driver keeps both) or to a sender that does not know the packet.
    The refresh brings the LEDs back after the pad reconnected or a packet was lost.
    """
    vals = leds_target(i)
    if vals is None or not sender_leds(i):
        return
    last = leds_sent[i]
    if last is None or last[0] != vals:
        leds_sent[i] = (vals, now)
        send_leds(i, vals, TRIG_REPEAT)
    elif now - last[1] >= IDLE_REFRESH_S:
        leds_sent[i] = (vals, now)
        send_leds(i, vals, 1)


def sender_fx(i: int) -> bool:
    """Tell whether this pad's sender knows trig2 (click, trigger vibration, game rumble)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= FX_SENDER and bool(info[1] & CAP_TRIG_FX)


def sender_raw(i: int) -> bool:
    """Tell whether this pad's sender knows trig3 (trigger effects set by the game)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= RAW_SENDER and bool(info[1] & CAP_TRIG_RAW)


def game_drives(game: Trig) -> bool:
    """Tell whether the game holds an effect (other than none / Off) on some trigger."""
    return any(t is not None and t[0] not in TRIG_OFF_MODES for t in game)


def trigger_target(i: int) -> tuple[Any, ...]:
    """Return the values to send to the sender.

    trig3 (profile + the game's trigger effects) when the game set them and the sender knows them, else trig2 for a new
    sender, trig (resistance only) for an older one; no effects (game ones too) when trigger effects are master-only and
    this is not the master. On a trigger with a profile effect the profile wins (the sender decides - any mode other than
    TFX_OFF); when the game itself drives the triggers, game rumble on the profile's triggers is switched off.
    """
    import rx_master        # rx_master imports this module
    vals: tuple[int, ...]
    if not rx_master.allowed(i, "triggers"):
        vals, game = TRIG2_OFF, NO_GAME_TRIG
    else:
        vals, game = trigger_values(S.remap_maps[i]["triggers"]), fb[i]["trig"] if i < len(fb) else NO_GAME_TRIG
    if game != NO_GAME_TRIG and sender_raw(i):
        if game_drives(game):
            vals = (*vals[:8], 0, 0, 0, 0)
        flags = (TRG3_L2 if game[0] is not None else 0) | (TRG3_R2 if game[1] is not None else 0)
        return (*vals, *(t if t is not None else bytes(TRIG_RAW_LEN) for t in game), flags)
    return vals if sender_fx(i) else legacy_values(vals)


def _has_effect(vals: Sequence[Any]) -> bool:
    """Tell whether a trigger value sequence (ints, and raw ``bytes`` effects in trig3) holds anything but zeros.

    A plain ``any(vals)`` would count every non-empty ``bytes`` as set, even an all-zero raw effect.
    """
    return any(any(v) if isinstance(v, bytes) else v for v in vals)


def triggers_tick(i: int, now: float, send_trig: SendTriggers) -> None:
    """Trigger effects of the slot's profile.

    A change -> at once (several times), constant effects -> refreshed every TRIG_REFRESH_S. The first pass after the
    pad connects always sends the state, "no effects" too: the sender and the pad may hold effects from a previous
    receiver run (a restart after an update, closing mid-game with a click on R2) - without this the trigger stayed hard
    when the profile in use after startup has no effects. Afterwards nothing is refreshed while there are no effects.
    """
    vals = trigger_target(i)
    last = trig_sent[i]
    if last is None or last[0] != vals:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, TRIG_REPEAT)
    elif _has_effect(vals) and now - last[1] >= TRIG_REFRESH_S:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, 1)


def release_triggers(send_trig: SendTriggers) -> None:
    """Receiver shutdown (the feedback thread is already stopped): pads we set trigger effects on get "no effects".

    The sender keeps an effect until the next change, so without this the resistance / click stayed after leaving the
    receiver (before a restart and an update too) until the pad disconnected. The sender also has its own guard
    (sender_rumble) that releases the triggers when the PC refreshes stop (PC sleep / crash).
    """
    for i, last in enumerate(trig_sent):
        if last is not None and _has_effect(last[0]):
            # trig3 (game effects) also releases trig2 - a v11 sender then forgets the game effects
            send_trig(i, TRIG2_OFF if len(last[0]) >= len(TRIG2_OFF) else (0, 0, 0, 0), TRIG_REPEAT)
            trig_sent[i] = None
