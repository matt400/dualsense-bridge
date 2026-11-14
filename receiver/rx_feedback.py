"""Receiver: feedback to the pad (rumble, lightbar) from the game, shortcut confirmations and trigger effects.

Rumble and lightbar come from the game through the virtual pad (ViGEm / HIDMaestro); the confirmations are short pulses
for shortcuts. Trigger effects come from the active profile (a game cannot set them through DS4 - the sender does it with an HID report):
resistance, click, trigger vibration and game rumble on the triggers (computed on the sender from the same rumble
packets). The master feature "triggers" (Settings -> Master) = effects only on the master pad, other pads get plain
triggers.

Lightbar: a colour from the game applies only when a game is in the foreground (rx_games.in_game: not the desktop, Steam,
a launcher) and the pad has "Game may change colour" (PadLight.game). Outside a game, and with the option off, the colour comes
from the pad settings (solid / by battery, rx_lightbar). Steam sets the virtual pad's colour on the desktop too - this keeps it from
covering the own colour until the pad disconnects. The PC colour is remembered, so it returns at once when the game comes back. The first
such colour goes to the log - otherwise "my colour does not work" would leave no trace. Low battery blinks red
also over the game colour; confirmations (e.g. the master's golden flash) are always on top.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from types import ModuleType

import rx_events as ev
import rx_lightbar as L
import rx_settings
import rx_state as S
import rx_swap
from proto import STATE_CHARGING, clamp
from rx_fbstate import (
    FX_SENDER, FeedbackState, GAME_RUMBLE_GAP_S, GAME_RUMBLE_STALE_S, IDLE_REFRESH_S, Leds, NO_GAME_TRIG, NO_LEDS,
    PCT_MAX, PULSES, Pulse, RAW_SENDER, RUMBLE_REFRESH_S, SendFeedback, SendLeds, SendTriggers, TRIG_OFF_MODES,
    TRIG_REFRESH_S, TRIG_REPEAT, Trig, cbs, fb, fb_event, fb_sent, leds_sent, pulses, trig_sent,
)
from rx_fbtrig import (
    _has_effect, game_drives, leds_target, leds_tick, release_triggers, sender_fx, sender_leds, sender_raw,
    trigger_target, triggers_tick,
)
from rx_config import C
from rx_i18n import _
from rx_shape import BYTE_MAX

__all__ = [  # the state and the trigger / LED ticks moved out (rx_fbstate, rx_fbtrig); this module stays their one public face
    "FX_SENDER",
    "GAME_RUMBLE_GAP_S",
    "GAME_RUMBLE_STALE_S",
    "IDLE_REFRESH_S",
    "NO_GAME_TRIG",
    "NO_LEDS",
    "PCT_MAX",
    "PULSES",
    "RAW_SENDER",
    "RUMBLE_REFRESH_S",
    "TRIG_OFF_MODES",
    "TRIG_REFRESH_S",
    "TRIG_REPEAT",
    "FeedbackState",
    "Leds",
    "Pulse",
    "SendFeedback",
    "SendLeds",
    "SendTriggers",
    "Trig",
    "_has_effect",
    "cbs",
    "fb",
    "fb_event",
    "fb_sent",
    "game_drives",
    "leds_sent",
    "leds_target",
    "leds_tick",
    "pulses",
    "release_triggers",
    "sender_fx",
    "sender_leds",
    "sender_raw",
    "trig_sent",
    "trigger_target",
    "triggers_tick",
]


_games: ModuleType | None = None          # rx_games (it imports this module) - loaded lazily, on first use


def in_game() -> bool:
    """Tell whether a game is in the foreground (rx_games.in_game, imported lazily to avoid a circular import)."""
    global _games
    if _games is None:
        import rx_games
        _games = rx_games
    return bool(_games.in_game())


def make_cb(i: int):  # noqa: ANN202 - the return type would only be the unannotated cb below
    """Build the ViGEm callback (on the ViGEm thread) for slot i.

    The parameter names match vgamepad.dummy_callback - vgamepad compares the signature with the names and rejects any
    other (a TypeError = no rumble from games). led_number is either the lightbar colour DS4_LIGHTBAR_COLOR (registered
    through rx_ds4.register_feedback) or a meaningless number (registered through vgamepad, which declares this argument
    as a byte) - then the game colour is unknown and the lightbar keeps its own.
    """
    # No annotations on purpose: vgamepad compares inspect.signature() with its own dummy_callback, return annotation included.
    def cb(client, target, large_motor, small_motor, led_number, user_data):  # noqa: ANN001, ANN202 - see above
        try:
            f = fb[rx_swap.route(i)]            # virtual pad i -> the physical pad it is fed by (swapped players)
            # Wake the feedback thread only for a real change: games repeat the same rumble state every frame, and f["t"]
            # alone needs no wake-up (_wait_time computes the refresh deadline from it).
            changed = not f["set"] or (f["large"], f["small"]) != (int(large_motor), int(small_motor))
            f["large"], f["small"] = int(large_motor), int(small_motor)
            f["t"] = time.monotonic()
            if hasattr(led_number, "Red"):
                changed = _merge_rgb(f, (led_number.Red, led_number.Green, led_number.Blue)) or changed
            f["set"] = True
        except Exception:  # noqa: BLE001 - driver thread: a malformed callback must never raise into ViGEm
            changed = True
        if changed:
            fb_event.set()
    return cb


def _merge_leds(f: FeedbackState, leds: Sequence[int | None]) -> bool:
    """Take the LED bytes the game set (None = unchanged); True when they differ from the stored ones."""
    new_leds = tuple(old if v is None else int(v) for v, old in zip(leds, f["leds"], strict=True))
    if new_leds == f["leds"]:
        return False
    f["leds"] = new_leds                # the whole tuple is replaced - the feedback thread reads a consistent one
    return True


def _merge_trig(f: FeedbackState, trig: Sequence[bytes | None]) -> bool:
    """Take the trigger effects the game set (None = that trigger unchanged); True when they differ from the stored ones."""
    new = tuple(bytes(t) if t is not None else old for t, old in zip(trig, f["trig"], strict=True))
    if new == f["trig"]:
        return False
    f["trig_note"] = f["trig_note"] or f["trig"] == NO_GAME_TRIG
    f["trig"], f["trig_owner"] = new, S.game     # the whole tuple is replaced - the feedback thread reads a consistent one
    return True


def _merge_rgb(f: FeedbackState, rgb: Sequence[int]) -> bool:
    """Take the lightbar colour the game set; True when it is the first one or differs from the stored colour."""
    new_rgb = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    changed = not f["rgb"] or (f["r"], f["g"], f["b"]) != new_rgb
    f["r"], f["g"], f["b"] = new_rgb
    f["note"] = f["note"] or not f["rgb"]
    f["rgb"] = True
    return changed


def game(i: int, large: int | None = None, small: int | None = None, rgb: Sequence[int] | None = None,
         trig: Sequence[bytes | None] | None = None, leds: Sequence[int | None] | None = None) -> None:
    """Record rumble / lightbar colour / trigger effects / LEDs from the game for slot i (on the virtual pad backend thread).

    None = leave that part unchanged. A DualSense output report carries the motors, the colour, each trigger and each LED
    separately, with their own validity flags. trig = (L2, R2): the raw 11 B effect or None (that trigger unchanged).
    leds = (mute button LED, player LEDs): the raw bytes or None (that LED unchanged).
    """
    try:
        f = fb[rx_swap.route(i)]                # virtual pad i -> the physical pad it is fed by (swapped players)
    except IndexError:
        return
    changed = not f["set"]                      # only a real change wakes the feedback thread (games repeat reports every frame)
    if leds is not None:
        changed = _merge_leds(f, leds) or changed
    if trig is not None and any(t is not None for t in trig):
        changed = _merge_trig(f, trig) or changed
    if large is not None:
        motors = (int(large), int(small or 0))
        changed = changed or (f["large"], f["small"]) != motors
        f["large"], f["small"] = motors
        f["t"] = time.monotonic()               # refresh only: _wait_time derives the RUMBLE_REFRESH_S deadline from it
    elif rgb is not None or trig is not None:
        f["t_other"] = time.monotonic()        # a game report without motors (GAME_RUMBLE_GAP_S)
        changed = changed or bool(f["large"] or f["small"])   # rumble running: the gap timer must be re-evaluated
    if rgb is not None:
        changed = _merge_rgb(f, rgb) or changed
    f["set"] = True
    if changed:
        fb_event.set()


def forget_ended_games(running: Callable[[str], bool]) -> list[int]:
    """Release trigger effects set by a program that is gone (a game closed without releasing the triggers).

    Like the rumble in Forza Horizon. running(exe) tells whether the program is running. Effects of an unknown program
    (None) stay until changed by the game or until the pad disconnects. Returns the slots whose triggers were released.
    """
    out: list[int] = []
    for i, f in enumerate(fb):
        owner = f["trig_owner"]
        if owner and f["trig"] != NO_GAME_TRIG and owner != S.game and not running(owner):
            f["trig"], f["trig_owner"] = NO_GAME_TRIG, None
            print(f"Controller {i}: {owner} closed - its trigger effects released")
            out.append(i)
    if out:
        fb_event.set()
    return out


def stop_game_rumble(why: str) -> None:
    """The foreground program changed (left a game, alt-tab): game rumble -> 0.

    A game that ends mid-rumble (e.g. Forza Horizon on exit) never sends "motors 0", and the receiver refreshes the last
    value every 0.5 s, so without this the pad would vibrate forever. A game that returns to the foreground sends a
    fresh rumble itself.
    """
    for i, f in enumerate(fb):
        if f["large"] or f["small"]:
            f["large"] = f["small"] = 0
            print(f"Controller {i}: game rumble stopped ({why})")
            fb_event.set()


def init() -> None:
    """Create per-slot feedback state (motors, colour, trigger effects, timestamps) and callbacks.

    A remap change wakes the feedback thread.
    """
    n = C.NUM_PADS
    fb[:] = [{"small": 0, "large": 0, "r": 0, "g": 0, "b": 0, "set": False, "rgb": False, "note": False,
              "t": 0.0, "t_other": 0.0, "trig": NO_GAME_TRIG, "trig_owner": None, "trig_note": False, "leds": NO_LEDS}
             for _ in range(n)]
    fb_sent[:] = [None] * n
    trig_sent[:] = [None] * n
    leds_sent[:] = [None] * n
    pulses[:] = [None] * n
    cbs[:] = [make_cb(i) for i in range(n)]    # keep the references (GC)
    S.on_remap = fb_event.set


rx_settings.on_change(lambda _old, _new: fb_event.set())    # a new lightbar colour / mode takes effect at once


def reset(i: int) -> None:
    """Forget what the game set after a new / disconnected virtual DS4 in the slot (without init() - nothing to clear)."""
    if i < len(fb):
        fb[i].update(set=False, small=0, large=0, t_other=0.0, rgb=False, note=False, trig=NO_GAME_TRIG,
                     trig_owner=None, trig_note=False, leds=NO_LEDS)
        fb_sent[i] = leds_sent[i] = None


def pulse(i: int, kind: str, force: bool = False) -> None:
    """A short confirmation on the pad (rumble and/or colour), laid over the game feedback.

    force: also when confirm = false (the rumble test from the window).
    """
    if (C.CONFIRM or force) and 0 <= i < len(pulses) and S.alive[i]:
        d, small, large, rgb = PULSES[kind]
        pulses[i] = (time.monotonic() + d, small, large, rgb)
        fb_event.set()


def is_low(i: int, low_pct: float) -> bool:
    """Tell whether the battery is known, not charging, and at or below the threshold (the same test as the pad card)."""
    pct, flags = S.batt_info[i]
    return pct <= PCT_MAX and not flags & STATE_CHARGING and pct <= low_pct


def _note_game_triggers(i: int) -> None:
    """The first game trigger effects after the virtual pad was created - a log entry saying what will happen to them."""
    import rx_master        # rx_master imports this module
    f = fb[i]
    f["trig_note"] = False
    who = f["trig_owner"] or "?"
    if not sender_raw(i):
        why, text = "sender too old", _("{pad}: {exe} ustawia efekty spustów - nadajnik w starszej wersji ich nie przekaże "
                                         "(kliknij Aktualizuj)")
    elif not rx_master.allowed(i, "triggers"):
        why, text = "master only", _("{pad}: {exe} ustawia efekty spustów - pominięte (efekty spustów tylko dla pada "
                                     "master)")
    else:
        why, text = "", _("{pad}: {exe} ustawia efekty spustów - idą do pada (spust z efektem w profilu zostaje "
                          "z profilu)")
    print(f"Controller {i}: trigger effects set by {who}" + (f" - not used ({why})" if why else ""))
    ev.info(text.format(pad=ev.pad_name(i), exe=who))


def _note_game_color(i: int) -> None:
    """The first lightbar colour from a game / PC program after the virtual pad was created - a log entry."""
    f = fb[i]
    f["note"] = False
    rgb = tuple(clamp(f[k], 0, BYTE_MAX) for k in ("r", "g", "b"))
    game = rx_settings.cur.lightbar.get(S.profile_key(i)).game
    playing = in_game()
    print(f"Controller {i}: lightbar colour {L.rgb_hex(rgb)} set by a program on the PC ({S.game})"
          + ("" if game and playing else " - ignored (own colour always)" if not game else " - used only in a game"))
    text = (_("{pad}: gra ustawiła kolor lightbara {hex} - ma pierwszeństwo przed kolorem z ustawień "
              "(Ustawienia → Lightbar → „Gra może zmienić kolor”)") if game and playing else
            _("{pad}: program na PC ustawił kolor lightbara {hex} - pominięty (zawsze kolor z ustawień)") if not game else
            _("{pad}: program na PC ustawił kolor lightbara {hex} - poza grą świeci kolor z ustawień, w grze ten"))
    ev.info(text.format(pad=ev.pad_name(i), hex=L.rgb_hex(rgb)))


def rumble_expired(f: FeedbackState, now: float) -> str | None:
    """Return the reason (for the log) when game rumble is on but the game stopped driving it, else None.

    The game sends only other reports (colour, triggers) - a menu; or no motor reports since STALE (a lost "motors 0",
    the game hung / minimised).
    """
    if not (f["large"] or f["small"]):
        return None
    if f["t_other"] - f["t"] > GAME_RUMBLE_GAP_S:
        return f"game reports without motors for {GAME_RUMBLE_GAP_S:g}s"
    if now - f["t"] > GAME_RUMBLE_STALE_S:
        return f"no rumble report from the game for {GAME_RUMBLE_STALE_S:g}s"
    return None


def _target(i: int, now: float) -> tuple[tuple[int, ...], bool, bool]:
    """Return (the feedback value to send, whether we drive this pad at all, whether the lightbar blinks)."""
    f = fb[i]
    cfg = rx_settings.cur.lightbar
    light = cfg.get(S.profile_key(i))
    motors = tuple(clamp(f[k], 0, BYTE_MAX) for k in ("small", "large")) if f["set"] else (0, 0)
    if f["set"] and f["rgb"] and light.game and in_game():   # the game takes precedence (the colour only when we know it), unless
                                                             # the pad has "always my colour" or this is not a game (Steam)
        rgb = tuple(clamp(f[k], 0, BYTE_MAX) for k in ("r", "g", "b"))
    else:
        rgb, _custom = L.own_color(light, S.batt_info[i][0], cfg.low_pct, C.IDLE_RGB)
    base = (*motors, *rgb)
    blink = L.blinking(light, is_low(i, cfg.low_pct))
    if blink:
        base = (*base[:2], *L.blink_rgb(base[2:], now))
    p = pulses[i]
    if p is not None and now >= p[0]:
        pulses[i] = p = None
    if p is None:
        # the default colour (idle_lightbar) is sent too: the sender and the pad keep the last colour, so sending nothing would
        # leave the old one (e.g. by battery) after switching to "default", a receiver restart or a reconnect
        return base, True, blink
    return (p[1], p[2], *(p[3] or base[2:])), True, blink


def _wait_time(now: float, blink: bool) -> float:
    """Return how long the loop may sleep: until the next pulse end, rumble expiry or blink edge (at most RUMBLE_REFRESH_S)."""
    waits = [RUMBLE_REFRESH_S] + [p[0] - now for p in pulses if p is not None]     # 0.5 s: also expiry
    # expire the rumble exactly on time (without this, STALE + up to 0.5 s of waiting)
    # only pads with a signal: the loop handles expiry only for S.alive - for a pad without a signal
    # (the virtual DS4 still plugged, game rumble non-zero) the deadline would be in the past and the loop would spin
    waits += [f["t"] + GAME_RUMBLE_STALE_S + 0.001 - now for i, f in enumerate(fb) if S.alive[i] and (f["large"] or f["small"])]
    if blink:
        waits.append(L.next_edge(now) + 0.002)
    return max(0.0, min(waits))


def _tick_pad(i: int, now: float, send: SendFeedback, send_trig: SendTriggers | None, send_leds: SendLeds | None = None) -> bool:
    """Handle one connected pad: trigger effects, LEDs, log notes, rumble expiry and the feedback itself; return whether it blinks."""
    if send_trig is not None:
        triggers_tick(i, now, send_trig)
    if send_leds is not None:
        leds_tick(i, now, send_leds)
    if fb[i]["note"]:
        _note_game_color(i)
    if fb[i]["trig_note"]:
        _note_game_triggers(i)
    f = fb[i]
    if why := rumble_expired(f, now):
        f["large"] = f["small"] = 0
        print(f"Controller {i}: {why} - rumble stopped")
    val, ours, blink = _target(i, now)
    if not ours:
        return blink
    last = fb_sent[i]
    if last is None or last[0] != val or now - last[1] >= (RUMBLE_REFRESH_S if val[0] or val[1] else IDLE_REFRESH_S):
        fb_sent[i] = (val, now)
        send(i, val)    # type: ignore[arg-type]  # val is always the 5 values (small, large, r, g, b)
    return blink


def feedback_thread(send: SendFeedback, send_trig: SendTriggers | None = None, send_leds: SendLeds | None = None) -> None:
    """Run the feedback loop: send(slot, (small, large, r, g, b)) goes to the sender (rx_net.send_feedback).

    send_trig(slot, trig3 values (15), trig2 (12) or trig (4), repeats) sends trigger effects (rx_net.send_triggers).
    send_leds(slot, (flags, mute LED, player LEDs), repeats) sends the LEDs the game set (rx_net.send_leds).
    Feedback goes out on change, every 0.5 s while rumbling (refreshes its timeout on the sender) and every 5 s at rest
    (a lost packet will not leave a wrong lightbar colour).
    """
    blink = False
    while not S.stop.is_set():
        fb_event.wait(_wait_time(time.monotonic(), blink))
        fb_event.clear()
        now = time.monotonic()
        blink = False
        for i in range(C.NUM_PADS):
            if not S.alive[i]:
                fb_sent[i] = pulses[i] = trig_sent[i] = leds_sent[i] = None
                continue
            blink = _tick_pad(i, now, send, send_trig, send_leds) or blink
