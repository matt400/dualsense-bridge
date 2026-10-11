"""Receiver: konfiguracja (DEFAULTS + CLI), autostart z Windows, logi do pliku.

Wartości przeliczone trafiają do obiektu C; init() wywołaj raz na starcie.
"""
import argparse
import os
import sys
from types import SimpleNamespace

from proto import load_config

DEFAULTS = {
    "in_port": 5005,
    "dsu_port": 26760,
    "pads": 2,               # liczba slotów (max 4); wirtualne DS4 powstają tylko dla podłączonych
    "mouse_pads": [0],       # które kontrolery startują w trybie myszy
    "pad_hz": 1000,          # max częstotliwość raportów DS4 (zlewanie serii pakietów); sender sam ogranicza do ~250 Hz,
                             # więc 250 tutaj dublowało limit i przy jitterze sieci dokładało uśpienie; 0 = bez limitu
    "dsu_hz": 1000,          # j.w. dla DSU; 0 = bez limitu
    "dsu_tx_interval": 0.5,  # nie wysyłaj DSU bez zmian częściej niż to
    "dsu_batch": False,      # wiele pakietów DSU w jednym datagramie - nie każdy klient to akceptuje
    "acc_res": 8192.0,
    "gyro_res": 1024.0,
    "acc_sign": [1, 1, 1],
    "gyro_sign": [1, 1, 1],
    "mouse_sens": 0.5,       # za szybko -> 0.35, za wolno -> 0.8, odwrócone -> ujemna
    "scroll_sens": 0.6,      # przewijanie dwoma palcami (jednostki kółka na jednostkę touchpada); 120 = 1 działka
    "scroll_natural": False, # True = treść podąża za palcami (jak w telefonie)
    "stale_s": 0.5,          # cisza dłuższa niż tyle -> reset stanu (anty "zablokowane przyciski")
    "unplug_s": 10.0,        # cisza dłuższa niż tyle -> odłącz wirtualny DS4
    "allow": "",             # np. "192.168.1.50" - tylko pakiety z tego IP
    "key": "",               # wspólny klucz HMAC (lub zmienna środowiskowa PADKEY)
    "stats": False,          # wypisuj statystyki pakietów co 1 s
    "log_file": "",          # np. "receiver.log" (przydatne z pythonw / autostartem)
    "remap_file": "remap.json",  # profile remapowania (przycisk Save w oknie)
    "remap_ui": False,       # dodatkowy edytor remapowania w przeglądarce (to samo co w oknie), tylko 127.0.0.1
    "remap_ui_app": True,    # edytor w osobnym oknie (Edge/Chrome --app); False = zwykła karta przeglądarki
    "remap_ui_port": 8765,   # pierwszy próbowany port (kolejne 9, gdy zajęty); 0 = losowy
    "gui": True,             # okno (pady, bateria, remapowanie; --no-gui wyłącza)
    "tray": True,            # minimalizacja do zasobnika systemowego
    "gui_topmost": False,    # okno zawsze na wierzchu
    "gui_start_visible": False,  # True = pokaż okno od razu po starcie
    "gui_wait_pad": False,   # True = nie twórz okna ani ikony, dopóki żaden pad się nie połączy
    "low_batt": 15,          # próg (%) niskiej baterii
    "low_batt_notify": True, # powiadomienie z zasobnika przy niskiej baterii
    "ps_modifier": True,     # PS jako klawisz skrótów: krótkie PS trafia do gry po puszczeniu (+~0.1 s), PS + przycisk = skrót
    "ps_hold_s": 1.0,        # tyle trzymania PS (bez innych przycisków) = ps_hold_action
    "ps_hold_action": "gamebar",      # gamebar (Win+G) | bigpicture (Steam Big Picture) | none
    "ps_share_action": "screenshot",  # screenshot (Win+PrtScr, do Obrazy\Zrzuty ekranu) | capture (Game Bar) | none
    "confirm": True,         # potwierdzenia na padzie: krótka wibracja / mignięcie lightbara przy skrótach
    "idle_lightbar": [0, 0, 128],     # kolor lightbara po mignięciu, gdy gra sama go nie ustawia
    "game_profiles": True,   # automatyczny profil wg gry na pierwszym planie
    "game_ignore": [],       # dodatkowe programy pomijane na liście "ostatnie gry" w edytorze (np. "obs64.exe")
}

CHOICES = {"ps_hold_action": ("gamebar", "bigpicture", "none"), "ps_share_action": ("screenshot", "capture", "none")}

C = SimpleNamespace()


def set_autostart(on):
    """Autostart w HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run."""
    import subprocess
    import winreg

    name = "DualSenseBridge"

    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        if on:
            exe = sys.executable
            pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
            if os.path.exists(pyw):
                exe = pyw

            script = os.path.abspath(sys.argv[0])
            argv = sys.argv[1:]
            extra = []
            i = 0
            while i < len(argv):
                a = argv[i]
                if a == "--autostart":
                    i += 2
                    continue
                if a.startswith("--autostart="):
                    i += 1
                    continue
                if a.startswith("--config="):
                    extra.append("--config=" + os.path.abspath(a.split("=", 1)[1]))
                    i += 1
                    continue
                if a == "--config" and i + 1 < len(argv):
                    extra += [a, os.path.abspath(argv[i + 1])]
                    i += 2
                    continue
                extra.append(a)
                i += 1

            cmd = subprocess.list2cmdline([exe, script, *extra])
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
            print("Autostart enabled:")
            print(cmd)
        else:
            try:
                winreg.DeleteValue(key, name)
                print("Autostart disabled.")
            except FileNotFoundError:
                print("Autostart was not enabled.")
    finally:
        winreg.CloseKey(key)

def init(argv=None):
    ap = argparse.ArgumentParser(description="DualSense UDP receiver -> ViGEm DS4 + DSU")
    ap.add_argument("--config", help="plik konfiguracji .toml / .json")
    ap.add_argument("--allow", help="akceptuj pakiety tylko z tego IP")
    ap.add_argument("--key", help="wspólny klucz HMAC (uwierzytelnianie pakietów)")
    ap.add_argument("--pads", type=int, help="liczba slotów padów (1-4)")
    ap.add_argument("--remap-file", dest="remap_file", help="plik z profilami remapowania (domyślnie remap.json)")
    ap.add_argument("--stats", action="store_true", default=None, help="pokazuj statystyki pakietów")
    ap.add_argument("--no-gui", action="store_true", default=None, help="bez okna")
    ap.add_argument("--autostart", choices=["on", "off"], help="włącz/wyłącz uruchamianie z Windows i wyjdź")
    args = ap.parse_args(argv)

    if args.autostart:
        set_autostart(args.autostart == "on")
        sys.exit(0)

    cfg = dict(DEFAULTS)
    cfg.update(load_config(args.config, DEFAULTS))
    for k in ("allow", "key", "pads", "stats", "remap_file"):
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v
    if args.no_gui:
        cfg["gui"] = False
    if not cfg["key"]:
        cfg["key"] = os.environ.get("PADKEY", "")

    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    C.log_handle = None
    if cfg["log_file"]:   # logi do pliku (z rotacją przy starcie); pythonw nie ma konsoli
        lf = os.path.join(script_dir, cfg["log_file"])
        try:
            if os.path.exists(lf) and os.path.getsize(lf) > 1_000_000:
                os.replace(lf, lf + ".1")
        except OSError:
            # Windows: plik trzyma jeszcze inny proces (stary receiver tuż po restarcie z ustawień, podgląd logu) -
            # bez rotacji, ale logowanie musi działać dalej
            pass
        try:
            C.log_handle = open(lf, "a", buffering=1, encoding="utf-8")  # noqa: SIM115 - log do końca procesu
            sys.stdout = sys.stderr = C.log_handle
        except OSError:
            C.log_handle = None

    C.cfg = cfg
    C.IN_PORT = int(cfg["in_port"])
    C.DSU_PORT = int(cfg["dsu_port"])
    C.NUM_PADS = max(1, min(4, int(cfg["pads"])))
    C.MOUSE_PADS = {int(p) for p in cfg["mouse_pads"] if 0 <= int(p) < C.NUM_PADS}
    if len(C.MOUSE_PADS) != len(set(cfg["mouse_pads"])):
        print(f"Note: mouse_pads restricted to slots 0..{C.NUM_PADS - 1} -> {sorted(C.MOUSE_PADS)}")
    C.PAD_HZ = max(0.0, float(cfg["pad_hz"]))
    C.DSU_HZ = max(0.0, float(cfg["dsu_hz"]))
    C.DSU_TX_INTERVAL = float(cfg["dsu_tx_interval"])
    C.DSU_BATCH = bool(cfg["dsu_batch"])
    C.ACC_RES = float(cfg["acc_res"])
    C.GYRO_RES = float(cfg["gyro_res"])
    C.GYRO_TO_DS4 = 16.0 / C.GYRO_RES          # surowe gyro -> jednostki raportu DS4
    C.ACC_SIGN = tuple(cfg["acc_sign"])
    C.GYRO_SIGN = tuple(cfg["gyro_sign"])
    C.MOUSE_SENS = float(cfg["mouse_sens"])
    C.SCROLL_SENS = float(cfg["scroll_sens"])
    C.SCROLL_NATURAL = bool(cfg["scroll_natural"])
    C.STALE_S = float(cfg["stale_s"])
    C.UNPLUG_S = float(cfg["unplug_s"])
    C.ALLOWED_IP = cfg["allow"]
    C.KEY = str(cfg["key"]).encode() if cfg["key"] else b""     # key = 1234 (liczba w .toml) - jak w nadajniku
    C.DEBUG = bool(cfg["stats"])
    C.USE_GUI = bool(cfg["gui"])
    C.LOW_BATT = int(cfg["low_batt"])
    C.REMAP_PATH = os.path.join(script_dir, cfg["remap_file"])
    C.CONSOLE_PATH = os.path.join(script_dir, "console.json")   # ustawienia trybu konsoli (rx_console.py)
    C.SETTINGS_PATH = os.path.join(script_dir, "settings.json")  # mysz i pad master (rx_settings.py, okno -> Ustawienia)
    C.REMAP_UI = bool(cfg["remap_ui"])
    C.REMAP_UI_PORT = int(cfg["remap_ui_port"])
    C.REMAP_UI_APP = bool(cfg["remap_ui_app"])
    for k, allowed in CHOICES.items():
        if cfg[k] not in allowed:
            print(f"WARNING: {k} = {cfg[k]!r} is not one of {allowed} - using {DEFAULTS[k]!r}")
            cfg[k] = DEFAULTS[k]
    C.PS_MODIFIER = bool(cfg["ps_modifier"])
    C.PS_HOLD_S = max(0.3, float(cfg["ps_hold_s"]))
    C.PS_HOLD_ACTION, C.PS_SHARE_ACTION = cfg["ps_hold_action"], cfg["ps_share_action"]
    C.CONFIRM = bool(cfg["confirm"])
    C.IDLE_RGB = tuple(max(0, min(255, int(v))) for v in ([*list(cfg["idle_lightbar"]), 0, 0, 0])[:3])
    C.GAME_PROFILES = bool(cfg["game_profiles"])
    C.GAME_IGNORE = {str(x).lower() for x in cfg["game_ignore"]}
    return C
