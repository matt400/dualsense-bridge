"""Receiver: backend HIDMaestro - wirtualny DualSense (sterownik UMDF2 w trybie użytkownika, MIT, rozwijany).

HIDMaestro to biblioteka .NET (HIDMaestro.Core.dll, .NET 10) - wołana z Pythona przez pythonnet (CoreCLR). Sterownik
siedzi w samej DLL i instaluje się przy pierwszym użyciu (InstallDriver, idempotentne). Tworzenie urządzenia wymaga
uprawnień administratora (Windows: SeLoadDriverPrivilege) - bez nich backend się nie ładuje i receiver wraca do ViGEm.

  get(cfg_path)   -> Backend (ładowany raz na ścieżkę DLL; błąd zapamiętany do reset())
  find_dll(path)  ścieżka z ustawień albo najnowsza pobrana wersja (rx_hm_update: hidmaestro/vX.Y.Z/), potem
                  hidmaestro/HIDMaestro.Core.dll obok receivera (wypakowana ręcznie)
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

from proto import TRIG_RAW_LEN
from rx_report import build_ds5_report

DLL = "HIDMaestro.Core.dll"
PROFILE = "dualsense"          # profil USB DualSense (054C:0CE6) z HIDMaestro
REPORT_LEN = 63                # raport wejściowy 0x01 bez bajtu ID
ORDER_WAIT = 10.0              # tworzenie urządzenia PnP trwa od ~0.1 s do kilku s (wyższe sloty czekają na niższe)
OUT_REPORT = 0x02              # raport wyjściowy DualSense: silniki, efekty spustów, lightbar
HID_OUTPUT = 0                 # HMOutputSource.HidOutput


NET_MAJOR = 10                 # HIDMaestro.Core.dll jest pod net10.0 - starszy runtime nie wczyta jej typów
WINGET_NET = "Microsoft.DotNet.Runtime.10"


class HMError(Exception):
    """HIDMaestro niedostępny; missing = brak DLL (w trybie auto to zwykła sytuacja, bez ostrzeżenia);
    need_dotnet = brak .NET 10 (okno proponuje instalację)."""

    def __init__(self, msg, missing=False, need_dotnet=False):
        super().__init__(msg)
        self.missing, self.need_dotnet = missing, need_dotnet


_lock = threading.Lock()
_cache = {}                    # ścieżka DLL -> Backend albo HMError (nie próbujemy w kółko - reset() przy zmianie ustawień)
_clr_ready = False
_loaded = None                 # załadowany Backend: jedna wersja HIDMaestro na proces (CLR nie zwalnia DLL) - nowsza
#                                pobrana w trakcie działa od restartu receivera


def here():
    return os.path.dirname(os.path.abspath(__file__))


def candidates(cfg_path=""):
    if cfg_path:
        p = os.path.expandvars(os.path.expanduser(cfg_path))
        return [os.path.join(p, DLL) if os.path.isdir(p) else p]
    import rx_hm_update
    newest = rx_hm_update.newest_dll()
    return [*([newest] if newest else []), os.path.join(here(), "hidmaestro", DLL), os.path.join(here(), DLL)]


def find_dll(cfg_path=""):
    return next((p for p in candidates(cfg_path) if os.path.isfile(p)), None)


def is_admin():
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def loaded():
    return _loaded


def reset():
    """Zmiana ustawień: następne get() próbuje od nowa (np. po wskazaniu innej DLL). Załadowany backend zostaje."""
    with _lock:
        for k in [k for k, v in _cache.items() if isinstance(v, HMError)]:
            del _cache[k]


def get(cfg_path=""):
    """-> Backend albo HMError. Ładowanie (CoreCLR + sterownik) tylko raz - pierwszy wątek slotu płaci ~0.5-1 s."""
    global _loaded
    if _loaded is not None:
        return _loaded
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
            _cache[path] = _loaded = hit
        return hit


def _major(version):
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return 0


def net_runtimes():
    """Zainstalowane wersje Microsoft.NETCore.App (clr_loader szuka ich jak dotnet: DOTNET_ROOT, Program Files).
    None = nie wiadomo (brak pythonnet/clr_loader) - to nie to samo co brak .NET."""
    try:
        from clr_loader import find_runtimes
    except ImportError:
        return None
    try:
        return [r for r in find_runtimes() if r.name == "Microsoft.NETCore.App"]
    except Exception:
        return []


def best_runtime(runtimes=None):
    """Najnowszy runtime >= NET_MAJOR albo None."""
    ok = [r for r in (net_runtimes() if runtimes is None else runtimes) or () if _major(r.version) >= NET_MAJOR]
    return max(ok, key=lambda r: tuple(_major(x) for x in str(r.version).split("."))) if ok else None


def need_dotnet():
    """Okno: brak .NET 10. Bez pythonnet nie wiadomo - wtedy błąd pythonnet, a nie propozycja instalacji .NET."""
    if os.name != "nt" or _clr_ready:
        return False
    rts = net_runtimes()
    return rts is not None and best_runtime(rts) is None


def install_dotnet():
    """.NET 10 przez winget (receiver działa jako administrator - bez okien). -> (ok, opis). Wołać w tle (do kilku min)."""
    import subprocess
    cmd = ["winget", "install", "--id", WINGET_NET, "--exact", "--silent", "--accept-package-agreements",
           "--accept-source-agreements", "--disable-interactivity"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"winget: {e!r}"
    reset()
    if best_runtime() is not None:
        return True, ".NET 10 installed"
    tail = (r.stdout or r.stderr or "").strip().splitlines()[-1:] or [f"exit code {r.returncode}"]
    return False, f"winget: {tail[0]}"


def _load_clr(path):
    """CoreCLR przez pythonnet (raz na proces) i referencja do HIDMaestro.Core.dll z jej folderu (zależności obok).
    Runtime wybierany jawnie (>= .NET 10): clr_loader bez wskazania bierze najnowszy zainstalowany, a starszy niż 10
    wczytywał DLL bez żadnego typu - import kończył się "No module named 'HIDMaestro'"."""
    global _clr_ready
    if not _clr_ready:
        rts = net_runtimes()                    # najpierw .NET: konkretny błąd zamiast ogólnego "brak pythonnet"
        rt = best_runtime(rts)
        if rt is None and rts is not None:
            found = ", ".join(sorted(str(r.version) for r in rts)) or "none"
            raise HMError(f".NET {NET_MAJOR} runtime not installed (found: {found})", need_dotnet=True)
        try:
            from pythonnet import load
        except ImportError:
            raise HMError("pythonnet not installed (pip install pythonnet)") from None
        try:
            load("coreclr", runtime_spec=rt)
        except RuntimeError:
            pass                                # runtime już załadowany (np. drugi backend po zmianie ustawień)
        except Exception as e:
            raise HMError(f".NET runtime: {e}") from e
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


def _type_error(path):
    """DLL wczytana, a typów nie ma: pierwszy brakujący składnik z LoaderExceptions (zależność, wersja runtime)."""
    try:
        from System.Reflection import Assembly
        Assembly.LoadFrom(path).GetTypes()
        return "no HIDMaestro types (unknown cause)"
    except Exception as e:
        inner = getattr(getattr(e, "InnerException", None), "LoaderExceptions", None) or getattr(e, "LoaderExceptions", None)
        first = next((str(x.Message) for x in inner or () if x is not None), None)
        return f"cannot load its types: {first or e}"


class Backend:
    name = "hm"
    order_wait = ORDER_WAIT

    def __init__(self, path):
        if not is_admin():
            raise HMError("administrator rights required (Windows needs them to create a virtual device)")
        clr = _load_clr(path)
        try:
            from HIDMaestro import HMContext, HMController
        except ImportError:
            raise HMError(f"{os.path.basename(path)}: {_type_error(path)}") from None
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


OUT_LEN = 47                                  # część raportu wyjściowego, którą czytamy
OUT_BASE = (0, 1, 2, 3)                       # flagi ważności 0 / 1, silniki - czytane zawsze
OUT_RGB = (44, 45, 46)                        # kolor lightbara - tylko z flagą 1 bit 2
R2_AT, L2_AT = 10, 21                         # efekty spustów (po TRIG_RAW_LEN B) - tylko z flagą 0 bit 2 / bit 3
R2_BYTES, L2_BYTES = range(R2_AT, R2_AT + TRIG_RAW_LEN), range(L2_AT, L2_AT + TRIG_RAW_LEN)


def out_used(f0, f1):
    """Bajty raportu wyjściowego potrzebne parse_output przy tych flagach ważności (reszty nie czytamy)."""
    return ((*OUT_RGB,) if f1 & 0x04 else ()) + (tuple(R2_BYTES) if f0 & 0x04 else ()) + (tuple(L2_BYTES) if f0 & 0x08 else ())


def parse_output(report_id, data):
    """Raport wyjściowy DualSense (bez bajtu ID) -> ((duży, mały) | None, (r, g, b) | None, (L2, R2) | None).
    Flagi ważności decydują, które części gra ustawia: bit 0/1 flagi 0 = silniki, bit 2 / 3 flagi 0 = efekt prawego /
    lewego spustu (surowe 11 B; spust bez flagi = None), bit 2 flagi 1 = kolor lightbara."""
    if report_id != OUT_REPORT or len(data) < OUT_LEN:
        return None, None, None
    f0 = data[0]
    motors = (data[3], data[2]) if f0 & 0x03 else None           # lewy = duży (niski), prawy = mały (wysoki)
    rgb = (data[44], data[45], data[46]) if data[1] & 0x04 else None
    trig = ((bytes(data[L2_AT:L2_AT + TRIG_RAW_LEN]) if f0 & 0x08 else None,
             bytes(data[R2_AT:R2_AT + TRIG_RAW_LEN]) if f0 & 0x04 else None) if f0 & 0x0C else None)
    return motors, rgb, trig


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
            n = len(d)
            # tylko bajty, których używa parse_output: każdy odczyt tablicy .NET przez pythonnet to osobne wywołanie
            # (47 na raport, gry wysyłają je co klatkę - z GIL-em, którego potrzebuje wątek sieci)
            if n < OUT_LEN:
                return
            data = [0] * OUT_LEN
            for k in OUT_BASE:
                data[k] = d[k]
            for k in out_used(data[0], data[1]):        # spusty (22 B) tylko, gdy gra je w tym raporcie ustawia
                data[k] = d[k]
            motors, rgb, trig = parse_output(int(pkt.ReportId), data)
            if motors is not None or rgb is not None or trig is not None:
                self.on_fb(motors, rgb, trig)
        except Exception:
            pass

    def close(self):
        try:
            self.ctrl.OutputReceived -= self._handler
        except Exception:
            pass
        self.ctrl.Dispose()                    # usuwa urządzenie z systemu
