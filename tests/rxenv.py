"""Środowisko testowe receivera na Linuksie: zaślepki Windows (ctypes.windll, vgamepad) i rejestr wywołań."""
import ctypes, sys, types, tempfile, os
RX = sys.argv[1]
sys.path.insert(0, RX)
sys.path.insert(1, RX + "/../common")
class _F:
    def __getattr__(s, n): return _F()
    def __call__(s, *a, **k): return 1
ctypes.windll = types.SimpleNamespace(kernel32=_F(), user32=_F(), winmm=_F(), shcore=_F())
vg = types.ModuleType("vgamepad"); vg.DS4_REPORT_EX = ctypes.c_char * 63; vg.VDS4Gamepad = object
sys.modules["vgamepad"] = vg
TMP = tempfile.mkdtemp()
import rx_config
rx_config.init(["--pads", "2", "--remap-file", os.path.join(TMP, "remap.json")])      # testy działają na dwóch slotach
from rx_config import C
C.REMAP_PATH = os.path.join(TMP, "remap.json")
import rx_winapi as W
calls = []
W.send_mouse = lambda f, dx=0, dy=0, data=0: calls.append(("m", f, dx, dy, data))
W.send_click = lambda d, u: calls.append(("c", d, u))
W.send_keys = lambda *vks: calls.append(("k",) + vks)
W.foreground_exe = lambda: FG[0]
FG = [None]
import rx_buttons, rx_state as S, rx_mouse as M, rx_net as N, rx_hotkeys as H, rx_feedback as F
store = rx_buttons.RemapStore(C.REMAP_PATH)
S.init(store); M.init(C.NUM_PADS); H.init(C.NUM_PADS); F.init()
import rx_master; rx_master.init(C.NUM_PADS); import rx_testmode; rx_testmode.init(C.NUM_PADS); C.SETTINGS_PATH = os.path.join(TMP, "settings.json")
from proto import Codec, PAD_KEYS
N.codec = Codec()
N.clock.base = 0          # zegar PC = perf_counter (testy liczą czasy pong2 / state2 bez losowego przesunięcia)
ORIG_SEND_PEER = N.send_peer
ORIG_SEND_REPEAT = N.send_repeat
N.send_repeat = lambda pkt, n: [N.send_peer(pkt) for _ in range(n)]   # testy: kopie od razu (rozłożenie: test_receiver_core)
sent = []                                      # pakiety wysłane do nadajnika
N.send_peer = lambda pkt: sent.append(N.codec.parse(pkt) + (pkt,))
BIT = {c: 1 << k for k, c in enumerate(PAD_KEYS)}

def feed(t, i, *v):
    import time
    data = N.codec.pack(t, i, *v); p = N.codec.parse(data); S.last_pkt = time.monotonic(); N.handle(p[0], p[1], data)

def state(i=0, mask=0, hat=(0, 0), lt=0, rt=0, sticks=(128, 128, 128, 128), gyro=(0, 0, 0), batt=80):
    from proto import T_STATE
    lx, ly, rx, ry = sticks
    feed(T_STATE, i, lx, ly, lt, rx, ry, rt, hat[0], hat[1], mask, batt, 0, 0, 0, 8192, *gyro)
