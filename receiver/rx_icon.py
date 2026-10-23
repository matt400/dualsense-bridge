"""Receiver: the pad icon drawn in code (window and tray) - no image files needed."""
from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence
from typing import Final, TypeAlias

RGBA: TypeAlias = tuple[int, int, int, int]
GRID: Final = 32.0                  # the size of the grid the pictogram is defined on
SUPERSAMPLE: Final = 3              # samples per pixel edge (3x3) for the smooth edges
PNG_COLOR_RGBA: Final = 6


def render_icon(rgb: Sequence[int], size: int = 32) -> list[list[RGBA]]:
    """Render the pad pictogram as RGBA rows (top to bottom).

    Each pixel is sampled 3x3 times; a sample counts when it lies inside the solid body (rounded bar plus two grips)
    and outside the cut-outs (d-pad and four buttons). The fraction of hits becomes the alpha channel, which
    gives smooth edges. Coordinates are defined on a 32x32 grid and scaled by `k` for other sizes.
    """
    def rr(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def circ(x: float, y: float, cx: float, cy: float, r: float) -> bool:
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def plus(x: float, y: float, cx: float, cy: float, arm: float, half_width: float) -> bool:
        return (abs(x - cx) < half_width and abs(y - cy) < arm) or (abs(y - cy) < half_width and abs(x - cx) < arm)

    def solid(x: float, y: float) -> bool:
        return rr(x, y, 3, 8, 29, 20, 5) or circ(x, y, 8.5, 20.5, 6) or circ(x, y, 23.5, 20.5, 6)

    def cut(x: float, y: float) -> bool:
        return (plus(x, y, 10, 14.5, 3.4, 1.2) or circ(x, y, 22.5, 12.5, 1.7) or circ(x, y, 25.7, 15.5, 1.7)
                or circ(x, y, 19.3, 15.5, 1.7) or circ(x, y, 22.5, 18.5, 1.7))

    ss, k = SUPERSAMPLE, GRID / size
    rows: list[list[RGBA]] = []
    for py in range(size):
        row: list[RGBA] = []
        for px in range(size):
            hits = 0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + 0.5) / ss) * k
                    y = (py + (sy + 0.5) / ss) * k
                    if solid(x, y) and not cut(x, y):
                        hits += 1
            row.append((rgb[0], rgb[1], rgb[2], round(255 * hits / (ss * ss))))
        rows.append(row)
    return rows


def icon_png(rows: Sequence[Sequence[RGBA]]) -> bytes:
    """Encode square RGBA rows as a PNG (8-bit RGBA, filter 0 per scanline, one zlib-compressed IDAT chunk)."""
    n = len(rows)
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in rows)

    def chunk(t: bytes, d: bytes) -> bytes:
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, PNG_COLOR_RGBA, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


ICON_OK: Final = (90, 170, 255)
ICON_LOW: Final = (229, 57, 53)
