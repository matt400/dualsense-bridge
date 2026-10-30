"""Receiver: configuration (DEFAULTS + CLI), enabling autostart with Windows (rx_autostart), logging to a file.

The computed values end up in the object C; call init() once at startup.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Final

from proto import load_config

DEFAULTS: dict[str, Any] = {
    "in_port": 5005,
    "dsu_port": 26760,
    "dsu_bind": "127.0.0.1", # DSU server address: this computer only; "0.0.0.0" = the emulator runs on another device in the network
    "dsu_max_clients": 4,    # the most DSU subscribers at once (each gets a stream of up to dsu_hz packets/s)
    "hm_min_age_days": 7,    # unpinned mode only (rx_hm_update.PINNED_TAG = ""): days a new HIDMaestro waits after publication; 0 = now
    "pads": 4,               # number of slots (max 4); virtual pads are created only for connected pads (which cards the
                             # window shows: Settings -> Pads - only connected ones, or a fixed number)
    "mouse_pads": [0],       # which controllers start in mouse mode (when there is a master pad: the master has the mouse)
    "pad_hz": 1000,          # max rate of DS4 reports (merging bursts of packets, like the sender's pad_min_dt = 1 ms);
                             # the report is sent at once from the network thread, too early -> the slot thread sends it later; 0 = no limit
    "dsu_hz": 1000,          # as above, for DSU; 0 = no limit
    "dsu_tx_interval": 0.5,  # do not send unchanged DSU more often than this
    "dsu_batch": False,      # many DSU packets in one datagram - not every client accepts that
    "acc_res": 8192.0,
    "gyro_res": 1024.0,
    "acc_sign": [1, 1, 1],
    "gyro_sign": [1, 1, 1],
    "mouse_sens": 0.5,       # initial value 0.05-5 (too fast -> 0.35, too slow -> 0.8); later Settings -> Mouse
    "scroll_sens": 0.6,      # two-finger scrolling (wheel units per touchpad unit); 120 = 1 notch
    "scroll_natural": False, # True = the content follows the fingers (like on a phone)
    "stale_s": 0.5,          # silence longer than this -> reset the state (anti "stuck buttons")
    "unplug_s": 10.0,        # silence longer than this -> disconnect the virtual DS4
    "allow": "",             # e.g. "192.168.1.50" - only packets from this IP
    "key": "",               # shared HMAC key (or the PADKEY environment variable)
    "stats": False,          # print packet statistics every 1 s
    "log_file": "",          # e.g. "receiver.log" (useful with pythonw / autostart)
    "remap_file": "remap.json",  # remap profiles (the Save button in the window)
    "gui": True,             # the window (pads, battery, remapping; --no-gui disables it)
    "tray": True,            # minimise to the system tray
    "gui_topmost": False,    # keep the window always on top
    "gui_start_visible": False,  # True = show the window right after startup
    "gui_wait_pad": False,   # True = do not create the window or icon until a pad connects
    "low_batt": 15,          # low battery threshold (%)
    "low_batt_notify": True, # tray notification on low battery
    "ps_modifier": True,     # PS as the shortcut key: a short PS goes to the game on release (+~0.1 s), PS + button = shortcut
    "ps_hold_s": 1.0,        # this long a PS hold (with no other buttons) = ps_hold_action
    "ps_hold_action": "gamebar",      # gamebar (Win+G) | bigpicture (Steam Big Picture) | none
    "ps_share_action": "screenshot",  # screenshot (Win+PrtScr, to Pictures\Screenshots) | capture (Game Bar) | none
    "confirm": True,         # confirmations on the pad: a short rumble / lightbar flash on shortcuts
    "idle_lightbar": [0, 0, 128],     # lightbar colour after the flash, when the game does not set one itself
    "game_profiles": True,   # automatic profile by the foreground game
    "game_ignore": [],       # extra programs skipped in the "recent games" list in the editor (e.g. "obs64.exe")
}

CHOICES: Final = {"ps_hold_action": ("gamebar", "bigpicture", "none"), "ps_share_action": ("screenshot", "capture", "none")}

C = SimpleNamespace()
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = "receiver.toml"     # loaded without --config when it lies next to the receiver


LOG_ROTATE_BYTES: Final = 1_000_000   # a log larger than this is moved to <log>.1 at startup
MAX_PADS: Final = 4
MIN_PS_HOLD_S: Final = 0.3            # a shorter PS hold would fire by accident
RGB_LEN: Final = 3
COLOR_MAX: Final = 255
DEFAULT_DSU_BIND: Final = "127.0.0.1"
GYRO_DS4_RAW: Final = 16.0            # raw gyro counts per DS4 report unit at gyro_res 1.0 (see GYRO_TO_DS4)


def default_config(folder: str | None = None) -> str | None:
    """Return the receiver.toml next to the receiver, or None.

    Without this the file worked only with --config: entries in receiver.toml (e.g. log_file) were silently ignored on a
    normal start and on an autostart without that argument.
    """
    path = os.path.join(folder or HERE, DEFAULT_CONFIG)
    return path if os.path.isfile(path) else None


def autostart_command(argv: Sequence[str] | None = None) -> tuple[str, str]:
    """Return (program, arguments) for autostart.

    Pythonw instead of python (no console), the script and the arguments without --autostart, the --config path made
    absolute.
    """
    import subprocess

    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    args = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--autostart":
            i += 2                    # the flag and its on/off value
            continue
        if a.startswith("--autostart="):
            i += 1
            continue
        if a.startswith("--config="):
            extra.append("--config=" + os.path.abspath(a.split("=", 1)[1]))
            i += 1
            continue
        if a == "--config" and i + 1 < len(args):
            extra += [a, os.path.abspath(args[i + 1])]
            i += 2
            continue
        extra.append(a)
        i += 1
    return exe, subprocess.list2cmdline([os.path.abspath(sys.argv[0]), *extra])


def set_autostart(on: bool) -> None:
    """Enable or disable autostart with Windows: a Task Scheduler task at logon (no delay - rx_autostart)."""
    import rx_autostart
    if on:
        import rx_winapi
        exe, args = autostart_command()
        print(rx_autostart.enable(exe, args, elevated=rx_winapi.is_admin()))   # as administrator = HIDMaestro at startup
    else:
        print(rx_autostart.disable())


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Define and parse the command line (the help texts are the user-facing Polish ones)."""
    ap = argparse.ArgumentParser(description="DualSense UDP receiver -> ViGEm DS4 + DSU")
    ap.add_argument("--config", help=f"plik konfiguracji .toml / .json (domyślnie {DEFAULT_CONFIG} obok receivera, "
                                     "jeśli jest; --config \"\" = bez pliku)")
    ap.add_argument("--allow", help="akceptuj pakiety tylko z tego IP")
    ap.add_argument("--key", help="wspólny klucz HMAC (uwierzytelnianie pakietów)")
    ap.add_argument("--pads", type=int, help="liczba slotów padów (1-4)")
    ap.add_argument("--remap-file", dest="remap_file", help="plik z profilami remapowania (domyślnie remap.json)")
    ap.add_argument("--stats", action="store_true", default=None, help="pokazuj statystyki pakietów")
    ap.add_argument("--no-gui", action="store_true", default=None, help="bez okna")
    ap.add_argument("--no-elevate", action="store_true", help="bez ponownego uruchomienia jako administrator (UAC)")
    ap.add_argument("--autostart", choices=["on", "off"], help="włącz/wyłącz uruchamianie z Windows i wyjdź")
    return ap.parse_args(argv)


def _merge_settings(args: argparse.Namespace) -> tuple[dict[str, Any], str | None]:
    """Merge DEFAULTS <- config file <- CLI flags (and the PADKEY variable for an empty key).

    Returns:
        The merged settings and the config file that was used (None = defaults only).
    """
    cfg = dict(DEFAULTS)
    config = default_config() if args.config is None else args.config
    cfg.update(load_config(config, DEFAULTS))
    for k in ("allow", "key", "pads", "stats", "remap_file"):
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v
    if args.no_gui:
        cfg["gui"] = False
    if not cfg["key"]:
        cfg["key"] = os.environ.get("PADKEY", "")
    return cfg, config


def _open_log(log_file: str, script_dir: str) -> None:
    """Log to a file (rotated at startup) and redirect stdout / stderr to it; pythonw has no console."""
    C.log_handle = None
    if not log_file:
        return
    path = os.path.join(script_dir, log_file)
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_ROTATE_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        # Windows: another process still holds the file (the old receiver right after a restart from the settings, a log
        # viewer) - no rotation, but logging has to keep working
        pass
    try:
        C.log_handle = open(path, "a", buffering=1, encoding="utf-8")  # noqa: SIM115 - the log stays open until the process ends
        sys.stdout = sys.stderr = C.log_handle
    except OSError:
        C.log_handle = None


def _apply_network(cfg: dict[str, Any]) -> None:
    """Fill C with the network, DSU and report-rate settings."""
    C.IN_PORT = int(cfg["in_port"])
    C.DSU_PORT = int(cfg["dsu_port"])
    C.DSU_BIND = str(cfg["dsu_bind"] or DEFAULT_DSU_BIND)
    C.DSU_MAX_CLIENTS = max(1, int(cfg["dsu_max_clients"]))
    C.HM_MIN_AGE_DAYS = max(0.0, float(cfg["hm_min_age_days"]))
    C.NUM_PADS = max(1, min(MAX_PADS, int(cfg["pads"])))
    C.MOUSE_PADS = {int(p) for p in cfg["mouse_pads"] if 0 <= int(p) < C.NUM_PADS}
    if len(C.MOUSE_PADS) != len(set(cfg["mouse_pads"])):
        print(f"Note: mouse_pads restricted to slots 0..{C.NUM_PADS - 1} -> {sorted(C.MOUSE_PADS)}")
    C.PAD_HZ = max(0.0, float(cfg["pad_hz"]))
    C.DSU_HZ = max(0.0, float(cfg["dsu_hz"]))
    C.DSU_TX_INTERVAL = float(cfg["dsu_tx_interval"])
    C.DSU_BATCH = bool(cfg["dsu_batch"])


def _apply_motion(cfg: dict[str, Any]) -> None:
    """Fill C with the motion-sensor scaling, mouse and timeout settings."""
    C.ACC_RES = float(cfg["acc_res"])
    C.GYRO_RES = float(cfg["gyro_res"])
    C.GYRO_TO_DS4 = GYRO_DS4_RAW / C.GYRO_RES          # raw gyro -> DS4 report units
    C.ACC_SIGN = tuple(cfg["acc_sign"])
    C.GYRO_SIGN = tuple(cfg["gyro_sign"])
    C.MOUSE_SENS = float(cfg["mouse_sens"])
    C.SCROLL_SENS = float(cfg["scroll_sens"])
    C.SCROLL_NATURAL = bool(cfg["scroll_natural"])
    C.STALE_S = float(cfg["stale_s"])
    C.UNPLUG_S = float(cfg["unplug_s"])


def _apply_paths_and_ui(cfg: dict[str, Any], script_dir: str) -> None:
    """Fill C with the key, window and file-path settings."""
    C.ALLOWED_IP = cfg["allow"]
    C.KEY = str(cfg["key"]).encode() if cfg["key"] else b""     # key = 1234 (a number in .toml) - same as in the sender
    C.DEBUG = bool(cfg["stats"])
    C.USE_GUI = bool(cfg["gui"])
    C.LOW_BATT = int(cfg["low_batt"])
    C.REMAP_PATH = os.path.join(script_dir, cfg["remap_file"])
    C.CONSOLE_PATH = os.path.join(script_dir, "console.json")   # console mode settings (rx_console.py)
    C.SETTINGS_PATH = os.path.join(script_dir, "settings.json")  # mouse and master pad settings (rx_settings.py, window -> Settings)


def _apply_shortcuts(cfg: dict[str, Any]) -> None:
    """Validate the PS-shortcut choices (an unknown value falls back to the default) and fill C with them."""
    for k, allowed in CHOICES.items():
        if cfg[k] not in allowed:
            print(f"WARNING: {k} = {cfg[k]!r} is not one of {allowed} - using {DEFAULTS[k]!r}")
            cfg[k] = DEFAULTS[k]
    C.PS_MODIFIER = bool(cfg["ps_modifier"])
    C.PS_HOLD_S = max(MIN_PS_HOLD_S, float(cfg["ps_hold_s"]))
    C.PS_HOLD_ACTION, C.PS_SHARE_ACTION = cfg["ps_hold_action"], cfg["ps_share_action"]
    C.CONFIRM = bool(cfg["confirm"])
    C.IDLE_RGB = tuple(max(0, min(COLOR_MAX, int(v))) for v in ([*list(cfg["idle_lightbar"]), 0, 0, 0])[:RGB_LEN])
    C.GAME_PROFILES = bool(cfg["game_profiles"])
    C.GAME_IGNORE = {str(x).lower() for x in cfg["game_ignore"]}


def init(argv: Sequence[str] | None = None) -> SimpleNamespace:
    """Parse the command line, merge DEFAULTS <- config file <- CLI flags, validate and convert the values into C.

    Also opens the log file (rotating it past 1 MB) and redirects stdout / stderr to it when log_file is set. With
    --autostart it only enables / disables autostart and exits.

    Returns:
        The namespace C.
    """
    args = _parse_args(argv)
    if args.autostart:
        set_autostart(args.autostart == "on")
        sys.exit(0)
    cfg, config = _merge_settings(args)
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    _open_log(cfg["log_file"], script_dir)
    print(f"Config: {config or 'defaults (no config file)'}")
    C.cfg = cfg
    _apply_network(cfg)
    _apply_motion(cfg)
    _apply_paths_and_ui(cfg, script_dir)
    _apply_shortcuts(cfg)
    return C
