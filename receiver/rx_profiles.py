"""Receiver: operacje na profilach remapowania dla okna (rx_qt, rx_editor)."""
from rx_i18n import _
import re

import rx_buttons as B
import rx_hotkeys as hotkeys
import rx_master as master
import rx_mouse as mouse
import rx_net
import rx_padcal
import rx_state as S
from proto import CAL_MAX_S, CAP_CALIB, CMD_GYRO_CAL, CMD_POWER_OFF, CMD_RAW_STICKS

EXE_RE = re.compile(r"[a-z0-9_.()+\-][a-z0-9_.()+\- ]{0,78}\.exe")


def game_key(slot, text):
    """Tekst wpisany przez użytkownika ("EldenRing") -> klucz "<pad>@eldenring.exe" albo None, gdy to nie nazwa programu."""
    exe = (text or "").strip().lower()
    if exe and not exe.endswith(".exe"):
        exe += ".exe"
    return f"{S.profile_key(slot)}@{exe}" if EXE_RE.fullmatch(exe) else None


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


def test_live(i):
    """Widok Test: czujniki i dotyk na żywo (surowe, także przy blokadzie i w trybie myszy)."""
    s = S.states[i]
    with S.lock:
        return {"acc": tuple(s["acc"]), "gyro": tuple(s["gyro"]), "touch": s["touch"], "tp": s["rtp"],
                "blocked": s["blocked"], "mouse": bool(S.mouse_mode[i])}


# ---------- widok Test: blokada wyjścia, dryf gałek, kalibracja żyroskopu ----------
def block(slot, on):
    """Gra dostaje neutralny stan slotu (on), mysz touchpada / gyro i skróty pada nie działają; off = wszystko wraca.
    Skróty i master zaczynają od czystego stanu - przytrzymany PS sprzed blokady nie odpali kombinacji po niej."""
    if not S.set_blocked(slot, on):
        return False
    hotkeys.reset(slot)
    master.reset(slot)
    if on:
        mouse.reset(slot)               # trzymany klik touchpada (tryb myszy) zostałby wciśnięty
    print(f"Controller {slot}: output to games {'blocked (test view)' if on else 'restored'}")
    return True


def sender_calib(slot):
    """Nadajnik tego pada zna CMD_GYRO_CAL i CMD_RAW_STICKS (v9+)."""
    info = S.sender_info[slot]
    return info is not None and bool(info[1] & CAP_CALIB)


def raw_sticks(slot, secs):
    """Pomiar dryfu: gałki bez strefy martwej nadajnika przez secs sekund (0 = koniec)."""
    rx_net.send_cmd(slot, CMD_RAW_STICKS, secs)


def calibrate_gyro(slot, secs):
    """Kalibracja żyroskopu leżącego pada przez nadajnik (wynik: S.gyro_cal). -> False, gdy pad / nadajnik nie może."""
    if not (S.alive[slot] and sender_calib(slot)):
        return False
    S.gyro_cal[slot] = None
    rx_net.send_cmd(slot, CMD_GYRO_CAL, max(1, min(CAL_MAX_S, round(secs))))
    return True


def query_gyro_cal(slot):
    """Stan kalibracji żyroskopu (zapisana na nadajniku czy nie) - odpowiedź w S.gyro_cal."""
    if S.alive[slot] and sender_calib(slot):
        rx_net.send_cmd(slot, CMD_GYRO_CAL, 0)


def drift(slot):
    """Korekcja dryfu gałek pada w slocie -> {"left": dz, "right": dz}."""
    return rx_padcal.drift(S.profile_key(slot))


def set_drift(slot, values):
    """Zapisuje korekcję dryfu pada ({gałka: dz}, 0 = usuń) i od razu przelicza profil. -> (ok, komunikat)."""
    ok, msg = rx_padcal.set_drift(S.profile_key(slot), values)
    S.reload_profile(slot)
    return ok, msg
