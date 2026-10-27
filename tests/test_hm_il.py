"""Receiver: pomocnik IL z rx_hm (ReadOnlyMemory<byte> -> bufor natywny jedną kopią) na prawdziwym CoreCLR.
Bez pythonnet / .NET test sam się pomija (CI go nie ma). Bez zaślepek Windows - potrzebny jest prawdziwy clr.
    python3 tests/test_hm_il.py receiver"""
import ctypes, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "receiver"))
try:
    from pythonnet import load
    load("coreclr")
    import clr
    from System import Action, Array, Byte, IntPtr, Object, ReadOnlyMemory
except Exception as e:  # noqa: BLE001
    print("SKIP  brak pythonnet / CoreCLR:", type(e).__name__)
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
print("OK  kopia wycinka")
scratch[:] = bytes(CAP)
n = copy(ReadOnlyMemory[Byte](Array[Byte](bytes([7]) * 200)), ptr, CAP)
assert n == CAP and bytes(scratch) == bytes([7]) * CAP, n
print("OK  obcięcie do pojemności bufora")
got = []
act = Action[Object, ReadOnlyMemory[Byte]](lambda s, m: got.append(copy(m, ptr, CAP)))
scratch[:] = bytes(CAP)
act.Invoke(None, mem)
assert got == [24] and bytes(scratch[:24]) == data[4:28], got
print("OK  wywołanie z .NET jako delegat zdarzenia")
print("HM IL OK")
