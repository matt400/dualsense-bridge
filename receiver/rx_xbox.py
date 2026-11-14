"""Receiver: a virtual Xbox 360 pad (ViGEmBus, vgamepad's VX360Gamepad) for an Xbox / other physical pad.

The state the receiver keeps is in the DualSense layout (after the remap): sticks 0..255 with 0 = up, triggers 0..255, a hat,
and a button mask of ``proto.PAD_KEYS``. XInput wants signed 16-bit sticks with up positive, 8-bit triggers and its own button
bits, so ``fill`` converts. The face buttons go by position, as on the real pad: the DualSense cross (bottom) is A, circle B,
square (left) X, triangle (top) Y; create / options / PS are Back / Start / Guide.

``XboxPad`` wraps the vgamepad pad so rx_ds4.Out can drive it like a HIDMaestro pad (buf / build / submit / close). vgamepad is not
imported here: the pad is given to the constructor, which keeps this module importable (and testable) without ViGEmBus.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from proto import clamp
from rx_buttons import bits_from

# XUSB_BUTTON bits (ViGEm / XInput)
DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT = 0x0001, 0x0002, 0x0004, 0x0008
START, BACK, LEFT_THUMB, RIGHT_THUMB = 0x0010, 0x0020, 0x0040, 0x0080
LEFT_SHOULDER, RIGHT_SHOULDER, GUIDE = 0x0100, 0x0200, 0x0400
BTN_A, BTN_B, BTN_X, BTN_Y = 0x1000, 0x2000, 0x4000, 0x8000
# evdev key code of the canonical (DualSense) button -> XUSB bit
BUTTONS: Final = {304: BTN_A, 305: BTN_B, 308: BTN_X, 307: BTN_Y, 310: LEFT_SHOULDER, 311: RIGHT_SHOULDER, 314: BACK, 315: START,
                  316: GUIDE, 317: LEFT_THUMB, 318: RIGHT_THUMB}
HAT_BITS: Final = {(0, -1): DPAD_UP, (1, -1): DPAD_UP | DPAD_RIGHT, (1, 0): DPAD_RIGHT, (1, 1): DPAD_DOWN | DPAD_RIGHT, (0, 1): DPAD_DOWN,
                   (-1, 1): DPAD_DOWN | DPAD_LEFT, (-1, 0): DPAD_LEFT, (-1, -1): DPAD_UP | DPAD_LEFT, (0, 0): 0}
INT16_MAX, INT16_MIN = 32767, -32768
CENTER: Final = 128


def _axis_table() -> tuple[int, ...]:
    """0..255 (128 = centre) -> signed 16 bit, with the centre at exactly 0 and both ends reaching the full range."""
    return tuple((v - CENTER) * INT16_MAX // (255 - CENTER) if v >= CENTER else (v - CENTER) * -INT16_MIN // CENTER for v in range(256))


AXIS: Final = _axis_table()
_cache: dict[int, int] = {}


def buttons_for(mask: int) -> int:
    """The XUSB button bits of a button mask (memoised per mask: the hot path reuses the cache)."""
    r = _cache.get(mask)
    if r is None:
        r = _cache[mask] = bits_from(mask, BUTTONS)[0]
    return r


def fill(rep: Any, s: Mapping[str, Any]) -> None:
    """Write the state into an XUSB_REPORT (wButtons, triggers, sticks); every field is overwritten."""
    rep.wButtons = buttons_for(s["mask"]) | HAT_BITS[(s["hx"], s["hy"])]
    rep.bLeftTrigger, rep.bRightTrigger = s["lt"], s["rt"]
    rep.sThumbLX, rep.sThumbRX = AXIS[s["lx"]], AXIS[s["rx"]]
    rep.sThumbLY, rep.sThumbRY = clamp(-AXIS[s["ly"]], INT16_MIN, INT16_MAX), clamp(-AXIS[s["ry"]], INT16_MIN, INT16_MAX)


class XboxPad:
    """A vgamepad VX360Gamepad that speaks the pad interface rx_ds4.Out expects.

    ``build`` fills the pad's own report, ``submit`` sends it to ViGEm. ``buf`` only exists because Out reads it for pads with a
    ``submit``; nothing is written into it. Game rumble comes back through ``register_notification`` (the same callback signature
    as the DS4's: large motor, small motor), the player LED number of the game is ignored.
    """

    kind = "x360"

    def __init__(self, pad: Any) -> None:
        """Wrap a vgamepad pad."""
        self.pad: Any = pad
        self.buf = bytearray(1)

    def build(self, s: Mapping[str, Any], counter: int, mmode: bool, b: bytearray | None = None) -> bytearray:
        """Fill the XUSB report from the state (counter / mouse mode / buffer are the DS4 builders' arguments and unused here)."""
        fill(self.pad.report, s)
        return self.buf

    def submit(self) -> None:
        """Send the report to ViGEm."""
        self.pad.update()

    def register_feedback(self, cb: Callable[..., None]) -> None:
        """Rumble from the game -> cb(client, target, large_motor, small_motor, led_number, user_data)."""
        self.pad.register_notification(cb)

    def close(self) -> None:
        """Unregister the callback and drop the pad (vgamepad unplugs it when its last reference goes)."""
        pad, self.pad = self.pad, None
        if pad is not None:
            try:
                pad.unregister_notification()
            except Exception:  # noqa: BLE001, S110 - best effort: the pad may already be gone
                pass
