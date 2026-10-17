"""Receiver: backend HIDMaestro - wirtualny DualSense (sterownik UMDF2 w trybie użytkownika, MIT, rozwijany).

HIDMaestro to biblioteka .NET (HIDMaestro.Core.dll, .NET 10) - wołana z Pythona przez pythonnet (CoreCLR). Sterownik
siedzi w samej DLL i instaluje się przy pierwszym użyciu (InstallDriver, idempotentne). Tworzenie urządzenia wymaga
uprawnień administratora (Windows: SeLoadDriverPrivilege) - bez nich backend się nie ładuje i receiver wraca do ViGEm.

  get(cfg_path)   -> Backend (ładowany raz na ścieżkę DLL; błąd zapamiętany do reset())
  find_dll(path)  ścieżka z ustawień albo hidmaestro/HIDMaestro.Core.dll obok receivera
  Backend.create(slot, on_fb) -> Pad: build() pisze raport DualSense do pad.buf, submit() wysyła go bez kopiowania

Gorąca ścieżka: SubmitRawReport(ReadOnlySpan<byte>) nie da się zawołać z pythonnet wprost (Span to ref struct), więc
przy ładowaniu powstaje mała metoda IL: (kontroler, wskaźnik, długość) -> SubmitRawReport(new ReadOnlySpan(wsk, dł)).
Raport leży w stałym buforze Pythona - jedno wywołanie delegata na raport (~4 us), bez alokacji i kopiowania po stronie
Pythona. Interop sprawdzony z pythonnet 3.2 i .NET 10 na atrapie API HIDMaestro (te same sygnatury).
"""
import ctypes
import os
import sys
import threading

from rx_report import build_ds5_report

DLL = "HIDMaestro.Core.dll"
PROFILE = "dualsense"          # profil USB DualSense (054C:0CE6) z HIDMaestro
REPORT_LEN = 63                # raport wejściowy 0x01 bez bajtu ID
ORDER_WAIT = 10.0              # tworzenie urządzenia PnP trwa od ~0.1 s do kilku s (wyższe sloty czekają na niższe)
OUT_REPORT = 0x02              # raport wyjściowy DualSense: silniki, efekty spustów, lightbar
HID_OUTPUT = 0                 # HMOutputSource.HidOutput


class HMError(Exception):
    """HIDMaestro niedostępny; missing = brak DLL (w trybie auto to zwykła sytuacja, bez ostrzeżenia)."""

    def __init__(self, msg, missing=False):
        super().__init__(msg)
        self.missing = missing


_lock = threading.Lock()
_cache = {}                    # ścieżka DLL -> Backend albo HMError (nie próbujemy w kółko - reset() przy zmianie ustawień)
_clr_ready = False


def here():
    return os.path.dirname(os.path.abspath(__file__))


def candidates(cfg_path=""):
    if cfg_path:
        p = os.path.expandvars(os.path.expanduser(cfg_path))
        return [os.path.join(p, DLL) if os.path.isdir(p) else p]
    return [os.path.join(here(), "hidmaestro", DLL), os.path.join(here(), DLL)]


def find_dll(cfg_path=""):
    return next((p for p in candidates(cfg_path) if os.path.isfile(p)), None)


def is_admin():
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def reset():
    """Zmiana ustawień: następne get() próbuje od nowa (np. po wskazaniu innej DLL). Załadowany backend zostaje."""
    with _lock:
        for k in [k for k, v in _cache.items() if isinstance(v, HMError)]:
            del _cache[k]


def get(cfg_path=""):
    """-> Backend albo HMError. Ładowanie (CoreCLR + sterownik) tylko raz - pierwszy wątek slotu płaci ~0.5-1 s."""
    path = find_dll(cfg_path)
    if path is None:
        raise HMError(f"{DLL} not found ({', '.join(candidates(cfg_path))})", missing=True)
    with _lock:
        hit = _cache.get(path)
        if isinstance(hit, HMError):
            raise hit
        if hit is None:
            try:
                hit = Backend(path)
            except HMError as e:
                _cache[path] = e
                raise
            except Exception as e:            # pythonnet / .NET: każdy wyjątek jako czytelny błąd backendu
                err = HMError(f"{type(e).__name__}: {e}")
                _cache[path] = err
                raise err from e
            _cache[path] = hit
        return hit


def _load_clr(path):
    """CoreCLR przez pythonnet (raz na proces) i referencja do HIDMaestro.Core.dll z jej folderu (zależności obok)."""
    global _clr_ready
    if not _clr_ready:
        try:
            from pythonnet import load
        except ImportError:
            raise HMError("pythonnet not installed (pip install pythonnet)") from None
        try:
            load("coreclr")
        except RuntimeError:
            pass                                # runtime już załadowany (np. drugi backend po zmianie ustawień)
        except Exception as e:
            raise HMError(f".NET runtime: {e} (install .NET 10 Desktop Runtime)") from e
        _clr_ready = True
    import clr
    folder = os.path.dirname(path)
    if folder not in sys.path:
        sys.path.append(folder)
    clr.AddReference(path)
    return clr


def submit_delegate(clr, ctrl_type):
    """Delegat (kontroler, IntPtr, int) -> SubmitRawReport(new ReadOnlySpan<byte>((void*)ptr, len)) jako metoda IL.
    pythonnet nie przekazuje bytes / tablic jako ReadOnlySpan, więc bez tego delegata nie ma jak wysłać raportu."""
    try:
        from System import Action, Array, Byte, Int32, IntPtr, Type, Void
        from System.Reflection.Emit import DynamicMethod, OpCodes
        t_byte, t_int, t_ptr = clr.GetClrType(Byte), clr.GetClrType(Int32), clr.GetClrType(IntPtr)
        t_ctrl = clr.GetClrType(ctrl_type)
        span = t_byte.Assembly.GetType("System.ReadOnlySpan`1").MakeGenericType(t_byte)
        ctor = span.GetConstructor(Array[Type]([clr.GetClrType(Void).MakePointerType(), t_int]))
        meth = t_ctrl.GetMethod("SubmitRawReport", Array[Type]([span]))
        if ctor is None or meth is None:
            raise HMError("SubmitRawReport(ReadOnlySpan<byte>) not found in this HIDMaestro version")
        dm = DynamicMethod("dsbridge_submit_raw", clr.GetClrType(Void), Array[Type]([t_ctrl, t_ptr, t_int]), t_ctrl.Module, True)
        il = dm.GetILGenerator()
        for op in (OpCodes.Ldarg_0, OpCodes.Ldarg_1, OpCodes.Ldarg_2):
            il.Emit(op)
        il.Emit(OpCodes.Newobj, ctor)
        il.Emit(OpCodes.Callvirt, meth)
        il.Emit(OpCodes.Ret)
        fn = dm.CreateDelegate(clr.GetClrType(Action[ctrl_type, IntPtr, Int32]))
        return getattr(fn, "Invoke", fn)
    except HMError:
        raise
    except Exception as e:
        raise HMError(f"cannot bind SubmitRawReport: {e!r}") from e


class Backend:
    name = "hm"
    order_wait = ORDER_WAIT

    def __init__(self, path):
        if not is_admin():
            raise HMError("administrator rights required (Windows needs them to create a virtual device)")
        clr = _load_clr(path)
        from HIDMaestro import HMContext, HMController
        from System import IntPtr
        self.IntPtr = IntPtr
        self.ctx = HMContext()
        self.ctx.LoadDefaultProfiles()
        self.ctx.InstallDriver()               # idempotentne: ~50 ms, gdy sterownik jest aktualny
        self.profile = self.ctx.GetProfile(PROFILE)
        if self.profile is None:
            raise HMError(f"HIDMaestro has no '{PROFILE}' profile")
        self.submit_fn = submit_delegate(clr, HMController)
        self.path = path
        print(f"HIDMaestro loaded: {path}")

    def create(self, slot, on_fb):
        """Wirtualny DualSense na stałym indeksie i ze stałą tożsamością slotu: te same ścieżki urządzenia po każdym
        odtworzeniu (Steam / gry pamiętają ustawienia pada). Stara wersja SDK bez klucza tożsamości - sam indeks."""
        key = f"dualsense-bridge:{slot}"
        try:
            ctrl = self.ctx.CreateControllerAt(slot, self.profile, key)
        except TypeError:
            ctrl = self.ctx.CreateControllerAt(slot, self.profile)
        try:
            return Pad(self, ctrl, on_fb)
        except Exception:
            ctrl.Dispose()
            raise


def parse_output(report_id, data):
    """Raport wyjściowy DualSense (bez bajtu ID) -> ((duży, mały) | None, (r, g, b) | None). Flagi ważności decydują,
    które części gra ustawia: bit 0/1 flagi 0 = silniki, bit 2 flagi 1 = kolor lightbara."""
    if report_id != OUT_REPORT or len(data) < 47:
        return None, None
    motors = (data[3], data[2]) if data[0] & 0x03 else None      # lewy = duży (niski), prawy = mały (wysoki)
    rgb = (data[44], data[45], data[46]) if data[1] & 0x04 else None
    return motors, rgb


class Pad:
    """Jeden wirtualny DualSense. buf ma stały adres (eksport do ctypes blokuje zmianę rozmiaru) - raport budowany
    w miejscu i wysyłany przez delegata bez kopii po stronie Pythona."""
    kind = "hm"
    build = staticmethod(build_ds5_report)

    def __init__(self, backend, ctrl, on_fb):
        self.ctrl, self.on_fb = ctrl, on_fb
        self.buf = bytearray(REPORT_LEN)
        self._view = (ctypes.c_char * REPORT_LEN).from_buffer(self.buf)
        self._ptr = backend.IntPtr(ctypes.addressof(self._view))
        self._fn = backend.submit_fn
        self._handler = self._on_output        # referencja (delegat .NET nie może zniknąć przez GC)
        ctrl.OutputReceived += self._handler
        self.submit()                          # próba od razu: zła ścieżka wysyłki = błąd tutaj, nie w grze

    def submit(self):
        self._fn(self.ctrl, self._ptr, REPORT_LEN)

    def _on_output(self, _ctrl, pkt):
        """Wątek wyjścia HIDMaestro: tylko szybkie odczyty (SDK nadpisuje najstarsze pakiety, gdy handler stoi)."""
        try:
            if int(pkt.Source) != HID_OUTPUT:
                return
            d = pkt.Data.ToArray()
            motors, rgb = parse_output(int(pkt.ReportId), [d[k] for k in range(min(len(d), 47))])
            if motors is not None or rgb is not None:
                self.on_fb(motors, rgb)
        except Exception:
            pass

    def close(self):
        try:
            self.ctrl.OutputReceived -= self._handler
        except Exception:
            pass
        self.ctrl.Dispose()                    # usuwa urządzenie z systemu
