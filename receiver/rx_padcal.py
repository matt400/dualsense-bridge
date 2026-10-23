"""Receiver: hardware correction of pads - a deadzone against stick drift, separately for every pad (MAC).

Drift is a trait of a particular pad, not a player preference, so it is not part of the profiles: it applies in ALL
profiles of that pad (also game profiles that replace the pad profile) as a minimum deadzone:
    output deadzone = max(deadzone from the profile, drift of the physical stick that feeds it)
It is computed for the physical stick, so swapping the sticks in a profile does not break it. The padcal.json file lives
next to remap.json:
    {"version": 1, "pads": {"aa:bb:cc:dd:ee:ff": {"drift": {"left": 0.07, "right": 0.04}}}}
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Final

from proto import write_json
from rx_config import C
from rx_i18n import _
from rx_shape import STICK_DEF, STICK_LIMITS, Profile

STICKS: Final = ("left", "right")
DZ_MAX: Final = STICK_LIMITS["dz"][1]
FILE_VERSION: Final = 1
OUTER_GAP: Final = 0.05      # the stick range ends at least this far beyond the raised deadzone
DZ_DECIMALS: Final = 3

_lock = threading.Lock()
_pads: dict[str, dict[str, float]] | None = None             # pad key -> {"left": dz, "right": dz} (non-zero values only)


def path() -> str:
    """Return the file path: next to remap.json (remap_file can be changed in the configuration - padcal follows it)."""
    return os.path.join(os.path.dirname(C.REMAP_PATH), "padcal.json")


def _clean(e: Any) -> dict[str, float]:
    """Convert a pad entry from the file to {stick: dz} clamped to 0..DZ_MAX, without zeros and unknown keys."""
    out: dict[str, float] = {}
    for name in STICKS:
        try:
            v = round(min(DZ_MAX, max(0.0, float(e.get(name, 0)))), DZ_DECIMALS)
        except (TypeError, ValueError, AttributeError):
            continue
        if v:
            out[name] = v
    return out


def _load() -> dict[str, dict[str, float]]:
    """Lazily load the file into _pads (a missing file = no corrections, an unreadable one is reported and ignored).

    Call with _lock held.
    """
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


def reload() -> None:
    """Make the next use re-read the file (tests, restoring a settings backup)."""
    global _pads
    with _lock:
        _pads = None


def drift(key: str | None) -> dict[str, float]:
    """Return the drift correction of a pad as {"left": dz, "right": dz} (no entry = no correction)."""
    with _lock:
        return dict(_load().get((key or "").lower(), {}))


def set_drift(key: str | None, values: dict[str, float]) -> tuple[bool, str]:
    """Set the pad's drift correction ({stick: dz}, 0 = no correction), merged with the existing one, and save at once.

    Returns:
        ``(ok, message)``.
    """
    key = (key or "").lower()
    with _lock:
        pads = _load()
        d = _clean({**pads.get(key, {}), **values})
        if d:
            pads[key] = d
        else:
            pads.pop(key, None)
        return _write(pads)


def _write(pads: dict[str, dict[str, float]]) -> tuple[bool, str]:
    """Write atomically (temporary file + replace) through proto.write_json; call with _lock held."""
    p = path()
    try:
        write_json(p, {"version": FILE_VERSION, "pads": {k: {"drift": v} for k, v in pads.items()}}, indent=2, sort_keys=True)
    except OSError as e:
        return False, _("Zapis nieudany: {err}").format(err=e)
    return True, _("Zapisano - {file}").format(file=os.path.basename(p))


def apply_drift(prof: Profile, d: dict[str, float]) -> Profile:
    """Return the profile (normalised, a copy) with the deadzone raised on every output fed by a stick that drifts.

    The source stick of each output is looked up in the profile's stick mapping, so a swap is handled. The deadzone is
    only ever raised, never lowered, and the outer limit is pushed up so that the usable range does not collapse.
    """
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
        tun[out] = {**cur, "dz": dz, "outer": max(cur["outer"], round(dz + OUTER_GAP, DZ_DECIMALS))}
    return prof
