"""Sender: konfiguracja (DEFAULTS + argumenty CLI) i stałe urządzeń.

Moduły czytają wartości z obiektu C w czasie działania (C.PORT, ...), dlatego init() musi
zostać wywołane raz na starcie, zanim ruszy cokolwiek innego.
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
    "slots_file": "slots.json",    # pad (MAC) -> stały slot także po restarcie (gracz 1 / 2 w grze); "" = bez pamięci
    "wol_file": "wol.json",        # Wake-on-LAN i zdarzenia do dziennika - ustawienia z okna receivera; "" = bez zapisu
    "gyro_cal_file": "gyro_cal.json",  # kalibracja żyroskopu z okna receivera per pad (MAC); "" = bez zapisu
    "bt_adapter": "auto",          # kontroler Bluetooth: auto (dongle USB przed wbudowanym) | builtin | manual (bez zmian)
    "bt_file": "bt.json",          # wybór kontrolera z okna receivera (ma pierwszeństwo przed bt_adapter); "" = bez zapisu
    "auth_file": "auth.json",      # z kluczem: zapamiętane przejście na protokół v3 (ochrona przed replay); "" = bez zapisu
    "grab_pad": True,
    "grab_touch": True,
    "tap_click": True,
    "tap_max_s": 0.2,
    "tap_max_move": 40,
    "heartbeat_s": 0.05,
    "pad_min_dt": 0.001,           # min odstęp pakietów stanu (1 ms = 1000 Hz; było 4 ms - do 3 ms opóźnienia gałek); pierwszy po ciszy
                                   # idzie od razu, zmiany w oknie zlewają się w jeden pakiet na końcu okna; 0 = bez limitu
    "motion_min_dt": 0.001,        # j.w. dla ruchu; próbki gyro/acc w oknie są uśredniane
    "motion_deadband_gyro": 128,
    "motion_deadband_acc": 16,
    "rescan_s": 2.0,               # zapasowy skan padów; nowy pad wykrywa od razu inotify na /dev/input
    "stick_deadzone": 3,
    "calibrate_s": 1.0,
    "calibrate_max_range": 4096,
    "autobias": True,
    "autobias_s": 1.0,
    "autobias_range": 1024,
    "battery_poll_s": 5.0,
    "touch_coalesce_ms": 2,        # DS4/DSU dotyk: max 1 pakiet na tyle ms (0 = każda zmiana)
    "touch_deadzone": 2,           # ignoruj zmiany palca mniejsze niż tyle jednostek (szum czujnika)
    "touch_start_move": 5,         # mysz: kursor rusza dopiero po przesunięciu o tyle (brak drgania przy tapie)
    "gestures": True,              # 2 palce = przewijanie, tap 2 palcami = prawy przycisk myszy
    "rumble": "auto",              # wibracja: auto (sterownik evdev i raport HID przez hidraw naraz) | evdev | hidraw | off
    "idle_off_min": 15,            # wyłącz pada (rozłącz Bluetooth) po tylu minutach bez użycia; 0 = nigdy
    "rt_priority": 10,             # SCHED_FIFO z tym priorytetem (1-99); 0 = zwykły planista; wymaga doas
    "low_latency": True,           # procesor bez głębokiego uśpienia + najszybsze rdzenie (sender_rt); wymaga doas
    "cpu_latency_us": 50,          # low_latency: max czas wybudzenia procesora [µs] (głębokie stany śpią do ~6.5 ms)
    "key": "",
    "stats": False,
}

# stałe niezależne od konfiguracji
AUTOBIAS_MAX_STEP = 2048
BURST_DELAYS = (0.003, 0.009)      # powtórki zmiany przycisku (odporność na zgubiony pakiet)
SETTLE_S = 0.5                     # tyle czekamy na komplet urządzeń pada (pad+motion+touch)
PAD_ABS = (0, 1, 2, 3, 4, 5)
STICK_AXES = (0, 1, 3, 4)
TOUCH_W, TOUCH_H = 1919, 942
IDLE_TP = ((0, 0, 0, 0), (0, 0, 0, 0))
NEUTRAL_PAD = (128, 128, 0, 128, 128, 0)

C = SimpleNamespace()


def _beside(name):
    """Nazwa pliku względem folderu nadajnika (usługa startuje z innego katalogu); "" = wyłączone."""
    name = str(name or "")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name) if name and not os.path.isabs(name) else name


def init(argv=None):
    ap = argparse.ArgumentParser(description="DualSense UDP sender")
    # default=None: inaczej host z domyślnej wartości nadpisywałby host z --config
    ap.add_argument("host", nargs="?", default=None, help="IP odbiornika (PC z Windows)")
    ap.add_argument("--config", help="plik konfiguracji .toml / .json")
    ap.add_argument("--key", help="wspólny klucz HMAC (uwierzytelnianie pakietów)")
    ap.add_argument("--stats", action="store_true", default=None, help="pokazuj RTT co 5 s")
    args = ap.parse_args(argv)

    if args.config:                    # stare wartości domyślne w pliku -> nowe (własne ustawienia zostają)
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
