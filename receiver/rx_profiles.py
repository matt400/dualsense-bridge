"""Receiver: operations on remap profiles for the window (rx_qt, rx_editor)."""
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


def game_key(slot, text) -> str | None:
    """Text typed by the user ("EldenRing") -> the key "<pad>@eldenring.exe", or None when it is not a valid program name.

    The text is lower-cased and ".exe" is appended when missing; EXE_RE rejects anything that is not a plain file name.
    """
    exe = (text or "").strip().lower()
    if exe and not exe.endswith(".exe"):
        exe += ".exe"
    return f"{S.profile_key(slot)}@{exe}" if EXE_RE.fullmatch(exe) else None


def apply(slot, key, profile, save: bool = False):
    """Apply a profile: live (save=False: kept in memory only) or saved to remap.json -> (ok, message).

    The profile is normalised first; afterwards the slot reloads its profile so the change takes effect immediately.
    """
    prof = B.normalize_profile(profile)
    if save:
        ok, msg = S.store.save(key, prof)
    else:
        S.store.set_live(key, prof)
        ok, msg = True, _("Zastosowano (niezapisane)")
    S.reload_profile(slot)
    return ok, msg


def delete(slot, key):
    """Delete a game profile -> (ok, message). If the slot was using it as a manual override, the override is cleared."""
    if not B.is_game_key(key):
        return False, _("usuwać można tylko profile gier")
    with S.profile_lock:
        ok, msg = S.store.delete(key)
        if S.override[slot] == key:
            S.override[slot] = None
        S.reload_profile(slot)
    return ok, msg


def use(slot, key):
    """A manually chosen profile takes effect on the pad at once (until the next change of the active window). -> False when it does not
    exist.
    """
    return S.use_profile(slot, key)


def power_off(slot) -> bool:
    """Switch the pad off (the sender disconnects its Bluetooth link) -> False when the pad is not connected."""
    if not S.alive[slot]:
        return False
    rx_net.send_cmd(slot, CMD_POWER_OFF)
    return True


def live(i):
    """Live pad state: physical and after remapping (*_out = what the game sees). Used by the profile editor to highlight inputs."""
    s = S.states[i]
    raw, st = s["raw"], s["rawst"]
    return {"connected": bool(S.alive[i]), "active": S.active[i], "gyro_on": bool(s["gyro_on"]),
            "pressed": sorted(B.pressed_inputs(*raw)),
            "ls": [st[0], st[1]], "rs": [st[2], st[3]], "l2": raw[3], "r2": raw[4],
            "pressed_out": sorted(B.pressed_inputs(s["mask"], s["hx"], s["hy"], s["lt"], s["rt"])),
            "tilt": s["gs"][0] if s["gs"] else None,          # the input that the pad tilt drives right now (rx_gstick_io)
            "ls_out": [s["lx"], s["ly"]], "rs_out": [s["rx"], s["ry"]], "l2_out": s["lt"], "r2_out": s["rt"]}


def test_live(i):
    """Test view: live sensors and touch (raw, also while the output is blocked and in mouse mode)."""
    s = S.states[i]
    with S.lock:
        return {"acc": tuple(s["acc"]), "gyro": tuple(s["gyro"]), "touch": s["touch"], "tp": s["rtp"],
                "blocked": s["blocked"], "mouse": bool(S.mouse_mode[i])}


# ---------- Test view: output block, stick drift, gyro calibration ----------
def block(slot, on) -> bool:
    """Block (on) or unblock the slot's output: while blocked, games get a neutral state and the touchpad / gyro mouse and
    the pad shortcuts do nothing; off restores everything. Shortcuts and master start from a clean state - a PS button
    held from before the block will not fire a combination after it.
    """
    if not S.set_blocked(slot, on):
        return False
    hotkeys.reset(slot, keep=True)          # start from the inputs held right now - a new PS press after the test goes to the game
    master.reset(slot, keep=True)
    if on:
        mouse.reset(slot)               # a held touchpad click (mouse mode) would stay pressed
    print(f"Controller {slot}: output to games {'blocked (test view)' if on else 'restored'}")
    return True


def sender_calib(slot):
    """The sender of this pad knows CMD_GYRO_CAL and CMD_RAW_STICKS (v9+); learned from the capability bits of its version packet."""
    info = S.sender_info[slot]
    return info is not None and bool(info[1] & CAP_CALIB)


def raw_sticks(slot, secs) -> None:
    """Drift measurement: ask the sender for sticks without its deadzone for `secs` seconds (0 = end)."""
    rx_net.send_cmd(slot, CMD_RAW_STICKS, secs)


def calibrate_gyro(slot, secs) -> bool:
    """Ask the sender to calibrate the gyro of a pad lying still (the result arrives in S.gyro_cal) -> False when the pad / sender
    cannot.
    """
    if not (S.alive[slot] and sender_calib(slot)):
        return False
    S.gyro_cal[slot] = None
    rx_net.send_cmd(slot, CMD_GYRO_CAL, max(1, min(CAL_MAX_S, round(secs))))
    return True


def query_gyro_cal(slot) -> None:
    """Ask for the gyro calibration status (whether a bias is saved on the sender) - the answer arrives in S.gyro_cal."""
    if S.alive[slot] and sender_calib(slot):
        rx_net.send_cmd(slot, CMD_GYRO_CAL, 0)


def drift(slot):
    """Stick drift correction of the pad in this slot -> {"left": dz, "right": dz}."""
    return rx_padcal.drift(S.profile_key(slot))


def set_drift(slot, values):
    """Save the pad's drift correction ({stick: dz}, 0 = remove) and recompute the profile at once -> (ok, message)."""
    ok, msg = rx_padcal.set_drift(S.profile_key(slot), values)
    S.reload_profile(slot)
    return ok, msg
