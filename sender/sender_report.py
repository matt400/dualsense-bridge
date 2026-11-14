"""Sender: the DualSense output report and the trigger effects - pure builders, no I/O.

Everything here turns numbers into bytes: the 47-byte "common" part of the output report (valid flags, motors, trigger
effects, lightbar, LEDs), its USB / Bluetooth wrapping (report 0x02 / 0x31 with the CRC32 seeded with 0xA2), the haptics
setup report, the 11-byte trigger effects (resistance, weapon click, vibration) and the unpacking of the trig2 / trig3
packet values. Nothing touches a file descriptor or the event loop, so the module is tested with plain byte
comparisons; the file descriptors, timers and the sequence counter live in sender_rumble (class Rumble).
"""

from collections.abc import Sequence
from typing import Any, Final
import zlib

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))   # proto.py is shared, it lives in common/

from proto import (PLAYER_LEDS_MASK, RFX_GAIN_MAX, TFX_CLICK, TFX_RES, TFX_VIB, TRG3_L2, TRG3_R2, TRIG2_OFF, TRIG_AMP_MAX,
                   TRIG_CLICK_MAX, TRIG_CLICK_MIN, TRIG_FORCE_MAX, TRIG_FREQ_MAX, TRIG_START_MAX, UINT32_MASK, trig2_from_legacy)

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
BYTE_MASK: Final = 0xFF
VALID_FLAG0_AT, VALID_FLAG1_AT = 0, 1       # "valid_flag" bytes: which parts of the report the pad should apply
MOTOR_RIGHT_AT, MOTOR_LEFT_AT = 2, 3        # the right (weak) and the left (strong) motor
VALID_FLAG2_AT = 38
RIGHT_TRIGGER_AT, LEFT_TRIGGER_AT, TRIGGER_LEN = 10, 21, 11
LIGHTBAR_AT, RGB_LEN = 44, 3                # lightbar_red / green / blue in the common part
FLAG0_COMPAT_VIBRATION, FLAG0_HAPTICS_SELECT = 0x01, 0x02
FLAG0_RIGHT_TRIGGER, FLAG0_LEFT_TRIGGER = 0x04, 0x08    # valid_flag0: the right / left trigger effect from this report
FLAG1_LIGHTBAR: Final = 0x04                # valid_flag1: the lightbar colour from this report
FLAG1_MUTE_LED, FLAG1_PLAYER_LEDS = 0x01, 0x10   # valid_flag1: the mute button LED / the player LEDs from this report
MUTE_LED_AT, PLAYER_LEDS_AT = 8, 43         # mute_button_led (0 off, 1 on, 2 pulse) and player_leds (5 bits) in the common part
FLAG2_COMPAT_VIBRATION2: Final = 0x04       # newer pad firmware (like use_vibration_v2 in the kernel)


# valid_flag1 of the setup report (measured on the real pad, P0: without it nothing vibrates, with valid_flag0 left at 0)
FLAG1_HAPTICS_SETUP: Final = 0x02 | 0x20 | 0x40
MOTOR_POWER_AT: Final = 36                  # "reduce motor power" in the common part; the setup report sends 0 = no reduction

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


MOTOR_MAX: Final = 255
PERCENT: Final = 100


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


def _bt_report(common: bytearray, seq: int) -> bytes:
    """Wrap a 47-byte common part as a Bluetooth report 0x31: id, sequence number, tag, padding and the CRC32 (seed 0xA2)."""
    report = bytearray([BT_REPORT_ID, (seq & BT_SEQ_MASK) << BT_SEQ_SHIFT, OUT_TAG]) + common + bytes(BT_PADDING)
    crc = zlib.crc32(CRC_SEED + bytes(report)) & UINT32_MASK
    return bytes(report) + crc.to_bytes(CRC_LEN, "little")


def haptics_setup_report(seq: int) -> bytes:
    """Build the Bluetooth report that puts the actuators into audio-haptics mode (sent when a haptics stream starts).

    Measured on the real pad: valid_flag0 stays 0 (a rumble bit would take the actuators out of audio mode again), valid_flag1
    carries FLAG1_HAPTICS_SETUP and the motor power byte is 0. Sending it again brought the haptics back every time they were cut.

    Args:
        seq: The report sequence number (only the low 4 bits are used).

    Returns:
        The report bytes (78 B).
    """
    common = bytearray(COMMON_LEN)
    common[VALID_FLAG1_AT] = FLAG1_HAPTICS_SETUP
    common[MOTOR_POWER_AT] = 0
    return _bt_report(common, seq)


def output_report(bt: bool, seq: int, small: int, large: int, v2: bool | None = None,
                  rgb: Sequence[int] | None = None, triggers: tuple[bytes, bytes] | None = None,
                  haptics: bool = False, leds: tuple[int | None, int | None] = (None, None)) -> bytes:
    """Build the DualSense output report: rumble and (optionally) the lightbar colour, trigger effects and the LEDs.

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
        haptics: A haptics stream is running (Bluetooth): the report carries no rumble at all - valid_flag0 keeps only the
            trigger bits and valid_flag2 stays 0, so the motor values are ignored (and sent as 0). Lightbar and trigger
            reports do not take the actuators out of audio mode, any rumble bit does.
        leds: ``(mute button LED, player LEDs)`` set by the game; None = that LED is left to the kernel driver (its valid flag
            stays clear). Neither flag is a rumble bit, so they are safe in audio mode too.

    Returns:
        The report bytes.
    """
    common = bytearray(COMMON_LEN)
    if haptics:
        small = large = 0
    else:
        common[VALID_FLAG0_AT] = FLAG0_HAPTICS_SELECT | (0 if v2 else FLAG0_COMPAT_VIBRATION)
        common[VALID_FLAG2_AT] = FLAG2_COMPAT_VIBRATION2 if v2 is not False else 0
    if triggers is not None:
        common[VALID_FLAG0_AT] |= FLAG0_RIGHT_TRIGGER | FLAG0_LEFT_TRIGGER
        common[LEFT_TRIGGER_AT:LEFT_TRIGGER_AT + TRIGGER_LEN] = triggers[0]
        common[RIGHT_TRIGGER_AT:RIGHT_TRIGGER_AT + TRIGGER_LEN] = triggers[1]
    common[MOTOR_RIGHT_AT], common[MOTOR_LEFT_AT] = small & BYTE_MASK, large & BYTE_MASK
    if rgb is not None:
        common[VALID_FLAG1_AT] |= FLAG1_LIGHTBAR
        common[LIGHTBAR_AT:LIGHTBAR_AT + RGB_LEN] = bytes(channel & BYTE_MASK for channel in rgb)
    mute, player = leds
    if mute is not None:
        common[VALID_FLAG1_AT] |= FLAG1_MUTE_LED
        common[MUTE_LED_AT] = mute & BYTE_MASK
    if player is not None:
        common[VALID_FLAG1_AT] |= FLAG1_PLAYER_LEDS
        common[PLAYER_LEDS_AT] = player & PLAYER_LEDS_MASK
    if not bt:
        return bytes([USB_REPORT_ID]) + bytes(common) + bytes(USB_PADDING)
    return _bt_report(common, seq)


