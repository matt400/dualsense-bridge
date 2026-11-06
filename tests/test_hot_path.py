"""Receiver: optimizations of the state packet path give the same results as before - apply_remap (only active inputs),
input_pressed instead of the pressed_inputs set, a DSU packet without needless copies, the sensor timestamp from the sample time,
one clock read per packet.
    python3 tests/test_hot_path.py receiver"""
import itertools, os, random, struct, sys, zlib
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa  (Windows stubs, TMP, store, state)
import rx_buttons as B
import rx_dsu
import rx_net
import rx_report as R
from rx_state import new_state

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- apply_remap: the result is identical to a full pass over the 17 inputs ----------
def reference_remap(m, mask, hx, hy, lt, rt):
    """The previous implementation (a loop over every input) as the reference."""
    if not m:
        return mask, hx, hy, lt, rt
    out_mask = dirs = olt = ort = 0
    for src in B.INPUTS:
        if src in B.BUTTON_CODES:
            v = B.BYTE_MAX if mask & B.BIT[B.BUTTON_CODES[src]] else 0
        elif src in B.DIRS:
            dx, dy = B.DIRS[src]
            v = B.BYTE_MAX if ((hx == dx) if dx else (hy == dy)) else 0
        else:
            v = lt if src == "l2" else rt
        if not v:
            continue
        dst = m.get(src, src)
        if dst is None:
            continue
        if dst == "l2":
            olt = max(olt, v)
        elif dst == "r2":
            ort = max(ort, v)
        elif v >= B.TRIG_ON:
            if dst in B.BUTTON_CODES:
                out_mask |= B.BIT[B.BUTTON_CODES[dst]]
            else:
                dirs |= B.DIR_BIT[dst]
    hat_x = bool(dirs & B.DIR_BIT["dpad_right"]) - bool(dirs & B.DIR_BIT["dpad_left"])
    hat_y = bool(dirs & B.DIR_BIT["dpad_down"]) - bool(dirs & B.DIR_BIT["dpad_up"])
    return out_mask, hat_x, hat_y, olt, ort

rnd = random.Random(7)
bad = 0
n = 0
for _ in range(20000):
    m = {}
    for src in rnd.sample(B.INPUTS, rnd.randint(0, 6)):
        m[src] = rnd.choice([*B.INPUTS, None])
    mask = rnd.getrandbits(len(B.BIT)) if rnd.random() < 0.8 else 0     # bits outside the buttons too (touchpad etc.)
    hx, hy = rnd.choice([-1, 0, 1, 2]), rnd.choice([-1, 0, 1, -3])      # values outside -1..1 too
    lt, rt = rnd.choice([0, 1, 39, 40, 255]), rnd.choice([0, 7, 40, 200])
    n += 1
    if B.apply_remap(m, mask, hx, hy, lt, rt) != reference_remap(m, mask, hx, hy, lt, rt):
        bad += 1
check(bad == 0, f"apply_remap = reference on {n} random maps and states (differences: {bad})")
check(B.apply_remap({}, 5, 1, -1, 9, 8) == (5, 1, -1, 9, 8), "empty map: input unchanged")


# ---------- input_pressed = "name in pressed_inputs(...)" ----------
bad = 0
for _ in range(5000):
    mask = rnd.getrandbits(len(B.BIT))
    hx, hy = rnd.choice([-2, -1, 0, 1, 2]), rnd.choice([-2, -1, 0, 1, 2])
    lt, rt = rnd.choice([0, 39, 40, 255]), rnd.choice([0, 39, 40, 255])
    active = B.pressed_inputs(mask, hx, hy, lt, rt)
    bad += sum((name in active) != B.input_pressed(name, mask, hx, hy, lt, rt) for name in B.INPUTS)
check(bad == 0, f"input_pressed agrees with pressed_inputs for every input (differences: {bad})")
check(B.input_pressed("always", 0xFFFF, 1, 1, 255, 255) is False and B.input_pressed("nie_ma", 1, 0, 0, 0, 0) is False,
      "unknown name: false, no exception")


# ---------- DSU packet: byte for byte as before, valid CRC ----------
def reference_pkt(event, payload):
    body = struct.pack("<I", event) + payload
    pkt = bytearray(struct.Struct("<4sHHII").pack(b"DSUS", rx_dsu.PROTOCOL_VERSION, len(body), 0, rx_dsu.SERVER_ID) + body)
    struct.pack_into("<I", pkt, 8, zlib.crc32(pkt) & 0xFFFFFFFF)
    return bytes(pkt)

same = all(rx_dsu.dsu_pkt(ev, pl) == reference_pkt(ev, pl)
           for ev, pl in ((e, os.urandom(k)) for e, k in itertools.product((0x100000, 0x100001, 0x100002), (0, 2, 11, 80))))
check(same, "dsu_pkt identical to the previous implementation (version, slot info, data)")
pk = rx_dsu.dsu_pkt(0x100002, os.urandom(80))
crc = struct.unpack_from("<I", pk, 8)[0]
check(zlib.crc32(pk[:8] + bytes(4) + pk[12:]) & 0xFFFFFFFF == crc and isinstance(pk, bytes), "CRC computed with the CRC field zeroed, the result is bytes")


# ---------- reports: the sensor timestamp from the sample time, not from the report build time ----------
s = new_state()
s["ts"] = 1_234_567_891
b5 = R.build_ds5_report(s, 1, False)
b4 = R.build_report(s, 1, False)
check(struct.unpack_from("<I", b5, 27)[0] == (1_234_567_891 * 3) & 0xFFFFFFFF, "DualSense: timestamp = ts[us] * 3 (jednostka 1/3 us)")
check(struct.unpack_from("<H", b4, 9)[0] == (1_234_567_891 * 3 // 16) & 0xFFFF, "DS4: timestamp = ts[us] * 3/16 (jednostka 5,33 us)")
check(R.build_ds5_report(s, 1, False) == b5 and R.build_report(s, 1, False) == b4,
      "the same state = the same report (the build time has no effect - less jitter when reports are merged)")
s["ts"] += 1000
check(struct.unpack_from("<I", R.build_ds5_report(s, 2, False), 27)[0] - struct.unpack_from("<I", b5, 27)[0] == 3000,
      "1 ms between samples = 3000 units")


# ---------- one clock read per packet: now from the network loop goes into last_seen and the sample timestamp ----------
orig_handle = rx_net.handle
rx_net.handle = lambda t, i, d, arr=0, now=None: orig_handle(t, i, d, arr, 123.5)       # feed() from rxenv called handle without now
import rxenv
rxenv.N.handle = rx_net.handle
state(0, mask=B.BIT[304], sticks=(10, 20, 30, 40))
rx_net.handle = orig_handle
check(S.last_seen[0] == 123.5 and S.states[0]["ts"] == 123_500_000,
      f"last_seen i ts z jednego now: {S.last_seen[0]}, {S.states[0]['ts']}")
state(0, mask=0, sticks=(11, 20, 30, 40))                                              # no now: handle reads the clock by itself
check(S.states[0]["ts"] > 0 and S.last_seen[0] != 123.5, "a call without now (tests, other modules) still works")

print("HOT PATH OK" if not fails else f"HOT PATH FAILURES: {len(fails)}")
sys.exit(1 if fails else 0)
