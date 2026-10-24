"""Sender: configuration (DEFAULTS + CLI arguments) and device constants.

Modules read values from the C object at run time (C.PORT, ...), so init() must be called once at startup,
before anything else starts.
"""
import argparse
import ipaddress
import os
import socket
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Final

from evdev import ecodes as ec

import sender_migrate
from proto import load_config

DEFAULTS: dict[str, Any] = {
    "host": "",                    # the receiver (the PC): an IP or a name; there is no default, so a missing host is an error
    "port": 5005,
    "max_pads": 2,
    "slots_file": "slots.json",    # pad (MAC) -> fixed slot, also across restarts (player 1 / 2 in a game); "" = no memory
    "wol_file": "wol.json",        # Wake-on-LAN and log events - settings from the receiver window; "" = do not save
    "gyro_cal_file": "gyro_cal.json",  # gyro calibration from the receiver window, per pad (MAC); "" = do not save
    "bt_adapter": "auto",          # Bluetooth controller: auto (USB dongle before the built-in one) | builtin | manual (leave as is)
    "bt_file": "bt.json",          # controller chosen in the receiver window (takes precedence over bt_adapter); "" = do not save
    "auth_file": "auth.json",      # with a key: remembered switch to protocol v3 (replay protection); "" = do not save
    "grab_pad": True,
    "grab_touch": True,
    "tap_click": True,
    "tap_max_s": 0.2,
    "tap_max_move": 40,
    "heartbeat_s": 0.05,
    "pad_min_dt": 0.001,           # min gap between state packets (1 ms = 1000 Hz; it was 4 ms - up to 3 ms of stick delay); the first one
                                   # after silence goes out at once, changes inside the window merge into one packet at its end; 0 = no
                                   # limit
    "motion_min_dt": 0.001,        # same for motion; gyro/acc samples inside the window are averaged
    "motion_deadband_gyro": 128,
    "motion_deadband_acc": 16,
    "rescan_s": 2.0,               # fallback pad scan; a new pad is detected at once by inotify on /dev/input
    "stick_deadzone": 3,
    "calibrate_s": 1.0,
    "calibrate_max_range": 4096,
    "autobias": True,
    "autobias_s": 1.0,
    "autobias_range": 1024,
    "battery_poll_s": 5.0,
    "touch_coalesce_ms": 2,        # DS4/DSU touch: at most 1 packet per this many ms (0 = every change)
    "touch_deadzone": 2,           # ignore finger changes smaller than this many units (sensor noise)
    "touch_start_move": 5,         # mouse: the cursor only starts moving after this much travel (no jitter on a tap)
    "gestures": True,              # 2 fingers = scroll, a 2-finger tap = right mouse button
    "rumble": "auto",              # rumble: auto (evdev driver and HID report over hidraw together) | evdev | hidraw | off
    "idle_off_min": 15,            # switch the pad off (Bluetooth disconnect) after this many minutes of no use; 0 = never
    "rt_priority": 10,             # SCHED_FIFO with this priority (1-99); 0 = normal scheduler; needs doas
    "low_latency": True,           # CPU without deep sleep + the fastest cores (sender_rt); needs doas
    "cpu_latency_us": 50,          # low_latency: max CPU wake-up time [µs] (deep states sleep up to ~6.5 ms)
    "key": "",
    "stats": False,
}

# constants independent of the configuration
AUTOBIAS_MAX_STEP: Final = 2048
BURST_DELAYS: Final = (0.003, 0.009)    # repeats of a button change (resilience to a lost packet)
MIN_PERIOD_S: Final = 0.01              # the shortest period of a polling loop (heartbeat, pad scan, battery): never a busy loop
SETTLE_S: Final = 0.5                   # how long we wait for the complete set of pad devices (pad+motion+touch)
# evdev axes of the DualSense: left stick x y, L2, right stick x y, R2 - their order is the order of the state packet's axis fields
PAD_ABS: Final = (ec.ABS_X, ec.ABS_Y, ec.ABS_Z, ec.ABS_RX, ec.ABS_RY, ec.ABS_RZ)
STICK_AXES: Final = (ec.ABS_X, ec.ABS_Y, ec.ABS_RX, ec.ABS_RY)
L2_AXIS, R2_AXIS = ec.ABS_Z, ec.ABS_RZ
HAT_AXES: Final = (ec.ABS_HAT0X, ec.ABS_HAT0Y)     # the D-pad: -1, 0, 1
AXIS_MAX: Final = 255                   # the pad reports its axes as 0..255
STICK_CENTER: Final = 128
TOUCH_W, TOUCH_H = 1919, 942            # the DS4 touchpad scale of the touch packet
IDLE_TP: Final = ((0, 0, 0, 0), (0, 0, 0, 0))     # the touch packet with no finger down
NEUTRAL_PAD: Final = (STICK_CENTER, STICK_CENTER, 0, STICK_CENTER, STICK_CENTER, 0)
# Everything a failing or fake evdev device can raise when asked for an axis: the device is going away (OSError), is not an
# evdev device at all (tests, another platform) or lacks the axis (AttributeError / KeyError).
DEVICE_READ_ERRORS: Final = (OSError, AttributeError, TypeError, ValueError, KeyError)

US_PER_S: Final = 1_000_000             # evdev event times are seconds + microseconds
RUMBLE_MODES: Final = ("auto", "evdev", "hidraw", "off")
RT_PRIORITY_MAX: Final = 99             # the highest SCHED_FIFO priority
SECONDS_PER_MINUTE: Final = 60
MS_PER_S: Final = 1000.0

C = SimpleNamespace()


def _beside(name: object) -> str:
    """Resolve a file name relative to the sender's folder (the service starts from another directory); "" = disabled.

    Absolute paths and empty names are returned unchanged, so a user can point a file anywhere or switch it off.
    """
    name = str(name or "")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name) if name and not os.path.isabs(name) else name


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line (None = sys.argv)."""
    ap = argparse.ArgumentParser(description="DualSense UDP sender")
    # default=None: otherwise the default host would override the host from --config
    ap.add_argument("host", nargs="?", default=None, help="receiver address (the Windows PC): an IP or a host name")
    ap.add_argument("--config", help="configuration file (.toml / .json)")
    ap.add_argument("--key", help="shared HMAC key (packet authentication)")
    ap.add_argument("--stats", action="store_true", default=None, help="print the RTT and report rates every 5 s")
    return ap.parse_args(argv)


def _merge_settings(args: argparse.Namespace) -> dict[str, Any]:
    """Merge the settings, lowest priority first: DEFAULTS, the --config file, the CLI flags (host, --key, --stats).

    The PADKEY environment variable is only a fallback when no key was given. Exits with a message if there is no host.
    """
    if args.config:                    # old default values in the file -> new ones (the user's own settings stay)
        sender_migrate.migrate(args.config)
    cfg = dict(DEFAULTS)
    cfg.update(load_config(args.config, DEFAULTS))
    for name in ("host", "key", "stats"):
        value = getattr(args, name)
        if value is not None:
            cfg[name] = value
    if not cfg.get("host"):
        raise SystemExit("No receiver host. Pass it as an argument (python sender_controller.py 192.168.1.100) "
                         "or set host in the --config file.")
    if not cfg.get("key"):
        cfg["key"] = os.environ.get("PADKEY", "")
    return cfg


def is_ip(host: str) -> bool:
    """Whether ``host`` is a literal IP address (nothing to re-resolve later when the PC's address changes)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def resolve(host: str) -> str:
    """Resolve ``host`` to an IPv4 address (blocking: call from a worker thread inside the event loop).

    Raises:
        OSError: The name cannot be resolved.
    """
    return socket.gethostbyname(host)


def _apply_network(cfg: dict[str, Any]) -> None:
    """Receiver address, port, pad limit and the HMAC key; exits with a message if the host cannot be resolved."""
    C.HOST = str(cfg["host"]).strip()
    try:
        C.PC_ADDR = resolve(C.HOST)
    except OSError as e:
        raise SystemExit(f"Cannot resolve host {C.HOST!r}: {e}") from None
    C.PORT = int(cfg["port"])
    C.MAX_PADS = int(cfg["max_pads"])
    C.KEY = str(cfg["key"]).encode() if cfg["key"] else b""


def _apply_files(cfg: dict[str, Any]) -> None:
    """The state files, resolved next to the sender ("" = not saved)."""
    C.SLOTS_FILE = _beside(cfg["slots_file"])
    C.WOL_FILE = _beside(cfg["wol_file"])
    C.GYRO_CAL_FILE = _beside(cfg["gyro_cal_file"])
    C.BT_ADAPTER, C.BT_FILE = str(cfg["bt_adapter"]), _beside(cfg["bt_file"])
    C.AUTH_FILE = _beside(cfg["auth_file"])


def _apply_pad_tuning(cfg: dict[str, Any]) -> None:
    """Pad and motion reading: send rate limits, deadbands, calibration, autobias, polling."""
    C.GRAB_PAD = bool(cfg["grab_pad"])
    C.PAD_MIN_DT = max(0.0, float(cfg["pad_min_dt"]))
    # a period of 0 (or less) would turn these loops into a busy loop - and the sender runs under SCHED_FIFO
    C.HEARTBEAT_S = max(MIN_PERIOD_S, float(cfg["heartbeat_s"]))
    C.MOTION_MIN_DT, C.RESCAN_S = float(cfg["motion_min_dt"]), max(MIN_PERIOD_S, float(cfg["rescan_s"]))
    C.DB_GYRO, C.DB_ACC = int(cfg["motion_deadband_gyro"]), int(cfg["motion_deadband_acc"])
    C.STICK_DZ = int(cfg["stick_deadzone"])
    C.CAL_S, C.CAL_MAX_RANGE = float(cfg["calibrate_s"]), int(cfg["calibrate_max_range"])
    C.AUTOBIAS, C.AUTOBIAS_S, C.AUTOBIAS_RANGE = bool(cfg["autobias"]), float(cfg["autobias_s"]), int(cfg["autobias_range"])
    C.BATT_POLL_S = max(MIN_PERIOD_S, float(cfg["battery_poll_s"]))


def _apply_touch(cfg: dict[str, Any]) -> None:
    """Touchpad: grabbing, taps, gestures, coalescing and deadzones."""
    C.GRAB_TOUCH, C.TAP_CLICK = bool(cfg["grab_touch"]), bool(cfg["tap_click"])
    C.TAP_MAX_S, C.TAP_MAX_MOVE = float(cfg["tap_max_s"]), int(cfg["tap_max_move"])
    C.TOUCH_COALESCE_S = max(0.0, float(cfg["touch_coalesce_ms"])) / MS_PER_S
    C.TOUCH_DEADZONE = int(cfg["touch_deadzone"])
    C.TOUCH_START_MOVE = max(0, int(cfg["touch_start_move"]))
    C.GESTURES = bool(cfg["gestures"])


def _apply_runtime(cfg: dict[str, Any]) -> None:
    """Rumble mode, idle power-off, real-time scheduling and statistics."""
    C.RUMBLE = cfg["rumble"] if cfg["rumble"] in RUMBLE_MODES else "auto"
    C.IDLE_OFF_S = max(0.0, float(cfg["idle_off_min"])) * SECONDS_PER_MINUTE
    C.RT_PRIORITY = max(0, min(RT_PRIORITY_MAX, int(cfg["rt_priority"])))
    C.LOW_LATENCY, C.CPU_LATENCY_US = bool(cfg["low_latency"]), max(0, int(cfg["cpu_latency_us"]))
    C.STATS = bool(cfg["stats"])


def init(argv: Sequence[str] | None = None) -> SimpleNamespace:
    """Build the global configuration object C from DEFAULTS, the config file and the CLI flags (later wins).

    Every value is coerced to its expected type and clamped to a sane range here, once, so the rest of the sender can read
    C.* without any further validation.

    Args:
        argv: The command line without the program name; None = sys.argv[1:].

    Returns:
        The configuration object (the same C every module imports).

    Raises:
        SystemExit: The host is missing or cannot be resolved.
    """
    cfg = _merge_settings(_parse_args(argv))
    _apply_network(cfg)
    _apply_files(cfg)
    _apply_pad_tuning(cfg)
    _apply_touch(cfg)
    _apply_runtime(cfg)
    return C
