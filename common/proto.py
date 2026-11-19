"""Shared wire protocol of the sender and the receiver (v2, v3 with an HMAC key) and the atomic settings writes.

Both sides come from one repository and are updated together, so each packet has one format: there are no variants for an
older peer. The version packet carries SENDER_VERSION; the receiver warns when the sender's differs from its own (the
Update button still reaches an older sender: ping, cmd and the update itself keep their formats).

The git update code shared by both sides is in gitupdate.py, next to this file.

There is one copy, in common/. The entry scripts (and the tests) add common/ to sys.path, so both sides `import proto`.


Header "!BBH" = (VER<<4 | type), controller idx, sequence number; then the body,
optionally 8 B of HMAC-SHA256 (when a key is set).
  1 state   "6BbbHBB6i"  lx ly lt rx ry rt hatx haty mask battery% flags acc xyz gyro xyz
  2 touch   "BBHHBBHH"   2 fingers: active id x y   (DS4 scale: 1920x943)
  3 click   "B"          touchpad button: 0 released, 1 pressed, 2 pressed with 2 fingers (right button in mouse mode)
  4 mouse   "hhB"        dx dy flags: bit0 = tap (left button), bit1 = scrolling (dx, dy = the sum of the movements
                         of two fingers, not cursor movement), bit2 = two-finger tap (right button),
                         bits 4-7 = tap number 1..15 (a tap is sent with repeats, the receiver clicks once)
  5 ping    "Q"          the sender's clock [ns], for the round trip and the clock offset (idx = 255); answered by pong2
  6 feedback (PC -> sender) "5B" small_rumble large_rumble R G B
  8 bye     ""           sender: the pad disconnected -> remove the virtual DS4
  9 ident   "17s"        sender: a fixed pad identifier (MAC) - the key of the remap profile
 10 cmd     "BB"         PC -> sender: command, argument (CMD_POWER_OFF = switch the pad off; CMD_GYRO_CAL = calibrate
                         the gyro of a pad lying still for arg seconds, 0 = only the status; CMD_RAW_STICKS = sticks without
                         the sender's deadzone for arg seconds, 0 = end - the drift measurement in the window)
 11 info    "HHH"        sender -> PC every 1 s: ping x10 [ms] (0xFFFF = none), pad report Hz, motion Hz
 12 version "BBB"        sender -> PC together with ident: SENDER_VERSION, rumble ways and capability bits (RUMBLE_*, CAP_*;
                         0 = none), the reason for no rumble through the HID report (WHY_*)
 14 updmsg  "120s"       sender -> PC after an update (idx = 255): the reason for a git pull failure (UTF-8, truncated) - the window shows
                         it instead of "details in the log on the phone"
  0 event   "HBBH56s"    sender -> PC (idx = pad slot or 255): an event for the log in the window - number (a repeat
                         does not duplicate the entry), level EV_*, code E_*, age [s] (an event from before the connection to the receiver,
                         e.g. a sent Wake-on-LAN), argument (UTF-8); only when the receiver enabled them in the scfg packet

Longer variants (the same type in the header, recognised by length - the type has only 4 bits):
  state2 (type 1) "6BbbHBB6iIHH" state + a latency measurement: the send time in the PC clock [µs, low 32 bits], the time on the
                         phone from the kernel event to sending [µs], the Bluetooth delay above the minimum over the last
                         2 s [µs] (LAT_NONE = none). Sent while the sender's PC clock is synchronised (pong2), the plain state otherwise
  pong2  (type 7) "QQ"   PC -> sender, the answer to every ping: the echo of the ping (the round trip), the PC time [µs] - the sender
                         computes the clock offset (like NTP, from the measurement with the smallest RTT)
  scfg   (type 10) "B6s4sBH"  PC -> sender (idx = 255), on change and every 5 s: SCFG_* flags, the PC's MAC for Wake-on-LAN
                         (zeros = take it from the phone's ARP table), the broadcast address (zeros = automatic), the button of the
                         combination with PS (the
                         bit number in the button mask), the hold time [ms]; the sender saves this itself (wol.json), because
                         while the PC is off there is nowhere else to get it
  build3 (type 13) "7sIB6s4sB" sender -> PC every 5 s and after an update (idx = 255): the sender's code commit (7 characters of the
                         hash), the commit time (unix), the update state UPD_*, the PC's MAC and broadcast that Wake-on-LAN will use
                         (zeros = unknown), the battery of the sender's own device (a phone, a laptop): bits 0-6 the percentage,
                         bit 7 = charging (BATT_CHARGING), BATT_UNKNOWN = the device has no battery
  calres (type 11) "BB3i3iI" sender -> PC: the answer to CMD_GYRO_CAL - state CAL_*, flags CALF_*, the saved gyro bias (x, y, z
                         in evdev units), the bias used now (from startup calibration / autobias), the save time (unix)
  trig3  (type 15) "12B11s11sB" PC -> sender: trigger effects. The first 12 B come from the profile - L2: mode TFX_*, a, b, c;
                         R2: mode, a, b, c; game rumble on the triggers: RFX_* flags, strength [%], frequency [Hz], 0. Mode parameters:
                           TFX_RES   a = start 0-9 (a zone = 10 % of the travel), b = strength 1-8
                           TFX_CLICK a = click point 2-7 (20-70 % of the travel), b = strength 1-8
                           TFX_VIB   a = start 0-9, b = amplitude 1-8, c = frequency 1-255 Hz
                         Game rumble (RFX_ON): L2 vibrates by the large motor, R2 by the small one; while it vibrates it replaces
                         the trigger effect, after RFX_HOLD_S of silence the trigger returns to its effect.
                         Then the trigger effects set by the game on the virtual DualSense (HIDMaestro): the raw 11 B of the L2, R2
                         effect (as in the pad's output report), TRG3_* flags (which game effect is valid). On every trigger the
                         profile wins (a mode other than TFX_OFF), otherwise the game's effect; when the game sets effects, the
                         receiver switches game rumble on the triggers off. Effects work only through the HID report (CAP_TRIGGERS)
  hapt   (type 10) "IB64s" PC -> sender (idx = pad slot): a chunk of the game's HD haptics for a Bluetooth pad - the number of
                         the chunk's first frame (3 kHz frames, wraps at 2**32: loss / reorder / drift detection), HAPT_* flags,
                         HAPT_FRAMES = 32 stereo frames (10.667 ms) of signed 8-bit samples, the left grip first. Not repeated (audio
                         tolerates a loss, a repeat only adds latency). Only to a pad whose version packet has CAP_HAPTICS
  spk    (type 10) "IB960s" PC -> sender (idx = pad slot): 10 ms of the game's sound for the pad's speaker - the number of the
                         frame (10 ms frames, wraps at 2**32), SPK_* flags, SPK_FRAME = 480 samples of 48 kHz mono, signed 16-bit
                         little endian (the sender encodes them to Opus itself). Not repeated. Only to a pad whose version packet
                         has CAP_SPEAKER
  leds   (type 6) "3B"   PC -> sender (idx = pad slot): what the game set on the virtual DualSense besides motors and colour -
                         LEDS_* flags (which of the two values is valid), the mute button LED (0 off, 1 on, 2 pulsing), the player
                         LEDs under the lightbar (5 bits, the middle LED is 0x04). The mute button itself travels in the state
                         packet: MUTE_BIT of the button mask (the sender sets it for a moment on every press)
  cfgset (type 10) "BBHd" PC -> sender (idx = 255): operation CFG_GET (answer: a cfgval for every setting) / CFG_SET (change the setting)
                         / CFG_RESET (drop the change: back to the value from sender.toml; CFG_ALL = all of them), the id of the
                         setting (position in cfgspec.SPECS), a reference that the answer repeats, the new value as a float64 (a bool
                         0 / 1, a choice = its index). Only with an HMAC key (so v3) - a sender without a key refuses (the answer
                         says so) and changes nothing. Repeated like a command (the sender handles one)
  cfgval (type 14) "BBHBdd" sender -> PC (idx = 255): the id of the setting (CFG_ALL for a refusal of the whole request), the result
                         CFGR_*, the reference of the request, flags CFGF_*, the value now in use and the one from sender.toml (so the
                         window can show "changed" and offer to go back). Sent for every setting after CFG_GET and for the one
                         changed after CFG_SET / CFG_RESET. In the answer to a read the last setting carries CFGF_LAST
The phone's Bluetooth driven from the window (a phone with no screen). The sender announces the support itself - with a btstate
packet (idx 255) to the receiver from which scfg arrived; the receiver sends btcmd only after btstate:
  btcmd   (type 10) "BB6s"      PC -> sender (idx 255): operation BT_*, argument (BT_SCAN_START: seconds, at most
                               BT_SCAN_MAX_S; BT_POWER: 1 / 0; BT_ADAPTER: mode BTA_AUTO / BTA_MANUAL;
                               BT_ADAPTER_USE: the number N of the hciN controller from the btctrl list), the device's MAC (zeros = none).
                               BT_ADAPTER* only to a sender that reports the mode in btstate. BT_LIST = "panel open":
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
                               device list, only from a sender with a controller choice (the mode in btstate)
  btlink  (type 12) "6sbBBB"    sender -> PC (idx 255): the radio link of one connected pad (the Diagnostics tab) - MAC, RSSI [dBm],
                               link quality 0-255, flags BTL_* (which of the two values were really read), N of hciN. One packet per
                               connected pad, with every btstate while the window is interested (BT_LIST). RSSI is the controller's
                               Read RSSI, as the controller reports it (most give dBm; the specification says dB relative to the Golden
                               Receive Power Range). Link quality 0 = the controller does not fill it in

  stcmd  (type 10) "BBHIH" PC -> sender (idx = pad slot): the pad self-test from the window - operation ST_*, argument, a reference
                         the answer repeats, a 32-bit value, a 16-bit value. ST_BEGIN arms the detailed measurement for the slot
                         (the sender answers with the system record), ST_SNAP asks for the counters of one audio stream (arg =
                         ST_HAPTICS / ST_SPEAKER) right now, or with arg ST_RECORDS the rumble records so far (it goes on measuring),
                         ST_END ends the measurement and sends everything it collected
  stres  (type 12) "BBH6I" sender -> PC (idx = pad slot): one record of the self-test - kind SR_*, flags, the reference of the request,
                         six 32-bit values whose meaning depends on the kind (see the SR_* constants). Times are in the PC clock
                         (low 32 bits of microseconds, like the state2 stamp), so the receiver compares them with its own. Only
                         in answer to stcmd, so a receiver that never asks never gets one

Format v3 (every packet when an HMAC key is set; without a key: v2, no trailer) - protection against replaying a recorded packet:
  a header with version 3 instead of 2, the body as above, then the freshness token "!I" and 8 B of HMAC-SHA256 (computed over the whole
  together with the token). The token is the RECEIVER's time in ms (low 32 bits): the sender writes the PC time (from PcClock -
  pong2), the receiver the sender's time (from the last ping + the time that has passed). The receiver accepts a packet when the token
  is at most FRESH_PAST_MS in the past and FRESH_FUTURE_MS in the future, and exactly the same packet (the same HMAC)
  did not arrive within that window - a recording from a few seconds ago or from another session is rejected. The clocks of both sides
  have a random offset per process (EpochClock), so a token from a previous run (or from before a system restart)
  does not fall into the window. A ping (FRESH_EXEMPT) is not checked - it is how the other side learns the sender's
  clock; a replayed ping yields at most a pong, which the sender rejects (RTT > 1 s). A signed v2 packet (a version from
  before v3, or a recording of one) is always rejected.

At the end: shared git functions (code version, git pull update) - the receiver and the sender update the same way.
"""
import contextlib
import hmac
import itertools
import json
import math
import os
import random
import secrets
import struct
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Collection, Hashable, Sequence
from typing import Any, Final, Protocol, TypeAlias, TypeVar

Packet: TypeAlias = bytes | bytearray | memoryview
# one body field of a packet: which kind each position takes is fixed by BODY[ptype] and checked by struct when packing
Field: TypeAlias = int | float | bytes


class EmitFn(Protocol):
    """Send one packet: its type (T_*), the pad slot (255 = none) and the body fields in the order of ``BODY[ptype]``."""

    def __call__(self, ptype: int, idx: int, /, *vals: Field) -> None:
        """Pack and send the packet."""

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
PERMISSION_BITS: Final = 0o7777                         # st_mode & this = the chmod value (rwx for user / group / other + setuid etc.)
UINT32_MODULUS: Final = 1 << 32
NS_PER_MS: Final = 1_000_000
EPOCH_BASE_BITS: Final = 60                             # ~36 years in ns: the clock plus its base still fits the ping's "Q"
KEY_MISMATCH_S: Final = 5.0     # for this long after a packet with a different HMAC key setting we also reject ambiguous lengths

# ---------------------------------------------------------------------------------------------------------------------
# Packet types. T_* below 0x100 are the plain 4-bit wire types; the longer variants carry the wire type in their low nibble
# and the variant number above it (0x1xx, 0x2xx), see WIRE.
# ---------------------------------------------------------------------------------------------------------------------
(T_EVENT, T_STATE, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_FB, T_PONG, T_BYE, T_IDENT, T_CMD, T_INFO, T_VERSION,
 T_BUILD, T_UPDMSG, T_TRIG) = range(16)
# T_PONG, T_BUILD and T_TRIG are only wire types now: their packets are pong2, build3 and trig3 (the variant numbers stay)
T_STATE2, T_PONG2, T_SCFG, T_CALRES = 0x101, 0x107, 0x10A, 0x10B     # longer variants
T_TRIG3 = 0x20F                                                     # trigger effects from the profile and from the game
T_BUILD3 = 0x30D                                                    # the sender's code version, Wake-on-LAN addresses, battery
T_BTDEV, T_BTSTATE, T_BTCMD = 0x109, 0x10C, 0x20A                                                 # Bluetooth
T_BTCTRL = 0x209                                                    # the sender's Bluetooth controllers (the choice from the window)
T_BTLINK = 0x30C                                                    # the radio link of a connected pad: RSSI, link quality
T_HAPT = 0x30A                                                      # a chunk of HD haptics for the pad (PC -> sender)
T_SPK = 0x40A                                                       # 10 ms of sound for the pad's speaker (PC -> sender)
T_CFGSET = 0x50A                                                    # read / change / reset a sender setting (PC -> sender)
T_CFGVAL = 0x10E                                                    # a sender setting: the value, the file's value, the result
T_LEDS = 0x106                                                      # mute button LED + player LEDs from the game (PC -> sender)
T_PADKIND = 0x40C                                                   # which kind of pad is in the slot + what it can do (sender -> PC)
T_STCMD = 0x60A                                                     # pad self-test request (PC -> sender)
T_STRES = 0x50C                                                     # pad self-test record (sender -> PC)
WIRE: Final[dict[int, int]] = {
    T_STATE2: T_STATE, T_PONG2: T_PONG, T_SCFG: T_CMD, T_CALRES: T_INFO,
    T_BTDEV: T_IDENT, T_BTSTATE: T_VERSION, T_BTCMD: T_CMD, T_BTCTRL: T_IDENT, T_TRIG3: T_TRIG,
    T_BUILD3: T_BUILD, T_BTLINK: T_VERSION, T_HAPT: T_CMD, T_SPK: T_CMD, T_LEDS: T_FB, T_CFGSET: T_CMD, T_CFGVAL: T_UPDMSG,
    T_PADKIND: T_VERSION, T_STCMD: T_CMD, T_STRES: T_VERSION,
}
FRESH_EXEMPT = frozenset({T_PING})

# ---------------------------------------------------------------------------------------------------------------------
# Capabilities and limits announced in the version packet, trigger effects.
# ---------------------------------------------------------------------------------------------------------------------
SENDER_VERSION: Final = 20                              # 20: one format per packet (no variants for an older peer)
# bits of the version packet: what works for this pad. CAP_TRIGGERS: trigger effects (the HID report); CAP_HAPTICS / CAP_SPEAKER:
# hapt chunks / spk frames (a Bluetooth pad with the HID report; the speaker also needs libopus). Bits 8, 16, 32 were version
# flags of older senders (trig2, calibration, trig3) and are left unused.
RUMBLE_EVDEV, RUMBLE_HIDRAW, CAP_TRIGGERS = 1, 2, 4
CAP_HAPTICS: Final = 64
HAPT_RATE: Final = 3000                                 # HD haptics: the pad's audio-haptics sample rate [Hz] ...
HAPT_FRAMES: Final = 32                                 # ... one chunk = 32 stereo frames = 10.667 ms ...
HAPT_CHUNK: Final = 2 * HAPT_FRAMES                     # ... = 64 B of signed 8-bit samples, the left grip first
HAPT_DATA_AT: Final = 5                                 # offset of the samples in the hapt body (after the u32 counter and the flags)
HAPT_START, HAPT_END = 1, 2                             # hapt flags: the first chunk of a stream / the last one
CAP_SPEAKER: Final = 128
SPK_RATE: Final = 48000                                 # speaker sound: 48 kHz mono, signed 16-bit little endian ...
SPK_FRAME: Final = 480                                  # ... one frame = 480 samples = 10 ms (one Opus frame) ...
SPK_BYTES: Final = 2 * SPK_FRAME                        # ... = 960 B
SPK_DATA_AT: Final = 5                                  # offset of the samples in the spk body (after the u32 counter and the flags)
SPK_START, SPK_END = 1, 2                               # spk flags: the first frame of a stream / the last one
PK_DUALSENSE, PK_DS4, PK_XBOX, PK_OTHER = range(4)       # padkind family: DualSense, DualShock 4, Xbox (360 / One / Series), any other pad
# padkind features: gyro + accelerometer, touchpad, RGB lightbar, adaptive triggers + HD haptics + speaker (DualSense only), rumble motors
PF_MOTION, PF_TOUCHPAD, PF_LIGHTBAR, PF_ADAPTIVE, PF_RUMBLE = 1, 2, 4, 8, 16
ST_BEGIN, ST_END, ST_SNAP = 1, 2, 3                     # stcmd operations: arm the measurement, end it and send the results, counters now
ST_RECORDS, ST_HAPTICS, ST_SPEAKER = 0, 1, 2     # stcmd ST_SNAP argument: the rumble records so far (measuring goes on) / an audio stream
ST_MAX_S: Final = 180                                   # the sender disarms by itself after this long without ST_END
# stres kinds and the meaning of their six values (v0..v5):
#   SR_SYS    flags SRF_*; v0 governor GOV_*, v1 clock [kHz], v2 maximum clock [kHz], v3 load x100, v4 pad firmware (0 = ?),
#             v5 CPUs | fast CPUs << 8
#   SR_COUNT  the always-on hidraw counters since the pad connected: v0 writes, v1 errors, v2 mean [us], v3 max [us], v4 queue full
#   SR_FB     one rumble write: flags SRC_* | way << 4 (1 evdev, 2 hidraw), ref = the packet's sequence number, v0 command arrived
#             [us], v1 write queued [us], v2 write done [us] (0 = never), v3 small | large << 8, v4 the write call [us], v5 errno
#   SR_WRITES hidraw write times of the test: flags 0 = reports, 1 = audio; v0 count, v1 p50, v2 p95, v3 p99, v4 max [us], v5 errors
#   SR_HAPT / SR_SPK  an audio stream, flags = part; part 0: chunks, played, underruns, overruns, dropped, repeated;
#             part 1: late, lost, stale, resyncs, unwritten, depth | target << 16 (both in 0.1 ms)
#   SR_DONE   the end of the answer to ST_END: v0 how many SR_FB records were sent
SR_SYS, SR_COUNT, SR_FB, SR_WRITES, SR_HAPT, SR_SPK, SR_DONE = range(1, 8)
SRF_LOW_LATENCY, SRF_RT, SRF_PINNED, SRF_QOS = 1, 2, 4, 8   # SR_SYS flags: low_latency, SCHED_FIFO, pinned to the fast cores, PM QoS
SRC_PC, SRC_REPEAT, SRC_WATCHDOG = 0, 1, 2              # SR_FB source: the PC's command, the sender's repeat, its watchdog stop
GOVERNORS: Final = ("performance", "schedutil", "ondemand", "conservative", "powersave", "userspace", "interactive")   # GOV_* = index + 1
LEDS_MUTE, LEDS_PLAYER = 1, 2                           # leds flags: the mute LED value / the player LEDs value is valid
MUTE_OFF, MUTE_ON, MUTE_PULSE = range(3)                # the pad's mute button LED: off, on, pulsing (the output report's byte)
PLAYER_LEDS_MASK: Final = 0x1F                          # the 5 player LEDs under the lightbar, one bit each
CFG_GET, CFG_SET, CFG_RESET = range(3)                  # cfgset operations: read, change a setting, drop the change (back to sender.toml)
CFG_ALL: Final = 255                                    # cfgset / cfgval key id: every setting (CFG_GET, CFG_RESET) or none (a refusal)
# cfgval result: applied now, saved but counts later (the next stream / pad connection), refused: the value is not allowed, an unknown
# setting, the sender has no HMAC key, the sender has remote changes switched off (remote_cfg_file = ""); 5 was "not v3" (unused)
CFGR_OK, CFGR_LATER, CFGR_INVALID, CFGR_UNKNOWN, CFGR_NO_KEY = range(5)
CFGR_OFF: Final = 6
CFGF_REMOTE: Final = 1                                  # cfgval flag: the value comes from a change made in the receiver window
CFGF_LAST: Final = 2                                    # cfgval flag: the last setting of a full read - the sender knows id + 1 settings
LAT_NONE: Final = 0xFFFF                                # the delay field in state2: no measurement (a repeat, no data)
TRIG_START_MAX, TRIG_FORCE_MAX = 9, 8                   # trigger resistance: the start zone 0-9 (every 10 %), strength 1-8
TFX_OFF, TFX_RES, TFX_CLICK, TFX_VIB = range(4)         # trigger effect mode (the profile part of trig3)
TRIG_CLICK_MIN, TRIG_CLICK_MAX = 2, 7                   # click point: zone 2-7 (pad: end = point + 1, max 8)
TRIG_AMP_MAX, TRIG_FREQ_MAX = 8, 255                    # trigger vibration: amplitude 1-8, frequency 1-255 Hz
RFX_ON: Final = 1                                       # trig3 flag: game rumble on the triggers
RFX_GAIN_MAX: Final = 200                               # the strength of game rumble on the triggers [%]
RFX_HOLD_S: Final = 0.3                                 # this much motor silence before a trigger returns to its own effect
TRIG2_OFF: Final = (TFX_OFF, 0, 0, 0) * 2 + (0, 0, 0, 0)    # the profile part of trig3 (12 B): everything off
TRG3_L2, TRG3_R2 = 1, 2                                 # trig3 flags: the game effect for L2 / R2 is valid
TRIG_RAW_LEN: Final = 11                                # a trigger effect in the DualSense output report [B]
CLICK_RELEASED, CLICK_PRESSED, CLICK_TWO_FINGERS = 0, 1, 2   # click packet: the touchpad button state (2 = pressed with 2 fingers)
NO_PAD: Final = 255                                     # controller idx of a packet about the sender / the link, not about one pad
IDENT_LEN: Final = 17                                   # the ident packet's text field: the pad's MAC (or a short hash)
BATT_UNKNOWN: Final = 255                               # battery %: unknown (state: USB pad, no power-supply entry; build3: none)
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
SCFG_WOL, SCFG_EVENTS = 1, 2                            # scfg packet flags: Wake-on-LAN, events for the log (4 was "build3 known")
EV_INFO, EV_WARN, EV_ERROR = range(3)                   # event level (event packet)
# the sender's event codes (event packet) - the text in the receiver window (rx_events.SENDER_TEXT), the argument in {arg}
(E_START, E_PAD_ON, E_PAD_OFF, E_POWER_OFF, E_WOL, E_WOL_FAIL, E_NO_RECEIVER, E_NET_DOWN, E_ERROR,
 E_NO_SLOT) = range(1, 11)          # E_NO_SLOT: a pad over max_pads

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
# btstate flags: a scan is running, an operation is running, a device list round follows right after this packet,
# there is a built-in controller, there is a USB dongle, the dongle is in use; the BTA_* mode in bits 6-7
BTF_SCANNING, BTF_BUSY, BTF_LIST = 1, 2, 4
BTF_HAS_BUILTIN, BTF_HAS_USB, BTF_ON_USB, BTF_MODE_SHIFT = 8, 16, 32, 6
# operation result: none, running, succeeded, failed, time exceeded, unknown device, pairing rejected, no permission,
# adapter off / blocked, adapter busy (another operation)
BTR_NONE, BTR_RUNNING, BTR_OK, BTR_FAILED, BTR_TIMEOUT, BTR_NOT_FOUND, BTR_AUTH, BTR_DENIED, BTR_OFF, BTR_BUSY = range(10)
BTD_PAIRED, BTD_CONNECTED, BTD_TRUSTED, BTD_PAD = 1, 2, 4, 8   # btdev flags (BTD_PAD = a game controller)
BT_SCAN_MAX_S: Final = 30     # a scan takes the radio from connected pads (delay spikes) - always with a limit
BT_ACTIVE_S: Final = 6.0      # for this long after the last btcmd the sender sends the device list every second
BT_RSSI_NONE: Final = -128
BT_DEV_MAX: Final = 32        # the most devices in a round
BTL_RSSI, BTL_LQ = 1, 2       # btlink flags: the RSSI value was read, the link quality value was read (the controller may not support one)

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
    T_BYE: struct.Struct("!"),
    T_IDENT: struct.Struct("!17s"),
    T_CMD: struct.Struct("!BB"),
    T_INFO: struct.Struct("!HHH"),
    T_VERSION: struct.Struct("!BBB"),           # the sender version, rumble ways (bits), the reason WHY_*
    T_UPDMSG: struct.Struct("!120s"),           # the reason for the sender's update failure (UTF-8)
    T_EVENT: struct.Struct("!HBBH56s"),         # number, level EV_*, code E_*, age [s], argument (UTF-8)
    T_STATE2: struct.Struct("!6BbbHBB6iIHH"),   # state + the send time (PC clock), phone, Bluetooth [µs]
    T_PONG2: struct.Struct("!QQ"),              # the echo of the ping, the PC time [µs]
    T_SCFG: struct.Struct("!B6s4sBH"),          # SCFG_* flags, the PC's MAC, broadcast, the combination button bit, the hold in ms
    T_CALRES: struct.Struct("!BB3i3iI"),        # state CAL_*, flags CALF_*, the saved bias xyz, the current bias xyz, the save time
    T_BUILD3: struct.Struct("!7sIB6s4sB"),      # commit, commit time, UPD_*, the PC's MAC and broadcast, battery (% | BATT_CHARGING)
    T_TRIG3: struct.Struct("!12B11s11sB"),      # the profile's effects (12 B) + the raw game effects L2, R2 + TRG3_* flags
    T_BTCMD: struct.Struct("!BB6s"),            # operation BT_*, argument, MAC
    T_BTSTATE: struct.Struct("!BBBBBHBB6s"),    # BTS_*, BTF_*, scan [s], connected, in the list, round, op, BTR_*, MAC
    T_BTDEV: struct.Struct("!HBB6sbB32s"),      # round, number, how many, MAC, RSSI, BTD_*, name
    T_BTCTRL: struct.Struct("!HBBBB40s"),       # round, number, how many, BTC_*, N of hciN, device name
    T_BTLINK: struct.Struct("!6sbBBB"),         # MAC, RSSI [dBm], link quality 0-255, BTL_*, N of hciN
    T_HAPT: struct.Struct(f"!IB{HAPT_CHUNK}s"),  # the first frame's number (3 kHz), HAPT_* flags, HAPT_FRAMES stereo frames (s8)
    T_SPK: struct.Struct(f"!IB{SPK_BYTES}s"),    # the frame's number (10 ms), SPK_* flags, SPK_FRAME mono samples (s16 little endian)
    T_PADKIND: struct.Struct("!BBHH"),          # family PK_*, features PF_*, USB vendor id, USB product id (0 = not known)
    T_STCMD: struct.Struct("!BBHIH"),           # operation ST_*, argument, reference, a 32-bit value, a 16-bit value
    T_STRES: struct.Struct("!BBH6I"),           # kind SR_*, flags, reference, six values
    T_LEDS: struct.Struct("!3B"),                # LEDS_* flags, the mute LED MUTE_*, the player LEDs (5 bits)
    T_CFGSET: struct.Struct("!BBHd"),           # operation CFG_*, setting id (cfgspec.SPECS) or CFG_ALL, reference, the value as float64
    T_CFGVAL: struct.Struct("!BBHBdd"),         # setting id, result CFGR_*, request reference, CFGF_* flags, value, sender.toml value
}
STATE_FIELDS: Final = 17                        # the state fields in state / state2 (without the latency measurement)
BODY_LEN: Final = {ptype: body.size for ptype, body in BODY.items()}
# (the type in the header, the body length) -> the type in the program
BY_SIZE: Final[dict[tuple[int, int], int]] = {(WIRE.get(ptype, ptype), body.size): ptype for ptype, body in BODY.items()}
# header + body (and, for v3, + the freshness token) as one Struct per type: Codec.pack builds a packet with a single
# pack() call instead of packing the header and the body separately and concatenating them (one bytes object, not three)
FRAMED: Final[dict[int, struct.Struct]] = {ptype: struct.Struct(HDR.format + body.format[1:]) for ptype, body in BODY.items()}
FRAMED3: Final[dict[int, struct.Struct]] = {ptype: struct.Struct(f.format + TOKEN.format[1:]) for ptype, f in FRAMED.items()}
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
# The mute button is not an evdev key (hid-playstation toggles its own state and drives the LED, no input event), so it is not in
# PAD_KEYS. The sender sets this bit above the real buttons for a moment on each press (v15+); a receiver masks unknown bits off.
MUTE_BIT: Final = 1 << len(PAD_KEYS)

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


def nearest_rank(sorted_values: Sequence[_Number], pct: float) -> _Number:
    """Return the ``pct`` percentile (0-100) of an ascending list by the nearest-rank method (the same as rx_latency uses).

    Args:
        sorted_values: The values, ascending; at least one.
        pct: The percentile, 0-100.

    Returns:
        The value at rank ``ceil(pct / 100 * n)`` (the first one for pct 0).
    """
    return sorted_values[max(0, math.ceil(len(sorted_values) * pct / 100) - 1)]


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

    With a key every packet is v3 (a freshness token, described at the top of the file); a signed v2 packet comes only
    from an older version of the other side (or a recording of one) and is rejected.

    Attributes:
        key: The shared HMAC key (empty = no authentication).
        token: Returns the token for the other side (its clock in ms) or None while it is not known yet - then the packet
            does not go out and ``pack()`` returns None (a ping goes with the token 0).
        now_ms: Returns our clock (``EpochClock.ms32``) for checking incoming tokens.
        mismatch_t: Time of the last packet from a side with a different key setting.
        legacy_t: Time of the last correctly signed v2 packet: the other side runs a version from before protocol v3.
        stale_t: Time of the last v3 packet with an expired token (a recording / the clock).
        stale: How many v3 packets with an expired token were rejected.
    """

    def __init__(
        self,
        key: bytes = b"",
        token: Callable[[], int | None] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        """Create a codec; see the class docstring for the meaning of the arguments."""
        self.key = key
        # next() of itertools.count is one C call that holds the GIL for its whole duration, so packets packed from several
        # threads (the receiver) still get distinct numbers - without a lock taken for every packet
        self._seq = itertools.count(random.getrandbits(16) + 1)
        self.mismatch_t = float("-inf")
        self.token, self.now_ms = token, now_ms
        self.legacy_t = float("-inf")
        self.stale_t = float("-inf")
        self.stale = 0
        self._seen: dict[bytes, float] = {}     # the HMAC of accepted v3 packets -> until when it is remembered (exact replays)

    def pack(self, ptype: int, idx: int, *vals: Field) -> bytes | None:
        """Build a packet.

        Layout: header (version | wire type, idx, sequence) + the struct-packed body. Without a key that is all (v2). With a
        key (v3) the freshness token follows, then an 8-byte truncated HMAC-SHA256 of everything before it.

        Args:
            ptype: The packet type (a T_* constant).
            idx: The controller index (or 255 for a packet that is not about a pad).
            *vals: The body fields in the order of ``BODY[ptype]``.

        Returns:
            The packet bytes, or None when v3 is in use and the other side's clock is not known yet.
        """
        seq = next(self._seq) & SEQ_MASK
        wire_type = WIRE.get(ptype, ptype)
        if not self.key:
            return FRAMED[ptype].pack(HDR0 | wire_type, idx, seq, *vals)
        token = self.token() if self.token is not None else None
        if token is None:
            if ptype not in FRESH_EXEMPT:
                return None
            token = 0
        message = FRAMED3[ptype].pack(HDR3 | wire_type, idx, seq, *vals, token & UINT32_MASK)
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
        be fresh (except for exempt types). Accepted HMACs are remembered for the length of the freshness window.
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
        return _identify(packet, ptype)

    def _reject_signed_v2(self, packet: Packet, wire: int, n: int) -> None:
        """Note why a v2 packet is rejected while a key is set (with a key only v3 is accepted).

        A correct signature: the other side has the key but a version from before protocol v3 (``legacy_t``). A bad one on a
        length that fits a known type: the other side has a different key setting (``mismatch_t``).
        """
        if n >= HEADER_LEN + MAC_LEN and self._signature_ok(packet, n):
            self.legacy_t = time.monotonic()
        elif (wire, n - HEADER_LEN) in BY_SIZE or (wire, n - HEADER_LEN - MAC_LEN) in BY_SIZE:
            self.mismatch_t = time.monotonic()

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
            self._reject_signed_v2(packet, wire, n)
            return None
        if self._unsigned_length_is_foreign(wire, n):
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
        for key in list(self._seen):        # a snapshot: ``fresh`` may add a key from another thread meanwhile
            if match(key):
                self._seen.pop(key, None)


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


def read_json_object(path: str) -> dict[str, Any]:
    """Read a JSON file whose top level must be an object (the settings and state files of both sides).

    Raises:
        FileNotFoundError: The file does not exist (callers usually treat that as "nothing saved yet").
        OSError: The file cannot be read.
        ValueError: The file is not valid JSON, or its top level is not an object (``json.JSONDecodeError`` is a ValueError).
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    return data


def write_file(path: str, content: str | bytes) -> None:
    """Write a file atomically: a temporary file, fsync, then replace.

    An interrupted write, a power loss, or a PC sleep / shutdown right after the write (master actions) will not leave an
    empty or truncated file. Without the fsync, ``os.replace`` could swap in a file whose data had not reached the disk yet.
    A failed write removes the temporary file. The new file keeps the permission bits of the one it replaces (POSIX): a
    config file that holds the HMAC key and was made 0600 must not come back from a rewrite as 0644. The temporary name
    carries the process and thread id, so two writers of the same file never share one temporary file.

    Args:
        path: The destination file.
        content: The text (written as UTF-8) or bytes to store.
    """
    temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        mode: int | None = os.stat(path).st_mode & PERMISSION_BITS
    except OSError:
        mode = None             # a new file: the default permissions
    try:
        with open(temp_path, "wb") as f:
            if mode is not None and sys.platform != "win32":
                os.fchmod(f.fileno(), mode)         # before the content is written: the file is never readable with data in it
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

