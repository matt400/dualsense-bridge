"""Shared wire protocol of the sender and the receiver (v2, v3 with an HMAC key) plus the shared git update code.

This file must be byte-for-byte identical in sender/ and receiver/ (a test checks it).


Header "!BBH" = (VER<<4 | type), controller idx, sequence number; then the body,
optionally 8 B of HMAC-SHA256 (when a key is set).
  1 state   "6BbbHBB6i"  lx ly lt rx ry rt hatx haty mask battery% flags acc xyz gyro xyz
  2 touch   "BBHHBBHH"   2 fingers: active id x y   (DS4 scale: 1920x943)
  3 click   "B"          touchpad button: 0 released, 1 pressed, 2 pressed with 2 fingers (right button in mouse mode)
  4 mouse   "hhB"        dx dy flags: bit0 = tap (left button), bit1 = scrolling (dx, dy = the sum of the movements
                         of two fingers, not cursor movement), bit2 = two-finger tap (right button),
                         bits 4-7 = tap number 1..15 (a tap is sent with repeats, the receiver clicks once)
  5 ping / 7 pong "Q"    latency measurement (idx = 255)
  6 feedback (PC -> sender) "5B" small_rumble large_rumble R G B
  8 bye     ""           sender: the pad disconnected -> remove the virtual DS4
  9 ident   "17s"        sender: a fixed pad identifier (MAC) - the key of the remap profile
 10 cmd     "BB"         PC -> sender: command, argument (CMD_POWER_OFF = switch the pad off; v9+: CMD_GYRO_CAL = calibrate
                         the gyro of a pad lying still for arg seconds, 0 = only the status; CMD_RAW_STICKS = sticks without
                         the sender's deadzone for arg seconds, 0 = end - the drift measurement in the window)
 11 info    "HHH"        sender -> PC every 1 s: ping x10 [ms] (0xFFFF = none), pad report Hz, motion Hz
 12 version "BBB"        sender -> PC together with ident: the sender version, rumble ways (bit 1 = evdev FF driver,
                         bit 2 = HID report through hidraw; 0 = none), the reason for none (WHY_*)
 13 build   "7sIB"       sender -> PC every 5 s and after an update (idx = 255): the sender's code commit (7 characters of the hash),
                         the commit time (unix), the update state UPD_*; an older receiver ignores the unknown type
 14 updmsg  "120s"       sender -> PC after an update (idx = 255): the reason for a git pull failure (UTF-8, truncated) - the window shows
                         it instead of "details in the log on the phone"; an older receiver ignores the unknown type
 15 trig    "4B"         PC -> sender: constant trigger resistance from the game profile: L2 start 0-9, L2 strength 0-8, R2 start,
                         R2 strength (strength 0 = no resistance); only through the HID report (hidraw); only to a v6+ sender
  0 event   "HBBH56s"    sender -> PC (idx = pad slot or 255): an event for the log in the window - number (a repeat
                         does not duplicate the entry), level EV_*, code E_*, age [s] (an event from before the connection to the receiver,
                         e.g. a sent Wake-on-LAN), argument (UTF-8); only when the receiver enabled them in the scfg packet

Longer variants (the same type in the header, recognised by length - the type has only 4 bits, and an older side rejects an
unknown length, so a variant only goes to a side that is known to understand it):
  state2 (type 1) "6BbbHBB6iIHH" state + a latency measurement: the send time in the PC clock [µs, low 32 bits], the time on the
                         phone from the kernel event to sending [µs], the Bluetooth delay above the minimum over the last
                         2 s [µs] (LAT_NONE = none). The sender sends it only once it gets a pong2 (the receiver knows it)
  pong2  (type 7) "QQ"   PC -> sender right before the plain pong: the echo of the ping, the PC time [µs] - the sender computes the clock
                         offset (like NTP, from the measurement with the smallest RTT); an older sender rejects the length, takes the pong
  scfg   (type 10) "B6s4sBH"  PC -> sender (idx = 255), on change and every 5 s: SCFG_* flags, the PC's MAC for Wake-on-LAN
                         (zeros = take it from the phone's ARP table), the broadcast address (zeros = automatic), the button of the
                         combination with PS (the
                         bit number in the button mask), the hold time [ms]; the sender saves this itself (wol.json), because
                         while the PC is off there is nowhere else to get it; an older sender rejects the length
  build2 (type 13) "7sIB6s4s" sender -> PC: build + the PC's MAC and broadcast that Wake-on-LAN will use (zeros = unknown);
                         only to a receiver from which scfg arrived (an older one would reject the length and would not know the version)
  build3 (type 13) "7sIB6s4sB" build2 + the battery of the sender's own device (a phone, a laptop): bits 0-6 the percentage,
                         bit 7 = charging (BATT_CHARGING). Only from a sender device that has a battery, and only to a receiver
                         that announced SCFG_BATT in scfg; otherwise build2 / build as before
  calres (type 11) "BB3i3iI" sender -> PC: the answer to CMD_GYRO_CAL - state CAL_*, flags CALF_*, the saved gyro bias (x, y, z
                         in evdev units), the bias used now (from startup calibration / autobias), the save time (unix);
                         only in answer to a command (a v9+ receiver sends it, so it knows this variant)
  trig2  (type 15) "12B" PC -> sender: trigger effects - L2: mode TFX_*, a, b, c; R2: mode, a, b, c; game rumble on the
                         triggers: RFX_* flags, strength [%], frequency [Hz], 0. Mode parameters:
                           TFX_RES   a = start 0-9 (a zone = 10 % of the travel), b = strength 1-8
                           TFX_CLICK a = click point 2-7 (20-70 % of the travel), b = strength 1-8
                           TFX_VIB   a = start 0-9, b = amplitude 1-8, c = frequency 1-255 Hz
                         Game rumble (RFX_ON): L2 vibrates by the large motor, R2 by the small one; while it vibrates it replaces
                         the trigger effect, after RFX_HOLD_S of silence the trigger returns to its effect. Only to a sender with
                         CAP_TRIG_FX
                         (v8+); an older one gets the plain trig (resistance only)
  trig3  (type 15) "12B11s11sB" PC -> sender: trig2 from the profile + trigger effects set by the game on the virtual DualSense
                         (HIDMaestro): the raw 11 B of the L2, R2 effect (as in the pad's output report), TRG3_* flags (which
                         game effect is valid). On every trigger the profile wins (a mode other than TFX_OFF), otherwise the game's effect;
                         when the game sets effects, the receiver switches game rumble on the triggers off in trig2. Only to a sender
                         with CAP_TRIG_RAW (v11+); an older one gets trig2 / trig (the profile only)
The phone's Bluetooth driven from the window (v10+; a phone with no screen). The sender announces the support itself - with a btstate
packet (idx 255)
to the receiver from which scfg arrived; the receiver sends btcmd only after btstate (an older sender would reject the length):
  btcmd   (type 10) "BB6s"      PC -> sender (idx 255): operation BT_*, argument (BT_SCAN_START: seconds, at most
                               BT_SCAN_MAX_S; BT_POWER: 1 / 0; BT_ADAPTER: mode BTA_AUTO / BTA_MANUAL;
                               BT_ADAPTER_USE: the number N of the hciN controller from the btctrl list), the device's MAC (zeros = none).
                               BT_ADAPTER* only to a sender that reports the mode in btstate (an older one skips the unknown
                               operation). BT_LIST = "panel open":
                               for BT_ACTIVE_S the sender sends the state and the device list every second, afterwards only the state
                               every 5 s
  btstate (type 12) "BBBBBHBB6s" sender -> PC (idx 255): the adapter state BTS_*, flags BTF_* (bits 6-7: the adapter choice
                               mode BTA_*, 0 = a sender without a choice), seconds of the scan left,
                               connected devices, devices in the list, the list round, the last operation BT_*, its result
                               BTR_*, its MAC; right after it (in active mode) the devices of the round
  btdev   (type 9)  "HBB6sbB32s" sender -> PC (idx 255): round, number, how many in the round, MAC, RSSI [dBm] (BT_RSSI_NONE =
                               none - outside a scan), flags BTD_*, name (UTF-8, truncated); the receiver replaces the list only
                               once it has the whole round (a lost packet = the list from the previous round, not one with holes)
  btctrl  (type 9)  "HBBBB40s"  sender -> PC (idx 255): the sender's Bluetooth controllers (built-in, USB dongle) -
                               round, number, how many, flags BTC_*, N of hciN, device name (UTF-8); a round goes along with the
                               device list, only from a sender with a controller choice (the mode in btstate); an older receiver
                               rejects the unknown length

Format v3 (only with an HMAC key) - protection against replaying a recorded packet (replay):
  a header with version 3 instead of 2, the body as above, then the freshness token "!I" and 8 B of HMAC-SHA256 (computed over the whole
  together with the token). The token is the RECEIVER's time in ms (low 32 bits): the sender writes the PC time (from PcClock -
  pong2), the receiver the sender's time (from the last ping + the time that has passed). The receiver accepts a packet when the token
  is at most FRESH_PAST_MS in the past and FRESH_FUTURE_MS in the future, and exactly the same packet (the same HMAC)
  did not arrive within that window - a recording from a few seconds ago or from another session is rejected. The clocks of both sides
  have a
  random offset per process (EpochClock), so a token from a previous run (or from before a system restart)
  does not fall into the window. A ping (FRESH_EXEMPT) is not checked - it is how the other side learns the sender's
  clock; a replayed ping yields at most a pong, which the sender rejects (RTT > 1 s).
  The switch to v3: a sender with a key sends an extra v3 ping every second (an older receiver rejects it like any
  unknown version). The side that gets a valid v3 packet sends only v3 from then on and rejects v2 with the key
  (recordings from before the update) - permanently: the key's fingerprint is saved in a state file (auth.json), so a restart does not
  open the window to recorded v2 packets. Changing the key = a new fingerprint = the switch starts over.

At the end: shared git functions (code version, git pull update) - the receiver and the sender update the same way.
"""
import contextlib
import hmac
import json
import os
import random
import secrets
import shlex
import shutil
import struct
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Collection, Hashable, Sequence
from typing import Any, Final, NamedTuple, TypeAlias, TypeVar

Packet: TypeAlias = bytes | bytearray | memoryview

# ---------------------------------------------------------------------------------------------------------------------
# Header layout. The first byte carries the protocol version in its high nibble and the wire packet type in the low one.
# ---------------------------------------------------------------------------------------------------------------------
HDR: Final = struct.Struct("!BBH")                      # (version << 4 | wire type, controller idx, sequence number)
HEADER_LEN: Final = HDR.size
IDX_OFFSET: Final = 1                                   # byte offset of the controller index in the header
SEQ_OFFSET: Final = 2                                   # byte offset of the big-endian 16-bit sequence number
VER_SHIFT: Final = 4
WIRE_TYPE_MASK: Final = 0x0F                            # the type has only 4 bits: longer variants are told apart by length
SEQ_MASK: Final = 0xFFFF
SEQ_HALF_RANGE: Final = 0x8000                          # a sequence step below this is "newer", above it "older" (modulo 2**16)

VER: Final = 2
HDR0: Final = VER << VER_SHIFT
VER3: Final = 3                                         # a packet with a key: a freshness token + HMAC (see above)
HDR3: Final = VER3 << VER_SHIFT
FRESH_PAST_MS: Final = 3000                             # the freshness window of a v3 token: how far back a token may be ...
FRESH_FUTURE_MS: Final = 1000                           # ... and how far ahead (clock error between the two sides)
FRESH_WINDOW_S: Final = (FRESH_PAST_MS + FRESH_FUTURE_MS) / 1000
MAC_LEN: Final = 8                                      # the HMAC-SHA256 is truncated to this many bytes
TOKEN: Final = struct.Struct("!I")
TRAILER3: Final = TOKEN.size + MAC_LEN
UINT32_MASK: Final = 0xFFFFFFFF
UINT32_MODULUS: Final = 1 << 32
NS_PER_MS: Final = 1_000_000
EPOCH_BASE_BITS: Final = 60                             # ~36 years in ns: the clock plus its base still fits the ping's "Q"
KEY_FINGERPRINT_BYTES: Final = 8
KEY_MISMATCH_S: Final = 5.0     # for this long after a packet with a different HMAC key setting we also reject ambiguous lengths

# ---------------------------------------------------------------------------------------------------------------------
# Packet types. T_* below 0x100 are the plain 4-bit wire types; the longer variants carry the wire type in their low nibble
# and the variant number above it (0x1xx, 0x2xx), see WIRE.
# ---------------------------------------------------------------------------------------------------------------------
(T_EVENT, T_STATE, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_FB, T_PONG, T_BYE, T_IDENT, T_CMD, T_INFO, T_VERSION,
 T_BUILD, T_UPDMSG, T_TRIG) = range(16)
T_STATE2, T_PONG2, T_SCFG, T_CALRES, T_BUILD2, T_TRIG2 = 0x101, 0x107, 0x10A, 0x10B, 0x10D, 0x10F   # longer variants
T_TRIG3 = 0x20F                                                     # trigger effects from the profile and from the game (v11+)
T_BUILD3 = 0x30D                                                    # build2 + the sender device's battery
T_BTDEV, T_BTSTATE, T_BTCMD = 0x109, 0x10C, 0x20A                                                 # Bluetooth (v10+)
T_BTCTRL = 0x209                                                    # the sender's Bluetooth controllers (the choice from the window)
WIRE: Final[dict[int, int]] = {
    T_STATE2: T_STATE, T_PONG2: T_PONG, T_SCFG: T_CMD, T_CALRES: T_INFO, T_BUILD2: T_BUILD, T_TRIG2: T_TRIG,
    T_BTDEV: T_IDENT, T_BTSTATE: T_VERSION, T_BTCMD: T_CMD, T_BTCTRL: T_IDENT, T_TRIG3: T_TRIG,
    T_BUILD3: T_BUILD,
}
FRESH_EXEMPT = frozenset({T_PING})

# ---------------------------------------------------------------------------------------------------------------------
# Capabilities and limits announced in the version packet, trigger effects.
# ---------------------------------------------------------------------------------------------------------------------
SENDER_VERSION: Final = 12
RUMBLE_EVDEV, RUMBLE_HIDRAW, CAP_TRIGGERS = 1, 2, 4     # bits of the version packet (CAP_TRIGGERS: trigger resistance works)
CAP_TRIG_FX: Final = 8                                  # a bit of the version packet: the sender knows trig2 (click, vibration)
CAP_CALIB: Final = 16                                   # a bit of the version packet: CMD_GYRO_CAL and CMD_RAW_STICKS (v9+)
CAP_TRIG_RAW: Final = 32                                # a bit of the version packet: the sender knows trig3 (effects from the game)
LAT_NONE: Final = 0xFFFF                                # the delay field in state2: no measurement (a repeat, no data)
TRIG_START_MAX, TRIG_FORCE_MAX = 9, 8                   # trigger resistance: the start zone 0-9 (every 10 %), strength 1-8
TFX_OFF, TFX_RES, TFX_CLICK, TFX_VIB = range(4)         # trigger effect mode (trig2 packet)
TRIG_CLICK_MIN, TRIG_CLICK_MAX = 2, 7                   # click point: zone 2-7 (pad: end = point + 1, max 8)
TRIG_AMP_MAX, TRIG_FREQ_MAX = 8, 255                    # trigger vibration: amplitude 1-8, frequency 1-255 Hz
RFX_ON: Final = 1                                       # trig2 flag: game rumble on the triggers
RFX_GAIN_MAX: Final = 200                               # the strength of game rumble on the triggers [%]
RFX_HOLD_S: Final = 0.3                                 # this much motor silence before a trigger returns to its own effect
TRIG2_OFF: Final = (TFX_OFF, 0, 0, 0) * 2 + (0, 0, 0, 0)    # trig2: everything off
TRG3_L2, TRG3_R2 = 1, 2                                 # trig3 flags: the game effect for L2 / R2 is valid
TRIG_RAW_LEN: Final = 11                                # a trigger effect in the DualSense output report [B]
CLICK_RELEASED, CLICK_PRESSED, CLICK_TWO_FINGERS = 0, 1, 2   # click packet: the touchpad button state (2 = pressed with 2 fingers)
NO_PAD: Final = 255                                     # controller idx of a packet about the sender / the link, not about one pad
IDENT_LEN: Final = 17                                   # the ident packet's text field: the pad's MAC (or a short hash)
BATT_UNKNOWN: Final = 255                               # the state packet's battery %: unknown (USB pad, no power-supply entry)
STATE_CHARGING: Final = 0x01                            # bit 0 of the state packet's flags byte: charging or full
BATT_CHARGING: Final = 0x80                             # bit 7 of build3's battery byte: the sender device is charging (bits 0-6 = %)
MOUSE_TAP: Final = 0x01                                 # mouse packet flags: tap = left button ...
MOUSE_SCROLL: Final = 0x02                              # ... dx, dy are a two-finger scroll, not cursor movement ...
MOUSE_TAP_RIGHT: Final = 0x04                           # ... two-finger tap = right button ...
MOUSE_TAP_ID_SHIFT: Final = 4                           # ... bits 4-7: the tap number 1..MOUSE_TAP_ID_MAX
MOUSE_TAP_ID_MAX: Final = 15
# why there is no rumble through the HID report (version packet): ok, rumble=off, no permission, a kernel without hidraw,
# the pad's hidraw was not found, another open error
WHY_OK, WHY_OFF, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OPEN = range(6)

# ---------------------------------------------------------------------------------------------------------------------
# Commands (PC -> sender), gyro calibration, update state, events.
# ---------------------------------------------------------------------------------------------------------------------
CMD_POWER_OFF, CMD_UPDATE, CMD_GYRO_CAL, CMD_RAW_STICKS = 1, 2, 3, 4
CAL_MAX_S, RAW_STICKS_MAX_S = 10, 30                    # the longest gyro calibration / sticks without a deadzone [s]
# state in the calres packet: calibrated, the pad moved, too few samples (no motion sensor), just the status (arg 0), running
CAL_OK, CAL_MOVED, CAL_FEW, CAL_STATUS, CAL_RUNNING = range(5)
CALF_STORED: Final = 1                                  # calres flag: the sender has a saved bias for this pad
# the sender's update state (build packet): none, running, no change, updated (restart), errors
UPD_NONE, UPD_RUNNING, UPD_NO_CHANGE, UPD_DONE, UPD_NOT_GIT, UPD_NO_GIT, UPD_FAILED = range(7)
RTT_NONE: Final = 0xFFFF
SCFG_WOL, SCFG_EVENTS, SCFG_BATT = 1, 2, 4                # scfg packet flags: Wake-on-LAN, events for the log, build3 understood
EV_INFO, EV_WARN, EV_ERROR = range(3)                   # event level (event packet)
# the sender's event codes (event packet) - the text in the receiver window (rx_events.SENDER_TEXT), the argument in {arg}
(E_START, E_PAD_ON, E_PAD_OFF, E_POWER_OFF, E_WOL, E_WOL_FAIL, E_NO_RECEIVER, E_NET_DOWN, E_ERROR,
 E_NO_SLOT) = range(1, 11)          # E_NO_SLOT: a pad over max_pads (an older receiver shows a generic text)

# ---------------------------------------------------------------------------------------------------------------------
# Wake-on-LAN magic packet.
# ---------------------------------------------------------------------------------------------------------------------
WOL_PORTS: Final = (9, 7)                               # magic packet: discard and echo (network cards listen to either)
WOL_SYNC_LEN: Final = 6                                 # the magic packet starts with 6 bytes of 0xFF ...
WOL_MAC_REPEATS: Final = 16                             # ... followed by the target MAC repeated 16 times
MAC_BYTES: Final = 6
MAC_TEXT_DIGITS: Final = 2 * MAC_BYTES

# ---------------------------------------------------------------------------------------------------------------------
# The phone's Bluetooth from the window (btcmd / btstate / btdev / btctrl).
# ---------------------------------------------------------------------------------------------------------------------
# operations
(BT_LIST, BT_SCAN_START, BT_SCAN_STOP, BT_PAIR, BT_CONNECT, BT_DISCONNECT, BT_REMOVE, BT_POWER, BT_ADAPTER,
 BT_ADAPTER_USE) = range(10)
# choice of the sender's Bluetooth controller: the best (a USB dongle before the built-in one), chosen in the window, unchanged (manual)
BTA_AUTO, BTA_PICK, BTA_MANUAL = 1, 2, 3
# btctrl flags: on USB, USB on a fixed port (laptop), in use, blocked (rfkill), chosen manually in the window
BTC_USB, BTC_FIXED, BTC_ACTIVE, BTC_BLOCKED, BTC_PICKED = 1, 2, 4, 8, 16
# adapter state: works, off, blocked (rfkill), no adapter, no bluetoothd, no permission, no busctl, another error
BTS_OK, BTS_OFF, BTS_BLOCKED, BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_DENIED, BTS_NO_TOOL, BTS_ERROR = range(8)
BTF_SCANNING, BTF_BUSY = 1, 2                           # btstate flags: a scan is running, an operation is running
# (4 = the sender's BTF_LIST) there is a built-in controller, there is a USB dongle, the dongle is in use; the BTA_* mode in bits 6-7
BTF_HAS_BUILTIN, BTF_HAS_USB, BTF_ON_USB, BTF_MODE_SHIFT = 8, 16, 32, 6
# operation result: none, running, succeeded, failed, time exceeded, unknown device, pairing rejected, no permission,
# adapter off / blocked, adapter busy (another operation)
BTR_NONE, BTR_RUNNING, BTR_OK, BTR_FAILED, BTR_TIMEOUT, BTR_NOT_FOUND, BTR_AUTH, BTR_DENIED, BTR_OFF, BTR_BUSY = range(10)
BTD_PAIRED, BTD_CONNECTED, BTD_TRUSTED, BTD_PAD = 1, 2, 4, 8   # btdev flags (BTD_PAD = a game controller)
BT_SCAN_MAX_S: Final = 30     # a scan takes the radio from connected pads (delay spikes) - always with a limit
BT_ACTIVE_S: Final = 6.0      # for this long after the last btcmd the sender sends the device list every second
BT_RSSI_NONE: Final = -128
BT_DEV_MAX: Final = 32        # the most devices in a round

# ---------------------------------------------------------------------------------------------------------------------
# Packet bodies (network byte order). The comment on each entry is the field order.
# ---------------------------------------------------------------------------------------------------------------------
BODY: Final[dict[int, struct.Struct]] = {
    T_STATE: struct.Struct("!6BbbHBB6i"),
    T_TOUCH: struct.Struct("!BBHHBBHH"),
    T_CLICK: struct.Struct("!B"),
    T_MOUSE: struct.Struct("!hhB"),
    T_PING: struct.Struct("!Q"),
    T_FB: struct.Struct("!5B"),
    T_PONG: struct.Struct("!Q"),
    T_BYE: struct.Struct("!"),
    T_IDENT: struct.Struct("!17s"),
    T_CMD: struct.Struct("!BB"),
    T_INFO: struct.Struct("!HHH"),
    T_VERSION: struct.Struct("!BBB"),           # the sender version, rumble ways (bits), the reason WHY_*
    T_BUILD: struct.Struct("!7sIB"),            # the sender's commit, the commit time, the update state UPD_*
    T_UPDMSG: struct.Struct("!120s"),           # the reason for the sender's update failure (UTF-8)
    T_TRIG: struct.Struct("!4B"),               # trigger resistance: L2 start, L2 strength, R2 start, R2 strength
    T_EVENT: struct.Struct("!HBBH56s"),         # number, level EV_*, code E_*, age [s], argument (UTF-8)
    T_STATE2: struct.Struct("!6BbbHBB6iIHH"),   # state + the send time (PC clock), phone, Bluetooth [µs]
    T_PONG2: struct.Struct("!QQ"),              # the echo of the ping, the PC time [µs]
    T_SCFG: struct.Struct("!B6s4sBH"),          # SCFG_* flags, the PC's MAC, broadcast, the combination button bit, the hold in ms
    T_CALRES: struct.Struct("!BB3i3iI"),        # state CAL_*, flags CALF_*, the saved bias xyz, the current bias xyz, the save time
    T_BUILD2: struct.Struct("!7sIB6s4s"),       # build + the PC's MAC and broadcast that Wake-on-LAN will use
    T_BUILD3: struct.Struct("!7sIB6s4sB"),      # build2 + the sender device's battery (% | BATT_CHARGING)
    T_TRIG2: struct.Struct("!12B"),             # effects L2, R2 (mode, a, b, c) + game rumble (flags, %, Hz, 0)
    T_TRIG3: struct.Struct("!12B11s11sB"),      # trig2 from the profile + the raw game effects L2, R2 + TRG3_* flags
    T_BTCMD: struct.Struct("!BB6s"),            # operation BT_*, argument, MAC
    T_BTSTATE: struct.Struct("!BBBBBHBB6s"),    # BTS_*, BTF_*, scan [s], connected, in the list, round, op, BTR_*, MAC
    T_BTDEV: struct.Struct("!HBB6sbB32s"),      # round, number, how many, MAC, RSSI, BTD_*, name
    T_BTCTRL: struct.Struct("!HBBBB40s"),       # round, number, how many, BTC_*, N of hciN, device name
}
STATE_FIELDS: Final = 17                        # the state fields in state / state2 (without the latency measurement)
BODY_LEN: Final = {ptype: body.size for ptype, body in BODY.items()}
# (the type in the header, the body length) -> the type in the program
BY_SIZE: Final[dict[tuple[int, int], int]] = {(WIRE.get(ptype, ptype), body.size): ptype for ptype, body in BODY.items()}
if len(BY_SIZE) != len(BODY):
    raise ValueError("two packet types share the same (wire type, body length) pair: the parser could not tell them apart")

# ---------------------------------------------------------------------------------------------------------------------
# Buttons. evdev key codes (linux/input-event-codes.h) of the DualSense buttons; the position in PAD_KEYS is the bit in the
# state packet's button mask, so the order is part of the protocol.
# ---------------------------------------------------------------------------------------------------------------------
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 304, 305, 307, 308           # cross, circle, triangle, square
BTN_TL, BTN_TR = 310, 311                                               # L1, R1
BTN_SELECT, BTN_START, BTN_MODE = 314, 315, 316                         # create, options, PS
BTN_THUMBL, BTN_THUMBR = 317, 318                                       # L3, R3
PAD_KEYS: Final = (BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR)
KEY_BIT: Final = {code: 1 << bit for bit, code in enumerate(PAD_KEYS)}

_Number = TypeVar("_Number", int, float)


def clamp(value: _Number, low: _Number, high: _Number) -> _Number:
    """Limit a value to the range ``low..high``.

    Args:
        value: The number to limit.
        low: The smallest allowed value.
        high: The largest allowed value.

    Returns:
        ``value`` limited to ``[low, high]``.
    """
    return max(low, min(high, value))


def trig2_from_legacy(vals: Sequence[int]) -> tuple[int, ...]:
    """Convert a trig packet into trig2 values (resistance only, no game rumble).

    Each side with a non-zero strength becomes a TFX_RES effect, a zero strength becomes TFX_OFF; the four trailing zeros
    are the (disabled) game-rumble fields.

    Args:
        vals: ``(L2 start, L2 strength, R2 start, R2 strength)``.

    Returns:
        The 12 values of a trig2 packet.
    """
    sides = ((TFX_RES, start, force, 0) if force else (TFX_OFF, 0, 0, 0) for start, force in (vals[:2], vals[2:4]))
    return (*(field for side in sides for field in side), 0, 0, 0, 0)


def mac_bytes(text: str | None) -> bytes | None:
    """Parse a MAC address written as ``aa:bb:cc:dd:ee:ff``, ``AA-BB-...`` or ``aabbccddeeff``.

    Separators are stripped, so any common notation works; an all-zero address counts as "no address".

    Args:
        text: The address text, or None.

    Returns:
        The 6 address bytes, or None for a bad notation or an all-zero address.
    """
    digits = "".join(c for c in (text or "") if c not in ":-. ").lower()
    if len(digits) != MAC_TEXT_DIGITS:
        return None
    try:
        raw = bytes.fromhex(digits)
    except ValueError:
        return None
    return raw if any(raw) else None


def mac_text(raw: bytes | None) -> str:
    """Format 6 address bytes as ``aa:bb:cc:dd:ee:ff``; an empty string for None or an all-zero address."""
    return ":".join(f"{octet:02x}" for octet in raw) if raw and any(raw) else ""


def magic_packet(mac: bytes) -> bytes:
    """Build a Wake-on-LAN magic packet: 6 x 0xFF and then the 6-byte MAC 16 times."""
    return b"\xff" * WOL_SYNC_LEN + bytes(mac) * WOL_MAC_REPEATS


def cstr(field: bytes) -> str:
    """Decode a text field of a packet (padded with zeros)."""
    return field.rstrip(b"\0").decode("utf-8", "replace")


def utf8_field(text: str | None, size: int, more: str = "") -> bytes:
    """Encode text as UTF-8 of at most ``size`` bytes, cut on a character boundary.

    Cutting on a boundary means :func:`cstr` never shows half a letter. A truncated text ends with ``more`` (e.g. "…"),
    so that the other side knows it is not the whole name.

    Args:
        text: The text to encode, or None for an empty field.
        size: The field size in bytes.
        more: The marker appended to a truncated text.

    Returns:
        The encoded bytes, never longer than ``size``.
    """
    raw = (text or "").encode("utf-8")
    if len(raw) <= size:
        return raw
    tail = more.encode("utf-8")
    return raw[:max(0, size - len(tail))].decode("utf-8", "ignore").encode("utf-8") + tail


class EpochClock:
    """A monotonic clock with a random per-process offset (nanoseconds).

    ``perf_counter`` counts from boot, so v3 freshness tokens (and pings) from a previous run or from before a system
    restart would fall into the freshness window; the random base keeps them out.

    Attributes:
        base: The random offset added to ``perf_counter_ns``.
    """

    def __init__(self) -> None:
        """Pick the random base of this process."""
        self.base = secrets.randbits(EPOCH_BASE_BITS)

    def ns(self) -> int:
        """Return the clock in nanoseconds (monotonic + the random per-process base)."""
        return time.perf_counter_ns() + self.base

    def ms32(self) -> int:
        """Return the clock in milliseconds, low 32 bits - the format of a v3 token."""
        return (self.ns() // NS_PER_MS) & UINT32_MASK


def token_fresh(token: int, now_ms: int) -> bool:
    """Tell whether a v3 token lies inside the freshness window of our clock (safe against 32-bit wrap-around).

    The age is computed modulo 2**32: a small age is the past (up to FRESH_PAST_MS), an age close to 2**32 is a slightly
    future token (up to FRESH_FUTURE_MS, i.e. a small clock error).

    Args:
        token: The token from the packet (ms, 32 bits).
        now_ms: Our clock (ms, 32 bits).

    Returns:
        True if the token is neither too old nor too far in the future.
    """
    age = (now_ms - token) & UINT32_MASK
    return age <= FRESH_PAST_MS or age >= UINT32_MODULUS - FRESH_FUTURE_MS


def key_fingerprint(key: bytes) -> str:
    """Return a fingerprint of the HMAC key for the state file (it does not reveal the key)."""
    return hmac.digest(key, b"dualsense-bridge v3", "sha256")[:KEY_FINGERPRINT_BYTES].hex()


def generate_key() -> str:
    """Return a random HMAC key to enter on both sides (``key = ...`` / ``--key`` / ``PADKEY``)."""
    return secrets.token_urlsafe(24)


def _identify(packet: Packet, ptype: int) -> tuple[int, int, int]:
    """Return ``(type, controller idx, sequence number)`` of a packet whose type has been resolved."""
    return ptype, packet[IDX_OFFSET], (packet[SEQ_OFFSET] << 8) | packet[SEQ_OFFSET + 1]


class Codec:
    """Packs and parses packets with an optional HMAC. ``pack()`` is thread-safe.

    The other side with a different key setting (a key on one side only, or different keys) -> packets are rejected
    and ``mismatch_t`` is the time of the last such packet (the window shows a warning). Without a key the length alone is
    not enough: state + 8 B of HMAC is as long as state2 (trig + HMAC = trig2, pong + HMAC = pong2), so a receiver without a
    key took the state of a sender with a key for state2 with the HMAC bytes as the latency measurement - pads "half
    worked" (without an identifier, a profile, rumble) instead of a clear error. A sender with a key also sends packets
    whose length with the HMAC fits no type without it (ping, ident, info - several times a second); after such a packet
    for KEY_MISMATCH_S we also reject those ambiguous ones.

    With a key: the v3 format (a freshness token, described at the top of the file).

    Attributes:
        key: The shared HMAC key (empty = no authentication).
        token: Returns the token for the other side (its clock in ms) or None while it is not known yet - then the packet
            does not go out and ``pack()`` returns None (a ping goes with the token 0).
        now_ms: Returns our clock (``EpochClock.ms32``) for checking incoming tokens.
        state_path: The file in which the key's fingerprint stays after the switch to v3 (without it the switch lasts
            only until the process ends).
        v3: Whether this side already speaks v3.
        mismatch_t: Time of the last packet from a side with a different key setting.
        legacy_t: Time of the last correctly signed v2 packet rejected after the switch to v3.
        stale_t: Time of the last v3 packet with an expired token (a recording / the clock).
        stale: How many v3 packets with an expired token were rejected.
        upgraded_t: Time of the switch to v3 in this process: for a moment afterwards v2 packets are still in flight.
    """

    def __init__(
        self,
        key: bytes = b"",
        token: Callable[[], int | None] | None = None,
        now_ms: Callable[[], int] | None = None,
        state_path: str | None = None,
    ) -> None:
        """Create a codec; see the class docstring for the meaning of the arguments."""
        self.key = key
        self._seq = random.getrandbits(16)
        self._lock = threading.Lock()
        self.mismatch_t = float("-inf")
        self.token, self.now_ms, self.state_path = token, now_ms, state_path
        self.legacy_t = float("-inf")
        self.stale_t = float("-inf")
        self.stale = 0
        self.upgraded_t = float("-inf")
        self._seen: dict[bytes, float] = {}     # the HMAC of accepted v3 packets -> until when it is remembered (exact replays)
        self.v3 = bool(key) and self._stored_fingerprint() == key_fingerprint(key)

    # ---- the switch to v3 (remembered in the state file) ----
    def _stored_fingerprint(self) -> str | None:
        """Return the key fingerprint saved in the state file by an earlier switch to v3, or None."""
        if not self.state_path:
            return None
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f).get("v3")
        except (OSError, ValueError, AttributeError):
            return None     # no file yet, unreadable or not our JSON object: behave as if v3 was never negotiated

    def _upgrade(self) -> None:
        """Switch to v3 for good and save the key fingerprint so that a restart keeps it.

        From now on only v3 is sent and v2 with the key is rejected.
        """
        self.v3 = True
        self.upgraded_t = time.monotonic()
        print("Packet authentication: peer speaks protocol v3 - replay protection on, v2 packets with the key rejected")
        if self.state_path:
            try:
                write_json(self.state_path, {"v3": key_fingerprint(self.key)})
            except OSError as e:
                print(f"WARNING: cannot save {self.state_path}: {e}")

    def pack(self, ptype: int, idx: int, *vals: int | bytes, v3: bool | None = None) -> bytes | None:
        """Build a packet.

        Layout: header (version | wire type, idx, sequence) + the struct-packed body. Without a key that is all. With a key,
        v3 adds the freshness token, and in both versions an 8-byte truncated HMAC-SHA256 of everything before it is appended.

        Args:
            ptype: The packet type (a T_* constant).
            idx: The controller index (or 255 for a packet that is not about a pad).
            *vals: The body fields in the order of ``BODY[ptype]``.
            v3: Force the format (a probe ping); None = the current one.

        Returns:
            The packet bytes, or None when v3 is in use and the other side's clock is not known yet.
        """
        with self._lock:
            self._seq = (self._seq + 1) & SEQ_MASK
            seq = self._seq
        wire_type = WIRE.get(ptype, ptype)
        body = BODY[ptype].pack(*vals)
        if not self.key:
            return HDR.pack(HDR0 | wire_type, idx, seq) + body
        if self.v3 if v3 is None else v3:
            token = self.token() if self.token is not None else None
            if token is None:
                if ptype not in FRESH_EXEMPT:
                    return None
                token = 0
            message = HDR.pack(HDR3 | wire_type, idx, seq) + body + TOKEN.pack(token & UINT32_MASK)
        else:
            message = HDR.pack(HDR0 | wire_type, idx, seq) + body
        return message + hmac.digest(self.key, message, "sha256")[:MAC_LEN]

    def _signature_ok(self, packet: Packet, length: int) -> bool:
        """Check, in constant time, the trailing HMAC of the first ``length`` bytes of a packet."""
        expected = hmac.digest(self.key, memoryview(packet)[:length - MAC_LEN], "sha256")[:MAC_LEN]
        return hmac.compare_digest(expected, packet[length - MAC_LEN:length])

    def _is_replay(self, mac: bytes, now: float) -> bool:
        """Tell whether a packet with this HMAC was accepted within the freshness window.

        Entries are kept in insertion order, which is also expiry order, so expired ones are dropped from the front.
        """
        seen = self._seen
        while seen:
            oldest = next(iter(seen))
            if seen[oldest] > now:
                break
            del seen[oldest]
        return mac in seen

    def _parse_v3(self, packet: Packet, wire: int) -> tuple[int, int, int] | None:
        """Validate a v3 packet and return ``(type, idx, seq)`` or None.

        The length must fit a known type, the HMAC must be right, the packet must not be an exact replay and its token must
        be fresh (except for exempt types). Accepted HMACs are remembered for the length of the freshness window. The first
        valid v3 packet switches this side to v3.
        """
        n = len(packet)
        ptype = BY_SIZE.get((wire, n - HEADER_LEN - TRAILER3)) if n >= HEADER_LEN + TRAILER3 else None
        if ptype is None:
            return None
        now = time.monotonic()
        if not self.key or not self._signature_ok(packet, n):
            self.mismatch_t = now                       # our packet, a bad signature: no key or a different key
            return None
        mac = bytes(packet[n - MAC_LEN:])
        if self._is_replay(mac, now):
            return None                                 # a recording or a deliberate copy of the packet
        if ptype not in FRESH_EXEMPT and self.now_ms is not None:
            token = TOKEN.unpack_from(packet, n - TRAILER3)[0]
            if not token_fresh(token, self.now_ms()):
                self.stale += 1
                self.stale_t = now
                return None
        self._seen[mac] = now + FRESH_WINDOW_S
        if not self.v3:
            self._upgrade()
        return _identify(packet, ptype)

    def _verified_v2_length(self, packet: Packet, wire: int, n: int) -> int | None:
        """Verify a v2 packet when a key is set; return the length without the HMAC, or None if it must be rejected.

        A bad signature on a length that fits a known type means the other side has a different key setting
        (``mismatch_t``). A correctly signed v2 packet after the switch to v3 is a recording or an old version
        (``legacy_t``), except right after the switch while v2 packets are still in flight.
        """
        if n < HEADER_LEN + MAC_LEN or not self._signature_ok(packet, n):
            if (wire, n - HEADER_LEN) in BY_SIZE or (wire, n - HEADER_LEN - MAC_LEN) in BY_SIZE:
                self.mismatch_t = time.monotonic()
            return None
        if self.v3:
            now = time.monotonic()
            if now - self.upgraded_t > KEY_MISMATCH_S:
                self.legacy_t = now
            return None
        return n - MAC_LEN

    def _unsigned_length_is_foreign(self, wire: int, n: int) -> bool:
        """Without a key: tell whether a length that fits a packet *with* an HMAC means the other side has a key.

        A length that fits only a signed packet is such a case, and it is remembered in ``mismatch_t``. A length that fits
        both (state + HMAC = state2) is ambiguous and is rejected for KEY_MISMATCH_S after the last such evidence.
        """
        if n < HEADER_LEN + MAC_LEN or (wire, n - HEADER_LEN - MAC_LEN) not in BY_SIZE:
            return False
        if (wire, n - HEADER_LEN) not in BY_SIZE:
            self.mismatch_t = time.monotonic()
            return True
        return time.monotonic() - self.mismatch_t < KEY_MISMATCH_S

    def parse(self, packet: Packet) -> tuple[int, int, int] | None:
        """Identify and authenticate a received packet.

        The packet type is found from the pair (type in the header, body length); a longer variant gets its own type. With a
        key, the HMAC is verified first; without one, lengths that only fit a packet *with* an HMAC mean the other side has
        a key, so such packets (and, for a while, the ambiguous ones) are rejected and remembered in ``mismatch_t``.

        Args:
            packet: The datagram as received.

        Returns:
            ``(type, controller idx, sequence number)``, or None for a bad signature / version / type / length / token.
        """
        n = len(packet)
        if n < HEADER_LEN:
            return None
        version = packet[0] >> VER_SHIFT
        wire = packet[0] & WIRE_TYPE_MASK
        if version == VER3:
            return self._parse_v3(packet, wire)
        if version != VER:
            return None
        if self.key:
            payload_end = self._verified_v2_length(packet, wire, n)
            if payload_end is None:
                return None
            n = payload_end
        elif self._unsigned_length_is_foreign(wire, n):
            return None
        ptype = BY_SIZE.get((wire, n - HEADER_LEN))
        return None if ptype is None else _identify(packet, ptype)


class SeqFilter:
    """Drops late and duplicated packets.

    After ``window`` seconds of silence it accepts any number (the other side may have restarted and begun from a random
    sequence number).

    Attributes:
        window: The silence, in seconds, after which any sequence number is accepted again.
    """

    def __init__(self, window: float = 1.0) -> None:
        """Create a filter with the given silence window in seconds."""
        self.window = window
        self._seen: dict[Hashable, tuple[int, float]] = {}

    def fresh(self, key: Hashable, seq: int, now: float) -> bool:
        """Tell whether packet number ``seq`` of stream ``key`` is new.

        Args:
            key: Identifies the stream (e.g. a pad slot and a packet type).
            seq: The 16-bit sequence number of the packet.
            now: The current monotonic time in seconds.

        Returns:
            True if ``seq`` is ahead of the last one (modulo 2**16) or the stream was silent for the whole window.
        """
        last = self._seen.get(key)
        if last is not None and now - last[1] < self.window and not 0 < ((seq - last[0]) & SEQ_MASK) < SEQ_HALF_RANGE:
            return False
        self._seen[key] = (seq, now)
        return True

    def forget(self, match: Callable[[Any], bool]) -> None:
        """Forget every stream whose key satisfies ``match(key)`` (e.g. when a pad disconnects).

        The keys are whatever the caller passed to :meth:`fresh`, hence the untyped argument of ``match``.
        """
        for key in [k for k in self._seen if match(k)]:
            del self._seen[key]


def load_config(path: str | None, defaults: Collection[str] | None = None) -> dict[str, Any]:
    """Load a ``.toml`` or ``.json`` configuration file and warn about unknown keys (typos).

    Args:
        path: The file to load; None or empty means "no file" and yields an empty dict.
        defaults: The known keys; any other key in the file is reported (and kept).

    Returns:
        The parsed top-level mapping.
    """
    if not path:
        return {}
    with open(path, "rb") as f:
        cfg = json.load(f) if path.lower().endswith(".json") else tomllib.load(f)
    if defaults:
        for key in cfg:
            if key not in defaults:
                print(f"WARNING: unknown config key {key!r} in {path} (ignored)")
    return cfg


def write_file(path: str, content: str | bytes) -> None:
    """Write a file atomically: a temporary file, fsync, then replace.

    An interrupted write, a power loss, or a PC sleep / shutdown right after the write (master actions) will not leave an
    empty or truncated file. Without the fsync, ``os.replace`` could swap in a file whose data had not reached the disk yet.
    A failed write removes the temporary file.

    Args:
        path: The destination file.
        content: The text (written as UTF-8) or bytes to store.
    """
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "wb") as f:
            f.write(content.encode("utf-8") if isinstance(content, str) else content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except BaseException:       # also KeyboardInterrupt / SystemExit: no stray temp file whatever stops the write
        with contextlib.suppress(OSError):
            os.remove(temp_path)
        raise


def write_json(path: str, obj: object, *, indent: int | None = None, sort_keys: bool = False, ensure_ascii: bool = True) -> None:
    """Write ``obj`` as JSON with :func:`write_file` (atomic), with a newline at the end.

    Args:
        path: The destination file.
        obj: A JSON-serialisable object.
        indent: As in ``json.dumps``.
        sort_keys: As in ``json.dumps``.
        ensure_ascii: As in ``json.dumps``.
    """
    write_file(path, json.dumps(obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n")


# ---------------- git: the code version and the update (receiver and sender) ----------------
GIT_PATHS: Final = (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe")
GIT_TIMEOUT_S: Final = 15
GIT_PULL_TIMEOUT_S: Final = 120
SSH_CONNECT_TIMEOUT_S: Final = 15
COMMIT_ABBREV_LEN: Final = 7                            # the commit hash length shown and sent in the build packet
# the sender's deploy key (read-only) and its known_hosts - in .git, so they never end up in the repository;
# created by sender/setup-update-key.sh. The service (root -> the repository owner) has no SSH agent or password for the key.
DEPLOY_KEY: Final = os.path.join(".git", "dualsense-deploy-key")
KNOWN_HOSTS: Final = os.path.join(".git", "dualsense-known-hosts")
# the generic ending of every connection error - the real reason is in the lines before it
_GENERIC_ERROR_LINES: Final = ("fatal: could not read from remote repository", "please make sure you have the correct access rights",
                               "and the repository exists")
# the first character of a fetch-summary line ("   a1b2..c3d4  main -> origin/main"): git's flag column (space, +, -, *, !, =, t)
_FETCH_REF_FLAGS: Final = " *+-=!t"
_AUTH_ERROR_MARKERS: Final = ("permission denied (publickey", "host key verification failed", "could not read username",
                              "authentication failed", "repository not found", "terminal prompts disabled", "passphrase")
_CREATE_NO_WINDOW: Final = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GitVersion(NamedTuple):
    """The code version of a repository."""

    commit: str         # a 7-character commit hash ("" when unknown)
    commit_time: int    # the commit's unix time
    subject: str        # the commit title


class PullResult(NamedTuple):
    """The outcome of :func:`git_pull`."""

    state: int          # an UPD_* constant
    error: str          # git's message on error, otherwise ""
    old_commit: str
    new_commit: str
    changed_files: list[str]


def find_git() -> str | None:
    """Locate the git executable: on PATH first, then the usual Windows install locations; None if not found."""
    if found := shutil.which("git"):
        return found
    per_user = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")
    return next((p for p in (*GIT_PATHS, per_user) if os.path.isfile(p)), None)


def ssh_command(root: str) -> str | None:
    """Build ``GIT_SSH_COMMAND`` for the sender.

    BatchMode means no password prompt (the service has no terminal, git would hang), and the deploy key is used if there is
    one.

    Args:
        root: The repository root, where the deploy key and known_hosts live (in ``.git``).

    Returns:
        The command line, or None to leave git's own setup unchanged (Windows: its own ssh / plink / Git Credential Manager).
    """
    if os.name != "posix" or "GIT_SSH_COMMAND" in os.environ or "GIT_SSH" in os.environ:
        return None
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}"]
    key = os.path.join(root, DEPLOY_KEY)
    if os.path.isfile(key):
        cmd += ["-i", key, "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={os.path.join(root, KNOWN_HOSTS)}",
                "-o", "StrictHostKeyChecking=accept-new"]
    return " ".join(shlex.quote(part) for part in cmd)


def _git_process_kwargs(root: str) -> dict[str, Any]:
    """Return the ``subprocess`` keyword arguments for running git in ``root``.

    On POSIX it disables terminal prompts (https without credentials becomes an error instead of waiting for a terminal;
    on Windows the Git Credential Manager may show a login window) and installs the SSH command. Only when running as root
    on a repository owned by someone else does it add user / group switching (with an empty supplementary group list) and a
    HOME that matches that user: the files after a pull then do not belong to root, and git does not refuse to work in
    someone else's repository ("dubious ownership").
    """
    env = dict(os.environ)
    if os.name == "posix":
        env["GIT_TERMINAL_PROMPT"] = "0"
    if ssh := ssh_command(root):
        env["GIT_SSH_COMMAND"] = ssh
    if os.name != "posix" or sys.platform == "win32" or os.geteuid() != 0:     # (sys.platform: POSIX-only calls below are
        return {"env": env}                                                    # skipped by type checkers on Windows)
    stat = os.stat(root)
    if stat.st_uid == 0:
        return {"env": env}
    import pwd      # POSIX only: imported here so that the module still loads on Windows
    try:
        entry = pwd.getpwuid(stat.st_uid)
        home, name = entry.pw_dir, entry.pw_name
    except KeyError:
        home, name = root, str(stat.st_uid)
    env.update(HOME=home, USER=name, LOGNAME=name)
    # extra_groups=[]: without root's groups (e.g. 0) - the git process has only the owner's privileges
    return {"user": stat.st_uid, "group": stat.st_gid, "extra_groups": [], "env": env}


def is_auth_error(message: str | None) -> bool:
    """Tell whether a git error is an access error to a private repository (SSH key / login).

    The fix is ``sender/setup-update-key.sh``.
    """
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _AUTH_ERROR_MARKERS)


def _is_noise(line: str) -> bool:
    """Tell whether a git output line carries no reason for an error.

    Noise is the generic ending of a connection error, git's hints (``hint:``) and the fetch progress (``From ...``,
    ``   a1b2..c3d4  main -> origin/main``, `` * [new branch] ...``).
    """
    lowered = line.lower()
    return lowered.startswith((*_GENERIC_ERROR_LINES, "hint:", "from ")) or (" -> " in line and line[:1] in _FETCH_REF_FLAGS)


def git_error(text: str | None) -> str:
    """Reduce git's output to the reason for an error, in one line.

    The reason starts at the first ``fatal:`` / ``error:`` line (with the files it concerns); hints, fetch progress and the
    generic "Could not read from remote repository..." are left out.
    """
    raw = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    useful = [line for line in raw if not _is_noise(line)] or raw
    start = next((k for k, line in enumerate(useful) if line.lstrip().lower().startswith(("fatal:", "error:"))), 0)
    return " / ".join(line.strip() for line in useful[start:start + 3])


def git(root: str, *args: str, git_exe: str | None = None, timeout: float = GIT_TIMEOUT_S,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run git in ``root`` (text output; without a console window on Windows).

    Args:
        root: The repository root (the working directory of the process).
        *args: The git arguments.
        git_exe: The git executable; None = :func:`find_git` or plain ``git``.
        timeout: Seconds before the process is killed.
        env: Extra environment variables for git.

    Returns:
        The completed process; the caller checks ``returncode``.
    """
    kwargs = _git_process_kwargs(root)
    if env:
        kwargs["env"] = {**kwargs["env"], **env}
    return subprocess.run([git_exe or find_git() or "git", "-c", "safe.directory=*", *args], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_CREATE_NO_WINDOW, **kwargs)


def git_version(root: str, git_exe: str | None = None) -> GitVersion:
    """Return the code version in the repository, or ``GitVersion("", 0, "")`` if it cannot be read."""
    try:
        out = git(root, "log", "-1", "--format=%h%x00%ct%x00%s", f"--abbrev={COMMIT_ABBREV_LEN}", git_exe=git_exe)
        commit, commit_time, subject = out.stdout.strip().split("\0", 2)
        return GitVersion(commit[:COMMIT_ABBREV_LEN], int(commit_time), subject)
    except (OSError, ValueError, subprocess.SubprocessError):
        return GitVersion("", 0, "")


def git_pull(root: str, git_exe: str | None = None, env: dict[str, str] | None = None,
             timeout: float = GIT_PULL_TIMEOUT_S) -> PullResult:
    """Run ``git pull --ff-only``: a fast-forward to the version from GitHub; local changes give an error, nothing is overwritten.

    Args:
        root: The repository root.
        git_exe: The git executable; None = :func:`find_git`.
        env: Extra variables for git. The receiver passes some that suppress login windows - otherwise a pull from a
            background process waited for an invisible Git Credential Manager window until the timeout.
        timeout: Seconds before the pull is given up.

    Returns:
        The UPD_* state, git's message on error, the old and the new commit and the changed files.
    """
    if not os.path.isdir(os.path.join(root, ".git")):
        return PullResult(UPD_NOT_GIT, "", "", "", [])
    git_exe = git_exe or find_git()
    if git_exe is None:
        return PullResult(UPD_NO_GIT, "", "", "", [])
    try:
        old = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        pull = git(root, "pull", "--ff-only", git_exe=git_exe, timeout=timeout, env=env)
        if pull.returncode:
            return PullResult(UPD_FAILED, git_error(pull.stderr or pull.stdout), old, old, [])
        new = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        if new == old:
            return PullResult(UPD_NO_CHANGE, "", old, new, [])
        changed = git(root, "diff", "--name-only", old, new, git_exe=git_exe).stdout.splitlines()   # names with spaces
    except (OSError, subprocess.SubprocessError) as e:
        return PullResult(UPD_FAILED, str(e), "", "", [])
    return PullResult(UPD_DONE, "", old, new, changed)
