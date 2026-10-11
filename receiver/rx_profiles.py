"""Receiver: operacje na profilach remapowania wspólne dla okna (rx_qt) i edytora w przeglądarce (rx_web)."""
from rx_i18n import _
import re

import rx_buttons as B
import rx_net
import rx_state as S
from proto import CMD_POWER_OFF
from rx_config import C

EXE_RE = re.compile(r"[a-z0-9_.()+\-][a-z0-9_.()+\- ]{0,78}\.exe")


def slot_ok(slot):
    return isinstance(slot, int) and not isinstance(slot, bool) and 0 <= slot < C.NUM_PADS


def key_for(slot, key):
    """Klucz profilu z żądania, sprawdzony: profil pada albo "<pad>@<gra.exe>" tego samego pada; inaczej None."""
    base = S.profile_key(slot)
    if not key or key == base:
        return base
    if isinstance(key, str) and key.startswith(base + "@") and EXE_RE.fullmatch(key[len(base) + 1:]):
        return key
    return None


def game_key(slot, text):
    """Tekst wpisany przez użytkownika ("EldenRing") -> klucz "<pad>@eldenring.exe" albo None, gdy to nie nazwa programu."""
    exe = (text or "").strip().lower()
    if exe and not exe.endswith(".exe"):
        exe += ".exe"
    return f"{S.profile_key(slot)}@{exe}" if EXE_RE.fullmatch(exe) else None


def info(key):
    return {"key": key, "label": S.profile_label(key), "profile": S.store.get(key),
            "saved": S.store.get_saved(key), "in_file": S.store.in_file(key)}


def apply(slot, key, profile, save=False):
    """Profil na żywo (save=False: tylko do pamięci) albo zapisany do remap.json. -> (ok, komunikat)."""
    prof = B.normalize_profile(profile)
    if save:
        ok, msg = S.store.save(key, prof)
    else:
        S.store.set_live(key, prof)
        ok, msg = True, _("Zastosowano (niezapisane)")
    S.reload_profile(slot)
    return ok, msg


def delete(slot, key):
    """Usuwa profil gry. -> (ok, komunikat)."""
    if not B.is_game_key(key):
        return False, _("usuwać można tylko profile gier")
    with S.profile_lock:
        ok, msg = S.store.delete(key)
        if S.override[slot] == key:
            S.override[slot] = None
        S.reload_profile(slot)
    return ok, msg


def use(slot, key):
    """Profil wybrany ręcznie działa od razu na padzie (do następnej zmiany aktywnego okna). -> False, gdy go nie ma."""
    return S.use_profile(slot, key)


def power_off(slot):
    """Wyłącza pada (nadajnik rozłącza Bluetooth). -> False, gdy pad nie jest podłączony."""
    if not S.alive[slot]:
        return False
    rx_net.send_cmd(slot, CMD_POWER_OFF)
    return True


def live(i):
    """Stan pada na żywo: fizyczny i po remapowaniu (*_out = to, co widzi gra)."""
    s = S.states[i]
    raw, st = s["raw"], s["rawst"]
    return {"connected": bool(S.alive[i]), "active": S.active[i], "gyro_on": bool(s["gyro_on"]),
            "pressed": sorted(B.pressed_inputs(*raw)),
            "ls": [st[0], st[1]], "rs": [st[2], st[3]], "l2": raw[3], "r2": raw[4],
            "pressed_out": sorted(B.pressed_inputs(s["mask"], s["hx"], s["hy"], s["lt"], s["rt"])),
            "ls_out": [s["lx"], s["ly"]], "rs_out": [s["rx"], s["ry"]], "l2_out": s["lt"], "r2_out": s["rt"]}
