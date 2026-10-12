"""Receiver: kształtowanie gałek i spustów (strefa martwa, zasięg, krzywa, anti-deadzone) oraz gyro -> gałka / mysz.

Ustawienia są częścią profilu remapowania (brak klucza = domyślnie, bez zmian):
  tuning = {"left" | "right": {"dz", "outer", "curve", "anti"},   # ułamki wychylenia 0..1, krzywa = wykładnik
            "l2" | "r2": {"dz", "max"}}                           # wartości spustu 0..255
  gyro   = {"mode": "off" | "stick" | "mouse", "hold": "always" | <wejście>, "sens", "inv_x", "inv_y", "anti"}
  triggers = {"l2" | "r2": {"mode": "off" | "res" | "click" | "vib", parametry trybu},   # efekty spustów DualSense
              "rumble": {"on", "gain", "freq"}}                     # wibracja z gry na spustach (nadajnik, raport HID)
     res   = opór:      start 0-9 (strefa = 10 % skoku), force 1-8
     click = klik:      pos 2-7 (punkt 20-70 % skoku), force 1-8
     vib   = wibracja:  start 0-9, amp 1-8, freq 1-255 Hz
     rumble: on, gain 10-200 % (siła drgań względem silników), freq 1-255 Hz; L2 wg dużego silnika, R2 wg małego
  Brak klucza w profilu gry = jak w profilu pada (S.effective_triggers), "off" = wyłączone mimo profilu pada.
  Stary zapis oporu {"start", "force"} (bez "mode") jest wczytywany jako "res" (siła 0 = brak wpisu).
Kształtowanie działa na WYJŚCIU (to, co dostaje gra, po zamianie gałek), gyro dodaje się do prawej gałki.
"""
import math

STICK_DEF = {"dz": 0.0, "outer": 1.0, "curve": 1.0, "anti": 0.0}
STICK_LIMITS = {"dz": (0.0, 0.5), "outer": (0.5, 1.0), "curve": (0.3, 3.0), "anti": (0.0, 0.5)}
TRIG_DEF = {"dz": 0, "max": 255}
GYRO_DEF = {"mode": "off", "hold": "always", "sens": 1.0, "inv_x": False, "inv_y": False, "anti": 0.0}
GYRO_MODES = ("off", "stick", "mouse")
TRIG_MODES = ("off", "res", "click", "vib")
# parametry trybu: nazwa -> (min, max, domyślnie); zakresy jak w proto (TRIG_*, RFX_*)
TRIG_PARAMS = {"off": {},
               "res": {"start": (0, 9, 0), "force": (1, 8, 4)},
               "click": {"pos": (2, 7, 5), "force": (1, 8, 6)},
               "vib": {"start": (0, 9, 0), "amp": (1, 8, 4), "freq": (1, 255, 30)}}
TRIG_FX_DEF = {"mode": "off", **{k: d for p in TRIG_PARAMS.values() for k, (_lo, _hi, d) in p.items()}}
RUMBLE_LIMITS = {"gain": (10, 200, 100), "freq": (1, 255, 40)}
RUMBLE_DEF = {"on": False, **{k: d for k, (_lo, _hi, d) in RUMBLE_LIMITS.items()}}

GYRO_DPS_FULL = 150.0    # tryb gałki: pełne wychylenie przy tylu °/s (dzielone przez sens)
GYRO_PX_PER_DEG = 10.0   # tryb myszy: pikseli na stopień obrotu (mnożone przez sens)
GYRO_SOFT_DPS = 0.75     # poniżej tej prędkości ruch jest płynnie wygaszany (drżenie ręki, dryf)


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else min(hi, max(lo, v))


def normalize_tuning(t):
    """Czyści ustawienia: tylko znane klucze, wartości w zakresach; wpisy równe domyślnym znikają."""
    t = t if isinstance(t, dict) else {}
    out = {}
    for name in ("left", "right"):
        e = t.get(name)
        if not isinstance(e, dict):
            continue
        ne = {k: round(_num(e.get(k, d), *STICK_LIMITS[k], d), 3) for k, d in STICK_DEF.items()}
        ne["outer"] = max(ne["outer"], round(ne["dz"] + 0.05, 3))      # zasięg zawsze za strefą martwą
        if ne != STICK_DEF:
            out[name] = ne
    for name in ("l2", "r2"):
        e = t.get(name)
        if not isinstance(e, dict):
            continue
        dz = round(_num(e.get("dz", 0), 0, 200, 0))
        ne = {"dz": dz, "max": max(round(_num(e.get("max", 255), 55, 255, 255)), dz + 20)}
        if ne != TRIG_DEF:
            out[name] = ne
    return out


def trigger_entry(e):
    """Jeden spust -> {"mode", parametry trybu} w zakresach albo None (zły wpis / stary zapis z siłą 0)."""
    if not isinstance(e, dict):
        return None
    mode = e.get("mode")
    if mode is None:                                  # stary zapis: sam opór
        if not round(_num(e.get("force"), 0, 8, 0)):
            return None
        mode = "res"
    if mode not in TRIG_MODES:
        return None
    return {"mode": mode, **{k: round(_num(e.get(k), lo, hi, d)) for k, (lo, hi, d) in TRIG_PARAMS[mode].items()}}


def normalize_triggers(t):
    """Efekty spustów: L2 / R2 (trigger_entry) i wibracja z gry ("rumble"); nieznane klucze odrzucone."""
    t = t if isinstance(t, dict) else {}
    out = {name: e for name in ("l2", "r2") if (e := trigger_entry(t.get(name))) is not None}
    r = t.get("rumble")
    if isinstance(r, dict):
        out["rumble"] = {"on": r.get("on") is True,
                         **{k: round(_num(r.get(k), lo, hi, d)) for k, (lo, hi, d) in RUMBLE_LIMITS.items()}}
    return out


def merge_triggers(pad, game):
    """Efekty spustów w grze: wpisy profilu gry, a czego w nim nie ma - z profilu pada."""
    return {**pad, **game}


def trigger_values(t):
    """Znormalizowane efekty spustów -> wartości pakietu trig2 (L2: tryb, a, b, c; R2: ...; flagi, %, Hz, 0)."""
    from proto import RFX_ON, TFX_CLICK, TFX_OFF, TFX_RES, TFX_VIB

    def side(e):
        match (e or {}).get("mode"):
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


def legacy_values(vals):
    """Wartości trig2 -> stary pakiet trig dla nadajnika bez CAP_TRIG_FX (sam opór; klik / wibracja = brak)."""
    from proto import TFX_RES

    def side(mode, a, b, _c):
        return (a, b) if mode == TFX_RES else (0, 0)
    return (*side(*vals[0:4]), *side(*vals[4:8]))


def needs_fx(t):
    """Czy efekty wymagają nadajnika z CAP_TRIG_FX (klik, wibracja spustu, wibracja z gry)."""
    return any((t.get(n) or {}).get("mode") in ("click", "vib") for n in ("l2", "r2")) or \
        bool((t.get("rumble") or {}).get("on"))


def normalize_gyro(g, inputs):
    """-> {} (domyślne, wyłączone) albo pełny słownik ustawień; `inputs` = nazwy wejść dozwolone jako "hold"."""
    if not isinstance(g, dict):
        return {}
    ne = {
        "mode": g.get("mode") if g.get("mode") in GYRO_MODES else "off",
        "hold": g.get("hold") if g.get("hold") in inputs else "always",
        "sens": round(_num(g.get("sens"), 0.1, 5.0, 1.0), 2),
        "inv_x": bool(g.get("inv_x")),
        "inv_y": bool(g.get("inv_y")),
        "anti": round(_num(g.get("anti"), 0.0, 0.5, 0.0), 3),
    }
    return {} if ne == GYRO_DEF else ne


def _unit(v):
    """Bajt osi 0..255 (środek 128) -> -1..1."""
    return (v - 128) / (128 if v < 128 else 127)


def _byte(f):
    """-1..1 -> bajt osi 0..255 (odwrotność _unit)."""
    return max(0, min(255, round(128 + f * (128 if f < 0 else 127))))


def shape_stick(x, y, t):
    """Radialna strefa martwa z przeskalowaniem, zasięg (outer), krzywa odpowiedzi i anti-deadzone."""
    if not t:
        return x, y
    fx, fy = _unit(x), _unit(y)
    r = math.hypot(fx, fy)
    dz = t["dz"]
    if r <= dz or r == 0:
        return 128, 128
    m = min(1.0, (r - dz) / (t["outer"] - dz))
    if t["curve"] != 1.0:
        m **= t["curve"]
    if t["anti"]:
        m = t["anti"] + (1.0 - t["anti"]) * m
    k = m / r
    return _byte(fx * k), _byte(fy * k)


def shape_trigger(v, t):
    """Strefa martwa i punkt pełnego wciśnięcia spustu, z przeskalowaniem na pełne 0..255."""
    if not t or v == 0:
        return v
    if v <= t["dz"]:
        return 0
    if v >= t["max"]:
        return 255
    return round((v - t["dz"]) * 255 / (t["max"] - t["dz"]))


def gyro_rates(gyro, g, res):
    """Surowe gyro (pitch, yaw, roll) -> (x, y) w °/s w układzie ekranu (x w prawo, y w dół), z wygaszaniem drżenia."""
    x = -gyro[1] / res        # obrót wokół osi pionowej (yaw) -> poziomo
    y = -gyro[0] / res        # pochylenie (pitch) -> pionowo
    if g["inv_x"]:
        x = -x
    if g["inv_y"]:
        y = -y
    mag = math.hypot(x, y)
    if mag < GYRO_SOFT_DPS:
        k = mag / GYRO_SOFT_DPS
        x, y = x * k, y * k
    return x, y


def gyro_stick(rx, ry, rates, g):
    """Dodaje gyro do prawej gałki; anti-deadzone pokonuje strefę martwą gry przy małych ruchach."""
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


def gyro_mouse_velocity(rates, g):
    """Prędkość kursora w px/s dla trybu myszy."""
    k = GYRO_PX_PER_DEG * g["sens"]
    return rates[0] * k, rates[1] * k
