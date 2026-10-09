"""Receiver: kształtowanie gałek i spustów (strefa martwa, zasięg, krzywa, anti-deadzone) oraz gyro -> gałka / mysz.

Ustawienia są częścią profilu remapowania (brak klucza = domyślnie, bez zmian):
  tuning = {"left" | "right": {"dz", "outer", "curve", "anti"},   # ułamki wychylenia 0..1, krzywa = wykładnik
            "l2" | "r2": {"dz", "max"}}                           # wartości spustu 0..255
  gyro   = {"mode": "off" | "stick" | "mouse", "hold": "always" | <wejście>, "sens", "inv_x", "inv_y", "anti"}
Kształtowanie działa na WYJŚCIU (to, co dostaje gra, po zamianie gałek), gyro dodaje się do prawej gałki.
"""
import math

STICK_DEF = {"dz": 0.0, "outer": 1.0, "curve": 1.0, "anti": 0.0}
STICK_LIMITS = {"dz": (0.0, 0.5), "outer": (0.5, 1.0), "curve": (0.3, 3.0), "anti": (0.0, 0.5)}
TRIG_DEF = {"dz": 0, "max": 255}
GYRO_DEF = {"mode": "off", "hold": "always", "sens": 1.0, "inv_x": False, "inv_y": False, "anti": 0.0}
GYRO_MODES = ("off", "stick", "mouse")

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
