"""Receiver: feedback to the pad (rumble, lightbar) from the game through the virtual pad (ViGEm / HIDMaestro) + short
confirmations of shortcuts, and trigger effects from the active profile (a game cannot set them through DS4 - the sender does it
with an HID report): resistance, click, trigger vibration and game rumble on the triggers (computed on the sender from the same
rumble packets). The master feature "triggers" (Settings -> Master) = effects only on the master pad, other pads get plain triggers.

Lightbar: a colour from the game applies only when a game is in the foreground (rx_games.in_game: not the desktop, Steam,
a launcher) and the pad has "Game may change colour" (PadLight.game). Outside a game, and with the option off, the colour comes
from the pad settings (solid / by battery, rx_lightbar). Steam sets the virtual pad's colour on the desktop too - it used to cover
the own colour until the pad disconnected. The PC colour is remembered, so it returns at once when the game comes back. The first
such colour goes to the log - otherwise "my colour does not work" would leave no trace. Low battery blinks red
also over the game colour; confirmations (e.g. the master's golden flash) are always on top."""
import threading
import time

import rx_events as ev
import rx_lightbar as L
import rx_settings
import rx_state as S
from proto import CAP_TRIG_FX, CAP_TRIG_RAW, TRG3_L2, TRG3_R2, TRIG2_OFF, TRIG_RAW_LEN, clamp
from rx_config import C
from rx_i18n import _
from rx_shape import legacy_values, trigger_values

_games = None          # rx_games (it imports this module) - loaded lazily, on first use


def in_game():
    """Whether a game is in the foreground (rx_games.in_game, imported lazily to avoid a circular import)."""
    global _games
    if _games is None:
        import rx_games
        _games = rx_games
    return _games.in_game()


FX_SENDER = 8          # from this version the sender knows trig2 (with the CAP_TRIG_FX bit in the version packet)
RAW_SENDER = 11        # from this version the sender knows trig3 - trigger effects from the game (the CAP_TRIG_RAW bit)
NO_GAME_TRIG = (None, None)    # the game has set no trigger effects (L2, R2)
TRIG_OFF_MODES = (0x00, 0x05)  # first byte of a trigger effect: none / Off - the game released the trigger

TRIG_REFRESH_S = 2.0   # trigger effects are refreshed this often (a pad after reconnecting / a lost packet)
TRIG_REPEAT = 3        # a change of effects is sent this many times (UDP)
GAME_RUMBLE_STALE_S = 1.0   # game rumble with no new motor report for longer than this -> 0. Games send reports every frame while
                            # rumbling; the last "motors 0" (entering a menu, the end of a drive) can get lost
                            # (HIDMaestro overwrites old packets) and the pad vibrated forever. It used to be 3 s -
                            # in the Forza Horizon menu the pad kept twitching that long after leaving a drive
GAME_RUMBLE_GAP_S = 0.25    # the game still sends reports (colour, triggers) but no motors for longer than this since the last
                            # motor report -> it stopped driving the rumble (a menu) -> 0, without waiting for STALE

# confirmations on the pad: (time s, small rumble, large rumble, lightbar colour | None = colour unchanged)
PULSES = {
    "mouse": (0.18, 0, 150, (0, 90, 255)),        # touchpad -> mouse
    "ds4": (0.18, 0, 150, (255, 255, 255)),       # touchpad -> DS4 touch
    "profile": (0.25, 0, 190, (0, 220, 90)),      # profile change (game / manual)
    "default": (0.25, 0, 190, (255, 170, 0)),     # back to the default profile
    "up": (0.07, 160, 0, None),                   # sensitivity up
    "down": (0.07, 80, 0, None),                  # sensitivity down
    "action": (0.10, 130, 0, None),               # screenshot, Game Bar etc.
    "auto_profile": (0.30, 0, 0, (0, 220, 90)),   # a game profile switched on by itself (alt-tab) - colour only, no rumble
    "auto_default": (0.30, 0, 0, (255, 170, 0)),
    "master": (0.60, 0, 200, (255, 200, 0)),      # this pad became the master - a golden flash
    "hold": (0.06, 90, 0, None),                  # a master action: keep holding
    "denied": (0.25, 0, 0, (255, 0, 0)),          # only the master may - a red blink
    "test": (0.6, 200, 255, (255, 255, 255)),     # the "Test rumble" button in the window
    # tilt-to-stick zone crossings (rx_gstick_io, per-pad "feedback": haptic / led / both)
    "tilt_edge_h": (0.06, 0, 170, None),          # full tilt reached - a firm tick
    "tilt_edge_l": (0.25, 0, 0, (255, 120, 0)),   # ... an orange flash
    "tilt_edge_b": (0.25, 0, 170, (255, 120, 0)),
    "tilt_center_h": (0.04, 90, 0, None),         # back in the deadzone - a light tick
    "tilt_center_l": (0.15, 0, 0, (0, 200, 255)), # ... a cyan flash
    "tilt_center_b": (0.15, 90, 0, (0, 200, 255)),
}
fb = []
fb_event = threading.Event()
pulses = []       # the active confirmation per slot: (end, small, large, colour | None) or None
fb_sent = []      # (value, time) of the feedback sent last; None = send at the next opportunity
trig_sent = []    # (trigger effects, time) sent last per slot; None = nothing (the pad has default triggers)
cbs = []


def make_cb(i):
    """ViGEm callback (on the ViGEm thread) for slot i. The parameter names match vgamepad.dummy_callback - vgamepad compares the
    signature with the names and rejects any other (a TypeError = no rumble from games). led_number is either the lightbar colour
    DS4_LIGHTBAR_COLOR (registered through rx_ds4.register_feedback) or a meaningless number (registered through vgamepad, which
    declares this argument as a byte) - then the game colour is unknown and the lightbar keeps its own."""
    def cb(client, target, large_motor, small_motor, led_number, user_data):
        try:
            f = fb[i]
            f["large"], f["small"] = int(large_motor), int(small_motor)
            f["t"] = time.monotonic()
            if hasattr(led_number, "Red"):
                f["r"], f["g"], f["b"] = int(led_number.Red), int(led_number.Green), int(led_number.Blue)
                f["note"] = f["note"] or not f["rgb"]
                f["rgb"] = True
            f["set"] = True
        except Exception:
            pass
        fb_event.set()
    return cb


def game(i, large=None, small=None, rgb=None, trig=None):
    """Rumble / lightbar colour / trigger effects from the game for slot i (on the virtual pad backend thread); None = leave that
    part unchanged. A DualSense output report carries the motors, the colour and each trigger separately, with their own validity
    flags. trig = (L2, R2): the raw 11 B effect or None (that trigger unchanged)."""
    try:
        f = fb[i]
    except IndexError:
        return
    if trig is not None and any(t is not None for t in trig):
        new = tuple(bytes(t) if t is not None else old for t, old in zip(trig, f["trig"], strict=True))
        if new != f["trig"]:
            f["trig_note"] = f["trig_note"] or f["trig"] == NO_GAME_TRIG
            f["trig"], f["trig_owner"] = new, S.game     # the whole tuple is replaced - the feedback thread reads a consistent one
    if large is not None:
        f["large"], f["small"] = int(large), int(small or 0)
        f["t"] = time.monotonic()
    elif rgb is not None or trig is not None:
        f["t_other"] = time.monotonic()        # a game report without motors (GAME_RUMBLE_GAP_S)
    if rgb is not None:
        f["r"], f["g"], f["b"] = (int(c) for c in rgb)
        f["note"] = f["note"] or not f["rgb"]
        f["rgb"] = True
    f["set"] = True
    fb_event.set()


def forget_ended_games(running):
    """Trigger effects set by a program that is gone (a game closed without releasing the triggers - like the rumble in
    Forza Horizon) -> released. running(exe) -> whether the program is running. Effects of an unknown program (None) stay until changed
    by the game or until the pad disconnects. -> the slots whose triggers were released."""
    out = []
    for i, f in enumerate(fb):
        owner = f["trig_owner"]
        if owner and f["trig"] != NO_GAME_TRIG and owner != S.game and not running(owner):
            f["trig"], f["trig_owner"] = NO_GAME_TRIG, None
            print(f"Controller {i}: {owner} closed - its trigger effects released")
            out.append(i)
    if out:
        fb_event.set()
    return out


def stop_game_rumble(why):
    """The foreground program changed (left a game, alt-tab): game rumble -> 0. A game that ends mid-rumble (e.g. Forza
    Horizon on exit) no longer sends "motors 0", and the receiver kept refreshing the last value every 0.5 s - the pad vibrated
    forever. A game that returns to the foreground sends a fresh rumble itself."""
    for i, f in enumerate(fb):
        if f["large"] or f["small"]:
            f["large"] = f["small"] = 0
            print(f"Controller {i}: game rumble stopped ({why})")
            fb_event.set()


def init():
    """Create per-slot feedback state (motors, colour, trigger effects, timestamps) and callbacks; a remap change wakes the feedback
    thread."""
    n = C.NUM_PADS
    fb[:] = [{"small": 0, "large": 0, "r": 0, "g": 0, "b": 0, "set": False, "rgb": False, "note": False,
              "t": 0.0, "t_other": 0.0, "trig": NO_GAME_TRIG, "trig_owner": None, "trig_note": False}
             for _ in range(n)]
    fb_sent[:] = [None] * n
    trig_sent[:] = [None] * n
    pulses[:] = [None] * n
    cbs[:] = [make_cb(i) for i in range(n)]    # keep the references (GC)
    S.on_remap = fb_event.set


rx_settings.on_change(lambda _old, _new: fb_event.set())    # a new lightbar colour / mode takes effect at once


def reset(i):
    """A new / disconnected virtual DS4 in the slot: the game has set nothing yet (without init() - nothing to clear)."""
    if i < len(fb):
        fb[i].update(set=False, small=0, large=0, t_other=0.0, rgb=False, note=False, trig=NO_GAME_TRIG,
                     trig_owner=None, trig_note=False)
        fb_sent[i] = None


def pulse(i, kind, force=False):
    """A short confirmation on the pad (rumble and/or colour), laid over the game feedback.
    force: also when confirm = false (the rumble test from the window)."""
    if (C.CONFIRM or force) and 0 <= i < len(pulses) and S.alive[i]:
        d, small, large, rgb = PULSES[kind]
        pulses[i] = (time.monotonic() + d, small, large, rgb)
        fb_event.set()


def is_low(i, low_pct):
    """Battery known, not charging, and at or below the threshold (the same test as the pad card in the window)."""
    pct, flags = S.batt_info[i]
    return pct <= 100 and not flags & 1 and pct <= low_pct


def _note_game_triggers(i):
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


def _note_game_color(i):
    """The first lightbar colour from a game / PC program after the virtual pad was created - a log entry."""
    f = fb[i]
    f["note"] = False
    rgb = tuple(clamp(f[k], 0, 255) for k in ("r", "g", "b"))
    game = rx_settings.cur.lightbar.get(S.profile_key(i)).game
    playing = in_game()
    print(f"Controller {i}: lightbar colour {L.rgb_hex(rgb)} set by a program on the PC ({S.game})"
          + ("" if game and playing else " - ignored (own colour always)" if not game else " - used only in a game"))
    text = (_("{pad}: gra ustawiła kolor lightbara {hex} - ma pierwszeństwo przed kolorem z ustawień "
              "(Ustawienia → Lightbar → „Gra może zmienić kolor”)") if game and playing else
            _("{pad}: program na PC ustawił kolor lightbara {hex} - pominięty (zawsze kolor z ustawień)") if not game else
            _("{pad}: program na PC ustawił kolor lightbara {hex} - poza grą świeci kolor z ustawień, w grze ten"))
    ev.info(text.format(pad=ev.pad_name(i), hex=L.rgb_hex(rgb)))


def rumble_expired(f, now):
    """Game rumble is on but the game stopped driving it -> the reason (for the log), else None.
    The game sends only other reports (colour, triggers) - a menu; or no motor reports since STALE (a lost "motors 0",
    the game hung / minimised)."""
    if not (f["large"] or f["small"]):
        return None
    if f["t_other"] - f["t"] > GAME_RUMBLE_GAP_S:
        return f"game reports without motors for {GAME_RUMBLE_GAP_S:g}s"
    if now - f["t"] > GAME_RUMBLE_STALE_S:
        return f"no rumble report from the game for {GAME_RUMBLE_STALE_S:g}s"
    return None


def _target(i, now):
    """-> (the feedback value to send, whether we drive this pad at all, whether the lightbar blinks)."""
    f = fb[i]
    cfg = rx_settings.cur.lightbar
    light = cfg.get(S.profile_key(i))
    motors = tuple(clamp(f[k], 0, 255) for k in ("small", "large")) if f["set"] else (0, 0)
    if f["set"] and f["rgb"] and light.game and in_game():   # the game takes precedence (the colour only when we know it), unless
                                                             # the pad has "always my colour" or this is not a game (Steam)
        rgb = tuple(clamp(f[k], 0, 255) for k in ("r", "g", "b"))
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
        # the default colour (idle_lightbar) is sent too: the sender and the pad keep the last colour, so after switching
        # "by battery" -> "default", after a receiver restart or a reconnect the pad kept the old colour
        # (e.g. by battery), because "default" used to mean "send nothing"
        return base, True, blink
    return (p[1], p[2], *(p[3] or base[2:])), True, blink


def feedback_thread(send, send_trig=None):
    """send(slot, (small, large, r, g, b)) - sending to the sender (rx_net.send_feedback);
    send_trig(slot, trig3 values (15), trig2 (12) or trig (4), repeats) - trigger effects (rx_net.send_triggers)."""
    _loop(send, send_trig)


def sender_fx(i):
    """This pad's sender knows trig2 (click, trigger vibration, game rumble)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= FX_SENDER and bool(info[1] & CAP_TRIG_FX)


def sender_raw(i):
    """This pad's sender knows trig3 (trigger effects set by the game)."""
    info = S.sender_info[i]
    return info is not None and info[0] >= RAW_SENDER and bool(info[1] & CAP_TRIG_RAW)


def game_drives(game):
    """The game holds an effect (other than none / Off) on some trigger."""
    return any(t is not None and t[0] not in TRIG_OFF_MODES for t in game)


def trigger_target(i):
    """Values to send: trig3 (profile + the game's trigger effects) when the game set them and the sender knows them, else
    trig2 for a new sender, trig (resistance only) for an older one; no effects (game ones too) when trigger effects are master-only
    and this is not the master. On a trigger with a profile effect the profile wins (the sender decides - any mode other than
    TFX_OFF); when the game itself drives the triggers, game rumble on the profile's triggers is switched off."""
    import rx_master        # rx_master imports this module
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


def triggers_tick(i, now, send_trig):
    """Trigger effects of the slot's profile: a change -> at once (several times), constant effects -> refreshed every TRIG_REFRESH_S.
    The first pass after the pad connects always sends the state, "no effects" too: the sender and the pad may hold effects from
    a previous receiver run (a restart after an update, closing mid-game with a click on R2) - without this the trigger stayed hard
    when the profile in use after startup has no effects. Afterwards nothing is refreshed while there are no effects."""
    vals = trigger_target(i)
    last = trig_sent[i]
    if last is None or last[0] != vals:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, TRIG_REPEAT)
    elif any(vals) and now - last[1] >= TRIG_REFRESH_S:
        trig_sent[i] = (vals, now)
        send_trig(i, vals, 1)


def release_triggers(send_trig):
    """Receiver shutdown (the feedback thread is already stopped): pads we set trigger effects on get "no effects".
    The sender keeps an effect until the next change, so without this the resistance / click stayed after leaving the receiver
    (before a restart and an update too) until the pad disconnected. The sender also has its own guard (sender_rumble)
    that releases the triggers when the PC refreshes stop (PC sleep / crash)."""
    for i, last in enumerate(trig_sent):
        if last is not None and any(last[0]):
            # trig3 (game effects) also releases trig2 - a v11 sender then forgets the game effects
            send_trig(i, TRIG2_OFF if len(last[0]) >= len(TRIG2_OFF) else (0, 0, 0, 0), TRIG_REPEAT)
            trig_sent[i] = None


def _loop(send, send_trig=None):
    """Send feedback on change, every 0.5 s while rumbling (refreshes its timeout on the sender)
    and every 5 s at rest (a lost packet will not leave a wrong lightbar colour)."""
    blink = False
    while not S.stop.is_set():
        now = time.monotonic()
        waits = [0.5] + [e - now for e in (p[0] for p in pulses if p is not None)]   # 0.5 s: also expiry
        # expire the rumble exactly on time (without this, STALE + up to 0.5 s of waiting)
        # only pads with a signal: the loop below handles expiry only for S.alive - for a pad without a signal
        # (the virtual DS4 still plugged, game rumble non-zero) the deadline was in the past and the loop spun without sleeping
        waits += [f["t"] + GAME_RUMBLE_STALE_S + 0.001 - now for i, f in enumerate(fb) if S.alive[i] and (f["large"] or f["small"])]
        if blink:
            waits.append(L.next_edge(now) + 0.002)
        fb_event.wait(max(0.0, min(waits)))
        fb_event.clear()
        now = time.monotonic()
        blink = False
        for i in range(C.NUM_PADS):
            if not S.alive[i]:
                fb_sent[i] = pulses[i] = trig_sent[i] = None
                continue
            if send_trig is not None:
                triggers_tick(i, now, send_trig)
            if fb[i]["note"]:
                _note_game_color(i)
            if fb[i]["trig_note"]:
                _note_game_triggers(i)
            f = fb[i]
            if why := rumble_expired(f, now):
                f["large"] = f["small"] = 0
                print(f"Controller {i}: {why} - rumble stopped")
            val, ours, b = _target(i, now)
            blink = blink or b
            if not ours:
                continue
            last = fb_sent[i]
            if last is None or last[0] != val or now - last[1] >= (0.5 if val[0] or val[1] else 5.0):
                fb_sent[i] = (val, now)
                send(i, val)
