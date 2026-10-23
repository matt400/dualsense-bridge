"""Receiver: gyro -> analog stick - the pad tilt (accelerometer) becomes a stick deflection.

Pure logic, no Qt and no rx_state (tested on Linux, the 250 Hz thread is in rx_gstick_io, the window in rx_qt_gstick):
  Config     ustawienia jednego pada (zapis per MAC: rx_gstick_io, gyrostick.json obok remap.json)
  tilt_basis, angles   gravity vector -> (pitch, roll) in degrees relative to the neutral center
  Engine     state machine: clutch, auto-sleep, deadzone with "snap-back", EMA filter, curve, sprint, delta threshold
  Sink       virtual pad abstraction layer (StateSink in rx_gstick_io; ViGEm / vJoy / uinput = further sinks)

Pad axes as in the DS4 / DualSense reports: X to the right, Y up (out of the plane of a pad lying flat), Z towards the player;
a pad lying flat reads ay = +1 g. Tilting the FRONT down = positive pitch = stick up / forward (Y +); tilting right
(right side down) = positive roll = stick right (X +). If the sensors have the opposite sign (other firmware), the
"Invert pitch / roll" switches in the window flip it.

One-sample pipeline (Engine.step, ~250 times per second):
  acc -> pitch / roll [°] relative to the center -> inversions and X/Y ratio -> vector in degrees (raw)
  raw inside the deadzone -> output 0 at once, the filter takes over raw (snap-back: no filter trail)
  otherwise: EMA (smoothing) -> deadzone and range (radially) -> curve -> output -1..1
The output goes to the sink only when the change exceeds the delta threshold (or it is zero, the edge of the range, sprint, a state change).
"""
import math
from dataclasses import dataclass, fields, replace

from rx_buttons import BIT, BUTTON_CODES, INPUTS

# ---------------- ustawienia ----------------
TARGETS = {"left": "Lewa gałka", "right": "Prawa gałka", "dpad": "D-pad", "none": "Brak (tylko podgląd)"}
CURVES = {"linear": "Liniowa", "cubic": "Wykładnicza (sześcienna)", "aggressive": "Agresywna"}
FEEDBACKS = {"off": "Wyłączony", "haptic": "Wibracja", "led": "Kolor lightbara", "both": "Wibracja i kolor"}
CLUTCHES = ("always", *INPUTS)                          # the input that has to be held ("always" = no clutch)
SPRINTS = ("none", *(n for n in BUTTON_CODES if n != "ps"))   # wirtualny przycisk wciskany na skraju zakresu
# ranges shared by file validation and the window sliders: name -> (min, max)
LIMITS = {"deadzone": (0.0, 20.0),      # martwa strefa [°]
          "outer": (10.0, 80.0),        # tilt giving full deflection [°]
          "smoothing": (0.0, 100.0),    # EMA smoothing [%]: 0 = none, 100 = time constant SMOOTH_MAX_S
          "ratio": (0.5, 2.0),          # vertical sensitivity relative to horizontal (wrists don't tilt equally)
          "idle_s": (0.0, 10.0),        # auto-sleep after stillness [s], 0 = off
          "delta": (0.0, 5.0),          # smallest change sent to the game [% of full range]
          "sprint_at": (50.0, 100.0)}   # sprint from this % of the range
MIN_SPAN = 5.0                          # range is always this many degrees larger than the deadzone
DPAD_AT = 0.5                           # d-pad mode: a direction from half deflection
SMOOTH_MAX_S = 0.150
I16_MAX = 32767                         # full output range -32768..+32767 (the DS4 report has 8 bits - the sink scales)


def _clamp(v, name):
    lo, hi = LIMITS[name]
    return min(hi, max(lo, v))


@dataclass(frozen=True, slots=True)
class Config:
    enabled: bool = False
    target: str = "left"            # which virtual input the gyro takes over (the physical one is then ignored)
    invert_pitch: bool = False
    invert_roll: bool = False
    deadzone: float = 3.0
    outer: float = 30.0
    smoothing: float = 25.0
    ratio: float = 1.0
    curve: str = "linear"
    idle_s: float = 1.5
    delta: float = 0.8
    clutch: str = "always"
    sprint: str = "none"
    sprint_at: float = 95.0
    feedback: str = "off"
    focus_release: bool = True      # losing focus / changing the program zeroes the stick
    center: tuple = None            # neutral center: gravity vector [g] from calibration; None = pad lies flat


def normalize(raw):
    """Dict from the file -> Config; a bad value = the default (message in the log), unknown keys are skipped."""
    raw = raw if isinstance(raw, dict) else {}
    base = Config()
    values = {}
    for f in fields(Config):
        if f.name not in raw:
            continue
        v, default = raw[f.name], getattr(base, f.name)
        try:
            if f.name == "center":
                values[f.name] = _center(v)
            elif f.name in LIMITS:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
                    raise ValueError("expected a number")
                values[f.name] = round(_clamp(float(v), f.name), 3)
            elif isinstance(default, bool):
                if not isinstance(v, bool):
                    raise ValueError("expected true / false")
                values[f.name] = v
            else:
                allowed = {"target": TARGETS, "curve": CURVES, "feedback": FEEDBACKS, "clutch": CLUTCHES,
                           "sprint": SPRINTS}[f.name]
                if not isinstance(v, str) or v not in allowed:
                    raise ValueError(f"expected one of {tuple(allowed)}")
                values[f.name] = v
        except (ValueError, TypeError) as e:
            print(f"Gyro stick: bad {f.name} = {v!r} ({e}) - using {default!r}")
    cfg = replace(base, **values)
    return replace(cfg, outer=max(cfg.outer, round(cfg.deadzone + MIN_SPAN, 3)))


def _center(v):
    """[x, y, z] in g (length about 1) or None."""
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 3 or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in v):
        raise ValueError("expected [x, y, z]")
    c = tuple(float(x) for x in v)
    if not all(math.isfinite(x) for x in c) or not 0.6 <= math.sqrt(sum(x * x for x in c)) <= 1.4:
        raise ValueError("expected a gravity vector of about 1 g")
    return tuple(round(x, 5) for x in c)


def to_raw(cfg):
    """Config -> dict for the JSON file (the center only when calibrated)."""
    out = {f.name: getattr(cfg, f.name) for f in fields(Config)}
    if cfg.center is None:
        del out["center"]
    else:
        out["center"] = list(cfg.center)
    return out


def to_i16(v):
    """-1..1 -> -32768..32767 (the range from the feature spec; the DS4 / DualSense report has 8 bits, XInput / vJoy 16)."""
    return max(-32768, min(I16_MAX, round(v * (32768 if v < 0 else I16_MAX))))


# ---------------- geometria ----------------
def tilt_basis(center):
    """Neutral center (gravity vector in g or None = pad flat) -> (uy, uz, pitch0, roll0).
    (uy, uz) is the "up" direction in the plane perpendicular to the pad's X axis, pitch0 / roll0 are the center angles."""
    cy, cz = (center[1], center[2]) if center else (1.0, 0.0)
    n = math.hypot(cy, cz)
    if n < 0.35:                         # center saved with the pad on its edge - the angles make no sense: treat as flat
        cy, cz, n, center = 1.0, 0.0, 1.0, None
    uy, uz = cy / n, cz / n
    p0, r0 = angles(*center, uy, uz) if center else (0.0, 0.0)
    return uy, uz, p0, r0


def angles(ax, ay, az, uy, uz):
    """Gravity vector [g] -> (pitch, roll) in degrees in the frame given by (uy, uz): positive pitch = front
    down, positive roll = right side down. Pitch is a rotation about the X axis, roll is measured from the "up" direction."""
    gu = ay * uy + az * uz                       # the "up" component (the neutral direction in the YZ plane)
    gf = az * uy - ay * uz                       # the "back" component; front down = more "back" points up
    return math.degrees(math.atan2(gf, math.hypot(ax, gu))), math.degrees(math.atan2(-ax, gu))


def curve(m, name):
    """Response curve 0..1 -> 0..1: linear, cubic (precision at small tilts), aggressive (fast start)."""
    if name == "cubic":
        return m * m * m
    if name == "aggressive":
        return math.sqrt(m)
    return m


def to_u8(v):
    """-1..1 -> DS4 report stick byte (128 = center)."""
    return max(0, min(255, 128 + round(v * 127)))


def drive_state(s, gs):
    """Applies the gyro output `gs` = (target, x, y, mask) to the output state `s` (an rx_state dict; call under the lock).
    A target taken by the gyro ignores the physical input; D-pad: a direction from half deflection (DPAD_AT); y > 0 = up."""
    target, x, y, mask = gs
    if target == "left":
        s["lx"], s["ly"] = to_u8(x), to_u8(-y)
    elif target == "right":
        s["rx"], s["ry"] = to_u8(x), to_u8(-y)
    elif target == "dpad":
        s["hx"] = 1 if x >= DPAD_AT else -1 if x <= -DPAD_AT else 0
        s["hy"] = -1 if y >= DPAD_AT else 1 if y <= -DPAD_AT else 0
    if mask:
        s["mask"] |= mask


def sprint_mask(name):
    """Virtual sprint button name -> report bit mask (0 = none)."""
    code = BUTTON_CODES.get(name)
    return BIT[code] if code else 0


# ---------------- sink ----------------
class Sink:
    """Warstwa abstrakcji wirtualnego pada: silnik zna tylko ten interfejs, nie ViGEm / HIDMaestro / uinput.
    drive(): the gyro takes over the `target` input ("left" | "right" | "dpad") with x, y in -1..1 (y > 0 = up) and holds
    the `button` (bit mask, 0 = none); release(): the gyro gives the input back (the physical one works again)."""

    def drive(self, slot, target, x, y, button):
        raise NotImplementedError

    def release(self, slot):
        raise NotImplementedError


# ---------------- state machine ----------------
ST_OFF, ST_HOLD, ST_SAFE, ST_IDLE, ST_CAL, ST_RUN = range(6)   # off / clutch released / waiting for a return to zero /
#                                                                 asleep / calibrating / driving
Z_DEAD, Z_ACTIVE, Z_OUTER = range(3)                            # deflection zone
EV_NONE, EV_DEAD_OUT, EV_DEAD_IN, EV_OUTER_IN, EV_OUTER_OUT = range(5)   # crossings of zone thresholds (signalling)

HYST_DEG = 0.4            # leaving the deadzone only this far past its edge (sensor noise at the boundary)
OUTER_LEAVE = 0.9         # the outer zone ends below 90 % of the range
SPRINT_HYST = 0.05
REARM_DEG = 2.0           # after zeroing (focus, profile, sleep) the stick moves again only when the pad is back below max(deadzone, 2°)
STILL_DPS, STILL_G = 0.5, 0.004   # "still": rotation below 0.5 °/s and the accelerometer within 0.004 g (a pad on a table;
#                                   held in a hand it shakes more, so it doesn't fall asleep with a held tilt)
WAKE_DPS, WAKE_G = 2.0, 0.02      # a sleeping pad is woken only by clear movement
CAL_S, CAL_MIN_SAMPLES, CAL_MAX_DPS = 0.4, 20, 12.0   # center calibration: averaging time, minimum samples, highest rotation
MAX_DT = 0.05
FORCE = ("force",)        # marker: the next output must be sent regardless of the delta threshold (target change etc.)


class Engine:
    """State of one pad. step() once per sample (250 Hz thread): -> True when `out` has to be sent to the sink.
    out = None (the gyro doesn't drive - the physical input works) or (x, y, sprint) with x, y in -1..1 (y > 0 = up).
    Preview fields: state, zone, raw / filt [°], xy (with the curve), mag (0..1 before the curve), event (threshold crossing)."""

    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self._cfg = None                 # the config the cache was computed for (_apply)
        self._basis = tilt_basis(None)
        self.t = 0.0
        self.armed = False               # without a return to zero there is no stick jump after enabling / returning to the window
        self.asleep = False
        self.dead = True
        self.filt = None
        self.zone = Z_DEAD
        self.sprint = False
        self.state = ST_OFF
        self.raw = self.filt_xy = self.xy = (0.0, 0.0)
        self.mag = 0.0
        self.out = None
        self.sent = None
        self.send = False
        self.event = EV_NONE
        self._ref = None                 # reference sample for stillness detection
        self._since = 0.0
        self._cal = None                 # [end, sx, sy, sz, n, max_dps] during center calibration
        self.cal_result = None           # (ok, center | None) after calibration - collected by rx_gstick_io

    # ---------- control from outside ----------
    @property
    def calibrating(self):
        return self._cal is not None

    def start_center(self, now, secs=CAL_S):
        """Center calibration: averages the pad position for `secs` s (result: cal_result)."""
        self.cal_result = None
        self._cal = [now + secs, 0.0, 0.0, 0.0, 0, 0.0]

    def release(self, rearm=True):
        """Hard clear: the gyro gives the input back, the filter and states are zeroed; rearm - the stick moves again only after
        the pad returns near the center (no jump after a window / profile change)."""
        if rearm:
            self.armed = False
        self.sent = None
        self._neutral(ST_SAFE)

    def snapshot(self):
        """Tuple for the window preview: (state, zone, raw °, filtered °, output -1..1, mag 0..1, sprint, driving)."""
        return (self.state, self.zone, self.raw, self.filt_xy, self.xy, self.mag, self.sprint, self.out is not None)

    # ---------- internals ----------
    def _apply(self, cfg):
        """New config: recompute the neutral center; a change of target / enabled / sprint = a full reset."""
        old, self._cfg = self._cfg, cfg
        if old is None or old.center != cfg.center:
            self._basis = tilt_basis(cfg.center)
        if old is None or (old.enabled, old.target, old.sprint) != (cfg.enabled, cfg.target, cfg.sprint):
            self.armed = False
            self.sprint = False
            self.sent = FORCE

    def _neutral(self, state):
        """The gyro doesn't drive: the physical input works, the preview is zero."""
        self.state, self.out = state, None
        self.filt = None
        self.dead, self.zone, self.sprint, self.mag = True, Z_DEAD, False, 0.0
        self.raw = self.filt_xy = self.xy = (0.0, 0.0)
        return self._decide()

    def _decide(self):
        """Whether to send `out`: a change above the delta threshold; always zero (snap-back), the edge of the range, a button
        change, taking and giving back the input."""
        out, sent = self.out, self.sent
        if sent is FORCE:
            send = True
        elif out == sent:
            send = False
        elif out is None or sent is None:
            send = True
        else:
            x, y, sp = out
            send = (sp != sent[2] or (x == 0.0 and y == 0.0) or x * x + y * y >= 0.998
                    or max(abs(x - sent[0]), abs(y - sent[1])) >= self._cfg.delta / 100)
        if send:
            self.sent = out
        self.send = send
        return send

    def _calibrate(self, now, ax, ay, az, gdps, alive):
        c = self._cal
        c[1] += ax
        c[2] += ay
        c[3] += az
        c[4] += 1
        c[5] = max(c[5], gdps)
        if now >= c[0] or not alive:
            self._cal = None
            n = c[4]
            if alive and n >= CAL_MIN_SAMPLES and c[5] <= CAL_MAX_DPS:
                try:
                    self.cal_result = (True, _center((c[1] / n, c[2] / n, c[3] / n)))
                except ValueError:
                    self.cal_result = (False, None)
            else:
                self.cal_result = (False, None)
            self.armed = False           # new center: the stick starts from zero, not from a jump
        return self._neutral(ST_CAL)

    def _still(self, now, ax, ay, az, gdps, idle_s):
        """Auto-sleep: a pad that stays still (lies) longer than idle_s -> True (zero on the stick); clear movement wakes it."""
        ref = self._ref
        dev = 1e9 if ref is None else max(abs(ax - ref[0]), abs(ay - ref[1]), abs(az - ref[2]))
        if not idle_s:
            self.asleep = False
        if self.asleep:
            if gdps > WAKE_DPS or dev > WAKE_G:
                self.asleep, self._ref, self._since = False, (ax, ay, az), now
                self.armed = False       # a picked-up tilted pad doesn't send a stick jump
            return self.asleep
        if not idle_s or gdps > STILL_DPS or dev > STILL_G:
            self._ref, self._since = (ax, ay, az), now
        elif now - self._since >= idle_s:
            self.asleep = True
        return self.asleep

    def step(self, now, ax, ay, az, gdps, held, alive=True):
        """One sample: now [s, monotonic clock], acc [g], gdps = rotation magnitude [°/s], held = clutch held,
        alive = pad connected. -> True when `out` has to be sent (`sent` itself is already updated)."""
        cfg = self.cfg
        if cfg is not self._cfg:
            self._apply(cfg)
        self.event = EV_NONE
        dt = min(MAX_DT, max(0.0005, now - self.t)) if self.t else 0.004
        self.t = now
        if self._cal is not None:
            return self._calibrate(now, ax, ay, az, gdps, alive)
        if not (cfg.enabled and alive):
            return self._neutral(ST_OFF)
        g2 = ax * ax + ay * ay + az * az
        if not 0.09 < g2 < 9.0:          # free fall / impact: the accelerometer doesn't measure tilt - no change
            self.send = False
            return False
        uy, uz, p0, r0 = self._basis
        p, r = angles(ax, ay, az, uy, uz)
        pitch, roll = p - p0, r - r0
        vx = -roll if cfg.invert_roll else roll
        vy = (-pitch if cfg.invert_pitch else pitch) * cfg.ratio
        if not held:
            send = self._neutral(ST_HOLD)
            self.raw = (vx, vy)          # the preview shows the tilt also with the clutch released
            self._ref = None             # after the clutch is pressed the auto-sleep countdown starts over
            return send
        if self._still(now, ax, ay, az, gdps, cfg.idle_s):
            return self._neutral(ST_IDLE)
        raw_r = math.hypot(vx, vy)
        dz = cfg.deadzone
        if not self.armed:
            if raw_r >= max(dz, REARM_DEG):
                send = self._neutral(ST_SAFE)
                self.raw = (vx, vy)
                return send
            self.armed = True
        self.state = ST_RUN
        self.raw = (vx, vy)
        # snap-back: inside the deadzone the output is zero at once and the filter follows the position (no trail)
        if self.dead:
            if raw_r > dz + HYST_DEG:
                self.dead = False
        elif raw_r < dz:
            self.dead = True
        if self.dead:
            self.filt = (vx, vy)
        else:
            tau = cfg.smoothing / 100 * SMOOTH_MAX_S
            a = 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0
            fx, fy = self.filt if self.filt is not None else (vx, vy)
            self.filt = (fx + a * (vx - fx), fy + a * (vy - fy))
        fx, fy = self.filt
        self.filt_xy = (fx, fy)
        fr = math.hypot(fx, fy)
        m = 0.0 if self.dead or fr <= dz else min(1.0, (fr - dz) / (cfg.outer - dz))
        mc = curve(m, cfg.curve)
        self.xy = (fx / fr * mc, fy / fr * mc) if m > 0 else (0.0, 0.0)
        self.mag = m
        self._zones(m, cfg)
        self.out = (self.xy[0], self.xy[1], self.sprint) if cfg.target != "none" else None
        return self._decide()

    def _zones(self, m, cfg):
        """Deflection zone (with edge hysteresis), the crossing event and the sprint state."""
        prev = self.zone
        zone = Z_DEAD if m <= 0 else Z_OUTER if m >= 1.0 or (prev == Z_OUTER and m >= OUTER_LEAVE) else Z_ACTIVE
        self.zone = zone
        if zone != prev:
            self.event = (EV_OUTER_IN if zone == Z_OUTER else EV_DEAD_IN if zone == Z_DEAD
                          else EV_DEAD_OUT if prev == Z_DEAD else EV_OUTER_OUT)
        at = cfg.sprint_at / 100
        self.sprint = cfg.sprint != "none" and (m >= at - (SPRINT_HYST if self.sprint else 0.0))
