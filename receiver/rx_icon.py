"""Receiver: the pad icon drawn in code (window and tray) - no image files needed."""
import struct
import zlib


def render_icon(rgb, size: int = 32):
    """Render the pad pictogram as RGBA rows (top to bottom).

    Each pixel is sampled 3x3 times; a sample counts when it lies inside the solid body (rounded bar plus two grips)
    and outside the cut-outs (d-pad and four buttons). The fraction of hits becomes the alpha channel, which
    gives smooth edges. Coordinates are defined on a 32x32 grid and scaled by `k` for other sizes.
    """
    def rr(x, y, x0, y0, x1, y1, r):
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def circ(x, y, cx, cy, r):
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def solid(x, y):
        return rr(x, y, 3, 8, 29, 20, 5) or circ(x, y, 8.5, 20.5, 6) or circ(x, y, 23.5, 20.5, 6)

    def cut(x, y):
        dpad = (abs(x - 10) < 1.2 and abs(y - 14.5) < 3.4) or (abs(y - 14.5) < 1.2 and abs(x - 10) < 3.4)
        return (dpad or circ(x, y, 22.5, 12.5, 1.7) or circ(x, y, 25.7, 15.5, 1.7)
                or circ(x, y, 19.3, 15.5, 1.7) or circ(x, y, 22.5, 18.5, 1.7))

    ss, k = 3, 32.0 / size
    rows = []
    for py in range(size):
        row = []
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


def icon_png(rows):
    """Encode square RGBA rows as a PNG (8-bit RGBA, filter 0 per scanline, one zlib-compressed IDAT chunk)."""
    n = len(rows)
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in rows)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


ICON_OK, ICON_LOW = (90, 170, 255), (229, 57, 53)
