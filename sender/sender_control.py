"""Sender: commands from the PC (power off a pad, gyro calibration, sticks without deadzone), idle power-off
and info packets (ping, Hz) for the receiver window."""
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
RETRY_S = 10.0          # the same pad is powered off again no sooner than this many seconds later


def per_s(n, dt):
    """Convert a counter delta n over dt seconds into a rate per second, clamped to the 16-bit packet field."""
    return max(0, min(0xFFFF, round(n / dt))) if dt > 0 else 0


async def power_off(c, reason):
    """Disconnect a pad over Bluetooth - a DualSense switches itself off when it loses the link.

    Over USB there is nothing to disconnect, so the call is skipped (the key of a Bluetooth pad is its MAC address).
    The request is rate-limited per pad (RETRY_S). The preferred path asks the Bluetooth manager, which knows which
    adapter the pad is attached to (also a dongle, hci1); if that fails, it falls back to `bluetoothctl disconnect`
    with a 10 s timeout, killing and reaping the process on timeout so no zombie is left behind.
    """
    now = time.monotonic()
    if now - c.powering_off < RETRY_S:
        return
    c.powering_off = now
    if not MAC_RE.fullmatch(c.key or ""):
        print(f"Controller {c.idx}: power off skipped - not a Bluetooth pad ({c.key})")
        return
    print(f"Controller {c.idx}: powering off ({reason})")
    events.info(E_POWER_OFF, c.idx, reason)
    if await sender_bt.bt.disconnect_pad(c.key):     # the adapter the pad is connected to (also the hci1 dongle)
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
        await proc.wait()              # without this a zombie process is left behind
        print(f"Controller {c.idx}: power off timed out")
        return
    if proc.returncode:
        print(f"Controller {c.idx}: power off failed: {err.decode(errors='replace').strip() or proc.returncode}")


def on_command(idx, cmd, arg):
    """Handle a command packet from the receiver (called by the network layer for the target slot idx).

    CMD_UPDATE concerns the whole sender, so it ignores idx. Every other command is looked up by slot and silently
    dropped if no pad currently sits there. Slow work (power off, update) is started as a background task so the
    UDP receive path never blocks.
    """
    if cmd == CMD_UPDATE:                  # concerns the whole sender, not a pad (idx = 255)
        state.spawn(sender_update.run())
        return
    c = state.ctrl_by_idx(idx)
    if c is None:
        return
    if cmd == CMD_POWER_OFF:
        state.spawn(power_off(c, "requested from PC"))
    elif cmd == CMD_GYRO_CAL:            # gyro calibration from the window (arg = seconds), or just the status (arg 0)
        if arg:
            calib.request(c, arg)
        else:
            calib.report(c, CAL_STATUS)
    elif cmd == CMD_RAW_STICKS:          # drift measurement: sticks without the sender's deadzone for arg seconds
        c.set_raw_sticks(arg)


async def info_loop():
    """Once a second: send one info packet per pad (ping, real report Hz) and power off pads that sat idle too long."""
    prev = {}        # Ctrl -> (pad reports, motion reports, time); keyed by object, because counters restart after a reconnect
    while True:
        await asyncio.sleep(1.0)
        try:
            _info_tick(prev, time.monotonic())
        except Exception as e:          # statistics must never stop the sender
            print(f"info error: {e!r}")


def _info_tick(prev, now):
    """One iteration of info_loop.

    Report rates are derived from the difference of the per-pad report counters since the previous tick, divided by
    the elapsed time. Pads that disappeared are dropped from `prev`; a pad seen for the first time gets a zero delta.
    If idle power-off is enabled and the pad has had no input for IDLE_OFF_S, a power-off task is spawned and
    last_input is reset, so the next attempt only happens after another full idle period.
    """
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
            c.last_input = now            # the next attempt only after another idle period
            state.spawn(power_off(c, f"idle for {C.IDLE_OFF_S / 60:g} min"))
