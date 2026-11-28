"""Receiver: a virtual Xbox 360 pad for an Xbox / other physical pad - the HIDMaestro state block and the ViGEmBus wrapper.

The state the receiver keeps is in the DualSense layout (after the remap): sticks 0..255 with 0 = up, triggers 0..255, a hat,
and a button mask of ``proto.PAD_KEYS``. XInput wants signed 16-bit sticks with up positive, 8-bit triggers and its own button
bits, so ``fill`` converts. The face buttons go by position, as on the real pad: the DualSense cross (bottom) is A, circle B,
square (left) X, triangle (top) Y; create / options / PS are Back / Start / Guide.

``build_state`` writes the same state as the native block HIDMaestro's SubmitState delegate reads (rx_hm.X360Pad): six floats
in 0..1 (left X, left Y, right X, right Y, left trigger, right trigger; Y grows downward as in the DualSense, 0.5 = centre), the
HMButton bits and the HMHat octant. Every value comes from a precomputed table, so a report allocates nothing.

``XboxPad`` wraps the vgamepad pad so rx_ds4.Out can drive it like a HIDMaestro pad (buf / build / submit / close). vgamepad is not
imported here: the pad is given to the constructor, which keeps this module importable (and testable) without ViGEmBus.
"""
from __future__ import annotations

import struct
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

# ---------------- HIDMaestro (SubmitState) ----------------
# HMButton bits (HIDMaestro): the canonical (DualSense) button -> the Xbox button at the same position
HM_A, HM_B, HM_X, HM_Y, HM_LB, HM_RB = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0020
HM_BACK, HM_START, HM_LS, HM_RS, HM_GUIDE = 0x0040, 0x0080, 0x0100, 0x0200, 0x0400
HM_BUTTONS: Final = {304: HM_A, 305: HM_B, 308: HM_X, 307: HM_Y, 310: HM_LB, 311: HM_RB, 314: HM_BACK, 315: HM_START,
                     316: HM_GUIDE, 317: HM_LS, 318: HM_RS}
# HMHat: 0 = centred, 1 = north ... 8 = north-west, clockwise
HM_HAT: Final = {(0, 0): 0, (0, -1): 1, (1, -1): 2, (1, 0): 3, (1, 1): 4, (0, 1): 5, (-1, 1): 6, (-1, 0): 7, (-1, -1): 8}
STATE: Final = struct.Struct("<6fIB")      # the native block the IL fill method reads
STATE_BUTTONS, STATE_HAT = 24, 28          # offsets of the HMButton uint32 and the HMHat byte (rx_hm.state_delegates)
STATE_LEN: Final = 32
# 0..255 -> 0..1 with 128 at exactly 0.5 (XInput then reads a centred stick as 0) and both ends reaching 0 / 1
STICK: Final = tuple(v / 256 if v < CENTER else 0.5 + (v - CENTER) / (2 * (255 - CENTER)) for v in range(256))
TRIGGER: Final = tuple(v / 255 for v in range(256))
_hm_cache: dict[int, int] = {}


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


def hm_buttons_for(mask: int) -> int:
    """The HMButton bits of a button mask (memoised per mask: the hot path reuses the cache)."""
    r = _hm_cache.get(mask)
    if r is None:
        r = _hm_cache[mask] = bits_from(mask, HM_BUTTONS)[0]
    return r


def build_state(s: Mapping[str, Any], counter: int, mmode: bool, b: bytearray | None = None) -> bytearray:
    """Fill (and return) the native state block of rx_hm.X360Pad; a supplied buffer is reused without allocation.

    counter / mouse mode are the DS4 builders' arguments and unused: an Xbox pad has no counter and no touchpad.
    """
    if b is None:
        b = bytearray(STATE_LEN)
    STATE.pack_into(b, 0, STICK[s["lx"]], STICK[s["ly"]], STICK[s["rx"]], STICK[s["ry"]], TRIGGER[s["lt"]], TRIGGER[s["rt"]],
                    hm_buttons_for(s["mask"]), HM_HAT[(s["hx"], s["hy"])])
    return b


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
