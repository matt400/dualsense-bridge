"""Sender: polecenia z PC (wyłączenie pada, kalibracja gyro, gałki bez strefy martwej), wyłączanie po bezczynności
i pakiety info (ping, Hz) dla okna receivera."""
import asyncio
import re
import time
from subprocess import DEVNULL, PIPE

import sender_bt
import sender_calib as calib
import sender_net as net
import sender_state as state
import sender_update
import sender_events as events
from proto import CAL_STATUS, CMD_GYRO_CAL, CMD_POWER_OFF, CMD_RAW_STICKS, CMD_UPDATE, E_POWER_OFF, T_INFO
from sender_config import C

MAC_RE = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", re.IGNORECASE)
RETRY_S = 10.0          # ponowne wyłączenie tego samego pada najwcześniej po tylu sekundach


def per_s(n, dt):
    return max(0, min(0xFFFF, round(n / dt))) if dt > 0 else 0


async def power_off(c, reason):
    """Rozłącza pada po Bluetooth - DualSense wtedy sam się wyłącza. Po USB nie ma czego rozłączać."""
    now = time.monotonic()
    if now - c.powering_off < RETRY_S:
        return
    c.powering_off = now
    if not MAC_RE.fullmatch(c.key or ""):
        print(f"Controller {c.idx}: power off skipped - not a Bluetooth pad ({c.key})")
        return
    print(f"Controller {c.idx}: powering off ({reason})")
    events.info(E_POWER_OFF, c.idx, reason)
    if await sender_bt.bt.disconnect_pad(c.key):     # kontroler, z którym pad jest połączony (także dongle hci1)
        return
    try:
        proc = await asyncio.create_subprocess_exec("bluetoothctl", "disconnect", c.key, stdout=DEVNULL, stderr=PIPE)
    except FileNotFoundError:
        print("Power off failed: bluetoothctl not found (install bluez)")
        return
    try:
        _, err = await asyncio.wait_for(proc.communicate(), 10)
    except TimeoutError:
        proc.kill()
        await proc.wait()              # bez tego zostaje proces-zombie
        print(f"Controller {c.idx}: power off timed out")
        return
    if proc.returncode:
        print(f"Controller {c.idx}: power off failed: {err.decode(errors='replace').strip() or proc.returncode}")


def on_command(idx, cmd, arg):
    if cmd == CMD_UPDATE:                  # dotyczy całego nadajnika, nie pada (idx = 255)
        state.spawn(sender_update.run())
        return
    c = state.ctrl_by_idx(idx)
    if c is None:
        return
    if cmd == CMD_POWER_OFF:
        state.spawn(power_off(c, "requested from PC"))
    elif cmd == CMD_GYRO_CAL:            # kalibracja żyroskopu z okna (arg sekund) albo sam stan (arg 0)
        if arg:
            calib.request(c, arg)
        else:
            calib.report(c, CAL_STATUS)
    elif cmd == CMD_RAW_STICKS:          # pomiar dryfu: gałki bez strefy martwej nadajnika przez arg sekund
        c.set_raw_sticks(arg)


async def info_loop():
    """Co sekundę: pakiet info per pad (ping, realne Hz raportów) + wyłączanie po bezczynności."""
    prev = {}        # Ctrl -> (raporty pada, raporty ruchu, czas); klucz to obiekt, bo po reconnekcie liczniki od zera
    while True:
        await asyncio.sleep(1.0)
        try:
            _info_tick(prev, time.monotonic())
        except Exception as e:          # statystyki nie mogą nigdy zatrzymać nadajnika
            print(f"info error: {e!r}")


def _info_tick(prev, now):
    rtt = net.rtt_x10()
    pads = list(state.active.values())
    for c in prev.keys() - set(pads):
        del prev[c]
    for c in pads:
        pad, motion = c.reports["pad"], c.reports["motion"]
        p0, m0, t0 = prev.get(c, (pad, motion, now))
        prev[c] = (pad, motion, now)
        dt = now - t0
        net.emit(T_INFO, c.idx, rtt, per_s(pad - p0, dt), per_s(motion - m0, dt))
        if C.IDLE_OFF_S and now - c.last_input > C.IDLE_OFF_S:
            c.last_input = now            # kolejna próba dopiero po następnym okresie bezczynności
            state.spawn(power_off(c, f"idle for {C.IDLE_OFF_S / 60:g} min"))
