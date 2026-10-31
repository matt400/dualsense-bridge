"""Receiver: build the virtual pad report from the raw state: DS4 (63 B, ViGEm) and DualSense (63 B without the ID byte, HIDMaestro)."""
from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import Any, Final, TypeAlias

from proto import clamp
from rx_buttons import PS_BIT, bits_from
from rx_config import C

Touch: TypeAlias = tuple[int, int, int, int]       # (active, id, x, y) in the DS4 touch scale

REPORT_LEN: Final = 63
FACE: Final = {304: 0x20, 305: 0x40, 307: 0x80, 308: 0x10}  # cross, circle, triangle, square
SHOULDER: Final = {310: 0x01, 311: 0x02, 314: 0x10, 315: 0x20, 317: 0x40, 318: 0x80}
DPAD: Final = {(0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3, (0, 1): 4,
        (-1, 1): 5, (-1, 0): 6, (-1, -1): 7, (0, 0): 8}
NO_TOUCH: Final = (0, 0, 0, 0)
_cache: dict[int, tuple[int, ...]] = {}
# precompiled once: struct.pack_into(fmt, ...) looks the format up on every call, a Struct does not
_U16: Final = struct.Struct("<H")
_DS4_MOTION: Final = struct.Struct("<hhhhhh")        # gyro xyz + accelerometer xyz
_DS5_MOTION: Final = struct.Struct("<hhhhhhI")       # the same + the sensor timestamp


def ds4_bits(mask: int) -> tuple[int, ...]:
    """Map a button mask to (face-button byte, shoulder byte) of the DS4 report, memoised per mask (the hot path reuses the cache)."""
    r = _cache.get(mask)
    if r is None:
        r = _cache[mask] = bits_from(mask, FACE, SHOULDER)
    return r


def put_touch(b: bytearray, off: int, f: Touch) -> None:
    """Write one finger f = (active, id, x, y) at offset `off` in the DS4 touch format.

    Byte 0 = id with an inverted "active" bit, then x and y packed as two 12-bit values over three bytes.
    """
    on, tid, x, y = f
    b[off] = (0 if on else 0x80) | (tid & 0x7F)
    b[off + 1] = x & 0xFF
    b[off + 2] = ((x >> 8) & 0x0F) | ((y & 0x0F) << 4)
    b[off + 3] = (y >> 4) & 0xFF


def build_report(s: Mapping[str, Any], counter: int, mmode: bool, b: bytearray | None = None) -> bytearray:
    """Fill (and return) the 63-byte DS4 report; a supplied buffer is reused without allocation.

    Every variable byte is overwritten on every call, so a reused buffer never keeps stale data.
    """
    if b is None:
        b = bytearray(REPORT_LEN)
    b[0] = s["lx"]
    b[1] = s["ly"]
    b[2] = s["rx"]
    b[3] = s["ry"]
    face, sh = ds4_bits(s["mask"])
    b[4] = DPAD[(s["hx"], s["hy"])] | face
    b[5] = sh | (0x04 if s["lt"] else 0) | (0x08 if s["rt"] else 0)
    b[6] = ((counter & 0x3F) << 2) | (1 if s["mask"] & PS_BIT else 0) | (2 if (s["tp"] and not mmode) else 0)
    b[7], b[8] = s["lt"], s["rt"]
    # DS4 timestamp = units of 5.33 us (187.5 kHz = 3/16 per us) of the time the sample arrived (s["ts"], us); reports are
    # sent on events, so a fixed increment (counter * 750) disturbed the gyro integration in apps that compute dt, and the
    # build time jitters when reports are merged (pad_hz) - the sample's own time is what dt must follow
    _U16.pack_into(b, 9, s["ts"] * 3 // 16 & 0xFFFF)
    gx, gy, gz = s["gyro"]
    ax, ay, az = s["acc"]
    k = C.GYRO_TO_DS4
    _DS4_MOTION.pack_into(b, 12,
                          clamp(int(gx * k), -32768, 32767),
                          clamp(int(gy * k), -32768, 32767),
                          clamp(int(gz * k), -32768, 32767),
                          clamp(ax, -32768, 32767), clamp(ay, -32768, 32767), clamp(az, -32768, 32767))
    b[29] = s["batt_ds4"]
    b[32] = 1
    b[33] = counter & 0xFF
    t1, t2 = (NO_TOUCH, NO_TOUCH) if mmode else s["touch"]
    put_touch(b, 34, t1)
    put_touch(b, 38, t2)
    return b


# ---------------- DualSense (HIDMaestro) ----------------
DS5_TOUCH_H: Final = 1079        # touch in the state uses the DS4 scale (1920x943) - the DualSense has 1920x1080
DS4_TOUCH_MAX_Y: Final = 942    # the last line of the DS4 touchpad (943 lines)


def put_touch_ds5(b: bytearray, off: int, f: Touch) -> None:
    """Like put_touch, but with y rescaled from the DS4 height (943 lines) to the DualSense height (1080)."""
    on, tid, x, y = f
    put_touch(b, off, (on, tid, x, y * DS5_TOUCH_H // DS4_TOUCH_MAX_Y))


def build_ds5_report(s: Mapping[str, Any], counter: int, mmode: bool, b: bytearray | None = None) -> bytearray:
    """Fill (and return) the DualSense USB input report 0x01 without the ID byte (63 B).

    Like hid-playstation's dualsense_input_report: the buttons use the same bits as on the DS4, but PS / touchpad sit
    in a separate byte and the counter has a byte of its own. Every variable byte is overwritten.
    """
    if b is None:
        b = bytearray(REPORT_LEN)
    b[0] = s["lx"]
    b[1] = s["ly"]
    b[2] = s["rx"]
    b[3] = s["ry"]
    b[4], b[5] = s["lt"], s["rt"]
    b[6] = counter & 0xFF
    face, sh = ds4_bits(s["mask"])
    b[7] = DPAD[(s["hx"], s["hy"])] | face
    b[8] = sh | (0x04 if s["lt"] else 0) | (0x08 if s["rt"] else 0)
    b[9] = (1 if s["mask"] & PS_BIT else 0) | (2 if (s["tp"] and not mmode) else 0) | (4 if s.get("mute") else 0)   # PS, touchpad, mute
    gx, gy, gz = s["gyro"]
    ax, ay, az = s["acc"]
    k = C.GYRO_TO_DS4                       # the DualSense and the DS4 use the same gyro and accelerometer units
    # sensor timestamp in units of 1/3 us (like on the real pad - apps compute dt from it), taken from the time the sample
    # arrived (s["ts"], us) rather than from the report build time: less jitter when reports are merged (pad_hz)
    _DS5_MOTION.pack_into(b, 15,
                          clamp(int(gx * k), -32768, 32767),
                          clamp(int(gy * k), -32768, 32767),
                          clamp(int(gz * k), -32768, 32767),
                          clamp(ax, -32768, 32767), clamp(ay, -32768, 32767), clamp(az, -32768, 32767),
                          s["ts"] * 3 & 0xFFFFFFFF)
    t1, t2 = (NO_TOUCH, NO_TOUCH) if mmode else s["touch"]
    put_touch_ds5(b, 32, t1)
    put_touch_ds5(b, 36, t2)
    b[52] = s["batt_ds5"]
    return b
