"""Receiver: shaping of sticks and triggers (deadzone, range, curve, anti-deadzone) and gyro -> stick / mouse.

The settings are part of the remap profile (a missing key = the default, no change):
  tuning = {"left" | "right": {"dz", "outer", "curve", "anti"},   # fractions of the deflection 0..1, curve = exponent
            "l2" | "r2": {"dz", "max"}}                           # trigger values 0..255
  gyro   = {"mode": "off" | "stick" | "mouse", "hold": "always" | <input>, "sens", "inv_x", "inv_y", "anti"}
  triggers = {"l2" | "r2": {"mode": "off" | "res" | "click" | "vib", mode parameters},   # DualSense trigger effects
              "rumble": {"on", "gain", "freq"}}                     # game rumble on the triggers (sender, HID report)
     res   = resistance: start 0-9 (a zone = 10 % of the travel), force 1-8
     click = click:      pos 2-7 (a point at 20-70 % of the travel), force 1-8
     vib   = vibration:  start 0-9, amp 1-8, freq 1-255 Hz
     rumble: on, gain 10-200 % (vibration strength relative to the motors), freq 1-255 Hz; L2 follows the large motor, R2 the small one
  A missing key in a game profile = as in the pad profile (S.effective_triggers), "off" = disabled despite the pad profile.
  The old resistance format {"start", "force"} (without "mode") is loaded as "res" (force 0 = no entry).
Shaping works on the OUTPUT (what the game receives, after the sticks are swapped); gyro is added to the right stick.
"""
from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from typing import Any, Final, TypeAlias

Profile: TypeAlias = dict[str, Any]       # one remap profile (see rx_buttons): buttons, sticks, tuning, gyro, triggers...
Tuning: TypeAlias = dict[str, dict[str, float]]
Triggers: TypeAlias = dict[str, dict[str, Any]]
Trig2: TypeAlias = tuple[int, ...]         # the 12 values of a trig2 packet

STICK_DEF = {"dz": 0.0, "outer": 1.0, "curve": 1.0, "anti": 0.0}
STICK_LIMITS = {"dz": (0.0, 0.5), "outer": (0.5, 1.0), "curve": (0.3, 3.0), "anti": (0.0, 0.5)}
TRIG_DEF = {"dz": 0, "max": 255}
GYRO_DEF = {"mode": "off", "hold": "always", "sens": 1.0, "inv_x": False, "inv_y": False, "anti": 0.0}
GYRO_MODES = ("off", "stick", "mouse")
TRIG_MODES = ("off", "res", "click", "vib")
# mode parameters: name -> (min, max, default); ranges as in proto (TRIG_*, RFX_*)
TRIG_PARAMS = {"off": {},
               "res": {"start": (0, 9, 0), "force": (1, 8, 4)},
               "click": {"pos": (2, 7, 5), "force": (1, 8, 6)},
               "vib": {"start": (0, 9, 0), "amp": (1, 8, 4), "freq": (1, 255, 30)}}
TRIG_FX_DEF = {"mode": "off", **{k: d for p in TRIG_PARAMS.values() for k, (_lo, _hi, d) in p.items()}}
RUMBLE_LIMITS = {"gain": (10, 200, 100), "freq": (1, 255, 40)}
RUMBLE_DEF = {"on": False, **{k: d for k, (_lo, _hi, d) in RUMBLE_LIMITS.items()}}

GYRO_DPS_FULL = 150.0    # stick mode: full deflection at this many deg/s (divided by sens)
GYRO_PX_PER_DEG = 10.0   # mouse mode: pixels per degree of rotation (multiplied by sens)
GYRO_SOFT_DPS = 0.75     # below this speed the movement is smoothly faded out (hand tremor, drift)
AXIS_CENTRE: Final = 128             # axis byte of a centred stick
AXIS_NEG_SPAN: Final = 128           # bytes below the centre (0..127 -> -1..0)
AXIS_POS_SPAN: Final = 127           # bytes above the centre (129..255 -> 0..1)
BYTE_MAX: Final = 255
MIN_OUTER_GAP: Final = 0.05          # the usable range of a stick always starts this far beyond the deadzone
TRIG_DZ_MAX: Final = 200             # the largest trigger deadzone
TRIG_MAX_MIN: Final = 55             # the lowest "fully pressed" point of a trigger
TRIG_MIN_SPAN: Final = 20            # the deadzone and the full-press point stay at least this far apart
GYRO_SENS_RANGE: Final = (0.1, 5.0)
GYRO_ANTI_MAX: Final = 0.5


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    """Convert to float and clamp to lo..hi; the default for anything that is not a number (or NaN)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else min(hi, max(lo, v))


def normalize_tuning(t: Any) -> dict[str, dict[str, Any]]:
    """Clean the settings: known keys only, values within their ranges; entries equal to the defaults disappear."""
    t = t if isinstance(t, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for name in ("left", "right"):
        e = t.get(name)
        if not isinstance(e, dict):
            continue
        ne = {k: round(_num(e.get(k, d), *STICK_LIMITS[k], d), 3) for k, d in STICK_DEF.items()}
        ne["outer"] = max(ne["outer"], round(ne["dz"] + MIN_OUTER_GAP, 3))      # the range always lies beyond the deadzone
        if ne != STICK_DEF:
            out[name] = ne
    for name in ("l2", "r2"):
        e = t.get(name)
        if not isinstance(e, dict):
            continue
        dz = round(_num(e.get("dz", 0), 0, TRIG_DZ_MAX, 0))
        ne = {"dz": dz, "max": max(round(_num(e.get("max", BYTE_MAX), TRIG_MAX_MIN, BYTE_MAX, BYTE_MAX)), dz + TRIG_MIN_SPAN)}
        if ne != TRIG_DEF:
            out[name] = ne
    return out


def trigger_entry(e: Any) -> dict[str, Any] | None:
    """Normalise one trigger to {"mode", mode parameters} within ranges, or None (a bad entry / an old format with force 0)."""
    if not isinstance(e, dict):
        return None
    mode = e.get("mode")
    if mode is None:                                  # old format: resistance only
        if not round(_num(e.get("force"), 0, 8, 0)):
            return None
        mode = "res"
    if mode not in TRIG_MODES:
        return None
    return {"mode": mode, **{k: round(_num(e.get(k), lo, hi, d)) for k, (lo, hi, d) in TRIG_PARAMS[mode].items()}}


def normalize_triggers(t: Any) -> Triggers:
    """Trigger effects: L2 / R2 (trigger_entry) and game rumble ("rumble"); unknown keys are dropped."""
    t = t if isinstance(t, dict) else {}
    out: Triggers = {name: e for name in ("l2", "r2") if (e := trigger_entry(t.get(name))) is not None}
    r = t.get("rumble")
    if isinstance(r, dict):
        out["rumble"] = {"on": r.get("on") is True,
                         **{k: round(_num(r.get(k), lo, hi, d)) for k, (lo, hi, d) in RUMBLE_LIMITS.items()}}
    return out


def merge_triggers(pad: Triggers, game: Triggers) -> Triggers:
    """Trigger effects in a game: the game profile entries, and whatever it lacks - from the pad profile."""
    return {**pad, **game}


def trigger_values(t: Triggers) -> Trig2:
    """Convert normalised trigger effects to trig2 packet values (L2: mode, a, b, c; R2: ...; flags, %, Hz, 0)."""
    from proto import RFX_ON, TFX_CLICK, TFX_OFF, TFX_RES, TFX_VIB

    def side(entry: dict[str, Any] | None) -> tuple[int, int, int, int]:
        e = entry or {}
        match e.get("mode"):
            case "res":
                return TFX_RES, e["start"], e["force"], 0
            case "click":
                return TFX_CLICK, e["pos"], e["force"], 0
            case "vib":
                return TFX_VIB, e["start"], e["amp"], e["freq"]
        return TFX_OFF, 0, 0, 0

    r = t.get("rumble") or RUMBLE_DEF
    rumble = (RFX_ON, r["gain"], r["freq"], 0) if r["on"] else (0, 0, 0, 0)
    return (*side(t.get("l2")), *side(t.get("r2")), *rumble)


def legacy_values(vals: Sequence[int]) -> tuple[int, int, int, int]:
    """Convert trig2 values to the old trig packet for a sender without CAP_TRIG_FX (resistance only; click / vibration = none)."""
    from proto import TFX_RES

    def side(mode: int, a: int, b: int, _c: int) -> tuple[int, int]:
        return (a, b) if mode == TFX_RES else (0, 0)
    return (*side(*vals[0:4]), *side(*vals[4:8]))


def needs_fx(t: Triggers) -> bool:
    """Tell whether the effects need a sender with CAP_TRIG_FX (click, trigger vibration, game vibration)."""
    return any((t.get(n) or {}).get("mode") in ("click", "vib") for n in ("l2", "r2")) or \
        bool((t.get("rumble") or {}).get("on"))


def normalize_gyro(g: Any, inputs: Collection[str]) -> dict[str, Any]:
    """Return {} (default, disabled) or a full settings dict; `inputs` = input names allowed as "hold"."""
    if not isinstance(g, dict):
        return {}
    ne = {
        "mode": g.get("mode") if g.get("mode") in GYRO_MODES else "off",
        "hold": g.get("hold") if g.get("hold") in inputs else "always",
        "sens": round(_num(g.get("sens"), *GYRO_SENS_RANGE, 1.0), 2),
        "inv_x": bool(g.get("inv_x")),
        "inv_y": bool(g.get("inv_y")),
        "anti": round(_num(g.get("anti"), 0.0, GYRO_ANTI_MAX, 0.0), 3),
    }
    return {} if ne == GYRO_DEF else ne


def _unit(v: float) -> float:
    """Convert an axis byte 0..255 (centre 128) to -1..1."""
    return (v - AXIS_CENTRE) / (AXIS_NEG_SPAN if v < AXIS_CENTRE else AXIS_POS_SPAN)


def _byte(f: float) -> int:
    """Convert -1..1 to an axis byte 0..255 (the inverse of _unit)."""
    return max(0, min(BYTE_MAX, round(AXIS_CENTRE + f * (AXIS_NEG_SPAN if f < 0 else AXIS_POS_SPAN))))


def shape_stick(x: int, y: int, t: dict[str, float] | None) -> tuple[int, int]:
    """Radial deadzone with rescaling, the range (outer), the response curve and the anti-deadzone."""
    if not t:
        return x, y
    fx, fy = _unit(x), _unit(y)
    r = math.hypot(fx, fy)
    dz = t["dz"]
    if r <= dz or r == 0:
        return AXIS_CENTRE, AXIS_CENTRE
    m = min(1.0, (r - dz) / (t["outer"] - dz))
    if t["curve"] != 1.0:
        m **= t["curve"]
    if t["anti"]:
        m = t["anti"] + (1.0 - t["anti"]) * m
    k = m / r
    return _byte(fx * k), _byte(fy * k)


def shape_trigger(v: int, t: dict[str, float] | None) -> int:
    """Deadzone and the full-press point of a trigger, rescaled to the full 0..255."""
    if not t or v == 0:
        return v
    if v <= t["dz"]:
        return 0
    if v >= t["max"]:
        return BYTE_MAX
    return round((v - t["dz"]) * BYTE_MAX / (t["max"] - t["dz"]))


def gyro_rates(gyro: Sequence[float], g: dict[str, Any], res: float) -> tuple[float, float]:
    """Convert the raw gyro (pitch, yaw, roll) to (x, y) in deg/s in screen space (x right, y down), with tremor fading."""
    x = -gyro[1] / res        # rotation around the vertical axis (yaw) -> horizontal
    y = -gyro[0] / res        # pitch -> vertical
    if g["inv_x"]:
        x = -x
    if g["inv_y"]:
        y = -y
    mag = math.hypot(x, y)
    if mag < GYRO_SOFT_DPS:
        k = mag / GYRO_SOFT_DPS
        x, y = x * k, y * k
    return x, y


def gyro_stick(rx: int, ry: int, rates: Sequence[float], g: dict[str, Any]) -> tuple[int, int]:
    """Add gyro to the right stick; the anti-deadzone overcomes the game's deadzone on small movements."""
    full = GYRO_DPS_FULL / g["sens"]
    gx, gy = rates[0] / full, rates[1] / full
    m = math.hypot(gx, gy)
    if m == 0:
        return rx, ry
    if g["anti"]:
        k = (g["anti"] + (1.0 - g["anti"]) * min(1.0, m)) / m
        gx, gy = gx * k, gy * k
    sx, sy = _unit(rx) + gx, _unit(ry) + gy
    r = math.hypot(sx, sy)
    if r > 1.0:
        sx, sy = sx / r, sy / r
    return _byte(sx), _byte(sy)


def gyro_mouse_velocity(rates: Sequence[float], g: dict[str, Any]) -> tuple[float, float]:
    """Cursor speed in px/s for the mouse mode."""
    k = GYRO_PX_PER_DEG * g["sens"]
    return rates[0] * k, rates[1] * k
