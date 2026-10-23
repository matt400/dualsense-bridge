"""Shared protocol v2 of the sender and the receiver (this file must be identical on both sides).

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
import hmac
import json
import os
import random
import secrets
import shlex
import shutil
import struct
import subprocess
import threading
import time

VER = 2
HDR0 = VER << 4
VER3 = 3                                                # a packet with a key: a freshness token + HMAC (see above)
HDR3 = VER3 << 4
FRESH_PAST_MS, FRESH_FUTURE_MS = 3000, 1000             # the freshness window of a v3 token (future: clock error)
MAC_LEN = 8
TOKEN = struct.Struct("!I")
TRAILER3 = TOKEN.size + MAC_LEN

(T_EVENT, T_STATE, T_TOUCH, T_CLICK, T_MOUSE, T_PING, T_FB, T_PONG, T_BYE, T_IDENT, T_CMD, T_INFO, T_VERSION,
 T_BUILD, T_UPDMSG, T_TRIG) = range(16)
T_STATE2, T_PONG2, T_SCFG, T_CALRES, T_BUILD2, T_TRIG2 = 0x101, 0x107, 0x10A, 0x10B, 0x10D, 0x10F   # longer variants
T_TRIG3 = 0x20F                                                     # trigger effects from the profile and from the game (v11+)
T_BTDEV, T_BTSTATE, T_BTCMD = 0x109, 0x10C, 0x20A                                                 # Bluetooth (v10+)
T_BTCTRL = 0x209                                                    # the sender's Bluetooth controllers (the choice from the window)
WIRE = {T_STATE2: T_STATE, T_PONG2: T_PONG, T_SCFG: T_CMD, T_CALRES: T_INFO, T_BUILD2: T_BUILD, T_TRIG2: T_TRIG,
        T_BTDEV: T_IDENT, T_BTSTATE: T_VERSION, T_BTCMD: T_CMD, T_BTCTRL: T_IDENT, T_TRIG3: T_TRIG}
SENDER_VERSION = 12
RUMBLE_EVDEV, RUMBLE_HIDRAW, CAP_TRIGGERS = 1, 2, 4     # bits of the version packet (CAP_TRIGGERS: trigger resistance works)
# a bit of the version packet: the sender knows trig2 (click, vibration, from the game)
CAP_TRIG_FX = 8
CAP_CALIB = 16                                          # a bit of the version packet: CMD_GYRO_CAL and CMD_RAW_STICKS (v9+)
# a bit of the version packet: the sender knows trig3 (trigger effects from the game)
CAP_TRIG_RAW = 32
LAT_NONE = 0xFFFF                                       # the delay field in state2: no measurement (a repeat, no data)
TRIG_START_MAX, TRIG_FORCE_MAX = 9, 8                   # trigger resistance: the start zone 0-9 (every 10 %), strength 1-8
TFX_OFF, TFX_RES, TFX_CLICK, TFX_VIB = range(4)         # trigger effect mode (trig2 packet)
TRIG_CLICK_MIN, TRIG_CLICK_MAX = 2, 7                   # click point: zone 2-7 (pad: end = point + 1, max 8)
TRIG_AMP_MAX, TRIG_FREQ_MAX = 8, 255                    # trigger vibration: amplitude 1-8, frequency 1-255 Hz
RFX_ON = 1                                              # trig2 flag: game rumble on the triggers
RFX_GAIN_MAX = 200                                      # the strength of game rumble on the triggers [%]
RFX_HOLD_S = 0.3                                        # this much motor silence before a trigger returns to its own effect
TRIG2_OFF = (TFX_OFF, 0, 0, 0) * 2 + (0, 0, 0, 0)       # trig2: everything off
TRG3_L2, TRG3_R2 = 1, 2                                 # trig3 flags: the game effect for L2 / R2 is valid
TRIG_RAW_LEN = 11                                       # a trigger effect in the DualSense output report [B]
# why there is no rumble through the HID report (version packet): ok, rumble=off, no permission, a kernel without hidraw,
# the pad's hidraw was not found, another open error
WHY_OK, WHY_OFF, WHY_ACCESS, WHY_NO_HIDRAW, WHY_NOT_FOUND, WHY_OPEN = range(6)
CMD_POWER_OFF, CMD_UPDATE, CMD_GYRO_CAL, CMD_RAW_STICKS = 1, 2, 3, 4
CAL_MAX_S, RAW_STICKS_MAX_S = 10, 30                    # the longest gyro calibration / sticks without a deadzone [s]
# state in the calres packet: calibrated, the pad moved, too few samples (no motion sensor), just the status (arg 0), running
CAL_OK, CAL_MOVED, CAL_FEW, CAL_STATUS, CAL_RUNNING = range(5)
CALF_STORED = 1                                         # calres flag: the sender has a saved bias for this pad
# the sender's update state (build packet): none, running, no change, updated (restart), errors
UPD_NONE, UPD_RUNNING, UPD_NO_CHANGE, UPD_DONE, UPD_NOT_GIT, UPD_NO_GIT, UPD_FAILED = range(7)
RTT_NONE = 0xFFFF
SCFG_WOL, SCFG_EVENTS = 1, 2                            # scfg packet flags: Wake-on-LAN, events for the log
EV_INFO, EV_WARN, EV_ERROR = range(3)                   # event level (event packet)
# the sender's event codes (event packet) - the text in the receiver window (rx_events.SENDER_TEXT), the argument in {arg}
(E_START, E_PAD_ON, E_PAD_OFF, E_POWER_OFF, E_WOL, E_WOL_FAIL, E_NO_RECEIVER, E_NET_DOWN, E_ERROR,
 E_NO_SLOT) = range(1, 11)          # E_NO_SLOT: a pad over max_pads (an older receiver shows a generic text)
WOL_PORTS = (9, 7)                                      # magic packet: discard and echo (network cards listen to either)
# the phone's Bluetooth from the window (btcmd / btstate / btdev): operations, adapter state, flags, operation result, device flags
(BT_LIST, BT_SCAN_START, BT_SCAN_STOP, BT_PAIR, BT_CONNECT, BT_DISCONNECT, BT_REMOVE, BT_POWER, BT_ADAPTER,
 BT_ADAPTER_USE) = range(10)
# choice of the sender's Bluetooth controller: the best (a USB dongle before the built-in one), chosen in the window, unchanged (manual)
BTA_AUTO, BTA_PICK, BTA_MANUAL = 1, 2, 3
# btctrl flags: on USB, USB on a fixed port (laptop), in use, blocked (rfkill), chosen manually in the window
BTC_USB, BTC_FIXED, BTC_ACTIVE, BTC_BLOCKED, BTC_PICKED = 1, 2, 4, 8, 16
# works, off, blocked (rfkill), no adapter, no bluetoothd, no permission, no busctl, another error
BTS_OK, BTS_OFF, BTS_BLOCKED, BTS_NO_ADAPTER, BTS_NO_SERVICE, BTS_DENIED, BTS_NO_TOOL, BTS_ERROR = range(8)
BTF_SCANNING, BTF_BUSY = 1, 2                           # btstate flags: a scan is running, an operation is running
# (4 = the sender's BTF_LIST) there is a built-in controller, there is a USB dongle, the dongle is in use; the BTA_* mode in bits 6-7
BTF_HAS_BUILTIN, BTF_HAS_USB, BTF_ON_USB, BTF_MODE_SHIFT = 8, 16, 32, 6
# none, running, succeeded, failed, time exceeded, unknown device, pairing rejected, no permission,
# adapter off / blocked, adapter busy (another operation)
BTR_NONE, BTR_RUNNING, BTR_OK, BTR_FAILED, BTR_TIMEOUT, BTR_NOT_FOUND, BTR_AUTH, BTR_DENIED, BTR_OFF, BTR_BUSY = range(10)
BTD_PAIRED, BTD_CONNECTED, BTD_TRUSTED, BTD_PAD = 1, 2, 4, 8   # btdev flags (BTD_PAD = a game controller)
BT_SCAN_MAX_S = 30            # a scan takes the radio from connected pads (delay spikes) - always with a limit
BT_ACTIVE_S = 6.0             # for this long after the last btcmd the sender sends the device list every second
BT_RSSI_NONE = -128
BT_DEV_MAX = 32               # the most devices in a round

HDR = struct.Struct("!BBH")
BODY = {
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
    T_TRIG2: struct.Struct("!12B"),             # effects L2, R2 (mode, a, b, c) + game rumble (flags, %, Hz, 0)
    T_TRIG3: struct.Struct("!12B11s11sB"),      # trig2 from the profile + the raw game effects L2, R2 + TRG3_* flags
    T_BTCMD: struct.Struct("!BB6s"),            # operation BT_*, argument, MAC
    T_BTSTATE: struct.Struct("!BBBBBHBB6s"),    # BTS_*, BTF_*, scan [s], connected, in the list, round, op, BTR_*, MAC
    T_BTDEV: struct.Struct("!HBB6sbB32s"),      # round, number, how many, MAC, RSSI, BTD_*, name
    T_BTCTRL: struct.Struct("!HBBBB40s"),       # round, number, how many, BTC_*, N of hciN, device name
}
STATE_FIELDS = 17                               # the state fields in state / state2 (without the latency measurement)
BODY_LEN = {t: s.size for t, s in BODY.items()}
assert BODY_LEN == {0: 62, 1: 36, 2: 12, 3: 1, 4: 5, 5: 8, 6: 5, 7: 8, 8: 0, 9: 17, 10: 2, 11: 6, 12: 3, 13: 12, 14: 120,
                    15: 4, T_STATE2: 44, T_PONG2: 16, T_SCFG: 14, T_CALRES: 30, T_BUILD2: 22, T_TRIG2: 12,
                    T_BTCMD: 8, T_BTSTATE: 15, T_BTDEV: 44, T_BTCTRL: 46, T_TRIG3: 35}, BODY_LEN
# (the type in the header, the body length) -> the type in the program
BY_SIZE = {(WIRE.get(t, t), s.size): t for t, s in BODY.items()}
assert len(BY_SIZE) == len(BODY), "dwa typy z tą samą długością w jednym typie nagłówka"

# evdev codes of the DualSense buttons -> a bit in the mask (the order = the bit position in the state packet)
PAD_KEYS = (304, 305, 307, 308, 310, 311, 314, 315, 316, 317, 318)
KEY_BIT = {c: 1 << k for k, c in enumerate(PAD_KEYS)}


def clamp(v, lo, hi):
    """Limit v to the range lo..hi."""
    return max(lo, min(hi, v))


def trig2_from_legacy(vals):
    """A trig packet (L2 start, strength, R2 start, strength) -> trig2 values (resistance only, no game rumble).

    Each side with a non-zero strength becomes a TFX_RES effect, a zero strength becomes TFX_OFF; the four trailing zeros
    are the (disabled) game-rumble fields.
    """
    sides = ((TFX_RES, start, force, 0) if force else (TFX_OFF, 0, 0, 0) for start, force in (vals[:2], vals[2:4]))
    return (*(v for side in sides for v in side), 0, 0, 0, 0)


def mac_bytes(text):
    """"aa:bb:cc:dd:ee:ff" / "AA-BB-..." / "aabbccddeeff" -> 6 B or None (a bad notation).

    Separators are stripped, so any common notation works; an all-zero address counts as "no address" (None).
    """
    h = "".join(c for c in (text or "") if c not in ":-. ").lower()
    if len(h) != 12:
        return None
    try:
        b = bytes.fromhex(h)
    except ValueError:
        return None
    return b if any(b) else None


def mac_text(b):
    """6 bytes -> "aa:bb:cc:dd:ee:ff"; an empty string for None or an all-zero address."""
    return ":".join(f"{x:02x}" for x in b) if b and any(b) else ""


def magic_packet(mac):
    """Wake-on-LAN: 6 x 0xFF and 16 x the MAC (6 B)."""
    return b"\xff" * 6 + bytes(mac) * 16


def cstr(b):
    """A text field of a packet (padded with zeros) -> str."""
    return b.rstrip(b"\0").decode("utf-8", "replace")


def utf8_field(text, size, more=""):
    """str -> UTF-8 bytes of at most `size`, cut on a character boundary (cstr() will not show half a letter); a truncated text
    ends with `more` (e.g. "…"), so that the other side knows it is not the whole name."""
    raw = (text or "").encode("utf-8")
    if len(raw) <= size:
        return raw
    tail = more.encode("utf-8")
    return raw[:max(0, size - len(tail))].decode("utf-8", "ignore").encode("utf-8") + tail


KEY_MISMATCH_S = 5.0          # for this long after a packet with a different HMAC key setting we also reject the ambiguous lengths
FRESH_EXEMPT = frozenset({T_PING})   # v3 without a token check: the receiver learns the sender's clock from the ping
_MASK32 = 0xFFFFFFFF


class EpochClock:
    """perf_counter with a random per-process offset [ns]: v3 freshness tokens (and pings) from a previous
    run or from before a system restart (perf_counter counts from boot) do not fall into the freshness window."""

    def __init__(self):
        self.base = secrets.randbits(60)              # ~36 years in ns - the sum fits in the ping's "Q"

    def ns(self):
        """The clock in nanoseconds (monotonic + the random per-process base)."""
        return time.perf_counter_ns() + self.base

    def ms32(self):
        """The clock in milliseconds, low 32 bits - the format of a v3 token."""
        return (self.ns() // 1_000_000) & _MASK32


def token_fresh(token, now_ms):
    """Whether a v3 token (ms, 32 bits) is inside the freshness window relative to our clock (now_ms, 32 bits); safe against wrap-around.

    The age is computed modulo 2**32: a small age is the past (up to FRESH_PAST_MS), an age close to 2**32 is a slightly
    future token (up to FRESH_FUTURE_MS, i.e. a small clock error).
    """
    age = (now_ms - token) & _MASK32
    return age <= FRESH_PAST_MS or age >= (1 << 32) - FRESH_FUTURE_MS


def key_fingerprint(key):
    """A fingerprint of the HMAC key for the state file (it does not reveal the key)."""
    return hmac.digest(key, b"dualsense-bridge v3", "sha256")[:8].hex()


def generate_key():
    """A random HMAC key to enter on both sides (key = ... / --key / PADKEY)."""
    return secrets.token_urlsafe(24)


class Codec:
    """Packing / parsing packets with an optional HMAC. pack() is thread-safe.

    The other side with a different key setting (a key on one side only, or different keys) -> packets are rejected
    and mismatch_t = the time of the last such packet (the window shows a warning). Without a key the length alone is not enough:
    state + 8 B of HMAC is as long as state2 (trig + HMAC = trig2, pong + HMAC = pong2), so a receiver without a key
    took the state of a sender with a key for state2 with the HMAC bytes as the latency measurement - pads "half worked" (without
    an identifier, a profile, rumble) instead of a clear error. A sender with a key also sends packets whose length
    with the HMAC fits no type without it (ping, ident, info - several times a second); after such a packet for
    KEY_MISMATCH_S we also reject those ambiguous ones.

    With a key: the v3 format (a freshness token, described at the top of the file). token() -> the token for the other side (its clock
    in ms) or None (we do not know it yet - the packet does not go out, pack() returns None; a ping goes with the token 0);
    now_ms() -> our clock (EpochClock.ms32) for checking incoming tokens; state_path - the file in which the key's
    fingerprint stays after the switch to v3 (without it the switch lasts only until the process ends)."""

    def __init__(self, key=b"", token=None, now_ms=None, state_path=None):
        self.key = key
        self._seq = random.getrandbits(16)
        self._lock = threading.Lock()
        self.mismatch_t = float("-inf")
        self.token, self.now_ms, self.state_path = token, now_ms, state_path
        self.legacy_t = float("-inf")   # the last correctly signed v2 packet rejected after the switch to v3
        self.stale_t = float("-inf")    # the last v3 packet with an expired token (a recording / the clock)
        self.stale = 0
        self.upgraded_t = float("-inf")  # the switch to v3 in this process: for a moment afterwards v2 packets are still in flight
        self._seen = {}                 # the HMAC of accepted v3 packets -> until when it is remembered (exact replays)
        self.v3 = bool(key) and self._stored_fp() == key_fingerprint(key)

    # ---- the switch to v3 (remembered in the state file) ----
    def _stored_fp(self):
        """The key fingerprint saved in the state file by an earlier switch to v3, or None."""
        if not self.state_path:
            return None
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f).get("v3")
        except (OSError, ValueError, AttributeError):
            return None

    def _upgrade(self):
        """Switch to v3 for good: from now on only v3 is sent and v2 with the key is rejected; the fingerprint is saved so a restart keeps
        it."""
        self.v3 = True
        self.upgraded_t = time.monotonic()
        print("Packet authentication: peer speaks protocol v3 - replay protection on, v2 packets with the key rejected")
        if self.state_path:
            try:
                write_json(self.state_path, {"v3": key_fingerprint(self.key)})
            except OSError as e:
                print(f"WARNING: cannot save {self.state_path}: {e}")

    def pack(self, ptype, idx, *vals, v3=None):
        """-> the packet bytes or None (v3 without a token for the other side). v3: force the format (a probe ping).

        Layout: header (version | wire type, idx, sequence) + the struct-packed body. Without a key that is all. With a key,
        v3 adds the freshness token, and in both versions an 8-byte truncated HMAC-SHA256 of everything before it is appended.
        """
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFF
            sq = self._seq
        body = BODY[ptype].pack(*vals)
        if not self.key:
            return HDR.pack(HDR0 | WIRE.get(ptype, ptype), idx, sq) + body
        if self.v3 if v3 is None else v3:
            tok = self.token() if self.token is not None else None
            if tok is None:
                if ptype not in FRESH_EXEMPT:
                    return None
                tok = 0
            msg = HDR.pack(HDR3 | WIRE.get(ptype, ptype), idx, sq) + body + TOKEN.pack(tok & _MASK32)
        else:
            msg = HDR.pack(HDR0 | WIRE.get(ptype, ptype), idx, sq) + body
        return msg + hmac.digest(self.key, msg, "sha256")[:MAC_LEN]

    def _sig_ok(self, data, n):
        """Constant-time check of the trailing HMAC of the first n bytes of a packet."""
        return hmac.compare_digest(hmac.digest(self.key, memoryview(data)[:n - MAC_LEN], "sha256")[:MAC_LEN],
                                   data[n - MAC_LEN:n])

    def _parse_v3(self, data, wire):
        """Validate a v3 packet: the length must fit a known type, the HMAC must be right, the packet must not be an exact
        replay and its token must be fresh (except for exempt types). Returns (type, idx, seq) or None.

        Accepted HMACs are remembered for the length of the freshness window (in insertion order, which is also expiry
        order, so expired ones are dropped from the front). The first valid v3 packet switches this side to v3.
        """
        n = len(data)
        t = BY_SIZE.get((wire, n - 4 - TRAILER3)) if n >= 4 + TRAILER3 else None
        if t is None:
            return None
        now = time.monotonic()
        if not self.key or not self._sig_ok(data, n):
            self.mismatch_t = now                       # our packet, a bad signature: no key or a different key
            return None
        mac = bytes(data[n - MAC_LEN:])
        seen = self._seen
        while seen:                                     # entries in the order of adding = the order of expiry
            k = next(iter(seen))
            if seen[k] > now:
                break
            del seen[k]
        if mac in seen:
            return None                                 # an exact replay (a recording or a deliberate copy of the packet)
        if t not in FRESH_EXEMPT and self.now_ms is not None:
            tok = TOKEN.unpack_from(data, n - TRAILER3)[0]
            if not token_fresh(tok, self.now_ms()):
                self.stale += 1
                self.stale_t = now
                return None
        seen[mac] = now + (FRESH_PAST_MS + FRESH_FUTURE_MS) / 1000
        if not self.v3:
            self._upgrade()
        return t, data[1], (data[2] << 8) | data[3]

    def parse(self, data):
        """-> (type, idx, seq) or None (a bad signature / version / type / length / token). A longer variant -> its own type.

        The packet type is found from the pair (type in the header, body length). With a key, the HMAC is verified first;
        without one, lengths that only fit a packet *with* an HMAC mean the other side has a key, so such packets (and, for
        a while, the ambiguous ones) are rejected and remembered in mismatch_t.
        """
        n = len(data)
        if n < 4:
            return None
        if data[0] >> 4 == VER3:
            return self._parse_v3(data, data[0] & 0x0F)
        if data[0] >> 4 != VER:
            return None
        wire = data[0] & 0x0F
        if self.key:
            if n < 12 or not self._sig_ok(data, n):
                if (wire, n - 4) in BY_SIZE or (wire, n - 12) in BY_SIZE:
                    self.mismatch_t = time.monotonic()  # our packet, a bad signature: no key or a different key
                return None
            if self.v3:                                 # the other side knows v3: v2 with the key = a recording / an old version
                now = time.monotonic()
                if now - self.upgraded_t > KEY_MISMATCH_S:   # not right after the switch (v2 packets are still in flight)
                    self.legacy_t = now
                return None
            n -= 8
        elif n >= 12 and (wire, n - 12) in BY_SIZE:     # the length fits a packet with an HMAC
            if (wire, n - 4) not in BY_SIZE:            # ... and none without it: the other side has a key
                self.mismatch_t = time.monotonic()
                return None
            if time.monotonic() - self.mismatch_t < KEY_MISMATCH_S:
                return None                             # ambiguous (state + HMAC = state2) next to a side with a key
        t = BY_SIZE.get((wire, n - 4))
        if t is None:
            return None
        return t, data[1], (data[2] << 8) | data[3]


class SeqFilter:
    """Drops late / duplicated packets. After `window` s of silence it accepts any number
    (the other side may have restarted and begun from a random seq)."""

    def __init__(self, window=1.0):
        self.window = window
        self._seen = {}

    def fresh(self, key, sq, now):
        """True if packet number `sq` of stream `key` is new (ahead of the last one, modulo 2**16, or after a silence)."""
        p = self._seen.get(key)
        if p is not None and now - p[1] < self.window and not 0 < ((sq - p[0]) & 0xFFFF) < 0x8000:
            return False
        self._seen[key] = (sq, now)
        return True

    def forget(self, match):
        """Forget every stream whose key satisfies match(key) (e.g. when a pad disconnects)."""
        for k in [k for k in self._seen if match(k)]:
            del self._seen[k]


def load_config(path, defaults=None):
    """Load .toml / .json; warn about unknown keys (typos)."""
    if not path:
        return {}
    with open(path, "rb") as f:
        if path.lower().endswith(".json"):
            cfg = json.load(f)
        else:
            import tomllib
            cfg = tomllib.load(f)
    if defaults:
        for k in cfg:
            if k not in defaults:
                print(f"WARNING: unknown config key {k!r} in {path} (ignored)")
    return cfg


def write_file(path, data):
    """Atomic write (a temporary file + fsync + replace): an interrupted write, a power loss, or a PC sleep / shutdown
    right after the write (master actions) will not leave an empty or truncated file - without fsync os.replace could have swapped in
    a file whose data had not reached the disk yet. A failed write removes the temporary file. data: str (UTF-8) or bytes."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json(path, obj, **kw):
    """write_file() for JSON (kw as in json.dumps, e.g. indent, sort_keys); with a newline at the end."""
    write_file(path, json.dumps(obj, **kw) + "\n")


# ---------------- git: the code version and the update (receiver and sender) ----------------
GIT_PATHS = (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe")
# the sender's deploy key (read-only) and its known_hosts - in .git, so they never end up in the repository;
# created by sender/setup-update-key.sh. The service (root -> the repository owner) has no SSH agent or password for the key.
DEPLOY_KEY = os.path.join(".git", "dualsense-deploy-key")
KNOWN_HOSTS = os.path.join(".git", "dualsense-known-hosts")
# the generic ending of every connection error - the real reason is in the lines before it
_GENERIC = ("fatal: could not read from remote repository", "please make sure you have the correct access rights",
            "and the repository exists")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_git():
    """Locate the git executable: on PATH first, then the usual Windows install locations; None if not found."""
    if found := shutil.which("git"):
        return found
    local = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")
    return next((p for p in (*GIT_PATHS, local) if os.path.isfile(p)), None)


def ssh_command(root):
    """GIT_SSH_COMMAND for the sender: BatchMode (no password prompt - the service has no terminal, git would hang) and the deploy
    key, if there is one. None = unchanged (Windows: its own ssh / plink / Git Credential Manager)."""
    if os.name != "posix" or "GIT_SSH_COMMAND" in os.environ or "GIT_SSH" in os.environ:
        return None
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    key = os.path.join(root, DEPLOY_KEY)
    if os.path.isfile(key):
        cmd += ["-i", key, "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={os.path.join(root, KNOWN_HOSTS)}",
                "-o", "StrictHostKeyChecking=accept-new"]
    return " ".join(shlex.quote(c) for c in cmd)


def _owner(root):
    """Root (the sender service) runs git as the repository owner: the files after a pull do not belong to root,
    and git does not refuse to work in someone else's repository ("dubious ownership"). -> kwargs for subprocess.

    On POSIX it also disables terminal prompts and installs the SSH command. Only when running as root on a repository
    owned by someone else does it add user / group switching (with an empty supplementary group list) and a HOME that
    matches that user.
    """
    env = dict(os.environ)
    if os.name == "posix":            # the sender: https without credentials = an error instead of waiting for a terminal
        env["GIT_TERMINAL_PROMPT"] = "0"  # (Windows: Git Credential Manager may show a login window)
    if ssh := ssh_command(root):
        env["GIT_SSH_COMMAND"] = ssh
    if os.name != "posix" or os.geteuid() != 0:
        return {"env": env}
    st = os.stat(root)
    if st.st_uid == 0:
        return {"env": env}
    import pwd
    try:
        pw = pwd.getpwuid(st.st_uid)
        home, name = pw.pw_dir, pw.pw_name
    except KeyError:
        home, name = root, str(st.st_uid)
    env.update(HOME=home, USER=name, LOGNAME=name)
    # extra_groups=[]: without root's groups (e.g. 0) - the git process has only the owner's privileges
    return {"user": st.st_uid, "group": st.st_gid, "extra_groups": [], "env": env}


_AUTH = ("permission denied (publickey", "host key verification failed", "could not read username",
         "authentication failed", "repository not found", "terminal prompts disabled", "passphrase")


def is_auth_error(msg):
    """An access error to a private repository (SSH key / login) - the fix: sender/setup-update-key.sh."""
    m = (msg or "").lower()
    return any(k in m for k in _AUTH)


def _noise(line):
    """Lines that do not carry the reason for an error: the generic ending of a connection error, git's hints (hint:), the fetch progress
    ("From ...", "   a1b2..c3d4  main -> origin/main", " * [new branch] ...")."""
    low = line.lower()
    return low.startswith((*_GENERIC, "hint:", "from ")) or (" -> " in line and line[:1] in " *+-=!t")


def git_error(text):
    """The reason for a git error in one line: from the first "fatal:" / "error:" line (with the files it concerns), without
    hints, fetch progress and the generic "Could not read from remote repository..."."""
    raw = [x.rstrip() for x in (text or "").splitlines() if x.strip()]
    useful = [x for x in raw if not _noise(x)] or raw
    start = next((k for k, x in enumerate(useful) if x.lstrip().lower().startswith(("fatal:", "error:"))), 0)
    return " / ".join(x.strip() for x in useful[start:start + 3])


def git(root, *args, git_exe=None, timeout=15, env=None):
    """-> CompletedProcess (text). Without a console window on Windows. env: extra environment variables for git."""
    kw = _owner(root)
    if env:
        kw["env"] = {**kw["env"], **env}
    return subprocess.run([git_exe or find_git() or "git", "-c", "safe.directory=*", *args], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW, **kw)


def git_version(root, git_exe=None):
    """The code version in the repository: (a 7-character commit hash, the commit's unix time, the commit title) or ("", 0, "")."""
    try:
        p = git(root, "log", "-1", "--format=%h%x00%ct%x00%s", "--abbrev=7", git_exe=git_exe)
        h, ts, subject = p.stdout.strip().split("\0", 2)
        return h[:7], int(ts), subject
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", 0, ""


def git_pull(root, git_exe=None, env=None, timeout=120):
    """git pull --ff-only (only a fast-forward to the version from GitHub, local changes -> an error, nothing overwritten).
    env: extra variables for git (the receiver: without login windows - otherwise a pull from a background process waited for an invisible
    Git Credential Manager window until the timeout). -> (UPD_*, git's message on error, the old commit, the new
    commit, the changed files)."""
    if not os.path.isdir(os.path.join(root, ".git")):
        return UPD_NOT_GIT, "", "", "", []
    git_exe = git_exe or find_git()
    if git_exe is None:
        return UPD_NO_GIT, "", "", "", []
    try:
        old = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        p = git(root, "pull", "--ff-only", git_exe=git_exe, timeout=timeout, env=env)
        if p.returncode:
            return UPD_FAILED, git_error(p.stderr or p.stdout), old, old, []
        new = git(root, "rev-parse", "HEAD", git_exe=git_exe).stdout.strip()
        if new == old:
            return UPD_NO_CHANGE, "", old, new, []
        files = git(root, "diff", "--name-only", old, new, git_exe=git_exe).stdout.splitlines()   # names with spaces
    except (OSError, subprocess.SubprocessError) as e:
        return UPD_FAILED, str(e), "", "", []
    return UPD_DONE, "", old, new, files
