"""Sender: pad rumble - through the driver (evdev FF) or directly with an HID report (hidraw).

The hid-playstation driver supports rumble only with the kernel option CONFIG_PLAYSTATION_FF - phone kernels
(e.g. postmarketOS) often lack it, and then the pad does not rumble at all. The "hidraw" mode sends the pad the same
output report the driver would send (DS4 compatibility mode: two motors), with the rumble and lightbar-colour flags -
the colour travels in the same report, because a separate driver report (changing the colour through sysfs) at the
same moment made the pad accept only one of them. The player LEDs are left untouched.

  rumble = "auto"    both at once: evdev FF (if the pad has it) and the HID report (default)
           "evdev" / "hidraw" / "off"

The report: USB 0x02 (63 B), Bluetooth 0x31 (78 B: sequence number, tag 0x10, CRC32 with seed 0xA2 at the end).
The same report carries the trigger effects (adaptive triggers) from the profile on the PC: resistance, a click (like
a weapon trigger), trigger vibration - games through the virtual DS4 cannot set them, and the kernel driver has no such
function. The pad keeps a trigger effect until the next change; reports without trigger flags (rumble, colour, the
driver) do not touch it.
Game rumble mapped onto the triggers (RFX_ON in trig2) is computed here, from the same rumble packets - no extra
network traffic: L2 vibrates by the large motor, R2 by the small one; while it vibrates it replaces the trigger effect,
and RFX_HOLD_S after the rumble stops the trigger returns to its own effect.
Effects set by a game on the virtual DualSense (HIDMaestro, trig3 packet) go to the pad raw (11 B, as the game sent
them) - on a trigger for which the profile sets nothing (the profile wins).
The pad keeps the last motor strength - the receiver refreshes rumble every 0.5 s, and the watchdog here stops it when
the refreshes cease (lost packets, a closed game), so the pad does not rumble forever.
"""
import asyncio
import errno
import glob
import os
import re
import stat
import time
import zlib

from evdev import ecodes as ec
from evdev import ff

from proto import (CAP_TRIG_FX, CAP_TRIG_RAW, CAP_TRIGGERS, RFX_GAIN_MAX, RFX_HOLD_S, RFX_ON, TFX_CLICK,
                   TFX_OFF, TFX_RES, TFX_VIB, TRG3_L2, TRG3_R2, TRIG2_OFF, TRIG_AMP_MAX, TRIG_CLICK_MAX,
                   TRIG_CLICK_MIN, TRIG_FORCE_MAX, TRIG_FREQ_MAX, TRIG_START_MAX, WHY_ACCESS, WHY_NO_HIDRAW,
                   WHY_NOT_FOUND, WHY_OFF, WHY_OK, WHY_OPEN, trig2_from_legacy)

FLAG0_COMPAT_VIBRATION, FLAG0_HAPTICS_SELECT = 0x01, 0x02
FLAG0_RIGHT_TRIGGER, FLAG0_LEFT_TRIGGER = 0x04, 0x08    # valid_flag0: the right / left trigger effect from this report
RIGHT_TRIGGER_AT, LEFT_TRIGGER_AT, TRIGGER_LEN = 10, 21, 11
TRIG_FEEDBACK, TRIG_OFF = 0x21, 0x05      # trigger effects: "Feedback" (resistance from a zone) and off
TRIG_WEAPON, TRIG_VIBRATION = 0x25, 0x26  # "Weapon" (resistance up to a point and a click) and "Vibration" (vibration from a zone)
FLAG2_COMPAT_VIBRATION2 = 0x04            # newer pad firmware (like use_vibration_v2 in the kernel)
FLAG1_LIGHTBAR = 0x04                     # valid_flag1: the lightbar colour from this report
LIGHTBAR_AT = 44                          # lightbar_red / green / blue in the common part of the report
REPEAT_S = 0.02                           # a rumble / colour change is sent a second time after this many seconds
OUT_TAG = 0x10
CRC_SEED = b"\xa2"
# write error: this many consecutive errors before a rumble method is switched off. A single EIO / ETIMEDOUT on a
# clogged Bluetooth link is not a failure - earlier it switched off the HID report (rumble, colour, triggers) until the pad reconnected.
# A pad that is gone (ENODEV...) and an error that will not change (no kernel support, a bad report, permissions)
# switch it off at once.
FAIL_MAX = 5
FATAL_ERRNO = frozenset({errno.ENODEV, errno.ENOENT, errno.ENXIO, errno.EBADF, errno.ENOSYS, errno.EOPNOTSUPP,
                         errno.EINVAL, errno.EACCES, errno.EPERM})
WATCHDOG_S = 1.5
# trigger effects from the PC: the receiver refreshes them every 2 s (rx_feedback.TRIG_REFRESH_S); after this many seconds without a refresh
# (the receiver closed without cleaning up, a sleeping / switched-off PC) the triggers go back to normal - the pad keeps an effect forever
TRIG_WATCHDOG_S = 7.0
EFFECT_MS = 1000
_HID_DIR = re.compile(r"^([0-9A-Fa-f]{4}):[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")
BUS_BLUETOOTH = 0x0005
FW_REPORT, FW_SIZE, FW_VERSION_AT = 0x20, 64, 44   # the feature report with the firmware version (as in hid-playstation)
VIBRATION_V2_FROM = 0x0221                         # DS_FEATURE_VERSION(2, 21)


def _hidiocgfeature(size):
    """Build the HIDIOCGFEATURE ioctl number (read an HID feature report of `size` bytes)."""
    return (3 << 30) | (size << 16) | (ord("H") << 8) | 0x07


def firmware_version(fd):
    """The pad's "update" firmware version from feature report 0x20, or None (no access / another pad).

    The version is a little-endian 16-bit value at a fixed offset of the report; the first byte echoes the report id,
    which doubles as a sanity check that the pad really answered with the expected report.
    """
    import array
    import fcntl
    buf = array.array("B", [FW_REPORT] + [0] * (FW_SIZE - 1))
    try:
        fcntl.ioctl(fd, _hidiocgfeature(FW_SIZE), buf, True)
    except OSError:
        return None
    return buf[FW_VERSION_AT] | buf[FW_VERSION_AT + 1] << 8 if buf[0] == FW_REPORT else None


OFF_EFFECT = bytes([TRIG_OFF]) + bytes(TRIGGER_LEN - 1)    # the Off effect: a trigger without resistance


def trigger_effect(start, force):
    """Constant trigger resistance (the "Feedback" effect 0x21, 11 B): from zone `start` (0-9, a zone = 10 % of the
    travel) to the end, strength `force` 1-8 in all those zones; force 0 -> the Off effect (a trigger without
    resistance). Bits: 10 active zones (2 B) and 3 bits of strength per zone (4 B), little endian."""
    if not force or not 0 <= start <= TRIG_START_MAX:
        return OFF_EFFECT
    out = bytearray(TRIGGER_LEN)
    out[0] = TRIG_FEEDBACK
    out[1:3] = _zones(start).to_bytes(2, "little")
    out[3:7] = _levels(start, min(force, TRIG_FORCE_MAX) - 1)
    return bytes(out)


def _zones(start):
    """Bit mask of the zones from `start` to the end of the travel (10 zones)."""
    return sum(1 << z for z in range(start, 10))


def _levels(start, level):
    """3 bits of level (0-7) for each zone from `start` (4 B, little endian)."""
    return sum(level << (3 * z) for z in range(start, 10)).to_bytes(4, "little")


def weapon_effect(pos, force):
    """A click like a weapon trigger (the "Weapon" effect 0x25): resistance from zone `pos` (2-7) builds up to zone
    pos + 1 and releases there (the click); strength 1-8; force 0 or a bad point -> Off."""
    if not force or not TRIG_CLICK_MIN <= pos <= TRIG_CLICK_MAX:
        return OFF_EFFECT
    out = bytearray(TRIGGER_LEN)
    out[0] = TRIG_WEAPON
    out[1:3] = ((1 << pos) | (1 << (pos + 1))).to_bytes(2, "little")
    out[3] = min(force, TRIG_FORCE_MAX) - 1
    return bytes(out)


def vibration_effect(start, amp, freq):
    """Trigger vibration (the "Vibration" effect 0x26) from zone `start` (0-9) to the end: amplitude 1-8, frequency 1-255 Hz;
    amplitude or frequency 0 -> Off."""
    if not amp or not freq or not 0 <= start <= TRIG_START_MAX:
        return OFF_EFFECT
    out = bytearray(TRIGGER_LEN)
    out[0] = TRIG_VIBRATION
    out[1:3] = _zones(start).to_bytes(2, "little")
    out[3:7] = _levels(start, min(amp, TRIG_AMP_MAX) - 1)
    out[9] = min(freq, TRIG_FREQ_MAX)
    return bytes(out)


def effect(mode, a, b, c):
    """A trigger effect from the trig2 packet (mode TFX_*, parameters a, b, c - described in proto) -> the 11 B of the report."""
    if mode == TFX_RES:
        return trigger_effect(a, b)
    if mode == TFX_CLICK:
        return weapon_effect(a, b)
    if mode == TFX_VIB:
        return vibration_effect(a, b, c)
    return OFF_EFFECT


def game_effect(raw):
    """A trigger effect set by a game (11 B from the virtual DualSense's output report) or None -> the 11 B of the report.
    Mode 0 (the game set nothing in this byte) = Off - the pad does not keep an effect from before the change."""
    return bytes(raw) if raw is not None and len(raw) == TRIGGER_LEN and raw[0] else OFF_EFFECT


def split_triggers(vals):
    """A trig (4), trig2 (12) or trig3 (12 + game effects L2, R2 + flags) packet -> (the trig2 values, (L2, R2) game
    effects: raw 11 B or None)."""
    vals = tuple(vals)
    if len(vals) == 4:
        return trig2_from_legacy(vals), (None, None)
    if len(vals) == len(TRIG2_OFF):
        return vals, (None, None)
    flags = vals[14]
    return vals[:12], tuple(bytes(raw) if flags & bit else None for raw, bit in ((vals[12], TRG3_L2), (vals[13], TRG3_R2)))


def rumble_amp(motor, gain):
    """Motor strength 0-255 and gain [%] -> trigger vibration amplitude 0-8 (0 = the trigger has no game vibration).
    The product is scaled to 0..TRIG_AMP_MAX with integer rounding (12750 = half of 25500 = 255 * 100)."""
    return min(TRIG_AMP_MAX, (motor * min(gain, RFX_GAIN_MAX) * TRIG_AMP_MAX + 12750) // 25500)


def output_report(bt, seq, small, large, v2=None, rgb=None, triggers=None):
    """The DualSense output report: rumble and (optionally) the lightbar colour and the trigger resistance. small = the right
    motor (weak), large = the left (strong), 0..255. v2: True = pad firmware >= 2.21 (the "vibration v2" flag), False =
    older, None = unknown -> both flags (like the kernel driver: older pads require the v1 flag, newer ones v2).
    triggers: (effect L2, effect R2) from trigger_effect() or None = triggers unchanged.

    Layout: a 47-byte "common" part (valid flags, motors, trigger effects, lightbar) is wrapped differently per bus. USB:
    report id 0x02 + common + padding. Bluetooth: report id 0x31, sequence number in the high nibble, tag 0x10,
    common + padding, then a CRC32 (seeded with 0xA2) over everything before it."""
    common = bytearray(47)
    common[0] = FLAG0_HAPTICS_SELECT | (0 if v2 else FLAG0_COMPAT_VIBRATION)
    if triggers is not None:
        common[0] |= FLAG0_RIGHT_TRIGGER | FLAG0_LEFT_TRIGGER
        common[LEFT_TRIGGER_AT:LEFT_TRIGGER_AT + TRIGGER_LEN] = triggers[0]
        common[RIGHT_TRIGGER_AT:RIGHT_TRIGGER_AT + TRIGGER_LEN] = triggers[1]
    common[2], common[3] = small & 0xFF, large & 0xFF        # motor_right, motor_left
    common[38] = FLAG2_COMPAT_VIBRATION2 if v2 is not False else 0   # valid_flag2
    if rgb is not None:
        common[1] = FLAG1_LIGHTBAR
        common[LIGHTBAR_AT:LIGHTBAR_AT + 3] = bytes(v & 0xFF for v in rgb)
    if not bt:
        return bytes([0x02]) + bytes(common) + bytes(15)
    rep = bytearray([0x31, (seq & 0x0F) << 4, OUT_TAG]) + common + bytes(24)
    crc = zlib.crc32(CRC_SEED + bytes(rep)) & 0xFFFFFFFF
    return bytes(rep) + crc.to_bytes(4, "little")


def _uevent(path):
    """Parse a sysfs uevent file (KEY=VALUE lines) into a dict; empty on any read error."""
    try:
        with open(path) as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return {}


def _by_uniq(uniq, sys_root):
    """Fallback: the hidraw whose HID device has the same HID_UNIQ (the pad's MAC address) - used when the sysfs path from
    eventN to the HID device is unusual (another kernel / Bluetooth stack). Returns (path, is_bluetooth)."""
    uniq = (uniq or "").lower()
    if not uniq:
        return None, False
    for raw in sorted(glob.glob(f"{sys_root}/class/hidraw/hidraw*")):
        ev = _uevent(os.path.join(raw, "device", "uevent"))
        if ev.get("HID_UNIQ", "").lower() == uniq:
            bus = int(ev.get("HID_ID", "0:0:0").split(":")[0], 16)
            return f"/dev/{os.path.basename(raw)}", bus == BUS_BLUETOOTH
    return None, False


def find_hidraw(pad_dev, sys_root="/sys"):
    """Return (/dev/hidrawN, is Bluetooth) for the same HID device as the pad's evdev node, or (None, False).

    Starting from the resolved sysfs path of the input node, it walks up the parents until it reaches the HID device
    directory (named like 0005:054C:0CE6.0001) and looks for a hidraw child there; the bus number in that name tells
    Bluetooth (0005) from USB. If the walk finds nothing, it falls back to matching by HID_UNIQ.
    """
    try:
        node = os.path.realpath(f"{sys_root}/class/input/{os.path.basename(pad_dev.path)}")
    except (OSError, AttributeError):
        node = "/"
    hid = node
    while hid not in ("/", "") and not _HID_DIR.match(os.path.basename(hid)):
        hid = os.path.dirname(hid)
    if hid not in ("/", ""):
        found = sorted(glob.glob(os.path.join(hid, "hidraw", "hidraw*")))
        if found:
            bus = int(_HID_DIR.match(os.path.basename(hid)).group(1), 16)
            return f"/dev/{os.path.basename(found[0])}", bus == BUS_BLUETOOTH
    return _by_uniq(getattr(pad_dev, "uniq", ""), sys_root)


def kernel_has_hidraw(sys_root="/sys"):
    """Whether the kernel has hidraw (CONFIG_HIDRAW): the hidraw device class exists in sysfs."""
    return os.path.isdir(f"{sys_root}/class/hidraw")


def ensure_node(path, sys_root="/sys"):
    """When /dev/hidrawN is missing although the device exists in sysfs (a system without udev / mdev) -> mknod using the
    numbers from sysfs (root only). No exceptions - an error will show up when the node is opened."""
    if os.path.exists(path):
        return
    try:
        with open(f"{sys_root}/class/hidraw/{os.path.basename(path)}/dev") as f:
            major, minor = map(int, f.read().split(":"))
        os.mknod(path, stat.S_IFCHR | 0o600, os.makedev(major, minor))
        print(f"Created missing device node {path}")
    except (OSError, ValueError):
        pass


def has_ff(dev):
    """Whether the evdev device advertises the FF_RUMBLE force-feedback effect (the kernel driver supports rumble)."""
    try:
        return ec.FF_RUMBLE in dev.capabilities().get(ec.EV_FF, [])
    except (OSError, AttributeError):
        return False


WHY_TEXT = {WHY_OK: "ok", WHY_OFF: "rumble = off / evdev in config",
            WHY_ACCESS: "no permission for /dev/hidraw - run with doas or install the service (doas ./install-service.sh)",
            WHY_NO_HIDRAW: "kernel without CONFIG_HIDRAW",
            WHY_NOT_FOUND: "hidraw of this pad not found", WHY_OPEN: "cannot open hidraw"}


class Rumble:
    """Rumble of one pad. set(small, large) on every feedback packet; close() on disconnect.

    In "auto" mode BOTH ways work at once if they are available: the driver (evdev FF) and the HID report (hidraw).
    The same motor strength sent both ways does no harm, and rumble works also when one of them silently does nothing
    (a kernel without full rumble support, an unusual Bluetooth stack). A way that reports an error is switched off.

    Besides rumble, the HID report path also carries the lightbar colour and the adaptive-trigger effects. Several
    timers keep the pad safe: a repeat resends a change once more shortly after (a lost report must not leave the
    pad silent), a watchdog stops the motors when refreshes cease, and a trigger watchdog releases the triggers."""

    def __init__(self, idx, pad_dev, mode="auto", hidraw=None):
        self.idx, self.dev = idx, pad_dev
        self.methods = []             # "evdev" and/or "hidraw" - in this order
        self.fd, self.bt, self.seq, self.ff_id, self.path = None, False, 0, None, None
        self.v2, self.fw = None, None
        self.why = WHY_OFF if mode in ("off", "evdev") else WHY_OK   # the reason for no HID report (packet to the receiver)
        self.logged = 0               # how many of the first rumble commands to write to the log (diagnostics)
        self.fails = {}               # method -> number of consecutive write errors (FAIL_MAX)
        self.cur = (0, 0)
        self.rgb = None               # the lightbar colour sent together with rumble (HID report only)
        self.trig = None              # trigger effects (L2, R2) sent to the pad, or None = never touched
        self.fx = None                # trigger settings from the PC (trig2 values) or None = nothing arrived
        self.game_fx = (None, None)   # L2 / R2 trigger effects set by the game (raw 11 B) or None
        self._rv = [None, None]       # game rumble on L2 / R2: an effect or None
        self._rv_off = [None, None]   # since when this trigger's motor has been still (after RFX_HOLD_S the trigger returns to its effect)
        self._rv_timer = None
        self._trig_watch = None       # watchdog of trigger effects (TRIG_WATCHDOG_S without a refresh from the PC -> switched off)
        self.t_set = 0.0
        self._watch = None
        self._repeat = None
        if mode in ("auto", "evdev") and has_ff(pad_dev):
            self.methods.append("evdev")
        if mode in ("auto", "hidraw") and self._open_hidraw(hidraw):
            self.methods.append("hidraw")
        print(f"Controller {idx}: rumble " + (self.describe() if self.methods else
                                              f"unavailable (no evdev FF; {WHY_TEXT[self.why]})"
                                              if mode != "off" else "off"))

    @property
    def mode(self):
        """The main way (for logs and tests): the first working one, or None."""
        return self.methods[0] if self.methods else None

    @property
    def owns_lightbar(self):
        """The HID report also carries the lightbar colour. A colour sent through the driver (sysfs) goes out in the pad's OWN
        output report at a separate moment - the pad then accepted only one of them, and rumble from the test button (which
        changes the colour) worked only every few presses. One report with both = no race."""
        return "hidraw" in self.methods

    @property
    def bits(self):
        """The ways as bits (the version packet to the receiver): 1 = driver, 2 = HID report, 4 = trigger resistance,
        8 = trigger effects from trig2 (click, vibration, game vibration), 32 = trigger effects from the game (trig3)."""
        hid = "hidraw" in self.methods
        return ("evdev" in self.methods) | hid << 1 | (CAP_TRIGGERS | CAP_TRIG_FX | CAP_TRIG_RAW if hid else 0)

    def describe(self):
        """A short log description of the active rumble ways (driver and/or HID reports with firmware and vibration mode)."""
        parts = []
        if "evdev" in self.methods:
            parts.append("kernel driver (evdev FF)")
        if "hidraw" in self.methods:
            fw = f"firmware {self.fw:04x}" if self.fw is not None else "firmware unknown"
            mode = "v2" if self.v2 else "v1" if self.v2 is False else "v1+v2"
            parts.append(f"direct HID reports ({self.path}, {fw}, vibration {mode})")
        return "via " + " + ".join(parts)

    def _open_hidraw(self, given=None):
        """Locate and open the pad's hidraw node, read its firmware version and decide the vibration flavour. True on success.

        On failure self.why records the reason (no hidraw in the kernel, node not found, no permission, other) so the
        receiver can show it. The firmware version selects vibration v1 / v2 reports (v2 from 2.21).
        """
        path, bt = given or find_hidraw(self.dev)
        if not path:
            self.why = WHY_NOT_FOUND if kernel_has_hidraw() else WHY_NO_HIDRAW
            return False
        if given is None:
            ensure_node(path)
        self._close_fd()
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)   # RDWR: also for reading the firmware version
        except OSError as e:
            self.why = WHY_ACCESS if e.errno in (errno.EACCES, errno.EPERM) else WHY_OPEN
            print(f"Controller {self.idx}: cannot open {path} for rumble: {e.strerror}")
            return False
        self.why = WHY_OK
        self.path, self.bt = path, bt
        self.fw = firmware_version(self.fd)
        self.v2 = None if self.fw is None else self.fw >= VIBRATION_V2_FROM
        return True

    # ---------- control ----------
    def set(self, small, large, rgb=None):
        """Rumble (and the lightbar colour when owns_lightbar). A change goes out at once and a second time after REPEAT_S
        (a lost / overwritten report does not leave the pad without rumble); with no change - a refresh at most every 0.4 s."""
        now = time.monotonic()
        if not self.methods:
            return
        rgb = rgb if rgb is not None else self.rgb
        changed = (small, large) != self.cur or rgb != self.rgb
        if not changed and now - self.t_set < 0.4:
            return                                   # no change and fresh - send nothing
        if self.logged < 3 and (small or large):
            self.logged += 1
            print(f"Controller {self.idx}: rumble {small}/{large} from PC {self.describe()}")
        self.rgb = rgb
        self._send(small, large)
        self.cur, self.t_set = (small, large), now
        self._arm_watchdog()
        if changed:
            self._arm_repeat()

    def set_triggers(self, vals):
        """Trigger effects from the PC: trig3 values (trig2 + game effects), trig2 (12) or the old trig (4: resistance only).
        Returns True when sent to the pad (HID report). The receiver refreshes them every few seconds, so after a reconnect
        the pad returns to the effects from the profile and the game.

        The packet is split into the profile part and the game part, remembered, and the watchdog is re-armed (a
        refresh must keep arriving). The report is then rebuilt with the current motor values so that sending trigger
        effects never changes the rumble."""
        vals, game = split_triggers(vals)
        if (vals, game) != (self.fx, self.game_fx):
            print(f"Controller {self.idx}: triggers L2 {vals[0:4]} R2 {vals[4:8]} game rumble {vals[8:11]}"
                  + "".join(f" game {n} {g.hex()}" for n, g in zip(("L2", "R2"), game, strict=True) if g is not None))
        self.fx, self.game_fx = vals, game
        if not vals[8] & RFX_ON:
            self._rv, self._rv_off = [None, None], [None, None]
        self._arm_trig_watch(any(vals) or game != (None, None))
        if "hidraw" not in self.methods:
            return False
        trig = self._want_triggers(*self.cur)
        try:
            self.seq = (self.seq + 1) & 0x0F
            os.write(self.fd, output_report(self.bt, self.seq, *self.cur, self.v2, self.rgb, trig))
        except (BlockingIOError, InterruptedError):
            return False                    # a full queue - the next refresh from the PC will deliver the effect
        except OSError as e:
            self._failed("hidraw", e)
            return False
        self.fails["hidraw"] = 0
        self.trig = trig
        return True

    def _arm_trig_watch(self, on):
        """(Re)start the trigger-effect watchdog timer when effects are active (`on`); cancel it otherwise."""
        if self._trig_watch is not None:
            self._trig_watch.cancel()
            self._trig_watch = None
        if on:
            try:
                self._trig_watch = asyncio.get_running_loop().call_later(TRIG_WATCHDOG_S, self._trig_expired)
            except RuntimeError:
                pass

    def _trig_expired(self):
        """Watchdog fired: no trigger refresh from the PC for TRIG_WATCHDOG_S - release the triggers (switch all effects off)."""
        self._trig_watch = None
        if self.fx is not None and (any(self.fx) or self.game_fx != (None, None)):
            print(f"Controller {self.idx}: no trigger refresh from PC for {TRIG_WATCHDOG_S:g}s - triggers released")
            self.set_triggers(TRIG2_OFF)

    def _want_triggers(self, small, large, now=None):
        """The effects the pad should have now: from the profile; on a trigger with no profile effect - the game's effect; and
        during game rumble (and RFX_HOLD_S after it) - vibration following the motor (L2 = large, R2 = small). None = the PC
        set nothing (we do not touch the triggers).

        For every trigger the effect is chosen in that order. With game-rumble mapping on, a motor above zero replaces the
        trigger effect with a vibration whose amplitude follows the motor; when the motor stops, the vibration is kept for
        RFX_HOLD_S (so it does not flicker) and then the trigger returns to its own effect."""
        fx = self.fx
        if fx is None:
            return None
        out = [effect(*fx[k:k + 4]) if fx[k] != TFX_OFF else game_effect(g)
               for k, g in zip((0, 4), self.game_fx, strict=True)]
        flags, gain, freq = fx[8:11]
        if not flags & RFX_ON:
            return tuple(out)
        now = time.monotonic() if now is None else now
        hold = False
        for k, motor in enumerate((large, small)):
            amp = rumble_amp(motor, gain)
            if amp:
                self._rv[k], self._rv_off[k] = vibration_effect(0, amp, freq or 1), None
            elif self._rv[k] is not None:
                if self._rv_off[k] is None:
                    self._rv_off[k] = now
                if now - self._rv_off[k] >= RFX_HOLD_S - 0.005:
                    self._rv[k] = self._rv_off[k] = None
                else:
                    hold = True
            if self._rv[k] is not None:
                out[k] = self._rv[k]
        self._arm_rv(hold)
        return tuple(out)

    def _arm_rv(self, hold):
        """After RFX_HOLD_S of silent motors the trigger must return to its effect, even if nothing else arrives from the PC."""
        if self._rv_timer is not None:
            self._rv_timer.cancel()
            self._rv_timer = None
        if hold:
            try:
                self._rv_timer = asyncio.get_running_loop().call_later(RFX_HOLD_S, self._rv_expired)
            except RuntimeError:
                pass

    def _rv_expired(self):
        """Hold time over: resend the report so the trigger falls back to its normal effect."""
        self._rv_timer = None
        if self.methods:
            self._send(*self.cur, only="hidraw")

    def _arm_repeat(self):
        """Schedule one extra send REPEAT_S from now (replacing a pending one) to protect a change against a lost report."""
        if self._repeat is not None:
            self._repeat.cancel()
        try:
            self._repeat = asyncio.get_running_loop().call_later(REPEAT_S, self._send_again)
        except RuntimeError:
            self._repeat = None

    def _send_again(self):
        """Timer callback of _arm_repeat: resend the current rumble over the HID report."""
        self._repeat = None
        if self.methods:
            self._send(*self.cur, only="hidraw")

    def _send(self, small, large, only=None):
        """Send the rumble through every active way (or only `only`), handling errors per way.

        A momentarily full report queue is not a failure. A real error is counted by _failed; if that switches the
        driver way off and no HID path is open yet, the HID report is opened as a replacement and used straight away.
        """
        for m in list(self.methods):
            if only and m != only:
                continue
            try:
                (self._send_hidraw if m == "hidraw" else self._send_evdev)(small, large)
            except (BlockingIOError, InterruptedError):
                pass        # the report queue is momentarily full - not a failure; a repeat / refresh will deliver the state
            except OSError as e:
                if self._failed(m, e) and m == "evdev" and "hidraw" not in self.methods and self._open_hidraw():
                    self.methods.append("hidraw")
                    self._send(small, large, only="hidraw")    # with the same error handling (the exception does not escape to the loop)
            else:
                self.fails[m] = 0

    def _failed(self, m, e):
        """A write error with way m. Returns True when the way was switched off (FAIL_MAX in a row, or the pad is gone)."""
        n = self.fails[m] = self.fails.get(m, 0) + 1
        if n < FAIL_MAX and e.errno not in FATAL_ERRNO:
            if n == 1:
                print(f"Controller {self.idx}: rumble via {m} failed ({e!r}) - retrying")
            return False
        print(f"Controller {self.idx}: rumble via {m} failed ({e!r}) - not using it any more")
        if m in self.methods:
            self.methods.remove(m)
        self.fails[m] = 0
        if m == "hidraw":
            self._close_fd()                          # without this the descriptor leaked (reopening overwrote fd)
        return True

    def _close_fd(self):
        """Close the hidraw file descriptor if one is open."""
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def _send_hidraw(self, small, large):
        """Rumble (and colour); trigger effects only when they are about to change (game rumble on the triggers)."""
        trig = self._want_triggers(small, large)
        if trig == self.trig:
            trig = None
        self.seq = (self.seq + 1) & 0x0F
        os.write(self.fd, output_report(self.bt, self.seq, small, large, self.v2, self.rgb, trig))
        if trig is not None:
            self.trig = trig

    def _send_evdev(self, small, large):
        """Rumble through the kernel force-feedback API: upload (or update) one long rumble effect and start it; stop it for 0/0.

        The effect lasts EFFECT_MS, so the watchdog / refresh logic of the caller decides when it really ends. The
        strong / weak magnitudes are scaled from 0..255 to the 16-bit range (x257).
        """
        if small or large:
            eff = ff.Effect(ec.FF_RUMBLE, -1 if self.ff_id is None else self.ff_id, 0, ff.Trigger(0, 0),
                            ff.Replay(EFFECT_MS, 0),
                            ff.EffectType(ff_rumble_effect=ff.Rumble(strong_magnitude=large * 257,
                                                                     weak_magnitude=small * 257)))
            self.ff_id = self.dev.upload_effect(eff)
            self.dev.write(ec.EV_FF, self.ff_id, 1)
        elif self.ff_id is not None:
            self.dev.write(ec.EV_FF, self.ff_id, 0)

    def _arm_watchdog(self):
        """(Re)start the rumble watchdog: if no refresh arrives within WATCHDOG_S while the motors run, they are stopped."""
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None
        if self.cur != (0, 0):
            try:
                self._watch = asyncio.get_running_loop().call_later(WATCHDOG_S, self._expired)
            except RuntimeError:
                pass

    def _expired(self):
        """Watchdog fired: the receiver stopped refreshing the rumble, so switch the motors off."""
        self._watch = None
        if self.cur != (0, 0) and time.monotonic() - self.t_set >= WATCHDOG_S - 0.05:
            self.set(0, 0)                           # the receiver stopped refreshing - stop the motors

    def close(self):
        """Shut rumble down cleanly: cancel timers, stop the motors, release hard triggers, erase the FF effect, close the fd."""
        for name in ("_watch", "_repeat", "_rv_timer", "_trig_watch"):
            t = getattr(self, name)
            if t is not None:
                t.cancel()
                setattr(self, name, None)
        if self.cur != (0, 0):
            self._send(0, 0)
            self.cur = (0, 0)       # set_triggers below sends a report with the current motor strength - it must not switch it on again
        if self.trig is not None and any(t[0] != TRIG_OFF for t in self.trig):
            self.set_triggers(TRIG2_OFF)         # the sender is closing - a trigger must not stay hard
        self._arm_rv(False)
        if "evdev" in self.methods and self.ff_id is not None:
            try:
                self.dev.erase_effect(self.ff_id)
            except OSError:
                pass
        self._close_fd()
        self.methods, self.cur = [], (0, 0)


# ---------------- command-line test: doas python3 sender_rumble.py --test ----------------
def _test():
    """Rumbles every connected DualSense in turn with every way - without the network and the receiver. It shows whether the problem
    is in the pad / kernel (nothing rumbles) or on the way PC -> sender (it rumbles here, but not from the game)."""
    import evdev
    pads = [evdev.InputDevice(p) for p in evdev.list_devices()]
    pads = [d for d in pads if "Wireless Controller" in d.name and "Motion" not in d.name and "Touchpad" not in d.name]
    if not pads:
        print("No DualSense found (is it connected? run with doas)")
        return
    for d in pads:
        path, bt = find_hidraw(d)
        print(f"== {d.name} {d.uniq or d.phys} ({d.path}): {'Bluetooth' if bt else 'USB'}, hidraw {path}, "
              f"evdev FF {'yes' if has_ff(d) else 'NO (kernel without CONFIG_PLAYSTATION_FF)'}"
              + ("" if kernel_has_hidraw() else ", kernel WITHOUT CONFIG_HIDRAW"))
        steps = [("evdev FF", "evdev", None)] if has_ff(d) else []
        steps += [("HID report v1", "hidraw", False), ("HID report v2", "hidraw", True), ("HID report v1+v2", "hidraw", None)]
        for label, mode, v2 in steps:
            r = Rumble(0, d, mode)
            if r.mode != mode:
                print(f"   {label}: not available")
                continue
            if mode == "hidraw":
                r.v2 = v2
            print(f"   {label}: rumbling 1.5 s ...", flush=True)
            try:
                r._send(255, 255)
                time.sleep(1.5)
                r._send(0, 0)
            except OSError as e:
                print(f"   {label}: ERROR {e!r}")
            r.close()
            time.sleep(0.8)
    print("Which step did you feel? Set rumble = evdev / hidraw in sender.toml if auto picks the wrong one.")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        _test()
    else:
        print("usage: doas python3 sender_rumble.py --test")
