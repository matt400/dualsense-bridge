"""Receiver: build the virtual pad report from the raw state: DS4 (63 B, ViGEm) and DualSense (63 B without the ID byte, HIDMaestro)."""
import struct
import time

from proto import clamp
from rx_buttons import PS_BIT, bits_from
from rx_config import C

FACE = {304: 0x20, 305: 0x40, 307: 0x80, 308: 0x10}  # cross, circle, triangle, square
SHOULDER = {310: 0x01, 311: 0x02, 314: 0x10, 315: 0x20, 317: 0x40, 318: 0x80}
DPAD = {(0, -1): 0, (1, -1): 1, (1, 0): 2, (1, 1): 3, (0, 1): 4,
        (-1, 1): 5, (-1, 0): 6, (-1, -1): 7, (0, 0): 8}
NO_TOUCH = (0, 0, 0, 0)
_cache = {}


def ds4_bits(mask):
    """Button mask -> (face-button byte, shoulder byte) of the DS4 report, memoised per mask (the hot path reuses the cache)."""
    r = _cache.get(mask)
    if r is None:
        r = _cache[mask] = bits_from(mask, FACE, SHOULDER)
    return r


def put_touch(b, off, f):
    """Write one finger f = (active, id, x, y) at offset `off` in the DS4 touch format: byte 0 = id with an inverted "active"
    bit, then x and y packed as two 12-bit values over three bytes."""
    on, tid, x, y = f
    b[off] = (0 if on else 0x80) | (tid & 0x7F)
    b[off + 1] = x & 0xFF
    b[off + 2] = ((x >> 8) & 0x0F) | ((y & 0x0F) << 4)
    b[off + 3] = (y >> 4) & 0xFF


def build_report(s, counter, mmode, b=None):
    """Fill (and return) the 63-byte DS4 report; a supplied buffer is reused without allocation.
    Every variable byte is overwritten on every call, so a reused buffer never keeps stale data."""
    if b is None:
        b = bytearray(63)
    b[0] = s["lx"]
    b[1] = s["ly"]
    b[2] = s["rx"]
    b[3] = s["ry"]
    face, sh = ds4_bits(s["mask"])
    b[4] = DPAD[(s["hx"], s["hy"])] | face
    b[5] = sh | (0x04 if s["lt"] else 0) | (0x08 if s["rt"] else 0)
    b[6] = ((counter & 0x3F) << 2) | (1 if s["mask"] & PS_BIT else 0) | (2 if (s["tp"] and not mmode) else 0)
    b[7], b[8] = s["lt"], s["rt"]
    # DS4 timestamp = units of 5.33 us (187.5 kHz) of real time; reports are sent on events,
    # so a fixed increment (counter * 750) disturbed the gyro integration in apps that compute dt
    struct.pack_into("<H", b, 9, int(time.perf_counter() * 187500) & 0xFFFF)
    gx, gy, gz = s["gyro"]
    ax, ay, az = s["acc"]
    k = C.GYRO_TO_DS4
    struct.pack_into("<hhhhhh", b, 12,
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
DS5_TOUCH_H = 1079        # touch in the state uses the DS4 scale (1920x943) - the DualSense has 1920x1080
_DS5_BATT = {0: 0x00, 1: 0x10, 2: 0x20}    # battery state in the upper nibble: discharging / charging / full


DS4_BATT_MAX = 8          # DS4 battery byte without a cable: level 0..8 (rx_net.batt_codes), with a cable 0..10 (11 = full)


def ds5_battery(b4):
    """DS4 battery byte (level 0..8 without a cable, 0..11 with a cable, 0x10 = cable) -> DualSense byte (level 0..10, state
    in bits 4..7). Without a cable the 0..8 scale is stretched to 0..10: copied as is, a full pad would report level 8,
    i.e. 85 % in games and Steam (hid-playstation: level * 10 + 5)."""
    level, cable = b4 & 0x0F, b4 & 0x10
    level = min(level, 10) if cable else min(10, round(level * 10 / DS4_BATT_MAX))
    return level | _DS5_BATT[(2 if level >= 10 else 1) if cable else 0]


def put_touch_ds5(b, off, f):
    """Like put_touch, but with y rescaled from the DS4 height (943 lines) to the DualSense height (1080)."""
    on, tid, x, y = f
    put_touch(b, off, (on, tid, x, y * DS5_TOUCH_H // 942))


def build_ds5_report(s, counter, mmode, b=None):
    """DualSense USB input report 0x01 without the ID byte (63 B, like hid-playstation's dualsense_input_report): the buttons
    use the same bits as on the DS4, but PS / touchpad sit in a separate byte and the counter has a byte of its own.
    Every variable byte is overwritten."""
    if b is None:
        b = bytearray(63)
    b[0] = s["lx"]
    b[1] = s["ly"]
    b[2] = s["rx"]
    b[3] = s["ry"]
    b[4], b[5] = s["lt"], s["rt"]
    b[6] = counter & 0xFF
    face, sh = ds4_bits(s["mask"])
    b[7] = DPAD[(s["hx"], s["hy"])] | face
    b[8] = sh | (0x04 if s["lt"] else 0) | (0x08 if s["rt"] else 0)
    b[9] = (1 if s["mask"] & PS_BIT else 0) | (2 if (s["tp"] and not mmode) else 0)
    gx, gy, gz = s["gyro"]
    ax, ay, az = s["acc"]
    k = C.GYRO_TO_DS4                       # the DualSense and the DS4 use the same gyro and accelerometer units
    # sensor timestamp in units of 1/3 us of real time (like on the real pad - apps compute dt from it)
    struct.pack_into("<hhhhhhI", b, 15,
                     clamp(int(gx * k), -32768, 32767),
                     clamp(int(gy * k), -32768, 32767),
                     clamp(int(gz * k), -32768, 32767),
                     clamp(ax, -32768, 32767), clamp(ay, -32768, 32767), clamp(az, -32768, 32767),
                     int(time.perf_counter() * 3_000_000) & 0xFFFFFFFF)
    t1, t2 = (NO_TOUCH, NO_TOUCH) if mmode else s["touch"]
    put_touch_ds5(b, 32, t1)
    put_touch_ds5(b, 36, t2)
    b[52] = ds5_battery(s["batt_ds4"])
    return b
