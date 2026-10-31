"""Sender: the pad's mute button as a button press for the PC.

hid-playstation reads the mute button but does not turn it into an input event: it toggles its own "microphone muted" state and
shows it on the pad's mute LED, which it also exposes as the LED class device ``*:micmute`` (brightness = muted or not). A
press therefore shows up as a change of that brightness. ``stream_mute`` polls it a few times a second and turns each change into
a short "held" pulse of MUTE_BIT in the state packet (``Ctrl.mute_down``), so the PC sees a normal button press and release.

The kernel keeps toggling its own state and LED on every press; what the LED shows afterwards is up to the game / program on the
PC (the leds packet), which sets it right after the press arrives.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from sender_config import BURST_DELAYS
from sender_ctrl import find_led

if TYPE_CHECKING:
    from sender_ctrl import Ctrl

MUTE_LED_GLOB: Final = "/sys/class/leds/*:micmute"      # the kernel's mute LED of a DualSense (LED_FUNCTION_MICMUTE)
MUTE_POLL_S: Final = 0.05                               # how often the LED is read: the press reaches the PC within this + the network
MUTE_HOLD_S: Final = 0.08                               # how long the press stays "held" - long enough for a game that polls at 30-60 Hz


def read_state(led: str) -> int | None:
    """Read the mute LED brightness (0 = not muted, anything else = muted); None when it cannot be read."""
    try:
        with open(f"{led}/brightness") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _send(c: Ctrl, loop: asyncio.AbstractEventLoop) -> None:
    """Send the state now and again after each of BURST_DELAYS (the repeats protect the change against a lost packet)."""
    c.send_state()
    for delay in BURST_DELAYS:
        loop.call_later(delay, c.send_state, True)


def _release(c: Ctrl, loop: asyncio.AbstractEventLoop) -> None:
    """End the pulse: the mute button is released."""
    c.mute_down = False
    if not c.closing:
        _send(c, loop)


def press(c: Ctrl, loop: asyncio.AbstractEventLoop) -> None:
    """Hold the mute button for MUTE_HOLD_S: a state with MUTE_BIT now, the release later."""
    c.mute_down = True
    _send(c, loop)
    loop.call_later(MUTE_HOLD_S, _release, c, loop)


async def stream_mute(c: Ctrl) -> None:
    """Watch the pad's mute LED and press the mute button on the PC side for every change.

    Ends quietly when the kernel has no such LED (an older kernel, a USB pad without the driver's LED): there is no way to see
    the button then, and nothing else is affected.
    """
    led = find_led(c.pad_dev, MUTE_LED_GLOB)
    if led is None:
        print(f"Controller {c.idx}: mute button not available (no {MUTE_LED_GLOB} LED from the kernel driver)")
        return
    loop = asyncio.get_running_loop()
    last = read_state(led)
    while True:
        await asyncio.sleep(MUTE_POLL_S)
        now = read_state(led)
        if now is not None and last is not None and (now != 0) != (last != 0):
            press(c, loop)
        if now is not None:
            last = now
