"""Receiver: korekcja sprzętowa padów - strefa martwa przeciw dryfowi gałek, osobno dla każdego pada (MAC).

Dryf to cecha egzemplarza pada, a nie preferencja gracza, więc nie jest częścią profili: działa we WSZYSTKICH profilach
tego pada (także profilach gier, które zastępują profil pada) jako minimum strefy martwej:
    strefa martwa wyjścia = max(strefa z profilu, dryf gałki fizycznej, która je zasila)
Liczona dla gałki fizycznej, więc zamiana gałek w profilu jej nie psuje. Plik padcal.json obok remap.json:
    {"version": 1, "pads": {"aa:bb:cc:dd:ee:ff": {"drift": {"left": 0.07, "right": 0.04}}}}
"""
import json
import os
import threading

from rx_config import C
from rx_i18n import _
from rx_shape import STICK_DEF, STICK_LIMITS

STICKS = ("left", "right")
DZ_MAX = STICK_LIMITS["dz"][1]

_lock = threading.Lock()
_pads = None             # klucz pada -> {"left": dz, "right": dz} (tylko niezerowe)


def path():
    """Obok remap.json (remap_file da się zmienić w konfiguracji - padcal idzie za nim)."""
    return os.path.join(os.path.dirname(C.REMAP_PATH), "padcal.json")


def _clean(e):
    """Wpis pada z pliku -> {gałka: dz} w zakresie 0..DZ_MAX, bez zer i nieznanych kluczy."""
    out = {}
    for name in STICKS:
        try:
            v = round(min(DZ_MAX, max(0.0, float(e.get(name, 0)))), 3)
        except (TypeError, ValueError, AttributeError):
            continue
        if v:
            out[name] = v
    return out


def _load():
    """Wołać pod _lock."""
    global _pads
    if _pads is None:
        _pads = {}
        try:
            with open(path(), encoding="utf-8") as f:
                raw = json.load(f).get("pads", {})
            for k, v in raw.items():
                if d := _clean((v or {}).get("drift")):
                    _pads[str(k).lower()] = d
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as e:
            print(f"Pad calibration file {path()}: cannot read ({e}) - no drift correction")
    return _pads


def reload():
    """Następne użycie wczyta plik od nowa (testy, przywrócenie kopii ustawień)."""
    global _pads
    with _lock:
        _pads = None


def drift(key):
    """Korekcja dryfu pada -> {"left": dz, "right": dz} (brak wpisu = brak korekcji)."""
    with _lock:
        return dict(_load().get((key or "").lower(), {}))


def set_drift(key, values):
    """Ustawia korekcję dryfu pada ({gałka: dz}, 0 = bez korekcji) i zapisuje od razu. -> (ok, komunikat)."""
    key = (key or "").lower()
    with _lock:
        pads = _load()
        d = _clean({**pads.get(key, {}), **values})
        if d:
            pads[key] = d
        else:
            pads.pop(key, None)
        return _write(pads)


def _write(pads):
    """Zapis atomowy (plik tymczasowy + zamiana). Wołać pod _lock."""
    p = path()
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "pads": {k: {"drift": v} for k, v in pads.items()}}, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except OSError as e:
        return False, _("Zapis nieudany: {err}").format(err=e)
    return True, _("Zapisano - {file}").format(file=os.path.basename(p))


def apply_drift(prof, d):
    """Profil (znormalizowany, kopia) z podniesioną strefą martwą wyjść zasilanych przez gałki z dryfem."""
    if not d:
        return prof
    tun = prof["tuning"]
    for out in STICKS:
        e = prof["sticks"].get(out)
        dz = d.get(e["src"] if e else out)
        if not dz:
            continue
        cur = tun.get(out) or STICK_DEF
        if cur["dz"] >= dz:
            continue
        tun[out] = {**cur, "dz": dz, "outer": max(cur["outer"], round(dz + 0.05, 3))}
    return prof
