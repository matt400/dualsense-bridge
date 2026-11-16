"""Sender: the per-pad input streams as the supervisor starts them, the battery poll and the heartbeat.

Every evdev device of a pad has one coroutine that turns its events into the fields of the pad's ``Ctrl`` object and asks
it to send packets: ``stream_pad`` (``sender_padstream``), ``stream_motion`` (``sender_motion``) and ``stream_touch``
(``sender_touch``), all built on ``sender_evread.pump``. This module gathers them for the supervisor and keeps the two
periodic jobs: ``stream_battery`` (the pad's battery from sysfs) and ``heartbeat`` (periodic refresh against lost packets).
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Final

import sender_padtype as padtype
import sender_state as state
from proto import BATT_UNKNOWN, STATE_CHARGING, clamp
from sender_config import C, IDLE_TP
from sender_evread import pump
from sender_motion import stream_motion
from sender_padstream import stream_pad
from sender_touch import stream_touch

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

__all__ = ["heartbeat", "pump", "stream_battery", "stream_motion", "stream_pad", "stream_touch"]

HEARTBEAT_SLOW_EVERY: Final = 5                         # every 5th heartbeat also refreshes ident, click and touch
HEARTBEAT_QUIET_FACTOR: Final = 2.0                     # a pad is "quiet" after this many heartbeat periods without a packet
BATTERY_PATH: Final = "/sys/class/power_supply/ps-controller-battery-"
BATTERY_FULL_PERCENT: Final = 100


async def heartbeat() -> None:
    """Refresh the state periodically so that a lost packet never leaves the receiver with a stale state for long.

    Every HEARTBEAT_S, each pad that has not sent anything for twice that time resends its full state. Every fifth tick
    additionally resends the pad identification, the click state and the touch state (the "finger lifted" packet of a
    pad without activity would otherwise never be repeated). The touch state is skipped for a pad without a touchpad
    whose touch state is idle (an Xbox pad, a DualSense whose touchpad device is gone): there is nothing to repeat, and
    the idle packet sent when a touchpad goes away is already repeated by ``_reset_lost_stream``.
    """
    tick = 0
    while True:
        now = time.monotonic()
        slow = tick % HEARTBEAT_SLOW_EVERY == 0
        for c in list(state.active.values()):
            if c.starting:                    # still being set up (rumble backend, grab): not ready to send anything
                continue
            if now - c.t_state >= C.HEARTBEAT_S * HEARTBEAT_QUIET_FACTOR:
                c.send_state(True)
            if slow:
                c.send_ident()
                c.send_click()
                if c.touch_dev is not None or c.touch != IDLE_TP:
                    c.send_touch(force=True)  # a lost "finger lifted" packet gets through
        tick += 1
        await asyncio.sleep(C.HEARTBEAT_S)


async def stream_battery(c: Ctrl) -> None:
    """Poll the kernel power-supply entry of the pad every BATT_POLL_S: battery percentage and charging flag.

    The sysfs name of a DualSense's entry contains the pad's MAC. Other pads' drivers name it their own way, so their entry is
    looked up under the pad's device (sender_padtype.battery_dir) until it is found - an Xbox pad's battery can appear only
    after the first report. When the entry cannot be read (USB pad, no driver support) the level is reported as BATT_UNKNOWN.
    """
    path: str | None = f"{BATTERY_PATH}{(c.key or '').lower()}" if c.plan is None else None
    while True:
        if path is None:
            path = await asyncio.to_thread(padtype.battery_dir, c.pad_dev)     # a sysfs walk: not on the loop
        try:
            if path is None:
                raise FileNotFoundError("no battery entry")
            with open(path + "/capacity") as f:
                percent = int(f.read().strip())
            with open(path + "/status") as f:
                status = f.read().strip().lower()
            c.batt = clamp(percent, 0, BATTERY_FULL_PERCENT)
            c.flags = STATE_CHARGING if status in ("charging", "full") else 0
        except (OSError, ValueError):
            c.batt, c.flags = BATT_UNKNOWN, 0
        await asyncio.sleep(C.BATT_POLL_S)
