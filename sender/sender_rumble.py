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

import array
import asyncio
import contextlib
import errno
import fcntl
import glob
import os
import re
import stat
import sys
import time
import zlib
from collections.abc import Callable, Sequence
from typing import Any, Final

from evdev import InputDevice
from evdev import ecodes as ec
from evdev import ff

from proto import (CAP_TRIG_FX, CAP_TRIG_RAW, CAP_TRIGGERS, RFX_GAIN_MAX, RFX_HOLD_S, RFX_ON, RUMBLE_EVDEV, RUMBLE_HIDRAW,
                   TFX_CLICK, TFX_OFF, TFX_RES, TFX_VIB, TRG3_L2, TRG3_R2, TRIG2_OFF, TRIG_AMP_MAX, TRIG_CLICK_MAX,
                   TRIG_CLICK_MIN, TRIG_FORCE_MAX, TRIG_FREQ_MAX, TRIG_START_MAX, WHY_ACCESS, WHY_NO_HIDRAW,
                   WHY_NOT_FOUND, WHY_OFF, WHY_OK, WHY_OPEN, trig2_from_legacy)

# Rumble modes (sender.toml) and the ways a rumble can actually be delivered.
MODE_AUTO: Final = "auto"
MODE_OFF: Final = "off"
WAY_EVDEV: Final = "evdev"
WAY_HIDRAW: Final = "hidraw"

# ---- DualSense output report (hid-playstation, struct dualsense_output_report_common) ----
USB_REPORT_ID: Final = 0x02                 # USB: id + common part + padding = 63 B
BT_REPORT_ID: Final = 0x31                  # Bluetooth: id, seq, tag + common part + padding + CRC32 = 78 B
OUT_TAG: Final = 0x10                       # Bluetooth: the tag byte after the sequence number
COMMON_LEN: Final = 47                      # the part shared by both buses
USB_PADDING: Final = 15
BT_PADDING: Final = 24
BT_SEQ_SHIFT: Final = 4                     # the sequence number is the high nibble of the byte after the report id
BT_SEQ_MASK: Final = 0x0F
CRC_SEED: Final = b"\xa2"                   # the CRC32 of a Bluetooth report is seeded with this byte
CRC_LEN: Final = 4
UINT32_MASK: Final = 0xFFFFFFFF
BYTE_MASK: Final = 0xFF
VALID_FLAG0_AT, VALID_FLAG1_AT = 0, 1       # "valid_flag" bytes: which parts of the report the pad should apply
MOTOR_RIGHT_AT, MOTOR_LEFT_AT = 2, 3        # the right (weak) and the left (strong) motor
VALID_FLAG2_AT = 38
RIGHT_TRIGGER_AT, LEFT_TRIGGER_AT, TRIGGER_LEN = 10, 21, 11
LIGHTBAR_AT, RGB_LEN = 44, 3                # lightbar_red / green / blue in the common part
FLAG0_COMPAT_VIBRATION, FLAG0_HAPTICS_SELECT = 0x01, 0x02
FLAG0_RIGHT_TRIGGER, FLAG0_LEFT_TRIGGER = 0x04, 0x08    # valid_flag0: the right / left trigger effect from this report
FLAG1_LIGHTBAR: Final = 0x04                # valid_flag1: the lightbar colour from this report
FLAG2_COMPAT_VIBRATION2: Final = 0x04       # newer pad firmware (like use_vibration_v2 in the kernel)

# ---- trigger effects (11 B each) ----
TRIG_FEEDBACK, TRIG_OFF = 0x21, 0x05        # effects: "Feedback" (resistance from a zone) and off
TRIG_WEAPON, TRIG_VIBRATION = 0x25, 0x26    # "Weapon" (resistance up to a point and a click) and "Vibration" (vibration from a zone)
TRIGGER_ZONES: Final = 10                   # the travel is split into 10 zones of 10 %
LEVEL_BITS: Final = 3                       # strength of a zone: 3 bits (0-7)
EFFECT_ZONES_AT, ZONES_LEN = 1, 2           # the mask of active zones (little endian)
EFFECT_LEVELS_AT, LEVELS_LEN = 3, 4         # 3 bits of strength per zone (little endian)
WEAPON_STRENGTH_AT: Final = 3
VIBRATION_FREQ_AT: Final = 9
OFF_EFFECT: Final = bytes([TRIG_OFF]) + bytes(TRIGGER_LEN - 1)    # the Off effect: a trigger without resistance

# ---- positions in the trig2 / trig3 packet values (see proto) ----
L2_EFFECT_AT, R2_EFFECT_AT, EFFECT_FIELDS = 0, 4, 4     # (mode, a, b, c) for L2, then for R2
RUMBLE_FX_AT = 8                                        # (flags, gain %, frequency Hz) of the game rumble on the triggers
TRIG2_LEN: Final = 12
TRIG3_L2_RAW_AT, TRIG3_R2_RAW_AT, TRIG3_FLAGS_AT = 12, 13, 14
LEGACY_TRIG_LEN: Final = 4

# ---- rumble motors ----
MOTOR_MAX: Final = 255
PERCENT: Final = 100
FF_MAGNITUDE_SCALE: Final = 257             # 0..255 -> 0..65535
EFFECT_MS: Final = 1000                     # an evdev rumble effect lasts this long; refreshes and the watchdog decide the real end

# ---- timers ----
REPEAT_S: Final = 0.02                      # a rumble / colour change is sent a second time after this many seconds
REFRESH_S: Final = 0.4                      # with no change, a refresh goes out at most this often
WATCHDOG_S: Final = 1.5
WATCHDOG_SLACK_S: Final = 0.05              # timers can fire a little early: do not wait for another whole period
HOLD_SLACK_S: Final = 0.005
# trigger effects from the PC: the receiver refreshes them every 2 s (rx_feedback.TRIG_REFRESH_S); after this many seconds without a refresh
# (the receiver closed without cleaning up, a sleeping / switched-off PC) the triggers go back to normal - the pad keeps an effect forever
TRIG_WATCHDOG_S: Final = 7.0
LOGGED_COMMANDS: Final = 3                  # how many of the first rumble commands are written to the log (diagnostics)

# write error: this many consecutive errors before a rumble method is switched off. A single EIO / ETIMEDOUT on a
# clogged Bluetooth link is not a failure - earlier it switched off the HID report (rumble, colour, triggers) until the pad reconnected.
# A pad that is gone (ENODEV...) and an error that will not change (no kernel support, a bad report, permissions)
# switch it off at once.
FAIL_MAX: Final = 5
FATAL_ERRNO: Final = frozenset({errno.ENODEV, errno.ENOENT, errno.ENXIO, errno.EBADF, errno.ENOSYS, errno.EOPNOTSUPP,
                                errno.EINVAL, errno.EACCES, errno.EPERM})

# ---- sysfs / hidraw ----
_HID_DIR = re.compile(r"^([0-9A-Fa-f]{4}):[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$")
BUS_BLUETOOTH: Final = 0x0005
FW_REPORT, FW_SIZE, FW_VERSION_AT = 0x20, 64, 44    # the feature report with the firmware version (as in hid-playstation)
VIBRATION_V2_FROM: Final = 0x0221                   # DS_FEATURE_VERSION(2, 21)
# HIDIOCGFEATURE(len) = _IOC(_IOC_READ | _IOC_WRITE, 'H', 0x07, len), see linux/hidraw.h and asm-generic/ioctl.h
IOC_READ_WRITE, IOC_DIR_SHIFT, IOC_SIZE_SHIFT, IOC_TYPE_SHIFT = 3, 30, 16, 8
HID_IOC_MAGIC, HIDIOCGFEATURE_NR = ord("H"), 0x07
HIDRAW_NODE_MODE: Final = stat.S_IFCHR | 0o600


def _hidiocgfeature(size: int) -> int:
    """Build the HIDIOCGFEATURE ioctl number (read an HID feature report of ``size`` bytes)."""
    return ((IOC_READ_WRITE << IOC_DIR_SHIFT) | (size << IOC_SIZE_SHIFT) | (HID_IOC_MAGIC << IOC_TYPE_SHIFT)
            | HIDIOCGFEATURE_NR)


def firmware_version(fd: int) -> int | None:
    """Read the pad's "update" firmware version from feature report 0x20.

    The version is a little-endian 16-bit value at a fixed offset of the report; the first byte echoes the report id,
    which doubles as a sanity check that the pad really answered with the expected report.

    Args:
        fd: An open hidraw file descriptor.

    Returns:
        The version, or None (no access / another pad).
    """
    buf = array.array("B", [FW_REPORT] + [0] * (FW_SIZE - 1))
    try:
        fcntl.ioctl(fd, _hidiocgfeature(FW_SIZE), buf, True)
    except OSError:
        return None
    return (buf[FW_VERSION_AT] | buf[FW_VERSION_AT + 1] << 8) if buf[0] == FW_REPORT else None


def _zones(start: int) -> int:
    """Bit mask of the zones from ``start`` to the end of the travel."""
    return sum(1 << zone for zone in range(start, TRIGGER_ZONES))


def _levels(start: int, level: int) -> bytes:
    """3 bits of ``level`` (0-7) for each zone from ``start`` (little endian)."""
    return sum(level << (LEVEL_BITS * zone) for zone in range(start, TRIGGER_ZONES)).to_bytes(LEVELS_LEN, "little")


def _zone_effect(effect_id: int, start: int, level: int) -> bytearray:
    """An effect that acts from zone ``start`` to the end of the travel with the same strength ``level`` in every zone."""
    out = bytearray(TRIGGER_LEN)
    out[0] = effect_id
    out[EFFECT_ZONES_AT:EFFECT_ZONES_AT + ZONES_LEN] = _zones(start).to_bytes(ZONES_LEN, "little")
    out[EFFECT_LEVELS_AT:EFFECT_LEVELS_AT + LEVELS_LEN] = _levels(start, level)
    return out


def trigger_effect(start: int, force: int) -> bytes:
    """Build the constant-resistance trigger effect ("Feedback", id 0x21).

    Bits: 10 active zones (2 B) and 3 bits of strength per zone (4 B), little endian.

    Args:
        start: The first zone with resistance, 0-9 (a zone = 10 % of the travel).
        force: The strength 1-8 in all zones from ``start`` to the end; 0 means no resistance.

    Returns:
        The 11 bytes of the effect; the Off effect for force 0 or a bad ``start``.
    """
    if not force or not 0 <= start <= TRIG_START_MAX:
        return OFF_EFFECT
    return bytes(_zone_effect(TRIG_FEEDBACK, start, min(force, TRIG_FORCE_MAX) - 1))


def weapon_effect(pos: int, force: int) -> bytes:
    """Build a click like a weapon trigger ("Weapon", id 0x25).

    Resistance from zone ``pos`` builds up to zone ``pos + 1`` and releases there (the click).

    Args:
        pos: The click point, 2-7.
        force: The strength 1-8.

    Returns:
        The 11 bytes of the effect; the Off effect for force 0 or a bad point.
    """
    if not force or not TRIG_CLICK_MIN <= pos <= TRIG_CLICK_MAX:
        return OFF_EFFECT
    out = bytearray(TRIGGER_LEN)
    out[0] = TRIG_WEAPON
    out[EFFECT_ZONES_AT:EFFECT_ZONES_AT + ZONES_LEN] = ((1 << pos) | (1 << (pos + 1))).to_bytes(ZONES_LEN, "little")
    out[WEAPON_STRENGTH_AT] = min(force, TRIG_FORCE_MAX) - 1
    return bytes(out)


def vibration_effect(start: int, amp: int, freq: int) -> bytes:
    """Build trigger vibration ("Vibration", id 0x26) from zone ``start`` to the end of the travel.

    Args:
        start: The first vibrating zone, 0-9.
        amp: The amplitude 1-8.
        freq: The frequency 1-255 Hz.

    Returns:
        The 11 bytes of the effect; the Off effect for amplitude or frequency 0, or a bad ``start``.
    """
    if not amp or not freq or not 0 <= start <= TRIG_START_MAX:
        return OFF_EFFECT
    out = _zone_effect(TRIG_VIBRATION, start, min(amp, TRIG_AMP_MAX) - 1)
    out[VIBRATION_FREQ_AT] = min(freq, TRIG_FREQ_MAX)
    return bytes(out)


def effect(mode: int, a: int, b: int, c: int) -> bytes:
    """Build the trigger effect from a trig2 packet.

    Args:
        mode: A TFX_* constant.
        a: First mode parameter (described in proto).
        b: Second mode parameter.
        c: Third mode parameter.

    Returns:
        The 11 bytes of the report's trigger effect.
    """
    if mode == TFX_RES:
        return trigger_effect(a, b)
    if mode == TFX_CLICK:
        return weapon_effect(a, b)
    if mode == TFX_VIB:
        return vibration_effect(a, b, c)
    return OFF_EFFECT


def game_effect(raw: bytes | None) -> bytes:
    """Take a trigger effect set by a game (11 B from the virtual DualSense's output report).

    Mode 0 (the game set nothing in this byte) means Off - the pad does not keep an effect from before the change.

    Args:
        raw: The raw 11 bytes, or None.

    Returns:
        The 11 bytes of the report's trigger effect.
    """
    return bytes(raw) if raw is not None and len(raw) == TRIGGER_LEN and raw[0] else OFF_EFFECT


def split_triggers(vals: Sequence[Any]) -> tuple[tuple[int, ...], tuple[bytes | None, bytes | None]]:
    """Split a trig (4), trig2 (12) or trig3 (12 + game effects L2, R2 + flags) packet.

    Args:
        vals: The packet's body fields (ints, and for trig3 two raw 11-byte effects).

    Returns:
        The trig2 values and the (L2, R2) game effects: raw 11 B or None.
    """
    vals = tuple(vals)
    if len(vals) == LEGACY_TRIG_LEN:
        return trig2_from_legacy(vals), (None, None)
    if len(vals) == len(TRIG2_OFF):
        return vals, (None, None)
    flags = vals[TRIG3_FLAGS_AT]
    game = tuple(bytes(raw) if flags & bit else None
                 for raw, bit in ((vals[TRIG3_L2_RAW_AT], TRG3_L2), (vals[TRIG3_R2_RAW_AT], TRG3_R2)))
    return vals[:TRIG2_LEN], (game[0], game[1])


# 255 * 100: a motor strength 0-255 times a gain in percent; half of it is added before the division to round to the nearest
_AMP_FULL_SCALE: Final = MOTOR_MAX * PERCENT
_AMP_HALF: Final = _AMP_FULL_SCALE // 2


def rumble_amp(motor: int, gain: int) -> int:
    """Convert a motor strength to a trigger vibration amplitude (integer rounding).

    Args:
        motor: The motor strength 0-255.
        gain: The gain in percent (capped at RFX_GAIN_MAX).

    Returns:
        The amplitude 0-8; 0 means the trigger has no game vibration.
    """
    return min(TRIG_AMP_MAX, (motor * min(gain, RFX_GAIN_MAX) * TRIG_AMP_MAX + _AMP_HALF) // _AMP_FULL_SCALE)


def output_report(bt: bool, seq: int, small: int, large: int, v2: bool | None = None,
                  rgb: Sequence[int] | None = None, triggers: tuple[bytes, bytes] | None = None) -> bytes:
    """Build the DualSense output report: rumble and (optionally) the lightbar colour and the trigger effects.

    Layout: a 47-byte "common" part (valid flags, motors, trigger effects, lightbar) is wrapped differently per bus. USB:
    report id 0x02 + common + padding. Bluetooth: report id 0x31, sequence number in the high nibble, tag 0x10,
    common + padding, then a CRC32 (seeded with 0xA2) over everything before it.

    Args:
        bt: True for a Bluetooth pad, False for USB.
        seq: The report sequence number (only the low 4 bits are used, Bluetooth only).
        small: The right (weak) motor, 0..255.
        large: The left (strong) motor, 0..255.
        v2: True = pad firmware >= 2.21 (the "vibration v2" flag), False = older, None = unknown, which sets both flags
            (like the kernel driver: older pads require the v1 flag, newer ones v2).
        rgb: The lightbar colour, or None to leave it alone.
        triggers: (effect L2, effect R2), or None = triggers unchanged.

    Returns:
        The report bytes.
    """
    common = bytearray(COMMON_LEN)
    common[VALID_FLAG0_AT] = FLAG0_HAPTICS_SELECT | (0 if v2 else FLAG0_COMPAT_VIBRATION)
    if triggers is not None:
        common[VALID_FLAG0_AT] |= FLAG0_RIGHT_TRIGGER | FLAG0_LEFT_TRIGGER
        common[LEFT_TRIGGER_AT:LEFT_TRIGGER_AT + TRIGGER_LEN] = triggers[0]
        common[RIGHT_TRIGGER_AT:RIGHT_TRIGGER_AT + TRIGGER_LEN] = triggers[1]
    common[MOTOR_RIGHT_AT], common[MOTOR_LEFT_AT] = small & BYTE_MASK, large & BYTE_MASK
    common[VALID_FLAG2_AT] = FLAG2_COMPAT_VIBRATION2 if v2 is not False else 0
    if rgb is not None:
        common[VALID_FLAG1_AT] = FLAG1_LIGHTBAR
        common[LIGHTBAR_AT:LIGHTBAR_AT + RGB_LEN] = bytes(channel & BYTE_MASK for channel in rgb)
    if not bt:
        return bytes([USB_REPORT_ID]) + bytes(common) + bytes(USB_PADDING)
    report = bytearray([BT_REPORT_ID, (seq & BT_SEQ_MASK) << BT_SEQ_SHIFT, OUT_TAG]) + common + bytes(BT_PADDING)
    crc = zlib.crc32(CRC_SEED + bytes(report)) & UINT32_MASK
    return bytes(report) + crc.to_bytes(CRC_LEN, "little")


def _uevent(path: str) -> dict[str, str]:
    """Parse a sysfs uevent file (KEY=VALUE lines) into a dict; empty on any read error."""
    try:
        with open(path) as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return {}


def _by_uniq(uniq: str | None, sys_root: str) -> tuple[str | None, bool]:
    """Find the hidraw whose HID device has the same HID_UNIQ (the pad's MAC address).

    This is the fallback for when the sysfs path from eventN to the HID device is unusual (another kernel / Bluetooth stack).

    Returns:
        ``(path, is_bluetooth)``, or ``(None, False)``.
    """
    uniq = (uniq or "").lower()
    if not uniq:
        return None, False
    for raw in sorted(glob.glob(f"{sys_root}/class/hidraw/hidraw*")):
        ev = _uevent(os.path.join(raw, "device", "uevent"))
        if ev.get("HID_UNIQ", "").lower() == uniq:
            bus = int(ev.get("HID_ID", "0:0:0").split(":")[0], 16)
            return f"/dev/{os.path.basename(raw)}", bus == BUS_BLUETOOTH
    return None, False


def _hid_device_dir(node: str) -> tuple[str, int] | None:
    """Walk up from a sysfs node to its HID device directory (named like 0005:054C:0CE6.0001).

    Returns:
        ``(that directory, the bus number from its name)``, or None when no parent looks like a HID device.
    """
    path = node
    while path not in ("/", ""):
        match = _HID_DIR.match(os.path.basename(path))
        if match:
            return path, int(match.group(1), 16)
        path = os.path.dirname(path)
    return None


def find_hidraw(pad_dev: InputDevice, sys_root: str = "/sys") -> tuple[str | None, bool]:
    """Find the hidraw node for the same HID device as the pad's evdev node.

    Starting from the resolved sysfs path of the input node, it walks up the parents until it reaches the HID device
    directory and looks for a hidraw child there; the bus number in that name tells Bluetooth (0005) from USB. If the walk
    finds nothing, it falls back to matching by HID_UNIQ.

    Args:
        pad_dev: The pad's evdev device.
        sys_root: The sysfs mount point (a parameter for tests).

    Returns:
        ``(/dev/hidrawN, is Bluetooth)``, or ``(None, False)``.
    """
    try:
        node = os.path.realpath(f"{sys_root}/class/input/{os.path.basename(os.fsdecode(pad_dev.path))}")
    except (OSError, AttributeError):
        node = "/"
    hid = _hid_device_dir(node)
    if hid is not None:
        directory, bus = hid
        found = sorted(glob.glob(os.path.join(directory, "hidraw", "hidraw*")))
        if found:
            return f"/dev/{os.path.basename(found[0])}", bus == BUS_BLUETOOTH
    return _by_uniq(getattr(pad_dev, "uniq", ""), sys_root)


def kernel_has_hidraw(sys_root: str = "/sys") -> bool:
    """Tell whether the kernel has hidraw (CONFIG_HIDRAW): the hidraw device class exists in sysfs."""
    return os.path.isdir(f"{sys_root}/class/hidraw")


def ensure_node(path: str, sys_root: str = "/sys") -> None:
    """Create /dev/hidrawN when it is missing although the device exists in sysfs.

    That happens on a system without udev / mdev. The node is made with ``mknod`` from the numbers in sysfs (root only).
    Failures are ignored on purpose: the error shows up when the node is opened, with a clearer reason.
    """
    if os.path.exists(path):
        return
    try:
        with open(f"{sys_root}/class/hidraw/{os.path.basename(path)}/dev") as f:
            major, minor = map(int, f.read().split(":"))
        os.mknod(path, HIDRAW_NODE_MODE, os.makedev(major, minor))
        print(f"Created missing device node {path}")
    except (OSError, ValueError):
        return


def has_ff(dev: InputDevice) -> bool:
    """Tell whether the evdev device advertises the FF_RUMBLE force-feedback effect (the kernel driver supports rumble)."""
    try:
        return ec.FF_RUMBLE in dev.capabilities().get(ec.EV_FF, [])
    except (OSError, AttributeError):
        return False


WHY_TEXT: Final = {
    WHY_OK: "ok", WHY_OFF: "rumble = off / evdev in config",
    WHY_ACCESS: "no permission for /dev/hidraw - run with doas or install the service (doas ./install-service.sh)",
    WHY_NO_HIDRAW: "kernel without CONFIG_HIDRAW",
    WHY_NOT_FOUND: "hidraw of this pad not found", WHY_OPEN: "cannot open hidraw",
}


def _call_later(delay: float, callback: Callable[[], None]) -> asyncio.TimerHandle | None:
    """Schedule ``callback`` on the running event loop; None when there is no running loop (the --test command, unit tests)."""
    try:
        return asyncio.get_running_loop().call_later(delay, callback)
    except RuntimeError:
        return None


class Rumble:
    """Rumble of one pad. ``set(small, large)`` on every feedback packet; ``close()`` on disconnect.

    In "auto" mode BOTH ways work at once if they are available: the driver (evdev FF) and the HID report (hidraw).
    The same motor strength sent both ways does no harm, and rumble works also when one of them silently does nothing
    (a kernel without full rumble support, an unusual Bluetooth stack). A way that reports an error is switched off.

    Besides rumble, the HID report path also carries the lightbar colour and the adaptive-trigger effects. Several
    timers keep the pad safe: a repeat resends a change once more shortly after (a lost report must not leave the
    pad silent), a watchdog stops the motors when refreshes cease, and a trigger watchdog releases the triggers.

    Attributes:
        idx: The pad slot (for log lines).
        dev: The pad's evdev device.
        methods: The working ways, ``"evdev"`` and/or ``"hidraw"``, in this order.
        why: A WHY_* reason for no HID report (sent to the receiver in the version packet).
        cur: The motor strengths ``(small, large)`` last sent.
    """

    def __init__(self, idx: int, pad_dev: InputDevice, mode: str = MODE_AUTO,
                 hidraw: tuple[str, bool] | None = None) -> None:
        """Open the available rumble ways of a pad.

        Args:
            idx: The pad slot (for log lines).
            pad_dev: The pad's evdev device.
            mode: ``"auto"``, ``"evdev"``, ``"hidraw"`` or ``"off"``.
            hidraw: ``(path, is_bluetooth)`` of the pad's hidraw node, to skip the lookup (tests).
        """
        self.idx, self.dev = idx, pad_dev
        self.methods: list[str] = []
        self.fd: int | None = None
        self.bt, self.seq, self.path = False, 0, None
        self.ff_id: int | None = None
        self.v2: bool | None = None
        self.fw: int | None = None
        self.why = WHY_OFF if mode in (MODE_OFF, WAY_EVDEV) else WHY_OK
        self.logged = 0
        self.fails: dict[str, int] = {}       # way -> number of consecutive write errors (FAIL_MAX)
        self.cur = (0, 0)
        self.rgb: Sequence[int] | None = None   # the lightbar colour sent together with rumble (HID report only)
        self.trig: tuple[bytes, bytes] | None = None       # trigger effects sent to the pad, or None = never touched
        self.fx: tuple[int, ...] | None = None             # trigger settings from the PC (trig2 values) or None = nothing arrived
        self.game_fx: tuple[bytes | None, bytes | None] = (None, None)   # L2 / R2 trigger effects set by the game (raw 11 B)
        self._rv: list[bytes | None] = [None, None]        # game rumble on L2 / R2: an effect or None
        self._rv_off: list[float | None] = [None, None]    # since when this trigger's motor has been still
        self._rv_timer: asyncio.TimerHandle | None = None
        self._trig_watch: asyncio.TimerHandle | None = None   # watchdog of trigger effects (TRIG_WATCHDOG_S without a refresh)
        self.t_set = 0.0
        self._watch: asyncio.TimerHandle | None = None
        self._repeat: asyncio.TimerHandle | None = None
        if mode in (MODE_AUTO, WAY_EVDEV) and has_ff(pad_dev):
            self.methods.append(WAY_EVDEV)
        if mode in (MODE_AUTO, WAY_HIDRAW) and self._open_hidraw(hidraw):
            self.methods.append(WAY_HIDRAW)
        print(f"Controller {idx}: rumble {self._status_text(mode)}")

    def _status_text(self, mode: str) -> str:
        """The log description of what the constructor found."""
        if self.methods:
            return self.describe()
        return "off" if mode == MODE_OFF else f"unavailable (no evdev FF; {WHY_TEXT[self.why]})"

    @property
    def mode(self) -> str | None:
        """The main way (for logs and tests): the first working one, or None."""
        return self.methods[0] if self.methods else None

    @property
    def owns_lightbar(self) -> bool:
        """Whether the HID report also carries the lightbar colour.

        A colour sent through the driver (sysfs) goes out in the pad's OWN output report at a separate moment - the pad
        then accepted only one of them, and rumble from the test button (which changes the colour) worked only every few
        presses. One report with both = no race.
        """
        return WAY_HIDRAW in self.methods

    @property
    def bits(self) -> int:
        """The ways as bits (the version packet to the receiver).

        1 = driver, 2 = HID report, 4 = trigger resistance, 8 = trigger effects from trig2 (click, vibration, game
        vibration), 32 = trigger effects from the game (trig3).
        """
        bits = RUMBLE_EVDEV if WAY_EVDEV in self.methods else 0
        if WAY_HIDRAW in self.methods:
            bits |= RUMBLE_HIDRAW | CAP_TRIGGERS | CAP_TRIG_FX | CAP_TRIG_RAW
        return bits

    def describe(self) -> str:
        """A short log description of the active rumble ways (driver and/or HID reports with firmware and vibration mode)."""
        parts = []
        if WAY_EVDEV in self.methods:
            parts.append("kernel driver (evdev FF)")
        if WAY_HIDRAW in self.methods:
            fw = f"firmware {self.fw:04x}" if self.fw is not None else "firmware unknown"
            vibration = "v2" if self.v2 else "v1" if self.v2 is False else "v1+v2"
            parts.append(f"direct HID reports ({self.path}, {fw}, vibration {vibration})")
        return "via " + " + ".join(parts)

    def _open_hidraw(self, given: tuple[str, bool] | None = None) -> bool:
        """Locate and open the pad's hidraw node, read its firmware version and decide the vibration flavour.

        On failure ``self.why`` records the reason (no hidraw in the kernel, node not found, no permission, other) so the
        receiver can show it. The firmware version selects vibration v1 / v2 reports (v2 from 2.21).

        Args:
            given: ``(path, is_bluetooth)`` to use instead of the lookup.

        Returns:
            True on success.
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
    def set(self, small: int, large: int, rgb: Sequence[int] | None = None) -> None:
        """Set the rumble (and the lightbar colour when ``owns_lightbar``).

        A change goes out at once and a second time after REPEAT_S (a lost / overwritten report does not leave the pad
        without rumble); with no change - a refresh at most every REFRESH_S.

        Args:
            small: The right (weak) motor, 0..255.
            large: The left (strong) motor, 0..255.
            rgb: The lightbar colour; None keeps the last one.
        """
        now = time.monotonic()
        if not self.methods:
            return
        rgb = rgb if rgb is not None else self.rgb
        changed = (small, large) != self.cur or rgb != self.rgb
        if not changed and now - self.t_set < REFRESH_S:
            return                                   # no change and fresh - send nothing
        if self.logged < LOGGED_COMMANDS and (small or large):
            self.logged += 1
            print(f"Controller {self.idx}: rumble {small}/{large} from PC {self.describe()}")
        self.rgb = rgb
        self._send(small, large)
        self.cur, self.t_set = (small, large), now
        self._arm_watchdog()
        if changed:
            self._arm_repeat()

    def set_triggers(self, vals: Sequence[Any]) -> bool:
        """Apply trigger effects from the PC.

        The packet is split into the profile part and the game part, remembered, and the watchdog is re-armed (a refresh
        must keep arriving). The report is then rebuilt with the current motor values so that sending trigger effects
        never changes the rumble. The receiver refreshes the effects every few seconds, so after a reconnect the pad
        returns to the effects from the profile and the game.

        Args:
            vals: trig3 values (trig2 + game effects), trig2 (12) or the old trig (4: resistance only).

        Returns:
            True when the effects were sent to the pad (HID report).
        """
        profile, game = split_triggers(vals)
        if (profile, game) != (self.fx, self.game_fx):
            print(f"Controller {self.idx}: triggers L2 {profile[0:4]} R2 {profile[4:8]} game rumble {profile[8:11]}"
                  + "".join(f" game {n} {g.hex()}" for n, g in zip(("L2", "R2"), game, strict=True) if g is not None))
        self.fx, self.game_fx = profile, game
        if not profile[RUMBLE_FX_AT] & RFX_ON:
            self._rv, self._rv_off = [None, None], [None, None]
        self._arm_trig_watch(any(profile) or game != (None, None))
        if WAY_HIDRAW not in self.methods or self.fd is None:
            return False
        trig = self._want_triggers(*self.cur)
        try:
            self.seq = (self.seq + 1) & BT_SEQ_MASK
            os.write(self.fd, output_report(self.bt, self.seq, *self.cur, self.v2, self.rgb, trig))
        except (BlockingIOError, InterruptedError):
            return False                    # a full queue - the next refresh from the PC will deliver the effect
        except OSError as e:
            self._failed(WAY_HIDRAW, e)
            return False
        self.fails[WAY_HIDRAW] = 0
        self.trig = trig
        return True

    def _arm_trig_watch(self, on: bool) -> None:
        """(Re)start the trigger-effect watchdog timer when effects are active (``on``); cancel it otherwise."""
        if self._trig_watch is not None:
            self._trig_watch.cancel()
            self._trig_watch = None
        if on:
            self._trig_watch = _call_later(TRIG_WATCHDOG_S, self._trig_expired)

    def _trig_expired(self) -> None:
        """Watchdog fired: no trigger refresh from the PC for TRIG_WATCHDOG_S - release the triggers (switch all effects off)."""
        self._trig_watch = None
        if self.fx is not None and (any(self.fx) or self.game_fx != (None, None)):
            print(f"Controller {self.idx}: no trigger refresh from PC for {TRIG_WATCHDOG_S:g}s - triggers released")
            self.set_triggers(TRIG2_OFF)

    def _profile_effects(self, fx: tuple[int, ...]) -> list[bytes]:
        """The effect of each trigger from the profile; a trigger with no profile effect takes the game's effect."""
        return [effect(*fx[at:at + EFFECT_FIELDS]) if fx[at] != TFX_OFF else game_effect(game)
                for at, game in zip((L2_EFFECT_AT, R2_EFFECT_AT), self.game_fx, strict=True)]

    def _overlay_game_rumble(self, out: list[bytes], motors: tuple[int, int], gain: int, freq: int, now: float) -> bool:
        """Replace trigger effects with vibration that follows the motors (L2 = large, R2 = small).

        A motor above zero starts a vibration whose amplitude follows the motor; when the motor stops, the vibration is
        kept for RFX_HOLD_S (so it does not flicker) and then the trigger returns to its own effect.

        Args:
            out: The effects of (L2, R2); modified in place.
            motors: The strengths ``(large, small)`` that drive ``(L2, R2)``.
            gain: The game-rumble gain in percent.
            freq: The vibration frequency in Hz (0 is sent as 1).
            now: The current monotonic time.

        Returns:
            True while some vibration is being held, i.e. a timer must bring the trigger back to its effect.
        """
        hold = False
        for k, motor in enumerate(motors):
            amp = rumble_amp(motor, gain)
            if amp:
                self._rv[k], self._rv_off[k] = vibration_effect(0, amp, freq or 1), None
            elif self._rv[k] is not None:
                silent_since = self._rv_off[k]
                if silent_since is None:
                    silent_since = self._rv_off[k] = now
                if now - silent_since >= RFX_HOLD_S - HOLD_SLACK_S:
                    self._rv[k] = self._rv_off[k] = None
                else:
                    hold = True
            if self._rv[k] is not None:
                out[k] = self._rv[k]
        return hold

    def _want_triggers(self, small: int, large: int, now: float | None = None) -> tuple[bytes, bytes] | None:
        """Work out the trigger effects the pad should have now.

        For every trigger, in this order: the profile's effect; with no profile effect - the game's effect; and during
        game rumble (and RFX_HOLD_S after it) - vibration following the motor.

        Args:
            small: The right (weak) motor, 0..255.
            large: The left (strong) motor, 0..255.
            now: The current monotonic time (a parameter for tests).

        Returns:
            The effects of (L2, R2), or None when the PC set nothing (the triggers are not touched).
        """
        fx = self.fx
        if fx is None:
            return None
        out = self._profile_effects(fx)
        flags, gain, freq = fx[RUMBLE_FX_AT:RUMBLE_FX_AT + 3]
        if flags & RFX_ON:
            hold = self._overlay_game_rumble(out, (large, small), gain, freq, time.monotonic() if now is None else now)
            self._arm_rv(hold)
        return out[0], out[1]

    def _arm_rv(self, hold: bool) -> None:
        """After RFX_HOLD_S of silent motors the trigger must return to its effect, even if nothing else arrives from the PC."""
        if self._rv_timer is not None:
            self._rv_timer.cancel()
            self._rv_timer = None
        if hold:
            self._rv_timer = _call_later(RFX_HOLD_S, self._rv_expired)

    def _rv_expired(self) -> None:
        """Hold time over: resend the report so the trigger falls back to its normal effect."""
        self._rv_timer = None
        if self.methods:
            self._send(*self.cur, only=WAY_HIDRAW)

    def _arm_repeat(self) -> None:
        """Schedule one extra send REPEAT_S from now (replacing a pending one) to protect a change against a lost report."""
        if self._repeat is not None:
            self._repeat.cancel()
        self._repeat = _call_later(REPEAT_S, self._send_again)

    def _send_again(self) -> None:
        """Timer callback of _arm_repeat: resend the current rumble over the HID report."""
        self._repeat = None
        if self.methods:
            self._send(*self.cur, only=WAY_HIDRAW)

    def _send(self, small: int, large: int, only: str | None = None) -> None:
        """Send the rumble through every active way (or only ``only``), handling errors per way.

        A momentarily full report queue is not a failure. A real error is counted by _failed; if that switches the
        driver way off and no HID path is open yet, the HID report is opened as a replacement and used straight away.
        """
        for way in list(self.methods):
            if only and way != only:
                continue
            try:
                (self._send_hidraw if way == WAY_HIDRAW else self._send_evdev)(small, large)
            except (BlockingIOError, InterruptedError):
                continue    # the report queue is momentarily full - not a failure; a repeat / refresh will deliver the state
            except OSError as e:
                if self._failed(way, e) and way == WAY_EVDEV and WAY_HIDRAW not in self.methods and self._open_hidraw():
                    self.methods.append(WAY_HIDRAW)
                    self._send(small, large, only=WAY_HIDRAW)    # with the same error handling (the exception does not escape to the loop)
            else:
                self.fails[way] = 0

    def _failed(self, way: str, error: OSError) -> bool:
        """Count a write error of ``way``; switch the way off after FAIL_MAX in a row or when the pad is gone.

        Returns:
            True when the way was switched off.
        """
        count = self.fails[way] = self.fails.get(way, 0) + 1
        if count < FAIL_MAX and error.errno not in FATAL_ERRNO:
            if count == 1:
                print(f"Controller {self.idx}: rumble via {way} failed ({error!r}) - retrying")
            return False
        print(f"Controller {self.idx}: rumble via {way} failed ({error!r}) - not using it any more")
        if way in self.methods:
            self.methods.remove(way)
        self.fails[way] = 0
        if way == WAY_HIDRAW:
            self._close_fd()                          # without this the descriptor leaked (reopening overwrote fd)
        return True

    def _close_fd(self) -> None:
        """Close the hidraw file descriptor if one is open."""
        if self.fd is not None:
            with contextlib.suppress(OSError):      # the descriptor is dropped either way; a dead one is not worth a report
                os.close(self.fd)
            self.fd = None

    def _send_hidraw(self, small: int, large: int) -> None:
        """Send rumble (and colour); trigger effects only when they are about to change (game rumble on the triggers)."""
        if self.fd is None:
            return
        trig = self._want_triggers(small, large)
        if trig == self.trig:
            trig = None
        self.seq = (self.seq + 1) & BT_SEQ_MASK
        os.write(self.fd, output_report(self.bt, self.seq, small, large, self.v2, self.rgb, trig))
        if trig is not None:
            self.trig = trig

    def _send_evdev(self, small: int, large: int) -> None:
        """Rumble through the kernel force-feedback API: upload (or update) one long rumble effect and start it; stop it for 0/0.

        The effect lasts EFFECT_MS, so the watchdog / refresh logic of the caller decides when it really ends. The
        strong / weak magnitudes are scaled from 0..255 to the 16-bit range (x257).
        """
        if small or large:
            eff = ff.Effect(ec.FF_RUMBLE, -1 if self.ff_id is None else self.ff_id, 0, ff.Trigger(0, 0),
                            ff.Replay(EFFECT_MS, 0),
                            ff.EffectType(ff_rumble_effect=ff.Rumble(strong_magnitude=large * FF_MAGNITUDE_SCALE,
                                                                     weak_magnitude=small * FF_MAGNITUDE_SCALE)))
            self.ff_id = self.dev.upload_effect(eff)
            self.dev.write(ec.EV_FF, self.ff_id, 1)
        elif self.ff_id is not None:
            self.dev.write(ec.EV_FF, self.ff_id, 0)

    def _arm_watchdog(self) -> None:
        """(Re)start the rumble watchdog: if no refresh arrives within WATCHDOG_S while the motors run, they are stopped."""
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None
        if self.cur != (0, 0):
            self._watch = _call_later(WATCHDOG_S, self._expired)

    def _expired(self) -> None:
        """Watchdog fired: the receiver stopped refreshing the rumble, so switch the motors off."""
        self._watch = None
        if self.cur != (0, 0) and time.monotonic() - self.t_set >= WATCHDOG_S - WATCHDOG_SLACK_S:
            self.set(0, 0)

    def close(self) -> None:
        """Shut rumble down cleanly: cancel timers, stop the motors, release hard triggers, erase the FF effect, close the fd."""
        for name in ("_watch", "_repeat", "_rv_timer", "_trig_watch"):
            timer = getattr(self, name)
            if timer is not None:
                timer.cancel()
                setattr(self, name, None)
        if self.cur != (0, 0):
            self._send(0, 0)
            self.cur = (0, 0)       # set_triggers below sends a report with the current motor strength - it must not switch it on again
        if self.trig is not None and any(effect_bytes[0] != TRIG_OFF for effect_bytes in self.trig):
            self.set_triggers(TRIG2_OFF)         # the sender is closing - a trigger must not stay hard
        self._arm_rv(False)
        if WAY_EVDEV in self.methods and self.ff_id is not None:
            with contextlib.suppress(OSError):      # the pad may already be gone; there is nothing left to erase then
                self.dev.erase_effect(self.ff_id)
        self._close_fd()
        self.methods, self.cur = [], (0, 0)


# ---------------- command-line test: doas python3 sender_rumble.py --test ----------------
def _test() -> None:
    """Rumble every connected DualSense in turn with every way - without the network and the receiver.

    It shows whether the problem is in the pad / kernel (nothing rumbles) or on the way PC -> sender (it rumbles here, but
    not from the game).
    """
    import evdev
    pads = [evdev.InputDevice(p) for p in evdev.list_devices()]
    pads = [d for d in pads if "Wireless Controller" in d.name and "Motion" not in d.name and "Touchpad" not in d.name]
    if not pads:
        print("No DualSense found (is it connected? run with doas)")
        return
    for d in pads:
        path, bt = find_hidraw(d)
        print(f"== {d.name} {d.uniq or d.phys} ({os.fsdecode(d.path)}): {'Bluetooth' if bt else 'USB'}, hidraw {path}, "
              f"evdev FF {'yes' if has_ff(d) else 'NO (kernel without CONFIG_PLAYSTATION_FF)'}"
              + ("" if kernel_has_hidraw() else ", kernel WITHOUT CONFIG_HIDRAW"))
        steps: list[tuple[str, str, bool | None]] = [("evdev FF", WAY_EVDEV, None)] if has_ff(d) else []
        steps += [("HID report v1", WAY_HIDRAW, False), ("HID report v2", WAY_HIDRAW, True), ("HID report v1+v2", WAY_HIDRAW, None)]
        for label, mode, v2 in steps:
            r = Rumble(0, d, mode)
            if r.mode != mode:
                print(f"   {label}: not available")
                continue
            if mode == WAY_HIDRAW:
                r.v2 = v2
            print(f"   {label}: rumbling 1.5 s ...", flush=True)
            try:
                r._send(MOTOR_MAX, MOTOR_MAX)
                time.sleep(1.5)
                r._send(0, 0)
            except OSError as e:
                print(f"   {label}: ERROR {e!r}")
            r.close()
            time.sleep(0.8)
    print("Which step did you feel? Set rumble = evdev / hidraw in sender.toml if auto picks the wrong one.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _test()
    else:
        print("usage: doas python3 sender_rumble.py --test")
