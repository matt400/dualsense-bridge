"""Receiver: the IL helpers from rx_hm on a real CoreCLR.

Always (with pythonnet + .NET): ReadOnlyMemory<byte> -> native buffer with one copy. With HM_DLL=path/to/HIDMaestro.Core.dll also
the real library: the profiles the receiver creates exist, their reports have the sizes our builders write, the SubmitRawReport /
SubmitTouch / SubmitState delegates bind, and the SubmitState fill method reads rx_xbox.build_state's block into the right
HMGamepadState (axis keys from StandardAxes, buttons, hat). No device is created, so it runs on Linux (.NET 10 from apt).
Without pythonnet / .NET the test skips itself (CI does not have it). No Windows stubs for .NET - a real clr is needed.
    python3 tests/test_hm_il.py receiver
    HM_DLL=receiver/hidmaestro/v2.4.1/HIDMaestro.Core.dll python3 tests/test_hm_il.py receiver"""
import ctypes, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "receiver"), os.path.join(HERE, "..", "common")]
if not hasattr(ctypes, "windll"):          # rx_hm imports rx_winapi, which binds Windows DLLs at import; only rx_hm's pure parts run here
    class _AnyDll:
        def __getattr__(self, name):
            return _AnyDll()
        def __call__(self, *a, **k):
            return 0
    ctypes.windll = _AnyDll()                # type: ignore[attr-defined]
    ctypes.WinDLL = lambda *a, **k: _AnyDll()  # type: ignore[attr-defined]
try:
    from pythonnet import load
    load("coreclr")
    import clr
    from System import Action, Array, Byte, IntPtr, Object, ReadOnlyMemory
except Exception as e:  # noqa: BLE001
    print("SKIP  no pythonnet / CoreCLR:", type(e).__name__)
    sys.exit(0)
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

dll = os.environ.get("HM_DLL")
if not dll:
    print("HM IL OK (set HM_DLL to also check a HIDMaestro.Core.dll)")
    sys.exit(0)
dll = os.path.abspath(dll)
sys.path.append(os.path.dirname(dll))
clr.AddReference(dll)
from HIDMaestro import HMAxis, HMButton, HMContext, HMController, HMHat, HMMouseButtons, HMTouchContact  # noqa: E402
from System import Single  # noqa: E402
from System.Collections.Generic import Dictionary  # noqa: E402
import rx_report, rx_state, rx_xbox  # noqa: E402

ctx = HMContext()
ctx.LoadDefaultProfiles()
sizes = {}
for name in (rx_hm.PROFILE, rx_hm.PROFILE_COMPOSITE, rx_hm.PROFILE_DS4, rx_hm.PROFILE_X360):
    p = ctx.GetProfile(name)
    assert p is not None, (name, rx_hm.profile_issues(ctx))
    sizes[name] = int(p.InputReportSize)
print("OK  profiles:", sizes)
# the raw input report without its ID byte is what rx_report builds (63 B)
for name in (rx_hm.PROFILE, rx_hm.PROFILE_DS4):
    assert sizes[name] - 1 == rx_report.REPORT_LEN == rx_hm.REPORT_LEN, (name, sizes[name])
ds4 = ctx.GetProfile(rx_hm.PROFILE_DS4)
assert (int(ds4.VendorId), int(ds4.ProductId)) == (0x054C, 0x09CC), (ds4.VendorId, ds4.ProductId)
x360 = ctx.GetProfile(rx_hm.PROFILE_X360)
assert (int(x360.VendorId), int(x360.ProductId)) == (0x045E, 0x028E), (x360.VendorId, x360.ProductId)
print("OK  report sizes and USB ids")

rx_hm.submit_delegate(clr, HMController)
rx_hm.touch_delegate(clr, HMController, HMTouchContact, HMMouseButtons)
keys = rx_hm.axis_keys(x360)
assert [str(k) for k in keys] == ["X", "Y", "Rx", "Ry", "Vx", "Vy"], [str(k) for k in keys]
fill, submit = rx_hm.state_delegates(clr, HMController, keys)
print("OK  SubmitRawReport / SubmitTouch / SubmitState delegates bind; Xbox 360 axis keys", [str(k) for k in keys])

from rx_buttons import BIT  # noqa: E402
s = dict(rx_state.NEUTRAL_OUT)
s.update(lx=0, ly=255, rx=128, ry=64, lt=255, rt=0, hx=1, hy=-1, mask=BIT[304] | BIT[307] | BIT[316])   # cross, triangle, PS
block = rx_xbox.build_state(s, 0, False)
bview = (ctypes.c_char * len(block)).from_buffer(block)
axes = Dictionary[HMAxis, Single]()
st = fill(IntPtr(ctypes.addressof(bview)), axes)
vals = [float(st.Axes[k]) for k in keys]
assert vals == [0.0, 1.0, 0.5, 64 / 256, 1.0, 0.0], vals
assert int(st.Buttons) == int(HMButton.A) | int(HMButton.Y) | int(HMButton.Guide), int(st.Buttons)
assert int(st.Hat) == int(HMHat.NorthEast), int(st.Hat)
assert st.Axes is not None and int(axes.Count) == 6
print("OK  SubmitState fill: axes, buttons, hat")
try:
    submit(None, IntPtr(ctypes.addressof(bview)), axes)
except Exception as e:  # noqa: BLE001 - a null controller: the IL ran up to the SubmitState call
    assert "NullReference" in type(e).__name__ + str(e), repr(e)
else:
    raise AssertionError("SubmitState on a null controller did not throw")
print("OK  SubmitState delegate runs up to the call")
print("HM IL OK (with", os.path.basename(os.path.dirname(dll)), ")")
