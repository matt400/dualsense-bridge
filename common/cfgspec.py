"""Shared list of the sender settings that the receiver window may change over the network (the cfgset / cfgval packets).

Both sides import it: the sender uses it to check a value that arrived (and a value read from remote_cfg.json), the receiver to
build the Sender tab (limits of the spin boxes, the choices of the combo boxes, "applies now / at the next stream / when a pad
connects").

The position of a setting in SPECS is its id on the wire. Never reorder or delete a setting, only append: an older receiver and
a newer sender (or the other way round) must agree on what id 7 means.

A value travels as one float64 (a bool as 0 / 1, a choice as the index in ``Spec.choices``), so a packet has a fixed length
whatever the setting is, and a new setting does not change the packet format.

Not on the list on purpose: host, port and key (a wrong value would cut the link or lock the receiver out), the paths of the
state files (a file name chosen over the network), bt_adapter (the Bluetooth panel already has it) and stats (a log switch).
"""
from __future__ import annotations

import math
from typing import Any, Final, NamedTuple, TypeAlias

Value: TypeAlias = bool | int | float | str

KIND_BOOL, KIND_INT, KIND_FLOAT, KIND_CHOICE = range(4)
# when a new value starts to count: at once, at the next audio stream of a pad (haptics / speaker), when a pad connects next
WHEN_NOW, WHEN_STREAM, WHEN_PAD = range(3)


class Spec(NamedTuple):
    """One setting: its name in sender.toml, the type, the allowed range or the choices, and when a change applies."""

    key: str
    kind: int
    lo: float = 0
    hi: float = 1
    choices: tuple[str, ...] = ()
    when: int = WHEN_NOW


RUMBLE_CHOICES: Final = ("auto", "evdev", "hidraw", "off")
MOTORS_CHOICES: Final = ("haptics", "rumble")
OPUS_MODE_CHOICES: Final = ("hybrid", "celt")

SPECS: Final[tuple[Spec, ...]] = (
    Spec("max_pads", KIND_INT, 1, 8),
    Spec("grab_pad", KIND_BOOL, when=WHEN_PAD),
    Spec("grab_touch", KIND_BOOL, when=WHEN_PAD),
    Spec("tap_click", KIND_BOOL),
    Spec("tap_max_s", KIND_FLOAT, 0.05, 1.0),
    Spec("tap_max_move", KIND_INT, 0, 500),
    Spec("heartbeat_s", KIND_FLOAT, 0.01, 0.5),
    Spec("pad_min_dt", KIND_FLOAT, 0.0, 0.05),
    Spec("motion_min_dt", KIND_FLOAT, 0.0, 0.05),
    Spec("motion_deadband_gyro", KIND_INT, 0, 4096),
    Spec("motion_deadband_acc", KIND_INT, 0, 1024),
    Spec("rescan_s", KIND_FLOAT, 0.1, 60.0),
    Spec("stick_deadzone", KIND_INT, 0, 40),
    Spec("calibrate_s", KIND_FLOAT, 0.0, 10.0, when=WHEN_PAD),
    Spec("calibrate_max_range", KIND_INT, 0, 65535, when=WHEN_PAD),
    Spec("autobias", KIND_BOOL),
    Spec("autobias_s", KIND_FLOAT, 0.1, 30.0),
    Spec("autobias_range", KIND_INT, 0, 8192),
    Spec("battery_poll_s", KIND_FLOAT, 1.0, 300.0),
    Spec("touch_coalesce_ms", KIND_INT, 0, 100),
    Spec("touch_deadzone", KIND_INT, 0, 50),
    Spec("touch_start_move", KIND_INT, 0, 200),
    Spec("gestures", KIND_BOOL),
    Spec("rumble", KIND_CHOICE, choices=RUMBLE_CHOICES, when=WHEN_PAD),
    Spec("haptics_motors", KIND_CHOICE, choices=MOTORS_CHOICES, when=WHEN_PAD),
    Spec("haptics_buffer_ms", KIND_INT, 10, 200, when=WHEN_STREAM),
    Spec("speaker_buffer_ms", KIND_INT, 20, 200, when=WHEN_STREAM),
    Spec("speaker_volume", KIND_INT, 0, 255, when=WHEN_STREAM),
    Spec("speaker_opus_bytes", KIND_INT, 30, 200, when=WHEN_STREAM),
    Spec("speaker_opus_mode", KIND_CHOICE, choices=OPUS_MODE_CHOICES, when=WHEN_STREAM),
    Spec("speaker_opus_complexity", KIND_INT, -1, 10, when=WHEN_STREAM),
    Spec("idle_off_min", KIND_FLOAT, 0.0, 1440.0),
    Spec("rt_priority", KIND_INT, 0, 99),
    Spec("low_latency", KIND_BOOL),
    Spec("cpu_latency_us", KIND_INT, 0, 10000),
)
BY_KEY: Final[dict[str, int]] = {spec.key: i for i, spec in enumerate(SPECS)}   # name -> id on the wire


def check(spec: Spec, value: Any) -> Value:
    """Return ``value`` as the type of the setting if it is allowed.

    Args:
        spec: The setting.
        value: A Python value (from JSON or from the window).

    Returns:
        The value as bool / int / float / str, whichever the setting is.

    Raises:
        ValueError: The type is wrong (a bool for a number, text for a bool), the number is not finite or out of range, a whole
            number is expected and a fraction came, or the text is not one of the choices.
    """
    if spec.kind == KIND_BOOL:
        if not isinstance(value, bool):
            raise ValueError("expected true / false")
        return value
    if spec.kind == KIND_CHOICE:
        if not isinstance(value, str) or value not in spec.choices:
            raise ValueError(f"expected one of {spec.choices}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("expected a number")
    if not spec.lo <= value <= spec.hi:
        raise ValueError(f"expected a number {spec.lo:g}..{spec.hi:g}")
    if spec.kind == KIND_INT:
        if value != int(value):
            raise ValueError("expected a whole number")
        return int(value)
    return float(value)


def to_wire(spec: Spec, value: Value) -> float:
    """Turn a checked value into the float64 of the packet (a choice becomes its index)."""
    if spec.kind == KIND_CHOICE:
        return float(spec.choices.index(str(value)))
    return float(value)


def from_wire(spec: Spec, number: float) -> Value:
    """Turn the float64 of a packet into a checked value.

    Raises:
        ValueError: Not a finite number, not 0 / 1 for a bool, not a valid index for a choice, or ``check`` refuses the value.
    """
    if not math.isfinite(number):
        raise ValueError("expected a finite number")
    if spec.kind == KIND_BOOL:
        if number not in (0.0, 1.0):
            raise ValueError("expected 0 or 1")
        return check(spec, number == 1.0)
    if spec.kind == KIND_CHOICE:
        index = int(number)
        if index != number or not 0 <= index < len(spec.choices):
            raise ValueError("expected the index of a choice")
        return spec.choices[index]
    return check(spec, number)
