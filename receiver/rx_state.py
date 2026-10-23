"""Receiver: the shared pad state. Modules use it as `S.xxx` (the lists are created in init())."""
import threading
import time
import traceback
from typing import Any

import rx_padcal
from proto import SeqFilter
from rx_buttons import apply_remap, apply_sticks, pressed_inputs, profile_size
from rx_config import C
from rx_gstick import drive_state
from rx_shape import gyro_mouse_velocity, gyro_rates, gyro_stick, merge_triggers, shape_stick, shape_trigger

lock = threading.Lock()
profile_lock = threading.RLock()            # profile choice (game / editor / shortcuts / ident) from several threads
stop = threading.Event()
restart = False                            # True = after a clean shutdown start the receiver again (saving settings in the window);
# "admin" = the same as administrator (UAC - HIDMaestro)
seen = SeqFilter(1.0)                      # dropping late / duplicated packets
MACS = [bytes([0, 2, 3, 4, 5, 6 + s]) for s in range(5)]
SENDER_TIMEOUT = 3.0                       # silence longer than this (3 pings) = the sender is offline

peer = None        # address of the last sender (for feedback / pong)
sender_build = None  # (commit, commit time, update state UPD_*, receive time) from the sender's build packet
sender_update_msg = ""  # the reason for the last sender update failure (the updmsg packet)
sender_wol = None  # (the PC's MAC, broadcast) - what the sender's Wake-on-LAN will use (the build2 packet); "" = it does not know
last_pkt = 0.0     # when the last valid packet arrived - for the online indicator
store = None       # RemapStore
dsu_wake = threading.Event()
dsu_active = False              # the DSU server has subscribers - without them state changes do not wake it (rx_dsu)
gyro_wake = threading.Event()   # wakes the gyro mouse thread (rx_gyro)
game = None                     # the foreground program (lower case, e.g. "eldenring.exe") or None
link_loss = None                # packet loss from the sender in the last second [%] (None = no data)

# Per-pad state, one entry per slot. All of these are None until init() builds the lists (a few listeners that can fire
# earlier check `S.states is not None`); the annotations describe the lists as they are afterwards.
states: list[dict[str, Any]] = None      # the live state, see new_state()
wake: list[threading.Event] = None       # wakes the slot thread after a state change
want: list[threading.Event] = None       # set = the virtual pad should exist
plugged: list[bool] = None               # the virtual pad is plugged in
last_seen: list[float] = None            # when the pad last sent a packet (monotonic)
alive: list[bool] = None                 # the pad is sending
ignore_until: list[float] = None         # mute a slot until then (after a bye packet)
mouse_mode: list[bool] = None            # the touchpad drives the mouse
tp_down: list[int] = None                # the last touchpad click value (0 / 1 / 2)
swallow: list[bool] = None               # the next touchpad release belongs to a shortcut
last_v: list[tuple | None] = None        # the last state packet - repeats are skipped
last_touch: list[tuple | None] = None    # the last touch packet
batt_info: list[tuple[int, int]] = None  # (percent, flags) for the window
pad_id: list[str | None] = None          # the pad identifier from the ident packet
remap_maps: list[Any] = None             # the normalised button map in use
override: list[str | None] = None        # a profile chosen by hand, None = automatic
active: list[str] = None                 # the profile key in use
info: list[tuple | None] = None          # (ping ms | None, pad Hz, motion Hz, time)
pps: list[float] = None                  # state packets per second
sender_info: list[tuple | None] = None   # (sender version, rumble ways, reason); None = old sender
probe: list[list | None] = None          # drift measurement samples (lx, ly, rx, ry) or None
gyro_cal: list[tuple | None] = None      # the last calres packet


def new_state():
    return {
        "lx": 128, "ly": 128, "rx": 128, "ry": 128, "lt": 0, "rt": 0,   # sticks/lt/rt/hx/hy/mask: AFTER remapping
        "hx": 0, "hy": 0, "mask": 0,
        "raw": (0, 0, 0, 0, 0),                                          # (mask, hatx, haty, l2, r2) raw
        "fwd": (0, 0, 0),                    # (mask, hatx, haty) passed to the remap - without inputs consumed by shortcuts
        "rawst": (128, 128, 128, 128),                                   # (lx, ly, rx, ry) raw
        "acc": (0, 0, 0), "gyro": (0, 0, 0),
        "touch": ((0, 0, 0, 0), (0, 0, 0, 0)), "tp": False,
        "rtp": 0,                            # live touchpad click (0/1/2), also in mouse mode - the Test view
        "blocked": False,                    # Test view: the game gets a neutral state, the mouse and shortcuts do not work
        "batt_dsu": 5, "batt_ds4": 0x1B,
        "ver": 0,
        "ts": 0,                                                         # when (us) the last state change arrived
        "gyro_on": False,                    # gyro active (stick or mouse mode and the "hold" condition is met)
        "gyro_vel": None,                    # cursor velocity from the gyro [px/s] in mouse mode, None = off
        "gs": None,                          # stick driven by pad tilt (rx_gstick_io): (target, x, y, button mask) or None
        "lat": None,                         # latency measurement of the last change (rx_net -> rx_ds4 -> rx_latency)
        "blat": None,                        # the same, only for a button / D-pad / trigger change (button -> game)
    }


def init(store_) -> None:
    global states, wake, want, plugged, last_seen, alive, ignore_until, mouse_mode
    global tp_down, swallow, last_v, last_touch, batt_info, pad_id, remap_maps, store
    global override, active, info, pps, sender_info, probe, gyro_cal
    n = C.NUM_PADS
    store = store_
    states = [new_state() for _ in range(n)]
    wake = [threading.Event() for _ in range(n)]     # wakes the pad thread after a state change
    want = [threading.Event() for _ in range(n)]     # set = the virtual DS4 should exist
    plugged = [False] * n                            # the virtual DS4 is plugged into ViGEm
    last_seen = [0.0] * n
    alive = [False] * n
    ignore_until = [0.0] * n                         # a short "mute" of the slot after a bye packet
    mouse_mode = [i in C.MOUSE_PADS for i in range(n)]
    tp_down = [0] * n                                # the last touchpad click value (0/1/2)
    swallow = [False] * n
    last_v = [None] * n                              # the last state / touch packet - repeats are skipped
    last_touch = [None] * n
    batt_info = [(255, 0)] * n                       # (%, flags) - for the window
    pad_id = [None] * n                              # pad identifier from the ident packet
    active = [store.key_for(None, i) for i in range(n)]   # the profile key in use
    remap_maps = [store.get(k) for k in active]
    override = [None] * n                            # a profile chosen manually (PS + d-pad), None = automatic
    info = [None] * n                                # (ping ms | None, pad Hz, motion Hz, time) from the info packet
    pps = [0.0] * n                                  # state packets per second
    sender_info = [None] * n                         # (sender version, rumble ways, reason) from the version packet; None = old
    probe = [None] * n                               # drift measurement (rx_drift): a list of samples (lx, ly, rx, ry) or None
    gyro_cal = [None] * n                            # the last calres packet: (state, flags, saved, current, time, received)


STABLE_S = 60.0                            # a thread ran this long without an error -> the next error is counted from scratch


def run_forever(name, fn, *args) -> None:
    """A background thread loop whose unexpected exception does not disable it for good: the error is printed
    (the first one with a full trace) and the loop starts again after a moment. It ends when fn returns on S.stop.
    The error counter resets after STABLE_S of calm work - rare errors do not grow into a 5 s pause (network thread:
    5 s without pads).
    """
    errors = 0
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            fn(*args)
            return
        except Exception as e:
            if time.monotonic() - t0 >= STABLE_S:
                errors = 0
            errors += 1
            if errors == 1:
                print(f"{name} thread error - restarting:\n{traceback.format_exc()}")
            else:
                print(f"{name} thread error #{errors} - restarting: {e!r}")
            stop.wait(min(5.0, 0.2 * errors))


def start_thread(name, fn, *args):
    """Start a daemon thread running fn under run_forever and return it."""
    t = threading.Thread(target=run_forever, args=(name, fn, *args), name=name, daemon=True)
    t.start()
    return t


def sender_online(now=None):
    """Whether a packet from the sender arrived within SENDER_TIMEOUT."""
    return (now or time.monotonic()) - last_pkt < SENDER_TIMEOUT


push = None        # rx_ds4.push: a report to the virtual DS4 at once in the changing thread (without rx_ds4 - only a wake-up)
on_remap = None    # rx_feedback: a new slot profile -> send the trigger resistance at once (without waiting for the loop)


def notify(i) -> None:
    """A change of slot i state: a DS4 report at once (rx_ds4.push), the DSU server sends from its own thread."""
    if push is not None:
        push(i)
    else:
        wake[i].set()
    # without DSU clients: no ~1000 empty wake-ups/s of the DSU thread (it fought the network thread for the GIL)
    if dsu_active:
        dsu_wake.set()


def recompute(s, prof) -> None:
    """Recompute the output state from the raw inputs according to the profile (call under lock):
    button remap -> stick swap / inversion -> stick and trigger shaping -> gyro.
    """
    tun, g = prof["tuning"], prof["gyro"]
    lt, rt = s["raw"][3:5]
    fwd_mask, fwd_hx, fwd_hy = s["fwd"]
    s["mask"], s["hx"], s["hy"], lt, rt = apply_remap(prof["buttons"], fwd_mask, fwd_hx, fwd_hy, lt, rt)
    s["lt"], s["rt"] = shape_trigger(lt, tun.get("l2")), shape_trigger(rt, tun.get("r2"))
    lx, ly, rx, ry = apply_sticks(prof["sticks"], *s["rawst"])
    lx, ly = shape_stick(lx, ly, tun.get("left"))
    rx, ry = shape_stick(rx, ry, tun.get("right"))
    s["gyro_on"] = (bool(g) and g["mode"] != "off" and not s["blocked"]
                    and (g["hold"] == "always" or g["hold"] in pressed_inputs(*s["raw"])))
    s["gyro_vel"] = None
    if s["gyro_on"]:
        rates = gyro_rates(s["gyro"], g, C.GYRO_RES)
        if g["mode"] == "stick":
            rx, ry = gyro_stick(rx, ry, rates, g)
        else:
            s["gyro_vel"] = gyro_mouse_velocity(rates, g)
    s["lx"], s["ly"], s["rx"], s["ry"] = lx, ly, rx, ry
    if s["gs"] is not None:          # pad tilt takes over the chosen input (after the remap and shaping)
        drive_state(s, s["gs"])


def set_remap(i, prof) -> None:
    """Set the profile of slot i and immediately recompute the current state from the raw inputs."""
    with lock:
        remap_maps[i] = prof
        s = states[i]
        recompute(s, prof)
        s["ver"] += 1
    notify(i)
    if s["gyro_vel"]:
        gyro_wake.set()
    if on_remap is not None:
        on_remap()


def profile_key(i):
    """The profile key of a pad (without a game)."""
    return store.key_for(pad_id[i], i)


def game_key(base, exe) -> str:
    """The game profile key "<pad>@<game.exe>"."""
    return f"{base}@{exe}"


def profile_label(key):
    """The label of a profile key: the game executable for a game profile, "default" otherwise."""
    return key.split("@", 1)[1] if "@" in key else "default"


def active_key(i):
    """The profile to use: manually chosen > the profile of the foreground game > the pad profile."""
    base = profile_key(i)
    if override[i] and override[i] in store.keys_for(base):
        return override[i]
    if game and C.GAME_PROFILES and store.has(game_key(base, game)):
        return game_key(base, game)
    return base


def effective_profile(i, key, drift=True):
    """The profile to use on a pad: a game profile inherits from the pad profile the trigger effects it does not set itself
    (the other parts of a game profile replace the pad profile as a whole, as before); this pad's stick drift correction
    (rx_padcal) works in every profile as a minimum deadzone (drift=False - without it: the number of profile changes).
    """
    prof = store.get(key)
    base = profile_key(i)
    if key != base:
        prof["triggers"] = merge_triggers(store.get(base)["triggers"], prof["triggers"])
    return rx_padcal.apply_drift(prof, rx_padcal.drift(base)) if drift else prof


def profile_changes(i):
    """The number of changes of the profile in use (pad card, log) - without the drift correction, which is not part of the profile."""
    return profile_size(effective_profile(i, active[i], drift=False))


NEUTRAL_OUT = {**new_state(), "ver": None}


def output(i):
    """The state of a slot for the game (DS4 report, DSU) - call under lock. A blocked slot (Test view) = a neutral state: released
    buttons, sticks centred, no motion and touch; battery and sample time as they really are.
    """
    s = states[i]
    if not s["blocked"]:
        return s
    return {**NEUTRAL_OUT, "batt_dsu": s["batt_dsu"], "batt_ds4": s["batt_ds4"], "ts": s["ts"], "ver": s["ver"]}


def set_blocked(i, on) -> bool:
    """Test view: the slot output to the game neutral (on) or live again. -> True when it changed."""
    with lock:
        s = states[i]
        if s["blocked"] == on:
            return False
        s["blocked"] = on
        recompute(s, remap_maps[i])
        s["lat"] = s["blat"] = None
        s["ver"] += 1
    notify(i)
    if s["gyro_vel"]:
        gyro_wake.set()
    return True


def reload_profile(i):
    """Set the profile per active_key(). -> True when the profile key changed."""
    with profile_lock:
        key = active_key(i)
        changed, active[i] = key != active[i], key
        set_remap(i, effective_profile(i, key))
        return changed


def use_profile(i, key) -> bool:
    """Manual profile choice (editor, shortcut) until the next window change. -> False when the profile does not exist."""
    with profile_lock:
        if key not in store.keys_for(profile_key(i)):
            return False
        override[i] = key
        reload_profile(i)
        return True


def cycle_profile(i, step):
    """The next / previous pad profile (manually, until the game changes). -> the new key."""
    with profile_lock:
        keys = store.keys_for(profile_key(i))
        cur = active[i] if active[i] in keys else keys[0]
        use_profile(i, keys[(keys.index(cur) + step) % len(keys)])
        return active[i]


def set_game(exe):
    """The foreground program changed. -> the slots whose profile changed."""
    global game
    with profile_lock:
        if exe == game:
            return []
        game = exe
        changed = []
        for i in range(len(states)):
            override[i] = None
            if reload_profile(i):
                changed.append(i)
        return changed


def reset_state(i) -> None:
    """A neutral pad state (released buttons, centred sticks); the version counter and the block flag are kept."""
    with lock:
        ver, blocked = states[i]["ver"] + 1, states[i]["blocked"]
        states[i].update(new_state())
        states[i]["ver"], states[i]["blocked"] = ver, blocked
        states[i]["ts"] = time.monotonic_ns() // 1000
    notify(i)
