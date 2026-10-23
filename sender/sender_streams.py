"""Sender: read loops of the evdev devices (pad, motion, touchpad, battery) and the heartbeat."""
import array
import asyncio
import collections
import fcntl
import time

from evdev import ecodes as ec

import sender_calib as calib
import sender_state as state
import sender_wol as wol
from sender_config import AUTOBIAS_MAX_STEP, BURST_DELAYS, C, IDLE_TP, TOUCH_H, TOUCH_W, PAD_ABS
from proto import CAL_RUNNING, KEY_BIT, LAT_NONE, clamp


def _eviocgmtslots(nslots):
    """Build the EVIOCGMTSLOTS ioctl number: _IOC(_IOC_READ, 'E', 0x0a, size) for a buffer of (code + one value per slot) 4-byte ints."""
    return (2 << 30) | ((4 * (nslots + 1)) << 16) | (ord("E") << 8) | 0x0A


def read_mt(dev, nslots=2):
    """Read the current finger state straight from the kernel: [(tracking_id, x, y), ...] for slots 0..nslots-1.

    Needed after SYN_DROPPED - the events were lost, but the kernel still knows which fingers are down and where.
    EVIOCGMTSLOTS returns, for one axis code at a time, the value of that axis in every multitouch slot, so the ioctl
    is issued once per axis (tracking id, X, Y) and the results are zipped per slot.
    """
    try:
        total = max(nslots, dev.absinfo(ec.ABS_MT_SLOT).max + 1)
    except Exception:
        total = nslots
    vals = {}
    for code in (ec.ABS_MT_TRACKING_ID, ec.ABS_MT_POSITION_X, ec.ABS_MT_POSITION_Y):
        buf = array.array("i", [code] + [0] * total)
        fcntl.ioctl(dev.fd, _eviocgmtslots(total), buf, True)
        vals[code] = buf[1:]
    return [(vals[ec.ABS_MT_TRACKING_ID][k], vals[ec.ABS_MT_POSITION_X][k], vals[ec.ABS_MT_POSITION_Y][k])
            for k in range(nslots)]


class RateMeter:
    """The real report rate of a device and the gaps between reports (only with --stats)."""

    def __init__(self):
        self.t_prev = 0.0
        self.gaps = []

    def tick(self, now):
        """Record one report at time `now`; the gap since the previous report is stored (capped at 4096 entries)."""
        if self.t_prev and len(self.gaps) < 4096:
            self.gaps.append(now - self.t_prev)
        self.t_prev = now

    def report(self):
        """Return (Hz, p99 gap ms, max gap ms) for the reports since the last call, or None if there are too few; resets the gaps."""
        g, self.gaps = sorted(self.gaps), []
        if len(g) < 10:
            return None
        return len(g) / sum(g), g[int(len(g) * 0.99)] * 1000, g[-1] * 1000   # Hz, p99 ms, max ms


class BtDelay:
    """Bluetooth delay above the minimum. The pad stamps every report with its own clock (MSC_TIMESTAMP from the motion
    device, µs) and the kernel stamps the time of reception. The difference = an unknown constant (the clocks) + the radio
    delay; its minimum over the last WINDOW_US is the best case, and the excess over it is BT queueing / retransmissions.
    A short window: the crystal drift of the pad and the phone (50 ppm = 0.1 ms per 2 s) does not pass for delay. The
    minimum over the window uses a monotonic queue, O(1)."""

    WINDOW_US = 2_000_000

    def __init__(self):
        self.epoch, self.last = 0, None
        self.win = collections.deque()        # (kernel time, difference), differences increasing

    def sample(self, kernel_us, pad_us):
        """Feed one (kernel receive time, pad timestamp) pair; returns the delay above the window minimum in µs.

        The pad counter is 32-bit, so a wrap-around (every ~71 min) is detected as a big backwards jump and added to
        `epoch`. The deque holds candidates for the window minimum in increasing order: a new sample pops all larger
        ones from the back, and samples older than the window are dropped from the front, so the front is always the
        current minimum.
        """
        pad_us &= 0xFFFFFFFF                  # the pad counter is 32-bit (overflow every ~71 min)
        if self.last is not None and pad_us < self.last - 0x80000000:
            self.epoch += 1 << 32
        self.last = pad_us
        d = kernel_us - pad_us - self.epoch
        win = self.win
        while win and win[-1][1] >= d:
            win.pop()
        win.append((kernel_us, d))
        while win[0][0] < kernel_us - self.WINDOW_US:
            win.popleft()
        return min(LAT_NONE - 1, d - win[0][1])


async def stream_pad(c):
    """Read the pad's buttons, sticks, triggers and D-pad and turn them into state packets.

    Events are accumulated until SYN_REPORT, which marks a complete pad report. If the report changed a button, the
    state goes out at once, followed by two delayed repeats (BURST_DELAYS) as protection against a lost packet; pure
    axis changes go through request_send, which merges them under the pad_min_dt limit. After SYN_DROPPED (the kernel
    buffer overflowed) the half-finished events are skipped until the next SYN_REPORT and the whole state is re-read.
    """
    loop = asyncio.get_running_loop()
    dirty = burst = False
    dropping = False
    meter = c.meters.setdefault("pad", RateMeter()) if C.STATS else None
    async for e in c.pad_dev.async_read_loop():
        et = e.type
        if et == ec.EV_SYN and e.code == ec.SYN_REPORT:
            c.reports["pad"] += 1
            if meter is not None:
                meter.tick(time.monotonic())
        if dropping:
            # the kernel buffer overflowed (e.g. a series of reports after a BT stall): events up to the nearest
            # SYN_REPORT are incomplete, so we skip them and read the whole state from the device
            if et == ec.EV_SYN and e.code == ec.SYN_REPORT:
                dropping = dirty = burst = False
                c.resync()
                c.send_state()
                for d in BURST_DELAYS:
                    loop.call_later(d, c.send_state, True)
                wol.on_mask(c)            # a combination released inside the lost events must not fire Wake-on-LAN
            continue
        if et == ec.EV_ABS:
            code = e.code
            if code in PAD_ABS:
                c.set_abs(code, e.value)
                dirty = True
            elif code == 16 or code == 17:
                c.hat[code - 16] = clamp(e.value, -1, 1)
                dirty = True
        elif et == ec.EV_KEY:
            bit = KEY_BIT.get(e.code)
            if bit:
                if e.value:
                    c.mask |= bit
                else:
                    c.mask &= ~bit
                dirty = burst = True
        elif et == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            dropping = True
        elif et == ec.EV_SYN and e.code == ec.SYN_REPORT and dirty:
            dirty = False
            c.mark_event(e)
            c.note_input(time.monotonic())
            if burst:
                burst = False
                c.send_state()
                for d in BURST_DELAYS:
                    loop.call_later(d, c.send_state, True)
                wol.on_mask(c)            # PS + a button held while the receiver is silent = Wake-on-LAN
            else:
                c.request_send()


async def heartbeat():
    """Periodic refresh so a lost packet never leaves the receiver with a stale state for long.

    Every HEARTBEAT_S, each pad that has not sent anything for twice that time resends its full state. Every fifth tick
    additionally resends the pad identification, the click state and the touch state (the "finger lifted" packet of a
    pad without activity would otherwise never be repeated).
    """
    n = 0
    while True:
        now = time.monotonic()
        slow = n % 5 == 0
        for c in list(state.active.values()):
            if now - c.t_state >= C.HEARTBEAT_S * 2.0:
                c.send_state(True)
            if slow:
                c.send_ident()
                c.send_click()
                c.send_touch(force=True)      # also without a touchpad: a lost "finger lifted" packet will get through
        n += 1
        await asyncio.sleep(C.HEARTBEAT_S)


def _start_cal(c, now):
    """Startup calibration: a pad with a saved calibration (from the receiver window) uses it at once, without waiting.

    Returns a GyroCal collecting samples, or None when a saved bias was applied or startup calibration is disabled.
    """
    st = calib.stored(c.key)
    if st is not None:
        c.gyro_bias = list(st["bias"])
        print(f"Controller {c.idx}: gyro bias {[round(b) for b in c.gyro_bias]} (saved calibration)")
        return None
    if C.CAL_S <= 0:
        return None
    print(f"Controller {c.idx}: calibrating gyro for {C.CAL_S}s - keep the pad still")
    return calib.GyroCal(now, C.CAL_S, user=False)


async def stream_motion(c):
    """Read the motion sensor (gyro + accelerometer), correct the gyro bias and send the averaged motion.

    Per SYN_REPORT it (1) feeds the Bluetooth delay estimator from the pad's own timestamp, (2) runs a calibration if
    one is active or requested from the window, (3) runs autobias - while the pad is quiet (small spread over a
    window) the bias is nudged half-way towards the window mean, (4) averages all samples since the last packet and
    sends only if the value moved past the deadband (or a send is already pending). Averaging, not taking the last
    sample, keeps the output smooth when samples arrive faster than MOTION_MIN_DT.
    """
    v = [0] * 6
    last = 0.0
    cal = _start_cal(c, time.monotonic())
    win_t0, win_n, win_sum = time.monotonic(), 0, [0, 0, 0]
    win_min, win_max = [1 << 60] * 3, [-(1 << 60)] * 3
    msum, mn = [0] * 6, 0       # sum of samples since the last packet: we send the AVERAGE, not an arbitrary last sample
    meter = c.meters.setdefault("motion", RateMeter()) if C.STATS else None
    bt = BtDelay()
    dropping = False
    async for e in c.motion_dev.async_read_loop():
        if e.type == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            dropping = True
            continue
        if dropping:
            if e.type != ec.EV_SYN or e.code != ec.SYN_REPORT:
                continue
            dropping = False
            for k in range(6):        # an axis whose change was lost must not keep its old value
                try:
                    v[k] = c.motion_dev.absinfo(k).value
                except Exception:
                    pass
        if e.type == ec.EV_ABS and 0 <= e.code <= 5:
            v[e.code] = e.value
        elif e.type == ec.EV_MSC and e.code == ec.MSC_TIMESTAMP:
            c.bt_us = bt.sample(e.sec * 1_000_000 + e.usec, e.value)
        elif e.type == ec.EV_SYN and e.code == ec.SYN_REPORT:
            now = time.monotonic()
            c.reports["motion"] += 1
            if meter is not None:
                meter.tick(now)
            if c.cal_request:                 # calibration from the receiver window (the pad lies still)
                cal = calib.GyroCal(now, c.cal_request, user=True)
                c.cal_request = 0
                calib.report(c, CAL_RUNNING)
                # the autobias window starts over: samples from before the calibration (pad still in the hand) in the first
                # window after it moved the freshly measured bias half-way to their mean - the camera "drifted" right after calibrating
                win_t0, win_n, win_sum = now, 0, [0, 0, 0]
                win_min, win_max = [1 << 60] * 3, [-(1 << 60)] * 3
            if cal is not None and (r := cal.feed(now, v[3:6])) is not None:
                (calib.finish if cal.user else calib.startup_done)(c, r)
                cal = None
            bias = c.gyro_bias
            if C.AUTOBIAS and cal is None:
                for k in range(3):
                    x = v[3 + k]
                    win_sum[k] += x
                    if x < win_min[k]:
                        win_min[k] = x
                    if x > win_max[k]:
                        win_max[k] = x
                win_n += 1
                if now - win_t0 >= C.AUTOBIAS_S:
                    if win_n >= 20 and all(win_max[k] - win_min[k] <= C.AUTOBIAS_RANGE for k in range(3)):
                        for k in range(3):
                            target = win_sum[k] / win_n
                            if abs(target - bias[k]) <= AUTOBIAS_MAX_STEP:
                                bias[k] += (target - bias[k]) * 0.5
                    win_t0, win_n, win_sum = now, 0, [0, 0, 0]
                    win_min, win_max = [1 << 60] * 3, [-(1 << 60)] * 3
            if cal is not None and not cal.user:
                continue                      # startup calibration: motion is not sent yet
            for k in range(6):
                msum[k] += v[k]
            mn += 1
            if now - last < C.MOTION_MIN_DT:
                continue        # samples arrive faster than the limit - they stay averaged until the next packet
            last = now
            m = c.motion
            a = [x / mn for x in msum]
            msum, mn = [0] * 6, 0
            new = [round(a[0]), round(a[1]), round(a[2]),
                   round(a[3] - bias[0]), round(a[4] - bias[1]), round(a[5] - bias[2])]
            if c.pending or any(abs(new[k] - m[k]) > (C.DB_ACC if k < 3 else C.DB_GYRO) for k in range(6)):
                c.motion = new
                c.mark_event(e)
                c.request_send()


def _same_finger(a, b):
    """Whether a finger (on, id, x, y) has not changed enough to be worth sending (deadzone)."""
    if a[0] != b[0]:
        return False
    if not a[0]:
        return True
    return (a[1] == b[1] and abs(a[2] - b[2]) < C.TOUCH_DEADZONE and abs(a[3] - b[3]) < C.TOUCH_DEADZONE)


class _Session:
    """One contact with the touchpad: from the placing of the first finger to the lifting of the last one."""
    __slots__ = ("t0", "moved", "max_n", "started", "scrolled", "clicked", "kind", "pend")

    def __init__(self, now):
        self.t0, self.moved, self.max_n = now, 0, 0
        self.started = self.scrolled = self.clicked = False
        self.kind = 0                 # 0 = nothing, 1 = cursor, 2 = scrolling (pend applies to this kind)
        self.pend = [0, 0]            # movement held back until touch_start_move is exceeded


async def stream_touch(c):
    """Touchpad -> (a) DS4/DSU touch packets, (b) mouse packets (cursor, tap, 2-finger scrolling).

    Mouse rules: 1 finger = cursor (it only starts moving after touch_start_move, so a tap does not jerk the cursor),
    2 fingers = scrolling, and until all fingers are lifted the cursor stays put (lifting fingers one by one does
    not fire a click or a jump). A short tap with 1 finger = left button, with 2 fingers = right button.

    How it works: the kernel sends multitouch events (slot selection, tracking id, X, Y); they update a small
    per-finger table, and on every SYN_REPORT the frame is processed - deltas from the previous known position drive
    the mouse, the same table drives the DS4 touch packet (with a per-finger deadzone) and the end of the contact
    decides whether it was a tap. After SYN_DROPPED the table is rebuilt from the kernel state."""
    try:
        maxx = c.touch_dev.absinfo(ec.ABS_MT_POSITION_X).max or 1919
        maxy = c.touch_dev.absinfo(ec.ABS_MT_POSITION_Y).max or 1079
    except Exception:
        maxx, maxy = 1919, 1079
    loop = asyncio.get_running_loop()
    slot = 0
    # x / y = the position remembered in the kernel slot (None = unknown). The kernel skips an axis event when the value in the slot
    # did not change - also for a NEW finger. So the position survives lifting the finger, and a new finger placed
    # at the same x or y (e.g. at an edge: 0 / max) takes it from here; reset to zero it lost such a touch and tap.
    # A finger that is already down (a pad connected with a palm on the touchpad, a touchpad attached later) is also "on":
    # the kernel will not send it ABS_MT_TRACKING_ID until it is lifted - without this its movement gave neither a cursor
    # nor DS4 touch. Such a contact does not end with a tap (as after SYN_DROPPED).
    fingers = [{"on": tid >= 0, "id": tid & 0x7F if tid >= 0 else 0, "x": x, "y": y}
               for tid, x, y in _kernel_slots(c.touch_dev, 2)]
    prev = [None, None]            # previous finger position (x, y); None = a fresh finger, no delta
    sess = None
    last_tp = IDLE_TP
    dropping = False
    resynced = any(f["on"] for f in fingers)
    async for e in c.touch_dev.async_read_loop():
        et = e.type
        if dropping:
            if et != ec.EV_SYN or e.code != ec.SYN_REPORT:
                continue              # an incomplete frame after a buffer overflow - skipped
            dropping = False
            _resync_touch(c, fingers, prev)
            try:
                slot = c.touch_dev.absinfo(ec.ABS_MT_SLOT).value
            except Exception:
                slot = 0
            if sess is not None:
                sess.clicked = True   # this contact can no longer end with a tap
            resynced = True
            # then: the usual SYN_REPORT handling with the restored finger state
        if et == ec.EV_ABS:
            if e.code == ec.ABS_MT_SLOT:
                slot = e.value
            elif slot in (0, 1):
                f = fingers[slot]
                if e.code == ec.ABS_MT_TRACKING_ID:
                    prev[slot] = None              # a new / lifted finger: no delta, the position stays (as in the kernel)
                    if e.value >= 0:
                        f.update(on=True, id=e.value & 0x7F)
                    else:
                        f["on"] = False
                elif e.code == ec.ABS_MT_POSITION_X:
                    f["x"] = e.value
                elif e.code == ec.ABS_MT_POSITION_Y:
                    f["y"] = e.value
        elif et == ec.EV_SYN and e.code == ec.SYN_DROPPED:
            dropping = True
            continue
        elif et == ec.EV_KEY and e.code == ec.BTN_LEFT:
            # a click with two fingers on the touchpad = 2 (in mouse mode the right button); the value is held until release
            c.tp = (2 if C.GESTURES and fingers[0]["on"] and fingers[1]["on"] else 1) if e.value else 0
            if e.value and sess is not None:
                sess.clicked = True      # a physical click during the touch cancels the tap until the fingers are lifted
            c.send_click()
            for d in BURST_DELAYS:
                loop.call_later(d, c.send_click)
        elif et == ec.EV_SYN and e.code == ec.SYN_REPORT:
            on = fingers[0]["on"] or fingers[1]["on"]
            now = time.monotonic()
            if on:
                c.last_input = now
            if sess is None:
                if not on:
                    continue
                sess = _Session(now)
                if resynced:
                    sess.clicked = True   # a finger placed during lost events - no tap
            resynced = False

            # --- mouse: finger deltas from the positions known in this frame ---
            deltas = []
            for k, f in enumerate(fingers):
                if f["on"] and f["x"] is not None and f["y"] is not None:
                    p = prev[k]
                    deltas.append((f["x"] - p[0], f["y"] - p[1]) if p else (0, 0))
                    prev[k] = (f["x"], f["y"])
            n = len(deltas)
            if n > sess.max_n:
                sess.max_n = n
            sess.moved += sum(abs(dx) + abs(dy) for dx, dy in deltas)
            kind = dx = dy = 0
            if C.GESTURES and n >= 2:
                kind, dx, dy = 2, deltas[0][0] + deltas[1][0], deltas[0][1] + deltas[1][1]
            elif n >= 1 and not (C.GESTURES and sess.max_n >= 2):
                kind, (dx, dy) = 1, deltas[0]
            if kind and (dx or dy):
                if kind != sess.kind:
                    sess.kind, sess.pend = kind, [0, 0]
                sess.pend[0] += dx
                sess.pend[1] += dy
                if sess.started or sess.moved >= C.TOUCH_START_MOVE:
                    sess.started = True
                    if kind == 2:
                        sess.scrolled = True
                    c.send_mouse(sess.pend[0], sess.pend[1], 2 if kind == 2 else 0)
                    sess.pend = [0, 0]

            # --- DS4/DSU touch: the deadzone checks BOTH fingers (the second must not be swallowed by the first) ---
            tp = []
            for f in fingers:
                if f["on"] and f["x"] is not None and f["y"] is not None:
                    tp.append((1, f["id"],
                               clamp(f["x"] * TOUCH_W // maxx, 0, TOUCH_W),
                               clamp(f["y"] * TOUCH_H // maxy, 0, TOUCH_H)))
                else:
                    tp.append((0, f["id"], 0, 0))
            tp = tuple(tp)
            if tp != last_tp and not (_same_finger(tp[0], last_tp[0]) and _same_finger(tp[1], last_tp[1])):
                last_tp = c.touch = tp
                c.send_touch()

            # --- end of the contact: tap ---
            if not on:
                if (C.TAP_CLICK and not sess.clicked and now - sess.t0 < C.TAP_MAX_S
                        and sess.moved < C.TAP_MAX_MOVE):
                    if C.GESTURES and sess.max_n >= 2:
                        if not sess.scrolled:
                            c.send_tap(4)
                    elif sess.max_n >= 1:
                        c.send_tap(1)
                sess = None


def _kernel_slots(dev, n):
    """The multitouch slot state from the kernel [(tracking_id, x, y)]; without the ioctl (another platform, tests) the
    fingers are lifted and the position is unknown (None)."""
    try:
        return read_mt(dev, n)
    except (OSError, AttributeError, TypeError, ValueError):
        return [(-1, None, None)] * n


def _resync_touch(c, fingers, prev):
    """Rebuild the fingers and the touchpad button from the kernel state; without the ioctl (another platform) all fingers are lifted."""
    for k, (tid, x, y) in enumerate(_kernel_slots(c.touch_dev, len(fingers))):
        fingers[k].update(on=tid >= 0, x=x, y=y)       # the position of a lifted finger too (see stream_touch)
        if tid >= 0:
            fingers[k]["id"] = tid & 0x7F
        prev[k] = None                # no delta in the first frame - the cursor will not jump
    try:
        tp = ec.BTN_LEFT in c.touch_dev.active_keys()
    except (OSError, AttributeError):
        return
    if bool(c.tp) != tp:
        c.tp = (2 if C.GESTURES and fingers[0]["on"] and fingers[1]["on"] else 1) if tp else 0
        c.send_click()


async def stream_battery(c):
    """Poll the kernel power-supply entry of the pad every BATT_POLL_S: battery percentage and charging flag.

    The sysfs name contains the pad's MAC. When the entry cannot be read (USB pad, no driver support) the level is
    reported as 255 = unknown.
    """
    path = f"/sys/class/power_supply/ps-controller-battery-{(c.key or '').lower()}"
    while True:
        try:
            with open(path + "/capacity") as f:
                pct = int(f.read().strip())
            with open(path + "/status") as f:
                st = f.read().strip().lower()
            c.batt = clamp(pct, 0, 100)
            c.flags = 1 if st in ("charging", "full") else 0
        except (OSError, ValueError):
            c.batt, c.flags = 255, 0
        await asyncio.sleep(C.BATT_POLL_S)
