"""Receiver: gyro -> right stick with an anti-deadzone must not turn the sensor noise of a still pad into a stick jump, and
the tilt stick reaches the full byte range like a physical stick.
    python3 tests/test_gyro_stick_noise.py receiver"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import RX  # noqa: F401  (paths, Windows stubs)
import rx_gstick as G
from rx_shape import GYRO_SOFT_DPS, gyro_rates, gyro_stick, unit
from chk import check, fails

g = {"sens": 1.0, "anti": 0.2, "inv_x": False, "inv_y": False}
res = 16.0                                      # counts per deg/s, as C.GYRO_RES

# a pad lying still: a few counts of noise in random directions
worst = 0.0
for raw in ((1, 0, 0), (0, -2, 0), (-1, 1, 0), (2, 2, 0), (0, 3, 0)):
    x, y = gyro_stick(128, 128, gyro_rates(raw, g, res), g)
    worst = max(worst, abs(unit(x)), abs(unit(y)))
check(worst < 0.03, f"sensor noise moves the stick by {worst:.3f} (< 0.03), not by the anti-deadzone 0.2")

# a clear rotation still gets the whole anti-deadzone
x, _y = gyro_stick(128, 128, (GYRO_SOFT_DPS * 2, 0.0), g)
check(unit(x) >= 0.2, f"a real rotation gets the anti-deadzone ({unit(x):.3f})")
# continuous at the edge of the fade band (no step in the response)
a = unit(gyro_stick(128, 128, (GYRO_SOFT_DPS * 0.999, 0.0), g)[0])
b = unit(gyro_stick(128, 128, (GYRO_SOFT_DPS * 1.001, 0.0), g)[0])
check(abs(a - b) < 0.01, f"no step at the edge of the fade band ({a:.3f} vs {b:.3f})")
# without an anti-deadzone nothing changed
x, _y = gyro_stick(128, 128, (15.0, 0.0), {**g, "anti": 0.0})
check(x == 128 + round(0.1 * 127), f"plain gyro stick unchanged ({x})")

# tilt stick: full scale both ways, the same bytes as a physical stick
check(G.to_u8(-1.0) == 0 and G.to_u8(1.0) == 255 and G.to_u8(0.0) == 128, "tilt stick: -1 -> 0, +1 -> 255, 0 -> 128")

sys.exit(1 if fails else 0)
