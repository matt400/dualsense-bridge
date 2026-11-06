"""Receiver: regressions from the eighth review - registering game rumble in ViGEm (the callback signature checked by
vgamepad, the lightbar colour as a struct), receiver without ViGEmBus, releasing audio COM objects before CoUninitialize.
    python3 tests/test_vigem_callbacks.py receiver"""
import ctypes, importlib, inspect, os, sys, threading, time, types
sys.path.insert(0, os.path.dirname(__file__))
from rxenv import *                                              # noqa
import rx_events as ev
import rx_feedback as F
import rx_state as S

from chk import check, fails                                  # noqa: E402  (shared helper, tests/chk.py)


# ---------- ViGEm callback: signature as in vgamepad (0.1.0 compares it together with the names) ----------
def dummy_callback(client, target, large_motor, small_motor, led_number, user_data):   # copy from vgamepad.win
    pass

F.init()
check(inspect.signature(F.cbs[0]) == inspect.signature(dummy_callback),
      "the callback passes the vgamepad.register_notification check (TypeError before = no game rumble)")


# ---------- registration directly in ViGEmClient: the lightbar colour as a struct ----------
import rx_ds4


class LIGHTBAR(ctypes.Structure):                 # like vgamepad.win.vigem_commons.DS4_LIGHTBAR_COLOR
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
        raise AssertionError("registration through vgamepad instead of ViGEmClient")


cli, pad = FakeClient(), Pad()
rx_ds4.register_feedback(pad, F.cbs[0], vcli=cli, lightbar_type=LIGHTBAR)
check(cli.args[:2] == (11, 22) and pad.cmp_func is cli.args[2], "callback zarejestrowany w ViGEmClient, referencja trzymana")
S.alive[0] = True
cli.args[2](None, None, 200, 90, LIGHTBAR(255, 10, 0), None)     # call through ctypes / libffi as from ViGEm
f = F.fb[0]
check((f["large"], f["small"], f["r"], f["g"], f["b"], f["rgb"]) == (200, 90, 255, 10, 0, True),
      f"game rumble and colour arrive as numbers (colour from the struct, not a byte): {f}")
S.game = "game.exe"                                              # the game colour applies only in a game
val, ours, _b = F._target(0, time.monotonic())
check(val == (90, 200, 255, 10, 0) and ours, f"to the sender: game rumble and colour {val}")


class BadClient(FakeClient):
    def vigem_target_ds4_register_notification(self, *a):
        return 0xE0000001

try:
    rx_ds4.register_feedback(Pad(), F.cbs[0], vcli=BadClient(), lightbar_type=LIGHTBAR)
    check(False, "ViGEm error on registration reported")
except OSError as e:
    check("0xe0000001" in str(e), f"ViGEm error on registration reported: {e}")

# vgamepad declares the colour as a byte: when registered through it the game colour is unknown - the lightbar keeps its own
F.reset(0)
got = []


class VgPad:
    def register_notification(self, cb):
        if inspect.signature(cb) != inspect.signature(dummy_callback):
            raise TypeError("Needed callback function signature")
        got.append(cb)


rx_ds4.register_feedback(VgPad(), F.cbs[0], vcli=None)
check(got == [F.cbs[0]], "without ViGEmClient: registration through vgamepad passes")
got[0](None, None, 150, 0, 37, None)                             # 37 = a random byte instead of a colour
val, ours, _b = F._target(0, time.monotonic())
check(val[:2] == (0, 150) and val[2:] == C.IDLE_RGB and ours,
      f"game rumble works, and the lightbar does not go dark at (0, 0, 0) - own colour: {val}")
F.reset(0)


# ---------- receiver without ViGEmBus: vgamepad raises already on import ----------
saved = {k: sys.modules.pop(k) for k in [m for m in sys.modules if m == "rx_ds4" or m.startswith("vgamepad")]}
sys.modules["vgamepad"] = None                                   # import vgamepad -> ImportError (like a missing ViGEmBus)
try:
    nods4 = importlib.import_module("rx_ds4")
    check(nods4.vg is None and nods4.VG_ERROR is not None, "importing rx_ds4 without vgamepad does not crash the receiver")
    nods4.init()
    import rx_settings
    from dataclasses import replace
    rx_settings.apply(replace(rx_settings.cur, vpad=rx_settings.VpadConfig(fixed_slots=False)))   # only slot 1, without the placeholder 0
    seq = ev.since(0)[-1].seq if ev.since(0) else 0
    S.want[1].set()
    t = threading.Thread(target=nods4.slot_thread, args=(1,), daemon=True)
    t.start()
    time.sleep(0.3)
    errs = [e for e in ev.since(seq) if e.level == "error" and "ViGEmBus" in e.text]
    check(t.is_alive() and len(errs) == 1 and S.plugged[1] is False,
          f"slot without ViGEm: the error once in the log, the thread keeps running: {[e.text for e in errs]}")
    S.want[1].clear()
    S.stop.set()
    t.join(2)
    S.stop.clear()
finally:
    sys.modules.pop("rx_ds4", None)
    sys.modules.pop("vgamepad", None)
    sys.modules.update(saved)


# ---------- audio: COM objects released before CoUninitialize ----------
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
check(out == [("1", "Głośniki")], f"output list: {out}")
check(log == ["init", "release", "release", "uninit"], f"Release of COM objects before CoUninitialize: {log}")

print("ALL OK" if not fails else f"FAILURES: {len(fails)}")
sys.exit(1 if fails else 0)
