"""Sender: which kind of pad is this (DualSense, DualShock 4, Xbox, other) and how its evdev events map onto the DualSense layout.

The rest of the sender (state packets, remap on the receiver) speaks one layout: the DualSense one - the stick / trigger axes are
the evdev codes ABS_X, ABS_Y, ABS_Z (L2), ABS_RX, ABS_RY, ABS_RZ (R2) scaled to 0..255, the D-pad is a hat, the buttons are the
keys of ``proto.PAD_KEYS``. A DualSense already reports exactly that, so it gets no plan (``plan_for`` returns None) and the read
loops keep their fast path. Any other pad gets a ``Plan``: a table from the codes the kernel driver really uses to the canonical
ones, built once per pad from its capabilities (the kernel has several drivers per pad - xpad, xpadneo, hid-microsoft,
hid-sony, hid-playstation - which disagree on where the right stick and the triggers live), so nothing is hardcoded per driver
except the two known quirks below.

Quirks:
  - The Xbox drivers label buttons (BTN_X = 0x133 = BTN_NORTH is the button that sits on the *left*), a DualSense driver names
    them by position (BTN_NORTH = triangle, the top one). The Xbox plan therefore swaps 0x133 and 0x134, so Y (top) lands on
    triangle and X (left) on square.
  - xpad with dpad_to_buttons (wireless 360 pads) reports the D-pad as BTN_TRIGGER_HAPPY1-4 (left, right, up, down), not a hat.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Any, Final

from evdev import ecodes as ec

from proto import (BTN_EAST, BTN_MODE, BTN_NORTH, BTN_SELECT, BTN_SOUTH, BTN_START, BTN_THUMBL, BTN_THUMBR, BTN_TL, BTN_TR, BTN_WEST,
                   PF_ADAPTIVE, PF_LIGHTBAR, PF_MOTION, PF_RUMBLE, PF_TOUCHPAD, PK_DS4, PK_DUALSENSE, PK_OTHER, PK_XBOX)

AXIS_MAX: Final = 255
SONY_VENDOR, MICROSOFT_VENDOR, NINTENDO_VENDOR = 0x054C, 0x045E, 0x057E
DUALSENSE_PRODUCTS: Final = frozenset({0x0CE6, 0x0DF2})                 # DualSense, DualSense Edge
DS4_PRODUCTS: Final = frozenset({0x05C4, 0x09CC, 0x0BA0})               # DualShock 4 v1, v2, the USB wireless adapter
XBOX_PRODUCTS: Final = frozenset({0x028E, 0x028F, 0x0291, 0x02D1, 0x02DD, 0x02E0, 0x02E3, 0x02EA, 0x02FD, 0x0719,
                                  0x0B00, 0x0B05, 0x0B12, 0x0B13, 0x0B20, 0x0B22})   # 360, One, One S, Elite (2), Series X|S, receivers
SWITCH_PRODUCTS: Final = frozenset({0x2009})                            # Switch Pro Controller
XBOX_NAME_MARKS: Final = ("xbox", "x-box")                              # third-party pads that announce themselves as an Xbox pad
SONY_NAME_MARKS: Final = ("wireless controller", "dualshock")           # every evdev node of a DualSense / DS4 (the DS4 USB adaptor
# is named "... DUALSHOCK®4 USB Wireless Adaptor") contains one of these
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
BTN_HAPPY1, BTN_HAPPY2, BTN_HAPPY3, BTN_HAPPY4 = 0x2C0, 0x2C1, 0x2C2, 0x2C3          # xpad dpad_to_buttons: left, right, up, down
BTN_TL2, BTN_TR2 = 0x138, 0x139
L2, R2 = ec.ABS_Z, ec.ABS_RZ
FEATURES: Final = {
    PK_DUALSENSE: PF_MOTION | PF_TOUCHPAD | PF_LIGHTBAR | PF_ADAPTIVE | PF_RUMBLE,
    PK_DS4: PF_MOTION | PF_TOUCHPAD | PF_RUMBLE,        # the lightbar only through hid-playstation's RGB LED (sysfs)
    PK_XBOX: PF_RUMBLE,
    PK_OTHER: PF_RUMBLE,
}
FAMILY_NAMES: Final = {PK_DUALSENSE: "DualSense", PK_DS4: "DualShock 4", PK_XBOX: "Xbox", PK_OTHER: "Other"}


@dataclass(frozen=True, slots=True)
class Kind:
    """What a pad is: the family (PK_*), its features (PF_*) and the USB ids it reported (0 = unknown)."""

    family: int
    vendor: int = 0
    product: int = 0

    @property
    def features(self) -> int:
        """The PF_* bits of this family."""
        return FEATURES[self.family]

    @property
    def native(self) -> bool:
        """A DualSense: the canonical layout, no translation (and the HID report features: lightbar, triggers, haptics)."""
        return self.family == PK_DUALSENSE


DUALSENSE: Final = Kind(PK_DUALSENSE)


def _ids(dev: Any) -> tuple[int, int, str]:
    """(vendor, product, name) of an evdev device; zeros for a device that has none (a fake one in tests, None)."""
    try:
        return int(dev.info.vendor), int(dev.info.product), str(dev.name)
    except (AttributeError, TypeError, ValueError):
        return 0, 0, ""


def _caps(dev: Any) -> dict[int, list[Any]]:
    """The capabilities of an evdev device by event type; empty when they cannot be read."""
    try:
        return dict(dev.capabilities())
    except (OSError, AttributeError, TypeError, ValueError):
        return {}


def _codes(caps: dict[int, list[Any]], ev_type: int) -> set[int]:
    """The set of event codes of one type; an absinfo entry (code, AbsInfo) is reduced to its code."""
    return {c[0] if isinstance(c, tuple) else c for c in caps.get(ev_type, ())}


def _is_gamepad(caps: dict[int, list[Any]]) -> bool:
    """A device with a stick and the face buttons: the filter that keeps a mouse, a keyboard or the pad's media node out."""
    return BTN_SOUTH in _codes(caps, ec.EV_KEY) and {ec.ABS_X, ec.ABS_Y} <= _codes(caps, ec.EV_ABS)


def classify(dev: Any) -> Kind | None:
    """Return the kind of pad an evdev device belongs to, or None when it is not a pad we serve.

    Sony devices must carry one of SONY_NAME_MARKS in the name: a DualSense / DualShock 4 shows up as three nodes (pad,
    motion sensors, touchpad) and all of them classify the same way. Xbox and Switch Pro pads are one node and must look like a
    gamepad by their capabilities. An unknown pad that calls itself an Xbox pad is treated as one.
    """
    vendor, product, name = _ids(dev)
    if vendor == SONY_VENDOR:
        if not any(m in name.lower() for m in SONY_NAME_MARKS):
            return None
        if product in DUALSENSE_PRODUCTS:
            return Kind(PK_DUALSENSE, vendor, product)
        return Kind(PK_DS4, vendor, product) if product in DS4_PRODUCTS else None
    if vendor == NINTENDO_VENDOR and product in SWITCH_PRODUCTS:
        return Kind(PK_OTHER, vendor, product) if _is_gamepad(_caps(dev)) else None
    by_name = any(m in name.lower() for m in XBOX_NAME_MARKS)
    if (vendor == MICROSOFT_VENDOR and product in XBOX_PRODUCTS) or by_name:
        return Kind(PK_XBOX, vendor, product) if _is_gamepad(_caps(dev)) else None
    return None


def kind_of(dev: Any) -> Kind:
    """Like classify, but a device that is not recognised (a test double, a pad opened by another path) counts as a DualSense."""
    return classify(dev) or DUALSENSE


def identify(dev: Any) -> tuple[Kind, Plan | None]:
    """(kind, translation plan) of a pad in one call - what a new Ctrl needs."""
    kind = kind_of(dev)
    return kind, plan_for(dev, kind)


@dataclass(slots=True)
class Plan:
    """The translation of one non-DualSense pad's events into the canonical layout (see the module docstring).

    Attributes:
        axes: evdev axis code -> (canonical axis code, lowest value, highest value) of the pad's own range.
        keys: evdev key code -> canonical key code (a key not in the table is ignored).
        dpad_keys: evdev key code -> (hat index, value) for pads whose D-pad is four buttons (BTN_DPAD_*).
        trig_keys: evdev key code -> canonical trigger axis for a pad whose triggers are only buttons (ZL / ZR).
        hat_axes: the D-pad is a hat (ABS_HAT0X / Y) - the normal case.
    """

    axes: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    keys: dict[int, int] = field(default_factory=dict)
    dpad_keys: dict[int, tuple[int, int]] = field(default_factory=dict)
    trig_keys: dict[int, int] = field(default_factory=dict)
    hat_axes: bool = True

    def scale(self, code: int, value: int) -> tuple[int, int] | None:
        """(canonical axis, 0..255 value) for an evdev axis event, or None when the axis is not mapped. Rounds to the nearest step."""
        m = self.axes.get(code)
        if m is None:
            return None
        canon, lo, hi = m
        span = hi - lo
        if span <= 0:
            return canon, 0
        v = ((value - lo) * AXIS_MAX + span // 2) // span
        return canon, 0 if v < 0 else AXIS_MAX if v > AXIS_MAX else v


def _range(dev: Any, code: int) -> tuple[int, int]:
    """The (min, max) of an axis as the device reports it; (0, 255) when unknown."""
    try:
        info = dev.absinfo(code)
        return int(info.min), int(info.max)
    except (OSError, AttributeError, TypeError, ValueError):
        return 0, AXIS_MAX


def _axis_plan(dev: Any, caps: dict[int, list[Any]]) -> dict[int, tuple[int, int, int]]:
    """Axes of a pad from what it has: left stick X / Y, then wherever the right stick and the triggers are.

    Decided by the capabilities, never by the driver name: hid-sony (since 4.10), hid-playstation, xpad and hid-nintendo put the
    right stick on RX / RY and the triggers on Z / RZ; hid-microsoft / hid-generic (an Xbox pad over Bluetooth) put the right
    stick on Z / RZ and the triggers on BRAKE / GAS.
    """
    have = _codes(caps, ec.EV_ABS)
    pairs = {ec.ABS_X: ec.ABS_X, ec.ABS_Y: ec.ABS_Y}
    if {ec.ABS_RX, ec.ABS_RY} <= have:
        pairs.update({ec.ABS_RX: ec.ABS_RX, ec.ABS_RY: ec.ABS_RY})
        trig = {ec.ABS_Z: L2, ec.ABS_RZ: R2}
    else:
        pairs.update({ec.ABS_Z: ec.ABS_RX, ec.ABS_RZ: ec.ABS_RY})
        trig = {}
    if {ec.ABS_BRAKE, ec.ABS_GAS} <= have:
        trig = {ec.ABS_BRAKE: L2, ec.ABS_GAS: R2}
    pairs.update(trig)
    return {src: (dst, *_range(dev, src)) for src, dst in pairs.items() if src in have}


def plan_for(dev: Any, kind: Kind) -> Plan | None:
    """The translation plan of a pad, or None for a DualSense (nothing to translate).

    Args:
        dev: The pad's evdev device (the node with the buttons and sticks).
        kind: Its kind from classify().
    """
    if kind.native:
        return None
    caps = _caps(dev)
    keys_have = _codes(caps, ec.EV_KEY)
    plan = Plan(axes=_axis_plan(dev, caps))
    base = {BTN_SOUTH: BTN_SOUTH, BTN_EAST: BTN_EAST, BTN_NORTH: BTN_NORTH, BTN_WEST: BTN_WEST, BTN_TL: BTN_TL, BTN_TR: BTN_TR,
            BTN_SELECT: BTN_SELECT, BTN_START: BTN_START, BTN_MODE: BTN_MODE, BTN_THUMBL: BTN_THUMBL, BTN_THUMBR: BTN_THUMBR}
    if kind.family == PK_XBOX:
        base[BTN_NORTH], base[BTN_WEST] = BTN_WEST, BTN_NORTH      # labelled by the Xbox drivers, positional on a DualSense
    plan.keys = base
    if ec.ABS_HAT0X not in _codes(caps, ec.EV_ABS):
        if BTN_DPAD_UP in keys_have:
            plan.hat_axes = False
            plan.dpad_keys = {BTN_DPAD_UP: (1, -1), BTN_DPAD_DOWN: (1, 1), BTN_DPAD_LEFT: (0, -1), BTN_DPAD_RIGHT: (0, 1)}
        elif BTN_HAPPY3 in keys_have:
            plan.hat_axes = False
            plan.dpad_keys = {BTN_HAPPY3: (1, -1), BTN_HAPPY4: (1, 1), BTN_HAPPY1: (0, -1), BTN_HAPPY2: (0, 1)}
    if L2 not in {dst for dst, _lo, _hi in plan.axes.values()} and BTN_TL2 in keys_have:     # triggers that are only buttons
        plan.trig_keys = {BTN_TL2: L2, BTN_TR2: R2}
    return plan


def battery_dir(dev: Any, sys_root: str = "/sys") -> str | None:
    """The power-supply folder of a pad's battery found through sysfs, whatever the driver names it.

    hid-playstation calls it ps-controller-battery-<mac>, hid-sony sony_controller_battery_<mac>, hid-nintendo, xpadneo, xpad and
    hid-generic have names of their own - but every one of them registers the battery under the device the evdev node hangs off
    (``eventN/device`` = inputN, its ``device`` = the HID device or the USB interface). None when there is no battery entry.
    """
    try:
        node = os.path.basename(os.fsdecode(dev.path))
    except (AttributeError, TypeError):
        return None
    found = sorted(glob.glob(f"{sys_root}/class/input/{node}/device/device/power_supply/*/capacity"))
    return os.path.dirname(found[0]) if found else None
