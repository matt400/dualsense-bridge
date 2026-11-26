"""Receiver: the IL helper from rx_hm (ReadOnlyMemory<byte> -> native buffer with one copy) on a real CoreCLR.
Without pythonnet / .NET the test skips itself (CI does not have it). A real clr is needed; the only stub is ctypes.windll
(importing rx_hm touches it).
The virtual touchpad part also needs HIDMaestro.Core.dll 2.4.0+ (HM_DLL=path, or the downloaded one) and skips without it.
    python3 tests/test_hm_il.py receiver"""
import ctypes, os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "receiver"))
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "..", "common"))
try:
    from pythonnet import load
    load("coreclr")
    import clr
    from System import Action, Array, Byte, IntPtr, Object, ReadOnlyMemory
except Exception as e:  # noqa: BLE001
    print("SKIP  brak pythonnet / CoreCLR:", type(e).__name__)
    sys.exit(0)
if not hasattr(ctypes, "windll"):                       # off Windows: enough for rx_winapi to import, nothing calls it
    class _F:
        def __getattr__(self, name): return _F()
        def __call__(self, *a, **k): return 1
    ctypes.windll = types.SimpleNamespace(kernel32=_F(), user32=_F(), winmm=_F(), shcore=_F())
import rx_hm

copy = rx_hm.memcopy_delegate(clr)
CAP = 64
scratch = bytearray(CAP)
view = (ctypes.c_char * CAP).from_buffer(scratch)
ptr = IntPtr(ctypes.addressof(view))
data = bytes(range(1, 41))
mem = ReadOnlyMemory[Byte](Array[Byte](data), 4, 24)
n = copy(mem, ptr, CAP)
assert n == 24 and bytes(scratch[:24]) == data[4:28] and not any(scratch[24:]), (n, bytes(scratch[:30]))
print("OK  copy of a slice")
scratch[:] = bytes(CAP)
n = copy(ReadOnlyMemory[Byte](Array[Byte](bytes([7]) * 200)), ptr, CAP)
assert n == CAP and bytes(scratch) == bytes([7]) * CAP, n
print("OK  cut to the buffer capacity")
got = []
act = Action[Object, ReadOnlyMemory[Byte]](lambda s, m: got.append(copy(m, ptr, CAP)))
scratch[:] = bytes(CAP)
act.Invoke(None, mem)
assert got == [24] and bytes(scratch[:24]) == data[4:28], got
print("OK  called from .NET as an event delegate")

dll = os.environ.get("HM_DLL") or rx_hm.find_dll()
if dll is None:
    print("SKIP  the virtual touchpad: no HIDMaestro.Core.dll")
    print("HM IL OK")
    sys.exit(0)
sys.path.append(os.path.dirname(os.path.abspath(dll)))
clr.AddReference(os.path.abspath(dll))
from rx_vtouch import Contact

profile, contact_type, submit = rx_hm.touchpad_parts(clr)
layout = profile.Touch
assert layout.IsTouchpad and layout.MaxContacts >= 2 and int(layout.Buttons) == 1, (layout.IsTouchpad, layout.MaxContacts)
print("OK  the touchpad profile builds: a touchpad with two fingers and one button")
sent = []
pad = rx_hm.Touchpad(None, layout, contact_type, lambda c, a, n, b: sent.append(([(a[k].Id, a[k].X, a[k].Y, a[k].Palm)
                                                                                 for k in range(n)], b)))
pad.submit((Contact(3, 0, 0), Contact(4, 1919, 942, True)), True)
assert sent == [([(3, 0, 0, False), (4, layout.X.Maximum, layout.Y.Maximum, True)], 1)], sent
print("OK  contacts reach the .NET array, scaled to the descriptor")
try:                       # no device off Windows: a null controller proves the IL up to the call itself
    submit(None, pad._array, 2, 1)
except Exception as e:  # noqa: BLE001
    assert "NullReference" in type(e).__name__, repr(e)
else:
    raise AssertionError("a null controller must throw")
print("OK  the SubmitTouch delegate binds and runs")
print("HM IL OK")
