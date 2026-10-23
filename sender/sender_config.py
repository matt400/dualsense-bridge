"""Sender: configuration (DEFAULTS + CLI arguments) and device constants.

Modules read values from the C object at run time (C.PORT, ...), so init() must be called once at startup,
before anything else starts.
"""
import argparse
import os
import socket
from types import SimpleNamespace

from proto import load_config

DEFAULTS = {
    "host": "192.168.1.4",
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
AUTOBIAS_MAX_STEP = 2048
BURST_DELAYS = (0.003, 0.009)      # repeats of a button change (resilience to a lost packet)
SETTLE_S = 0.5                     # how long we wait for the complete set of pad devices (pad+motion+touch)
PAD_ABS = (0, 1, 2, 3, 4, 5)
STICK_AXES = (0, 1, 3, 4)
TOUCH_W, TOUCH_H = 1919, 942
IDLE_TP = ((0, 0, 0, 0), (0, 0, 0, 0))
NEUTRAL_PAD = (128, 128, 0, 128, 128, 0)

C = SimpleNamespace()


def _beside(name):
    """Resolve a file name relative to the sender's folder (the service starts from another directory); "" = disabled.

    Absolute paths and empty names are returned unchanged, so a user can point a file anywhere or switch it off.
    """
    name = str(name or "")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name) if name and not os.path.isabs(name) else name


def init(argv=None):
    """Build the global configuration object C from DEFAULTS, the config file and the CLI flags (later wins).

    Priority, lowest to highest: DEFAULTS < --config file < CLI flags (host, --key, --stats) < for the key, the PADKEY
    environment variable is only a fallback when no key was given. Every value is coerced to its expected type and
    clamped to a sane range here, once, so the rest of the sender can read C.* without any further validation.
    Exits with a message if the host is missing or cannot be resolved.
    """
    ap = argparse.ArgumentParser(description="DualSense UDP sender")
    # default=None: otherwise the default host would override the host from --config
    ap.add_argument("host", nargs="?", default=None, help="IP odbiornika (PC z Windows)")
    ap.add_argument("--config", help="plik konfiguracji .toml / .json")
    ap.add_argument("--key", help="wspólny klucz HMAC (uwierzytelnianie pakietów)")
    ap.add_argument("--stats", action="store_true", default=None, help="pokazuj RTT co 5 s")
    args = ap.parse_args(argv)

    if args.config:                    # old default values in the file -> new ones (the user's own settings stay)
        import sender_migrate
        sender_migrate.migrate(args.config)
    cfg = dict(DEFAULTS)
    cfg.update(load_config(args.config, DEFAULTS))
    for k in ("host", "key", "stats"):
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v
    if not cfg.get("host"):
        raise SystemExit("Brak hosta odbiornika. Podaj argument (python sender_controller.py 192.168.1.100) "
                         "albo ustaw host w pliku --config.")
    if not cfg.get("key"):
        cfg["key"] = os.environ.get("PADKEY", "")

    C.HOST = str(cfg["host"]).strip()
    try:
        C.PC_ADDR = socket.gethostbyname(C.HOST)
    except OSError as e:
        raise SystemExit(f"Nie można rozwiązać hosta {C.HOST!r}: {e}") from None
    C.PORT = int(cfg["port"])
    C.MAX_PADS = int(cfg["max_pads"])
    C.SLOTS_FILE = _beside(cfg["slots_file"])
    C.WOL_FILE = _beside(cfg["wol_file"])
    C.GYRO_CAL_FILE = _beside(cfg["gyro_cal_file"])
    C.BT_ADAPTER, C.BT_FILE = str(cfg["bt_adapter"]), _beside(cfg["bt_file"])
    C.AUTH_FILE = _beside(cfg["auth_file"])
    C.GRAB_PAD, C.GRAB_TOUCH, C.TAP_CLICK = bool(cfg["grab_pad"]), bool(cfg["grab_touch"]), bool(cfg["tap_click"])
    C.TAP_MAX_S, C.TAP_MAX_MOVE = float(cfg["tap_max_s"]), int(cfg["tap_max_move"])
    C.PAD_MIN_DT = max(0.0, float(cfg["pad_min_dt"]))
    C.HEARTBEAT_S, C.MOTION_MIN_DT, C.RESCAN_S = (float(cfg["heartbeat_s"]), float(cfg["motion_min_dt"]),
                                                  float(cfg["rescan_s"]))
    C.DB_GYRO, C.DB_ACC = int(cfg["motion_deadband_gyro"]), int(cfg["motion_deadband_acc"])
    C.STICK_DZ = int(cfg["stick_deadzone"])
    C.CAL_S, C.CAL_MAX_RANGE = float(cfg["calibrate_s"]), int(cfg["calibrate_max_range"])
    C.AUTOBIAS, C.AUTOBIAS_S, C.AUTOBIAS_RANGE = bool(cfg["autobias"]), float(cfg["autobias_s"]), int(cfg["autobias_range"])
    C.BATT_POLL_S = float(cfg["battery_poll_s"])
    C.TOUCH_COALESCE_S = max(0.0, float(cfg["touch_coalesce_ms"])) / 1000.0
    C.TOUCH_DEADZONE = int(cfg["touch_deadzone"])
    C.TOUCH_START_MOVE = max(0, int(cfg["touch_start_move"]))
    C.GESTURES = bool(cfg["gestures"])
    C.RUMBLE = cfg["rumble"] if cfg["rumble"] in ("auto", "evdev", "hidraw", "off") else "auto"
    C.IDLE_OFF_S = max(0.0, float(cfg["idle_off_min"])) * 60
    C.RT_PRIORITY = max(0, min(99, int(cfg["rt_priority"])))
    C.LOW_LATENCY, C.CPU_LATENCY_US = bool(cfg["low_latency"]), max(0, int(cfg["cpu_latency_us"]))
    C.KEY = str(cfg["key"]).encode() if cfg["key"] else b""
    C.STATS = bool(cfg["stats"])
    return C
