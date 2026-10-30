"""Restart z ustawień: main() zamyka sloty (wirtualne DS4), gniazda UDP i blokadę instancji, a dopiero potem uruchamia nowy proces."""
import ctypes, sys, types, threading, time, socket, tempfile, os
RX = sys.argv[1]; sys.path.insert(0, RX)
sys.path.insert(1, RX + "/../common")
class _F:
    def __getattr__(s, n): return _F()
    def __call__(s, *a, **k): return 1
ctypes.windll = types.SimpleNamespace(kernel32=_F(), user32=_F(), winmm=_F(), shcore=_F())
EVENTS = []
class Pad:
    def __init__(self): EVENTS.append("plug")
    def register_notification(self, cb): pass
    def unregister_notification(self): pass
    def update_extended_report(self, rep): pass
    def __del__(self): EVENTS.append("unplug")
vg = types.ModuleType("vgamepad"); vg.DS4_REPORT_EX = ctypes.c_char * 63; vg.VDS4Gamepad = Pad
sys.modules["vgamepad"] = vg
errors = []
threading.excepthook = lambda a: errors.append(f"{a.thread.name}: {a.exc_type.__name__}: {a.exc_value}")
tmp = tempfile.mkdtemp()
sys.argv = ["receiver_controller.py", "--no-gui", "--pads", "2", "--remap-file", os.path.join(tmp, "r.json")]
import rx_config
_init = rx_config.init
def init(argv=None):
    C = _init(argv); C.IN_PORT = 25105; C.DSU_PORT = 26899; C.CONSOLE_PATH = os.path.join(tmp, "console.json")
    return C
rx_config.init = init
import rx_console, rx_winapi, rx_net, rx_dsu, rx_state as S
rx_console._display = lambda m: None; rx_console._audio = lambda n: None; rx_console._toast = lambda *a: None
rx_winapi.release_single_instance = lambda: EVENTS.append("release_mutex")
state_at_relaunch = {}
def relaunch(env=None, admin=False):
    EVENTS.append("relaunch")
    state_at_relaunch["env"] = env
    for name, port in (("in", 25105), ("dsu", 26899)):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try: s.bind(("0.0.0.0", port)); state_at_relaunch[name] = True
        except OSError: state_at_relaunch[name] = False
        finally: s.close()
    state_at_relaunch["plugged"] = list(S.plugged)
rx_winapi.relaunch = relaunch
import receiver_controller
t = threading.Thread(target=receiver_controller.main, name="main", daemon=True); t.start()
time.sleep(1.0)
from proto import Codec, T_STATE
c = Codec(); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for k in range(10):
    s.sendto(c.pack(T_STATE, 0, 200, 128, 0, 128, 128, 0, 0, 0, 1, 77, 0, 0, 0, 8192, 0, 0, 0), ("127.0.0.1", 25105)); time.sleep(0.02)
time.sleep(0.3)
assert S.plugged[0], "wirtualny DS4 powinien być podłączony"
for _ in range(100):                     # bez mastera pierwszy pad włącza tryb konsoli (rx_console.auto_pad)
    if rx_console.is_on():
        break
    time.sleep(0.02)
assert rx_console.is_on(), "pierwszy połączony pad (bez mastera) włącza tryb konsoli"
S.restart = True; S.stop.set()
t.join(5)
assert not t.is_alive(), "main() nie zakończył się"
assert "relaunch" in EVENTS, EVENTS
assert state_at_relaunch == {"in": True, "dsu": True, "plugged": [False, False], "env": {"DSB_CONSOLE_ON": "1"}}, state_at_relaunch
assert EVENTS.index("unplug") < EVENTS.index("release_mutex") < EVENTS.index("relaunch"), EVENTS
assert not errors, errors
print("RESTART OK - kolejność:", [e for e in EVENTS if e != "plug"], "| porty wolne, wirtualny DS4 odłączony")
