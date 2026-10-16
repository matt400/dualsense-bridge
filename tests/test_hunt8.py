"""Receiver: regresje z ósmego przeglądu - rejestracja wibracji z gry w ViGEm (sygnatura callbacku sprawdzana przez
vgamepad, kolor lightbara jako struktura), receiver bez ViGEmBus, zwalnianie obiektów COM audio przed CoUninitialize.
    python3 tests/test_hunt8.py receiver"""
import ctypes, importlib, inspect, os, sys, threading, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_events as ev
import rx_feedback as F
import rx_state as S

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond:
        fails.append(msg)


# ---------- callback ViGEm: sygnatura jak w vgamepad (0.1.0 porównuje ją razem z nazwami) ----------
def dummy_callback(client, target, large_motor, small_motor, led_number, user_data):   # kopia z vgamepad.win
    pass

F.init()
check(inspect.signature(F.cbs[0]) == inspect.signature(dummy_callback),
      "callback przechodzi sprawdzenie vgamepad.register_notification (wcześniej TypeError = brak wibracji z gier)")


# ---------- rejestracja bezpośrednio w ViGEmClient: kolor lightbara jako struktura ----------
import rx_ds4


class LIGHTBAR(ctypes.Structure):                 # jak vgamepad.win.vigem_commons.DS4_LIGHTBAR_COLOR
    _fields_ = [("Red", ctypes.c_ubyte), ("Green", ctypes.c_ubyte), ("Blue", ctypes.c_ubyte)]


class FakeClient:
    def __init__(self):
        self.args = None

    def vigem_target_ds4_register_notification(self, busp, devicep, fn, data):
        self.args = (busp, devicep, fn, data)
        return rx_ds4.VIGEM_ERROR_NONE


class Pad:
    _busp, _devicep = 11, 22

    def register_notification(self, cb):
        raise AssertionError("rejestracja przez vgamepad zamiast ViGEmClient")


cli, pad = FakeClient(), Pad()
rx_ds4.register_feedback(pad, F.cbs[0], vcli=cli, lightbar_type=LIGHTBAR)
check(cli.args[:2] == (11, 22) and pad.cmp_func is cli.args[2], "callback zarejestrowany w ViGEmClient, referencja trzymana")
S.alive[0] = True
cli.args[2](None, None, 200, 90, LIGHTBAR(255, 10, 0), None)     # wywołanie przez ctypes / libffi jak z ViGEm
f = F.fb[0]
check((f["large"], f["small"], f["r"], f["g"], f["b"], f["rgb"]) == (200, 90, 255, 10, 0, True),
      f"wibracja i kolor gry dochodzą jako liczby (kolor ze struktury, nie bajt): {f}")
val, ours, _b = F._target(0, time.monotonic())
check(val == (90, 200, 255, 10, 0) and ours, f"do nadajnika: wibracja i kolor gry {val}")


class BadClient(FakeClient):
    def vigem_target_ds4_register_notification(self, *a):
        return 0xE0000001

try:
    rx_ds4.register_feedback(Pad(), F.cbs[0], vcli=BadClient(), lightbar_type=LIGHTBAR)
    check(False, "błąd ViGEm przy rejestracji zgłaszany")
except OSError as e:
    check("0xe0000001" in str(e), f"błąd ViGEm przy rejestracji zgłaszany: {e}")

# vgamepad deklaruje kolor jako bajt: przy rejestracji przez niego kolor gry jest nieznany - lightbar zostaje własny
F.reset(0)
got = []


class VgPad:
    def register_notification(self, cb):
        if inspect.signature(cb) != inspect.signature(dummy_callback):
            raise TypeError("Needed callback function signature")
        got.append(cb)


rx_ds4.register_feedback(VgPad(), F.cbs[0], vcli=None)
check(got == [F.cbs[0]], "bez ViGEmClient: rejestracja przez vgamepad przechodzi")
got[0](None, None, 150, 0, 37, None)                             # 37 = przypadkowy bajt zamiast koloru
val, ours, _b = F._target(0, time.monotonic())
check(val[:2] == (0, 150) and val[2:] == C.IDLE_RGB and ours,
      f"wibracja z gry działa, a lightbar nie gaśnie do (0, 0, 0) - własny kolor: {val}")
F.reset(0)


# ---------- receiver bez ViGEmBus: vgamepad rzuca już przy imporcie ----------
saved = {k: sys.modules.pop(k) for k in [m for m in sys.modules if m == "rx_ds4" or m.startswith("vgamepad")]}
sys.modules["vgamepad"] = None                                   # import vgamepad -> ImportError (jak brak ViGEmBus)
try:
    nods4 = importlib.import_module("rx_ds4")
    check(nods4.vg is None and nods4.VG_ERROR is not None, "import rx_ds4 bez vgamepad nie wywraca receivera")
    nods4.init()
    seq = ev.since(0)[-1].seq if ev.since(0) else 0
    S.want[1].set()
    t = threading.Thread(target=nods4.slot_thread, args=(1,), daemon=True)
    t.start()
    time.sleep(0.3)
    errs = [e for e in ev.since(seq) if e.level == "error" and "ViGEmBus" in e.text]
    check(t.is_alive() and len(errs) == 1 and S.plugged[1] is False,
          f"slot bez ViGEm: błąd raz w dzienniku, wątek działa dalej: {[e.text for e in errs]}")
    S.want[1].clear()
    S.stop.set()
    t.join(2)
    S.stop.clear()
finally:
    sys.modules.pop("rx_ds4", None)
    sys.modules.pop("vgamepad", None)
    sys.modules.update(saved)


# ---------- audio: obiekty COM zwolnione przed CoUninitialize ----------
log = []
comtypes = types.ModuleType("comtypes")
comtypes.CoInitialize = lambda: log.append("init")
comtypes.CoUninitialize = lambda: log.append("uninit")


class Dev:
    def __init__(self, i, name):
        self.id, self.FriendlyName = i, name

    def __del__(self):
        log.append("release")


pycaw = types.ModuleType("pycaw")
consts = types.ModuleType("pycaw.constants")
consts.DEVICE_STATE = types.SimpleNamespace(ACTIVE=types.SimpleNamespace(value=1))
consts.EDataFlow = types.SimpleNamespace(eRender=types.SimpleNamespace(value=0))
utils = types.ModuleType("pycaw.utils")
utils.AudioUtilities = types.SimpleNamespace(GetAllDevices=lambda flow, state: [Dev("1", "Głośniki"), Dev("2", "")])
sys.modules.update({"comtypes": comtypes, "pycaw": pycaw, "pycaw.constants": consts, "pycaw.utils": utils})
import rx_audio
out = rx_audio.list_outputs()
check(out == [("1", "Głośniki")], f"lista wyjść: {out}")
check(log == ["init", "release", "release", "uninit"], f"Release obiektów COM przed CoUninitialize: {log}")

print("WSZYSTKO OK" if not fails else f"BŁĘDY: {len(fails)}")
sys.exit(1 if fails else 0)
