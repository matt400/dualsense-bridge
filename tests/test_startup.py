"""Uruchamia prawdziwe receiver_controller.main() (bez GUI) z zaślepkami Windows i wysyła pakiety przez UDP."""
import ctypes, sys, types, threading, time, socket, tempfile, os
RX = sys.argv[1]; sys.path.insert(0, RX)
class _F:
    def __getattr__(s, n): return _F()
    def __call__(s, *a, **k): return 1
ctypes.windll = types.SimpleNamespace(kernel32=_F(), user32=_F(), winmm=_F(), shcore=_F())
class Pad:
    def register_notification(self, cb): pass
    def unregister_notification(self): pass
    def update_extended_report(self, rep): REPORTS.append(bytes(rep)[:12])
REPORTS = []
vg = types.ModuleType("vgamepad"); vg.DS4_REPORT_EX = ctypes.c_char * 63; vg.VDS4Gamepad = Pad
sys.modules["vgamepad"] = vg
errors = []
threading.excepthook = lambda a: errors.append(f"{a.thread.name}: {a.exc_type.__name__}: {a.exc_value}")
tmp = tempfile.mkdtemp()
sys.argv = ["receiver_controller.py", "--no-gui", "--remap-file", os.path.join(tmp, "r.json")]
import rx_config
_init = rx_config.init
def init(argv=None):
    C = _init(argv); C.IN_PORT = 25005; C.DSU_PORT = 26799; C.REMAP_UI_PORT = 0; C.GAME_PROFILES = True
    return C
rx_config.init = init
import receiver_controller
t = threading.Thread(target=receiver_controller.main, name="main", daemon=True); t.start()
time.sleep(1.0)
import rx_state as S, rx_winapi as W
W.foreground_exe = lambda: "game.exe"
from proto import Codec, T_STATE, T_IDENT, T_PING
c = Codec(); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(c.pack(T_IDENT, 0, b"aa:bb:cc:dd:ee:ff"), ("127.0.0.1", 25005))
for k in range(20):
    s.sendto(c.pack(T_STATE, 0, 200 - (k % 2), 128, 0, 128, 128, 0, 0, 0, 1, 77, 0, 0, 0, 8192, 0, 0, 0), ("127.0.0.1", 25005))
    time.sleep(0.02)
time.sleep(0.6)   # okno gry: 2 odpytania co 0.5 s
for k in range(3):
    s.sendto(c.pack(T_STATE, 0, 200 - (k % 2), 128, 0, 128, 128, 0, 0, 0, 1, 77, 0, 0, 0, 8192, 0, 0, 0), ("127.0.0.1", 25005)); time.sleep(0.03)
print("alive:", S.alive[0], "pad_id:", S.pad_id[0], "game:", S.game, "DS4 reports:", len(REPORTS),
      "lx:", REPORTS[-1][0] if REPORTS else None)
assert S.alive[0] and S.pad_id[0] == "aa:bb:cc:dd:ee:ff" and S.game == "game.exe" and REPORTS and REPORTS[-1][0] == 200
S.stop.set(); time.sleep(0.5)
assert not errors, errors
print("STARTUP OK - main() startuje, wszystkie wątki działają bez wyjątków")
