"""Receiver: raport rozszerzony wirtualnego DS4 (63 B) z surowego stanu pada."""
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
    r = _cache.get(mask)
    if r is None:
        r = _cache[mask] = bits_from(mask, FACE, SHOULDER)
    return r


def put_touch(b, off, f):
    on, tid, x, y = f
    b[off] = (0 if on else 0x80) | (tid & 0x7F)
    b[off + 1] = x & 0xFF
    b[off + 2] = ((x >> 8) & 0x0F) | ((y & 0x0F) << 4)
    b[off + 3] = (y >> 4) & 0xFF


def build_report(s, counter, mmode, b=None):
    """Wypełnia (i zwraca) 63-bajtowy raport; podany bufor jest używany ponownie bez alokacji.
    Każdy zmienny bajt jest nadpisywany przy każdym wywołaniu."""
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
    # timestamp DS4 = jednostki 5.33 us (187.5 kHz) rzeczywistego czasu; raporty idą na zdarzenia,
    # więc stała przyrostowa (counter * 750) zakłócała całkowanie gyro w aplikacjach liczących dt
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
