"""Receiver: the HIDMaestro backend - virtual DualSense, DualShock 4 and Xbox 360 pads (a user-mode UMDF2 driver, MIT).

Compatible with HIDMaestro 1.x, 2.0.x, 2.1.x, 2.2.x, 2.3.x and 2.4.x up to 2.4.1 (no signature used here changed since 2.0.3; 2.1.0
only snapshots HMProfile.ButtonMap / AxisMap / ExtendedReport / Layout, which this module never touches; 2.2.0 only makes a frame
wait for a pending protocol reply on profiles with a protocol host, which the profiles used here do not have; 2.3.0 adds a
backend choice (HMBackend), and its default Auto keeps the plain DualSense on the UMDF2 driver on x64 and the composite on
USB/IP - this module never names a backend; 2.4.0 adds touch and pen devices and loads usbip-win2's filter on HIDMaestro's own
root hub, so Windows hands the composite pad's speaker stream over every 10 ms instead of in 20 ms pairs; 2.4.1 changes nothing
used here). Calls used: LoadDefaultProfiles, InstallDriver, GetProfile, CreateControllerAt, SubmitRawReport, SubmitState,
HMGamepadStateHelpers.StandardAxes, OutputReceived, Dispose - the same in all versions; 2.0 only adds diagnostics:
ProfileLoadIssues, LastDriverInstall, TransportIndex and exceptions derived from InvalidOperationException - see describe_error.
Composite pads also use UsbAudio.Output (FramesReceived, Channels, SampleRateHz, BitsPerSample, ChannelRoles), unchanged in 2.4.1.

HIDMaestro is a .NET library (HIDMaestro.Core.dll, .NET 10) - called from Python through pythonnet (CoreCLR). The driver
lives inside the DLL and installs on first use (InstallDriver, idempotent). Creating a device needs administrator
rights (Windows: SeLoadDriverPrivilege) - without them the backend does not load.

  get(cfg_path)   -> Backend (loaded once per DLL path; an error is remembered until reset())
  find_dll(path)  the path from the settings, or the newest downloaded version (rx_hm_update: hidmaestro/vX.Y.Z/), then
                  hidmaestro/HIDMaestro.Core.dll next to the receiver (unpacked by hand)
  Backend.create(slot, on_fb) -> Pad: build() writes a DualSense report into pad.buf, submit() sends it without copying
  Backend.create(slot, on_fb, composite=True): the "dualsense-composite" profile (the real four-interface USB DualSense) for game
                  haptics: Pad.attach_audio(sink) hands the PCM of the pad's USB audio OUT stream (ch 3/4 = the haptic voice coils)
                  to sink(view, n) on HIDMaestro's audio pump thread, copied with one IL delegate call (rx_haptics does the rest)
  Backend.create_ds4(slot, on_fb) -> DS4Pad: the "dualshock-4-v2" profile (USB 054C:09CC); the DS4 report of rx_report, rumble and
                  lightbar from output report 0x05
  Backend.create_x360(slot, on_fb) -> X360Pad: the "xbox-360-wired" profile (045E:028E with HIDMaestro's XUSB companion, so XInput
                  sees it); the state goes through SubmitState, rumble comes back as XInput vibration packets

Hot path: SubmitRawReport(ReadOnlySpan<byte>) cannot be called from pythonnet directly (Span is a ref struct), so
loading emits a small IL method: (controller, pointer, length) -> SubmitRawReport(new ReadOnlySpan(ptr, len)).
The report sits in a fixed Python buffer - one delegate call per report (~4 us), no allocation or copying on the
Python side. The interop was checked with pythonnet 3.2 and .NET 10 on a mock of the HIDMaestro API (same signatures).

The Xbox 360 profile cannot take a raw report: XInput reads a separate XUSB slice that only SubmitState fills (a raw report leaves
it as the last SubmitState did). Building an HMGamepadState from Python would cost a .NET call per field, so another IL method
reads a fixed native block (six floats, the HMButton bits, the HMHat byte: rx_xbox.build_state) into a stack HMGamepadState with
a reused axis dictionary and calls SubmitState(in state) - again one delegate call per report.

The audio callback gets a ReadOnlyMemory<byte> (a plain struct, not a ref struct, so pythonnet can pass it) - but only the
IL helper can read it without a per-element .NET call: (memory, pointer, capacity) -> copies min(length, capacity) bytes into
the fixed scratch buffer and returns the count. The helpers were run against a real CoreCLR (see tests/test_hm_il.py, which
skips itself without pythonnet and a .NET runtime).
"""
import ctypes
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, Final, TypeAlias

from proto import TRIG_RAW_LEN
import rx_winapi
from rx_config import HERE
import rx_xbox
from rx_report import build_ds5_report, build_report
from rx_state import NEUTRAL_OUT

DLL = "HIDMaestro.Core.dll"
PROFILE = "dualsense"          # the USB DualSense profile (054C:0CE6) from HIDMaestro
PROFILE_COMPOSITE = "dualsense-composite"   # the same pad with its USB audio (speaker + haptics out, microphone); needs usbip-win2
PROFILE_DS4: Final = "dualshock-4-v2"       # the USB DualShock 4 v2 (054C:09CC): the same 64-byte input report 0x01 as ViGEm's DS4
PROFILE_X360: Final = "xbox-360-wired"      # the wired Xbox 360 pad (045E:028E), XInput through HIDMaestro's XUSB companion
AUDIO_SCRATCH: Final = 1 << 16  # the fixed buffer one audio callback is copied into (a window is a few ms: 384 B per ms at 48 kHz x 4 ch)
REPORT_LEN = 63                # input report 0x01 without the ID byte
ORDER_WAIT = 10.0              # creating a PnP device takes from ~0.1 s to several s (higher slots wait for lower ones)
OUT_REPORT = 0x02              # the DualSense output report: motors, trigger effects, lightbar
HID_OUTPUT = 0                 # HMOutputSource.HidOutput
XINPUT_OUTPUT: Final = 2       # HMOutputSource.XInput: XInputSetState as the XUSB vibration packet


NET_MAJOR = 10                 # HIDMaestro.Core.dll targets net10.0 - an older runtime cannot load its types
WINGET_NET = "Microsoft.DotNet.Runtime.10"
RUNTIMES_TTL_S: Final = 30.0   # how long a list of installed .NET runtimes is reused (finding them starts the dotnet host)


class HMError(Exception):
    """HIDMaestro unavailable.

    Missing = no DLL (in auto mode an ordinary situation, no warning); need_dotnet = no .NET 10 (the window offers to
    install it).
    """

    def __init__(self, msg: str, missing: bool = False, need_dotnet: bool = False) -> None:
        """Keep the message and the two flags the window looks at."""
        super().__init__(msg)
        self.missing, self.need_dotnet = missing, need_dotnet


_lock = threading.Lock()
_cache: dict[str, "Backend | HMError"] = {}   # DLL path -> Backend or HMError (no endless retries - reset() on a settings change)
_clr_ready = False
_runtimes: tuple[float, list[Any]] | None = None   # (when, the runtimes) of the last probe - the window asks every ~0.5 s
_loaded: "Backend | None" = None   # the loaded Backend: one HIDMaestro version per process (the CLR does not unload DLLs) - a newer
# one downloaded while running takes effect after a receiver restart


def candidates(cfg_path: str = "") -> list[str]:
    """DLL paths to try, in order.

    The configured path (a folder gets the DLL name appended), else the newest downloaded version, hidmaestro/ and the
    receiver folder.
    """
    if cfg_path:
        p = os.path.expandvars(os.path.expanduser(cfg_path))
        return [os.path.join(p, DLL) if os.path.isdir(p) else p]
    import rx_hm_update
    newest = rx_hm_update.newest_dll()
    return [*([newest] if newest else []), os.path.join(HERE, "hidmaestro", DLL), os.path.join(HERE, DLL)]


def find_dll(cfg_path: str = "") -> str | None:
    """The first candidate that exists, or None."""
    return next((p for p in candidates(cfg_path) if os.path.isfile(p)), None)


def loaded() -> "Backend | None":
    """The backend loaded in this process, or None."""
    return _loaded


def reset() -> None:
    """Settings changed: the next get() tries again (e.g. after pointing to another DLL). A loaded backend stays."""
    global _runtimes
    with _lock:
        for k in [k for k, v in _cache.items() if isinstance(v, HMError)]:
            del _cache[k]
        _runtimes = None


def _load_failed(path: str, err: HMError) -> HMError:
    """A downloaded version failed to load: roll back to the previous one (after a restart) and say so in the error.

    Not a fault of the version: no DLL, no .NET 10, no administrator rights. The CLR cannot take a second copy of the
    assembly into this process, so the older version loads only after a restart (rx_hm_update.restart_tag).
    """
    if err.missing or err.need_dotnet or not rx_winapi.is_admin():
        return err
    import rx_hm_update
    tag = rx_hm_update.mark_bad(path, str(err))
    if tag is None:
        return err
    rx_hm_update.failed_tag = tag
    return HMError(f"{err} - HIDMaestro {tag} marked as not working, the previous version loads after a restart")


def get(cfg_path: str = "") -> "Backend":
    """-> Backend; raises HMError. Loading (CoreCLR + driver) happens once - the first slot thread pays ~0.5-1 s."""
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
                err = _load_failed(path, e)
                _cache[path] = err
                if err is e:
                    raise
                raise err from e
            except Exception as e:            # pythonnet / .NET: any exception becomes a readable backend error
                err = _load_failed(path, HMError(f"{type(e).__name__}: {e}"))
                _cache[path] = err
                raise err from e
            _cache[path] = _loaded = hit
        return hit


# HIDMaestro 2.0+ exceptions (all derive from InvalidOperationException) -> what they usually mean
_HM_HINTS = {
    "HMResourceInUseException": "another program or a second receiver holds this controller's index or identity,"
                                " or all USB/IP ports are taken (2.3.0: 30, shared by every program on the machine)",
    "HMDriverVersionException": "the installed driver does not serve this HIDMaestro version",
    "HMPayloadMissingException": "this HIDMaestro build lacks the driver it needs",
    "HMProfileException": "the profile was refused",
    "HMBackendNotSupportedException": "this profile cannot run on the requested driver (UMDF2 / USB/IP)",
    "HMDriverSigningException": "Windows does not allow installing the UMDF2 driver here (e.g. Windows on ARM64)",
}


def describe_error(e: BaseException) -> str:
    """A HIDMaestro error as one readable line: the .NET type, the message and (for 2.0 exceptions) a hint.

    The fields of HMProfileException / HMDriverVersionException (ProfileId, JsonPath, Reason, Problem) are already in
    the message - only the hint is added.
    """
    name = type(e).__name__
    hint = _HM_HINTS.get(name)
    return f"{name}: {e}" + (f" ({hint})" if hint else "")


def profile_issues(ctx: Any, limit: int = 3) -> list[str]:
    """Profile files refused by HIDMaestro (2.0: HMContext.ProfileLoadIssues; 1.x has none -> []) as ["file: path: reason"]."""
    try:
        issues = list(getattr(ctx, "ProfileLoadIssues", None) or ())
    except Exception:  # noqa: BLE001 - a .NET property that throws just means: no issues to show
        return []
    out: list[str] = []
    for i in issues[:limit]:
        parts = [str(getattr(i, k, "") or "") for k in ("File", "JsonPath", "Reason")]
        out.append(": ".join(p for p in parts if p) or str(i))
    return out + ([f"... +{len(issues) - limit} more"] if len(issues) > limit else [])


def _major(version: object) -> int:
    """Major part of a version string; 0 when it is not a number."""
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return 0


def net_runtimes() -> list[Any] | None:
    """Installed Microsoft.NETCore.App versions (clr_loader looks for them like dotnet: DOTNET_ROOT, Program Files).

    None = unknown (no pythonnet / clr_loader) - not the same as no .NET. The probe runs the dotnet host, so the answer is
    kept for RUNTIMES_TTL_S (and dropped by reset()): the settings window polls it while a download is running.
    """
    global _runtimes
    try:
        from clr_loader import find_runtimes
    except ImportError:
        return None
    now = time.monotonic()
    if _runtimes is not None and now - _runtimes[0] < RUNTIMES_TTL_S:
        return list(_runtimes[1])
    try:
        found = [r for r in find_runtimes() if r.name == "Microsoft.NETCore.App"]
    except Exception:  # noqa: BLE001 - clr_loader probing failed: treat as no runtime found
        found = []
    _runtimes = (now, found)
    return list(found)


def best_runtime(runtimes: list[Any] | None = None) -> Any:
    """The newest runtime >= NET_MAJOR, or None."""
    ok = [r for r in (net_runtimes() if runtimes is None else runtimes) or () if _major(r.version) >= NET_MAJOR]
    return max(ok, key=lambda r: tuple(_major(x) for x in str(r.version).split("."))) if ok else None


def need_dotnet() -> bool:
    """For the window: .NET 10 is missing. Without pythonnet it is unknown - then a pythonnet error, not an offer to install .NET."""
    if os.name != "nt" or _clr_ready:
        return False
    rts = net_runtimes()
    return rts is not None and best_runtime(rts) is None


def install_dotnet() -> tuple[bool, str]:
    """Install .NET 10 through winget (the receiver runs as administrator - no windows). -> (ok, description).

    Call in the background (up to several min).
    """
    import subprocess
    cmd = ["winget", "install", "--id", WINGET_NET, "--exact", "--silent", "--accept-package-agreements",
           "--accept-source-agreements", "--disable-interactivity"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, creationflags=rx_winapi.NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"winget: {e!r}"
    reset()
    if best_runtime() is not None:
        return True, ".NET 10 installed"
    tail = (r.stdout or r.stderr or "").strip().splitlines()[-1:] or [f"exit code {r.returncode}"]
    return False, f"winget: {tail[0]}"


def _load_clr(path: str) -> Any:
    """Load CoreCLR through pythonnet (once per process) and reference HIDMaestro.Core.dll.

    The DLL is added from its own folder (dependencies next to it). The runtime is chosen explicitly (>= .NET 10): without
    a choice clr_loader takes the newest installed, and one older than 10 loads the DLL with no types at all - the import
    then fails with "No module named 'HIDMaestro'".
    """
    global _clr_ready
    if not _clr_ready:
        rts = net_runtimes()                    # check .NET first: a specific error instead of the generic "pythonnet missing"
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
            pass                                # the runtime is already loaded (e.g. a second backend after a settings change)
        except Exception as e:
            raise HMError(f".NET runtime: {e}") from e
        _clr_ready = True
    import clr
    folder = os.path.dirname(path)
    if folder not in sys.path:
        sys.path.append(folder)
    clr.AddReference(path)
    return clr


def submit_delegate(clr: Any, ctrl_type: Any) -> Callable[[Any, Any, int], None]:
    """A delegate (controller, IntPtr, int).

    SubmitRawReport(new ReadOnlySpan<byte>((void*)ptr, len)) built as an IL method. pythonnet does not pass bytes /
    arrays as a ReadOnlySpan, so without this delegate there is no way to send a report.
    """
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
    except Exception as e:      # any reflection / IL error becomes a readable backend error
        raise HMError(f"cannot bind SubmitRawReport: {e!r}") from e


def memcopy_delegate(clr: Any) -> Callable[[Any, Any, int], int]:
    """A delegate (ReadOnlyMemory<byte>, IntPtr, int) -> int built as an IL method.

    Copies min(memory length, capacity) bytes from the memory to the pointer and returns that count. The audio callback of
    HIDMaestro hands over a ReadOnlyMemory<byte>; its Span (a ref struct) cannot be used from pythonnet, so the copy is one
    delegate call instead of a .NET read per sample. Needs no allocation on the Python side.
    """
    try:
        from System import Array, Byte, Int32, IntPtr, Math, Type, Void
        from System.Reflection.Emit import DynamicMethod, OpCodes
        core = clr.GetClrType(Byte).Assembly
        t_byte, t_int, t_ptr = clr.GetClrType(Byte), clr.GetClrType(Int32), clr.GetClrType(IntPtr)
        mem_t = core.GetType("System.ReadOnlyMemory`1").MakeGenericType(t_byte)
        ro_t = core.GetType("System.ReadOnlySpan`1").MakeGenericType(t_byte)
        span_t = core.GetType("System.Span`1").MakeGenericType(t_byte)
        get_span = mem_t.GetProperty("Span").GetGetMethod()
        get_len = ro_t.GetProperty("Length").GetGetMethod()
        slice_ = ro_t.GetMethod("Slice", Array[Type]([t_int, t_int]))
        copy_to = ro_t.GetMethod("CopyTo", Array[Type]([span_t]))
        span_ctor = span_t.GetConstructor(Array[Type]([clr.GetClrType(Void).MakePointerType(), t_int]))
        vmin = clr.GetClrType(Math).GetMethod("Min", Array[Type]([t_int, t_int]))
        func_t = core.GetType("System.Func`4").MakeGenericType(Array[Type]([mem_t, t_ptr, t_int, t_int]))
        if None in (get_span, get_len, slice_, copy_to, span_ctor, vmin):
            raise HMError("ReadOnlyMemory / Span members not found in this .NET runtime")
        dm = DynamicMethod("dsbridge_memcopy", t_int, Array[Type]([mem_t, t_ptr, t_int]), mem_t.Module, True)
        il = dm.GetILGenerator()
        mem, src, n, part = il.DeclareLocal(mem_t), il.DeclareLocal(ro_t), il.DeclareLocal(t_int), il.DeclareLocal(ro_t)
        # the structs are used through their addresses (ldloca): an instance call on a struct needs a managed pointer
        il.Emit(OpCodes.Ldarg_0)
        il.Emit(OpCodes.Stloc, mem)
        il.Emit(OpCodes.Ldloca, mem)
        il.Emit(OpCodes.Call, get_span)                 # src = mem.Span
        il.Emit(OpCodes.Stloc, src)
        il.Emit(OpCodes.Ldloca, src)
        il.Emit(OpCodes.Call, get_len)
        il.Emit(OpCodes.Ldarg_2)
        il.Emit(OpCodes.Call, vmin)                     # n = Math.Min(src.Length, capacity)
        il.Emit(OpCodes.Stloc, n)
        il.Emit(OpCodes.Ldloca, src)
        il.Emit(OpCodes.Ldc_I4_0)
        il.Emit(OpCodes.Ldloc, n)
        il.Emit(OpCodes.Call, slice_)                   # part = src.Slice(0, n): CopyTo throws when the target is shorter
        il.Emit(OpCodes.Stloc, part)
        il.Emit(OpCodes.Ldloca, part)
        il.Emit(OpCodes.Ldarg_1)
        il.Emit(OpCodes.Ldloc, n)
        il.Emit(OpCodes.Newobj, span_ctor)              # new Span<byte>(pointer, n), as in submit_delegate
        il.Emit(OpCodes.Call, copy_to)
        il.Emit(OpCodes.Ldloc, n)
        il.Emit(OpCodes.Ret)
        fn = dm.CreateDelegate(func_t)
        return getattr(fn, "Invoke", fn)
    except HMError:
        raise
    except Exception as e:      # any reflection / IL error becomes a readable backend error
        raise HMError(f"cannot bind the audio copy: {e!r}") from e


def axis_keys(profile: Any) -> tuple[Any, ...]:
    """The HMAxis keys a profile reads its six simple slots from: (left X, left Y, right X, right Y, left trigger, right trigger).

    Asked from HMGamepadStateHelpers.StandardAxes with six distinct values, so the profile's own layout (Vx / Vy triggers on the
    Xbox 360 profiles, Z / Rz right stick on Sony's) decides, not a table here.

    Raises:
        HMError: The answer does not hold six distinct keys.
    """
    from HIDMaestro import HMGamepadStateHelpers
    probe = (0.125, 0.25, 0.375, 0.5, 0.625, 0.75)        # exact in a float32: matched back by value
    axes = HMGamepadStateHelpers.StandardAxes(profile, *probe)
    by_value = {round(float(kv.Value), 3): kv.Key for kv in axes}
    keys = tuple(by_value.get(v) for v in probe)
    if None in keys:
        raise HMError(f"StandardAxes of '{profile.Id}' gave no key for every slot ({sorted(by_value)})")
    return keys


def _emit_fill(clr: Any, t_ctrl: Any, t_ptr: Any, t_dict: Any, keys: Sequence[Any]) -> Any:
    """The fill IL method: (pointer, axes) -> HMGamepadState read from rx_xbox.build_state's block (see state_delegates)."""
    from HIDMaestro import HMGamepadState
    from System import Array, Int32, Type
    from System.Reflection.Emit import DynamicMethod, OpCodes
    t_state = clr.GetClrType(HMGamepadState)
    set_item = t_dict.GetMethod("set_Item")
    f_axes, f_buttons, f_hat = (t_state.GetField(n) for n in ("Axes", "Buttons", "Hat"))
    if None in (set_item, f_axes, f_buttons, f_hat):
        raise HMError("HMGamepadState.Axes / Buttons / Hat not found in this HIDMaestro version")
    fill = DynamicMethod("dsbridge_fill_state", t_state, Array[Type]([t_ptr, t_dict]), t_ctrl.Module, True)
    il = fill.GetILGenerator()
    st = il.DeclareLocal(t_state)
    il.Emit(OpCodes.Ldloca, st)
    il.Emit(OpCodes.Initobj, t_state)                   # a zeroed state: no touch / motion / nullable hat forms
    il.Emit(OpCodes.Ldloca, st)
    il.Emit(OpCodes.Ldarg_1)
    il.Emit(OpCodes.Stfld, f_axes)
    for i, key in enumerate(keys):                      # axes[key] = *(float*)(pointer + 4 i)
        il.Emit(OpCodes.Ldarg_1)
        il.Emit(OpCodes.Ldc_I4, Int32(int(key)))
        il.Emit(OpCodes.Ldarg_0)
        il.Emit(OpCodes.Ldc_I4, Int32(4 * i))
        il.Emit(OpCodes.Add)
        il.Emit(OpCodes.Ldind_R4)
        il.Emit(OpCodes.Callvirt, set_item)
    for field, offset, load in ((f_buttons, rx_xbox.STATE_BUTTONS, OpCodes.Ldind_U4), (f_hat, rx_xbox.STATE_HAT, OpCodes.Ldind_U1)):
        il.Emit(OpCodes.Ldloca, st)
        il.Emit(OpCodes.Ldarg_0)
        il.Emit(OpCodes.Ldc_I4, Int32(offset))
        il.Emit(OpCodes.Add)
        il.Emit(load)
        il.Emit(OpCodes.Stfld, field)
    il.Emit(OpCodes.Ldloc, st)
    il.Emit(OpCodes.Ret)
    return fill


def state_delegates(clr: Any, ctrl_type: Any, keys: Sequence[Any]) -> tuple[Callable[[Any, Any], Any], Callable[[Any, Any, Any], None]]:
    """Two IL methods over the native state block of rx_xbox.build_state: (fill, submit).

    fill(pointer, axes) -> HMGamepadState: axes[keys[i]] = the i-th float, Buttons = the uint32 at STATE_BUTTONS, Hat = the
    byte at STATE_HAT, Axes = the dictionary (reused: SubmitState copies what it keeps). submit(controller, pointer, axes) calls
    fill and then controller.SubmitState(in state) - the state lives on the .NET stack, so nothing is allocated per report.
    fill is separate so tests can read the state without a driver.

    Raises:
        HMError: A member is missing in this HIDMaestro version, or emitting the IL failed.
    """
    try:
        from HIDMaestro import HMAxis, HMGamepadState
        from System import Action, Array, IntPtr, Single, Type, Void
        from System.Collections.Generic import Dictionary
        from System.Reflection.Emit import DynamicMethod, OpCodes
        t_ctrl, t_state, t_ptr = clr.GetClrType(ctrl_type), clr.GetClrType(HMGamepadState), clr.GetClrType(IntPtr)
        t_dict = clr.GetClrType(Dictionary[HMAxis, Single])
        submit = t_ctrl.GetMethod("SubmitState", Array[Type]([t_state.MakeByRefType()]))
        if submit is None:
            raise HMError("SubmitState(in HMGamepadState) not found in this HIDMaestro version")
        fill = _emit_fill(clr, t_ctrl, t_ptr, t_dict, keys)
        send = DynamicMethod("dsbridge_submit_state", clr.GetClrType(Void), Array[Type]([t_ctrl, t_ptr, t_dict]), t_ctrl.Module, True)
        il = send.GetILGenerator()
        st = il.DeclareLocal(t_state)
        il.Emit(OpCodes.Ldarg_1)
        il.Emit(OpCodes.Ldarg_2)
        il.Emit(OpCodes.Call, fill)
        il.Emit(OpCodes.Stloc, st)
        il.Emit(OpCodes.Ldarg_0)
        il.Emit(OpCodes.Ldloca, st)                     # `in` = a managed pointer to the local
        il.Emit(OpCodes.Callvirt, submit)
        il.Emit(OpCodes.Ret)
        func_t = clr.GetClrType(IntPtr).Assembly.GetType("System.Func`3").MakeGenericType(Array[Type]([t_ptr, t_dict, t_state]))
        fill_fn = fill.CreateDelegate(func_t)
        send_fn = send.CreateDelegate(clr.GetClrType(Action[ctrl_type, IntPtr, Dictionary[HMAxis, Single]]))
        return getattr(fill_fn, "Invoke", fill_fn), getattr(send_fn, "Invoke", send_fn)
    except HMError:
        raise
    except Exception as e:      # any reflection / IL error becomes a readable backend error
        raise HMError(f"cannot bind SubmitState: {e!r}") from e


def _type_error(path: str) -> str | None:
    """The DLL loaded but has no types: the first missing component from LoaderExceptions (a dependency, the runtime version)."""
    try:
        from System.Reflection import Assembly
        Assembly.LoadFrom(path).GetTypes()
        return "no HIDMaestro types (unknown cause)"
    except Exception as e:  # noqa: BLE001 - whatever .NET throws is only used to explain why the types are missing
        inner = getattr(getattr(e, "InnerException", None), "LoaderExceptions", None) or getattr(e, "LoaderExceptions", None)
        first = next((str(x.Message) for x in inner or () if x is not None), None)
        return f"cannot load its types: {first or e}"


def touch_delegate(clr: Any, ctrl_type: Any, contact_type: Any, buttons_type: Any) -> Callable[[Any, Any, int, Any], None]:
    """A delegate (controller, HMTouchContact[], count, buttons) built as an IL method.

    SubmitTouch(new ReadOnlySpan<HMTouchContact>(array, 0, count), buttons): the span is a ref struct pythonnet cannot pass.
    """
    from System import Action, Array, Int32, Type, Void
    from System.Reflection.Emit import DynamicMethod, OpCodes
    t_ctrl, t_item, t_buttons = clr.GetClrType(ctrl_type), clr.GetClrType(contact_type), clr.GetClrType(buttons_type)
    t_int, t_array = clr.GetClrType(Int32), t_item.MakeArrayType()
    span = clr.GetClrType(Int32).Assembly.GetType("System.ReadOnlySpan`1").MakeGenericType(t_item)
    ctor = span.GetConstructor(Array[Type]([t_array, t_int, t_int]))
    meth = t_ctrl.GetMethod("SubmitTouch", Array[Type]([span, t_buttons]))
    if ctor is None or meth is None:
        raise HMError("SubmitTouch not found in this HIDMaestro version")
    dm = DynamicMethod("dsbridge_submit_touch", clr.GetClrType(Void), Array[Type]([t_ctrl, t_array, t_int, t_buttons]),
                       t_ctrl.Module, True)
    il = dm.GetILGenerator()
    il.Emit(OpCodes.Ldarg_0)
    il.Emit(OpCodes.Ldarg_1)
    il.Emit(OpCodes.Ldc_I4_0)
    il.Emit(OpCodes.Ldarg_2)
    il.Emit(OpCodes.Newobj, ctor)
    il.Emit(OpCodes.Ldarg_3)
    il.Emit(OpCodes.Callvirt, meth)
    il.Emit(OpCodes.Ret)
    fn = dm.CreateDelegate(clr.GetClrType(Action[ctrl_type, Array[contact_type], Int32, buttons_type]))
    return getattr(fn, "Invoke", fn)


def create_touchpad(backend: "Backend", index: int, vid: int, pid: int, contacts: int, width_mm: int, height_mm: int,
                    key: str) -> tuple[Any, Callable[[Any, Sequence[tuple[int, int, int]], bool], None]]:
    """A virtual Windows precision touchpad (HIDMaestro 2.4.0+). -> (controller, send(controller, [(id, x, y)], clicked)).

    Raises:
        HMError: This HIDMaestro has no touch support, or binding SubmitTouch failed.
    """
    try:
        from HIDMaestro import HidDescriptorBuilder, HMController, HMMouseButtons, HMProfileBuilder, HMTouchContact
        from System import Array, UInt16
    except ImportError as e:
        raise HMError(f"HIDMaestro 2.4.0 or newer is needed for a touchpad ({e})") from None
    submit = touch_delegate(backend.clr, HMController, HMTouchContact, HMMouseButtons)
    descriptor = HidDescriptorBuilder().Touchpad(contacts, width_mm, height_mm, 1)
    name = "DualSense bridge touchpad"
    builder = HMProfileBuilder().Id("dsbridge-touchpad").Name(name).Vendor("dualsense-bridge")
    builder = builder.Vid(UInt16(vid)).Pid(UInt16(pid)).ProductString(name).ManufacturerString("dualsense-bridge")
    profile = builder.Type("touchpad").Connection("usb").FromDescriptorBuilder(descriptor).Build()
    ctrl = backend.ctx.CreateControllerAt(index, profile, key)
    array = Array[HMTouchContact]([HMTouchContact() for _unused in range(contacts)])
    none, click = HMMouseButtons(0), HMMouseButtons.Left

    def send(c: Any, fingers: Sequence[tuple[int, int, int]], clicked: bool) -> None:
        for n, (fid, x, y) in enumerate(fingers):
            item = HMTouchContact()                 # a struct: filled, then stored (array[n].X = ... would change a copy)
            item.Id, item.X, item.Y = fid, x, y
            array[n] = item
        submit(c, array, len(fingers), click if clicked else none)

    return ctrl, send


class Backend:
    """The loaded HIDMaestro library: creates virtual DualSense (create), DualShock 4 (create_ds4) and Xbox 360 (create_x360) pads."""

    name = "hm"

    def __init__(self, path: str) -> None:
        """Load the CLR and the DLL, install the driver.

        Find the DualSense profile and bind the IL submit delegate; any failure is an HMError.
        """
        if not rx_winapi.is_admin():
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
        try:
            self.ctx.InstallDriver()           # idempotent: ~50 ms when current (2.0: a full install once after a version change)
        except Exception as e:
            raise HMError(f"driver install: {describe_error(e)}") from e
        last = getattr(self.ctx, "LastDriverInstall", None)          # 2.0: what the install did (AlreadyCurrent / Upgraded ...)
        if last is not None:
            print(f"HIDMaestro driver: {last}")
        self.profile = self.ctx.GetProfile(PROFILE)
        if self.profile is None:
            # 2.0 loads profiles strictly - the reason for refusal is in ProfileLoadIssues
            why = "; ".join(profile_issues(self.ctx))
            raise HMError(f"HIDMaestro has no '{PROFILE}' profile" + (f" (refused: {why})" if why else ""))
        self.submit_fn = submit_delegate(clr, HMController)
        self.clr = clr
        self._composite: Any = None            # the "dualsense-composite" profile, looked up on first use
        self._copy_fn: Callable[[Any, Any, int], int] | None = None
        self._profiles: dict[str, Any] = {}    # the DS4 / Xbox 360 profiles, looked up on first use
        self._state_fn: Callable[[Any, Any, Any], None] | None = None
        self.path = path
        print(f"HIDMaestro loaded: {path}")

    def composite_profile(self) -> Any:
        """The composite DualSense profile (USB audio: speaker and haptics out); HMError when this HIDMaestro has none."""
        if self._composite is None:
            profile = self.ctx.GetProfile(PROFILE_COMPOSITE)
            if profile is None:
                why = "; ".join(profile_issues(self.ctx))
                raise HMError(f"HIDMaestro has no '{PROFILE_COMPOSITE}' profile (update HIDMaestro)"
                              + (f" (refused: {why})" if why else ""))
            self._composite = profile
        return self._composite

    def mem_copy(self) -> Callable[[Any, Any, int], int]:
        """The IL delegate that copies an audio window (ReadOnlyMemory<byte>) into a native buffer - built once."""
        if self._copy_fn is None:
            self._copy_fn = memcopy_delegate(self.clr)
        return self._copy_fn

    def profile_named(self, name: str) -> Any:
        """A profile of this HIDMaestro by id, looked up once; HMError when it has none (or refused to load it)."""
        profile = self._profiles.get(name)
        if profile is None:
            profile = self.ctx.GetProfile(name)
            if profile is None:
                why = "; ".join(profile_issues(self.ctx))
                raise HMError(f"HIDMaestro has no '{name}' profile (update HIDMaestro)" + (f" (refused: {why})" if why else ""))
            self._profiles[name] = profile
        return profile

    def state_submit(self) -> Callable[[Any, Any, Any], None]:
        """The SubmitState IL delegate for the Xbox 360 profile (its axis keys baked in) - built once."""
        if self._state_fn is None:
            from HIDMaestro import HMController
            keys = axis_keys(self.profile_named(PROFILE_X360))
            self._state_fn = state_delegates(self.clr, HMController, keys)[1]
        return self._state_fn

    def _controller(self, slot: int, profile: Any, key: str) -> Any:
        """CreateControllerAt on a fixed index with a fixed identity (an old SDK without the identity key: the index alone)."""
        try:
            ctrl = self.ctx.CreateControllerAt(slot, profile, key)
        except TypeError:
            ctrl = self.ctx.CreateControllerAt(slot, profile)
        # 2.0: when another program holds the index, the controller gets a different one
        ti = getattr(ctrl, "TransportIndex", None)
        if ti is not None and int(ti) != slot:
            print(f"Controller {slot}: HIDMaestro transport index {int(ti)} (slot {slot} is used by another program)")
        return ctrl

    def create(self, slot: int, on_fb: Callable[..., None], composite: bool = False) -> "Pad":
        """A virtual DualSense on a fixed index and with a fixed slot identity.

        The same device paths after every recreation (Steam / games remember the pad's settings).

        Args:
            slot: The slot (the HIDMaestro index).
            on_fb: Callback for what the game sends to the pad (motors, lightbar, triggers).
            composite: Use the four-interface USB profile (game haptics). The first one installs the usbip-win2 host
                controller (a kernel driver) and adds a Windows audio endpoint; any failure is raised to the caller.
        """
        profile = self.composite_profile() if composite else self.profile
        ctrl = self._controller(slot, profile, f"dualsense-bridge:{slot}")
        try:
            return Pad(self, ctrl, on_fb)
        except Exception:
            ctrl.Dispose()
            raise

    def create_ds4(self, slot: int, on_fb: Callable[..., None]) -> "DS4Pad":
        """A virtual DualShock 4 (USB) on a fixed index; its identity key names the kind, so its device paths are its own."""
        ctrl = self._controller(slot, self.profile_named(PROFILE_DS4), f"dualsense-bridge:ds4:{slot}")
        try:
            return DS4Pad(self, ctrl, on_fb)
        except Exception:
            ctrl.Dispose()
            raise

    def create_x360(self, slot: int, on_fb: Callable[..., None]) -> "X360Pad":
        """A virtual wired Xbox 360 pad on a fixed index (XInput through the XUSB companion), with its own identity key."""
        submit = self.state_submit()                   # before the device: a missing member fails without a device to remove
        ctrl = self._controller(slot, self.profile_named(PROFILE_X360), f"dualsense-bridge:x360:{slot}")
        try:
            return X360Pad(self, ctrl, on_fb, submit)
        except Exception:
            ctrl.Dispose()
            raise


OUT_LEN = 47                                  # the part of the output report we read
OUT_BASE = (0, 1, 2, 3)                       # validity flags 0 / 1, motors - always read
OUT_RGB = (44, 45, 46)                        # lightbar colour - only with flag 1 bit 2
R2_AT, L2_AT = 10, 21                         # trigger effects (TRIG_RAW_LEN B each) - only with flag 0 bit 2 / bit 3
R2_BYTES, L2_BYTES = range(R2_AT, R2_AT + TRIG_RAW_LEN), range(L2_AT, L2_AT + TRIG_RAW_LEN)
MUTE_LED_AT, PLAYER_LEDS_AT = 8, 43           # mute_button_led (only with flag 1 bit 0), player_leds (only with flag 1 bit 4)
FLAG1_MUTE_LED, FLAG1_PLAYER_LEDS = 0x01, 0x10


_F0_USED: Final = 0x04 | 0x08                                    # the flag bits out_used depends on (the triggers ...)
_F1_USED: Final = 0x04 | FLAG1_MUTE_LED | FLAG1_PLAYER_LEDS       # (... the lightbar, the mute LED and the player LEDs)


def _out_used(f0: int, f1: int) -> tuple[int, ...]:
    """Build the byte list of out_used (once per flag combination, at import)."""
    return (((*OUT_RGB,) if f1 & 0x04 else ()) + (tuple(R2_BYTES) if f0 & 0x04 else ()) + (tuple(L2_BYTES) if f0 & 0x08 else ())
            + ((MUTE_LED_AT,) if f1 & FLAG1_MUTE_LED else ()) + ((PLAYER_LEDS_AT,) if f1 & FLAG1_PLAYER_LEDS else ()))


# every combination of the flag bits that matter (32): the output thread looks its list up instead of building tuples per report
_OUT_USED: Final = {(f0, f1): _out_used(f0, f1) for f0 in range(_F0_USED + 1) if not f0 & ~_F0_USED
                    for f1 in range(_F1_USED + 1) if not f1 & ~_F1_USED}


def out_used(f0: int, f1: int) -> tuple[int, ...]:
    """The output report bytes parse_output and parse_leds need for these validity flags (the rest is not read)."""
    return _OUT_USED[f0 & _F0_USED, f1 & _F1_USED]


ParsedOutput: TypeAlias = tuple[
    tuple[int, int] | None,                      # (large, small) motors
    tuple[int, int, int] | None,                 # lightbar colour
    tuple[bytes | None, bytes | None] | None,    # raw trigger effects (L2, R2)
]


def parse_output(report_id: int, data: Sequence[int]) -> ParsedOutput:
    """A DualSense output report (without the ID byte) -> ((large, small) | None, (r, g, b) | None, (L2, R2) | None).

    The validity flags decide which parts the game sets: bit 0/1 of flag 0 = motors, bit 2 / 3 of flag 0 = the right /
    left trigger effect (raw 11 B; a trigger without its flag = None), bit 2 of flag 1 = the lightbar colour.
    """
    if report_id != OUT_REPORT or len(data) < OUT_LEN:
        return None, None, None
    f0 = data[0]
    motors = (data[3], data[2]) if f0 & 0x03 else None           # left = large (low), right = small (high)
    rgb = (data[44], data[45], data[46]) if data[1] & 0x04 else None
    trig = ((bytes(data[L2_AT:L2_AT + TRIG_RAW_LEN]) if f0 & 0x08 else None,
             bytes(data[R2_AT:R2_AT + TRIG_RAW_LEN]) if f0 & 0x04 else None) if f0 & 0x0C else None)
    return motors, rgb, trig


def parse_leds(report_id: int, data: Sequence[int]) -> tuple[int | None, int | None] | None:
    """A DualSense output report (without the ID byte) -> ``(mute LED, player LEDs)`` the game set, or None when it sets neither.

    Bit 0 of flag 1 = the mute button LED (byte 8: 0 off, 1 on, 2 pulsing), bit 4 of flag 1 = the player LEDs (byte 43, 5 bits);
    a value without its flag is None (unchanged).
    """
    if report_id != OUT_REPORT or len(data) < OUT_LEN:
        return None
    f1 = data[1]
    mute = data[MUTE_LED_AT] if f1 & FLAG1_MUTE_LED else None
    player = data[PLAYER_LEDS_AT] if f1 & FLAG1_PLAYER_LEDS else None
    return (mute, player) if mute is not None or player is not None else None


DS4_OUT_REPORT: Final = 0x05                  # the DS4 USB output report: flags, motors, lightbar, flash
DS4_OUT_LEN: Final = 8                        # the bytes read (without the ID): flags 0 ... blue
DS4_F_MOTORS, DS4_F_LIGHTBAR = 0x01, 0x02     # validity flag 0: the motors (bytes 3 / 4), the lightbar colour (5..7)
XINPUT_LEN: Final = 5                         # the XUSB vibration packet: cmd, size, low-frequency (left) motor, high (right), 0
XINPUT_RAW_LEN: Final = 4                     # a bare XINPUT_VIBRATION: two little-endian 16-bit motor speeds


def parse_ds4_output(report_id: int, data: Sequence[int]) -> tuple[tuple[int, int] | None, tuple[int, int, int] | None]:
    """A DS4 output report (without the ID byte) -> ((large, small) | None, (r, g, b) | None).

    Byte 0 = the validity flags (bit 0 motors, bit 1 lightbar), byte 3 = the right (small, high-frequency) motor, byte 4 = the
    left (large) one, bytes 5..7 = the colour. Only the bytes the flags ask for are read (each is a .NET call on the real array).
    """
    if report_id != DS4_OUT_REPORT or len(data) < DS4_OUT_LEN:
        return None, None
    f = data[0]
    motors = (data[4], data[3]) if f & DS4_F_MOTORS else None
    rgb = (data[5], data[6], data[7]) if f & DS4_F_LIGHTBAR else None
    return motors, rgb


def parse_xinput(data: Sequence[int]) -> tuple[int, int] | None:
    """An XInput vibration packet -> (large, small), or None when it is too short.

    The XUSB wire packet (5 bytes: cmd, size, left motor, right motor, 0) carries the motor bytes at 2 / 3; a bare
    XINPUT_VIBRATION (4 bytes: two little-endian 16-bit speeds) carries their high bytes at 1 / 3.
    """
    n = len(data)
    if n >= XINPUT_LEN:
        return data[2], data[3]
    if n == XINPUT_RAW_LEN:
        return data[1], data[3]
    return None


class RawPad:
    """A virtual pad fed with a raw input report. buf has a fixed address (the ctypes export blocks resizing).

    The report is built in place and sent through the IL delegate without a copy on the Python side; subclasses read what the
    game sends back (_on_output).
    """

    kind = "hm"
    build = staticmethod(build_ds5_report)

    def __init__(self, backend: Backend, ctrl: Any, on_fb: Callable[..., None]) -> None:
        """Pin the report buffer, take its address for the delegate.

        Subscribe to the output reports and send one neutral report to test the path.
        """
        self.ctrl, self.on_fb = ctrl, on_fb
        self._backend = backend
        # an all-zero buffer is not neutral: sticks 0/0 and the D-pad nibble 0 (= up). A game that polls the new pad
        # before the first real report would see that as input, so the first submit() sends centred sticks, no buttons.
        self.buf = self.build(NEUTRAL_OUT, 0, False)
        self._view = (ctypes.c_char * len(self.buf)).from_buffer(self.buf)
        self._ptr = backend.IntPtr(ctypes.addressof(self._view))
        self._bind(backend)
        self._handler = self._on_output        # a reference (the .NET delegate must not vanish through the GC)
        ctrl.OutputReceived += self._handler
        self.submit()                          # try at once: a wrong submit path fails here, not in a game

    def _bind(self, backend: Backend) -> None:
        """Take the delegate submit() calls."""
        self._fn = backend.submit_fn

    def submit(self) -> None:
        """Send the current contents of buf to the virtual device."""
        self._fn(self.ctrl, self._ptr, REPORT_LEN)

    def _on_output(self, _ctrl: Any, pkt: Any) -> None:
        """The HIDMaestro output thread (overridden per kind)."""

    def close(self) -> None:
        """Unsubscribe from the output reports and dispose of the controller."""
        try:
            self.ctrl.OutputReceived -= self._handler
        except Exception:  # noqa: BLE001, S110 - best effort: the controller may already be disposed
            pass
        self.ctrl.Dispose()                    # removes the device from the system


class DS4Pad(RawPad):
    """One virtual DualShock 4: the 63-byte DS4 report of rx_report (the profile's input report 0x01 without its ID)."""

    kind = "hm_ds4"
    build = staticmethod(build_report)

    def _on_output(self, _ctrl: Any, pkt: Any) -> None:
        """The HIDMaestro output thread: rumble and lightbar colour from report 0x05 (no trigger effects, no player LEDs)."""
        try:
            if int(pkt.Source) != HID_OUTPUT:
                return
            motors, rgb = parse_ds4_output(int(pkt.ReportId), pkt.Data.ToArray())
            if motors is not None or rgb is not None:
                self.on_fb(motors, rgb)
        except Exception:  # noqa: BLE001, S110 - the .NET event thread: an unparsable report must not raise into HIDMaestro
            pass


class X360Pad(RawPad):
    """One virtual wired Xbox 360 pad: buf is the native state block of rx_xbox.build_state, submit() is the SubmitState delegate."""

    kind = "hm_x360"
    build = staticmethod(rx_xbox.build_state)

    def __init__(self, backend: Backend, ctrl: Any, on_fb: Callable[..., None], submit: Callable[[Any, Any, Any], None]) -> None:
        """Keep the SubmitState delegate and a reused axis dictionary (SubmitState copies the values it keeps)."""
        from HIDMaestro import HMAxis
        from System import Single
        from System.Collections.Generic import Dictionary
        self._state_fn = submit
        self._axes = Dictionary[HMAxis, Single]()
        super().__init__(backend, ctrl, on_fb)

    def _bind(self, backend: Backend) -> None:
        """submit() goes through the SubmitState delegate given to the constructor."""

    def submit(self) -> None:
        """Send the state block (one IL call: fill an HMGamepadState on the .NET stack, SubmitState)."""
        self._state_fn(self.ctrl, self._ptr, self._axes)

    def _on_output(self, _ctrl: Any, pkt: Any) -> None:
        """The HIDMaestro output thread: XInputSetState arrives as an XInput vibration packet (the pad has no HID output)."""
        try:
            if int(pkt.Source) != XINPUT_OUTPUT:
                return
            motors = parse_xinput(pkt.Data.ToArray())
            if motors is not None:
                self.on_fb(motors, None)
        except Exception:  # noqa: BLE001, S110 - the .NET event thread: an unparsable packet must not raise into HIDMaestro
            pass


class Pad(RawPad):
    """One virtual DualSense: rumble, lightbar, trigger effects and LEDs from report 0x02; the composite one also has USB audio."""

    kind = "hm"
    build = staticmethod(build_ds5_report)

    def __init__(self, backend: Backend, ctrl: Any, on_fb: Callable[..., None]) -> None:
        """Note whether the controller has USB audio, then set up the report path (RawPad)."""
        self._audio_handler: Callable[[Any, Any], None] | None = None
        self._ctl_handler: Callable[[Any, Any], None] | None = None
        self.composite = getattr(ctrl, "UsbAudio", None) is not None
        super().__init__(backend, ctrl, on_fb)

    def _on_output(self, _ctrl: Any, pkt: Any) -> None:
        """The HIDMaestro output thread: quick reads only (the SDK overwrites the oldest packets while the handler is busy)."""
        try:
            if int(pkt.Source) != HID_OUTPUT:
                return
            d = pkt.Data.ToArray()
            n = len(d)
            # only the bytes parse_output uses: every .NET array read through pythonnet is a separate call
            # (47 per report, games send them every frame - holding the GIL the network thread needs)
            if n < OUT_LEN:
                return
            data = [0] * OUT_LEN
            for k in OUT_BASE:
                data[k] = d[k]
            for k in out_used(data[0], data[1]):        # triggers (22 B) only when the game sets them in this report
                data[k] = d[k]
            report_id = int(pkt.ReportId)
            motors, rgb, trig = parse_output(report_id, data)
            leds = parse_leds(report_id, data)
            if leds is not None:
                self.on_fb(motors, rgb, trig, leds)
            elif motors is not None or rgb is not None or trig is not None:
                self.on_fb(motors, rgb, trig)
        except Exception:  # noqa: BLE001, S110 - the .NET event thread: an unparsable report must not raise into HIDMaestro
            pass

    def attach_audio(self, sink: Callable[[memoryview, int], None]) -> tuple[int, int, int, tuple[str, ...]]:
        """Deliver the pad's USB audio OUT stream to sink(view, n): n bytes of interleaved 16-bit PCM at the start of view.

        Composite pads only. The callback runs on HIDMaestro's audio pump thread, which paces the stream: it copies the
        window with one IL delegate call into a fixed scratch buffer and calls sink - nothing else (no allocation). The view
        is only valid during the call.

        Returns:
            The format of the stream: (channels, sample rate [Hz], bits per sample, channel roles).

        Raises:
            HMError: This pad has no USB audio, or the copy helper cannot be built.
        """
        audio = getattr(self.ctrl, "UsbAudio", None)
        if audio is None:
            raise HMError("this virtual pad has no USB audio (not a composite profile)")
        self._detach_frames()
        out = audio.Output
        fmt = (int(out.Channels), int(out.SampleRateHz), int(out.BitsPerSample), tuple(str(r) for r in out.ChannelRoles))
        copy = self._backend.mem_copy()
        scratch = bytearray(AUDIO_SCRATCH)
        cview = (ctypes.c_char * AUDIO_SCRATCH).from_buffer(scratch)
        ptr = self._backend.IntPtr(ctypes.addressof(cview))
        view = memoryview(scratch)

        def on_frames(_out: Any, mem: Any) -> None:
            try:
                n = copy(mem, ptr, AUDIO_SCRATCH)
                if n > 0:
                    sink(view, n)
            except Exception:  # noqa: BLE001, S110 - the HIDMaestro audio thread: a failing sink must not stall the stream
                pass

        self._audio_keep = (scratch, cview, ptr, view)       # the buffer must outlive the callbacks (the IL copy writes to its address)
        self._audio_handler = on_frames                      # a reference: the .NET delegate must not vanish through the GC
        out.FramesReceived += on_frames
        return fmt

    def attach_audio_controls(self, sink: Callable[[str, bool, bool, float], None]) -> bool:
        """Deliver the volume and mute the host sets on the pad's audio endpoint: sink(function, is_mute, muted, volume_db).

        Windows does not scale the PCM of an endpoint with a hardware volume control: the mixer slider, the tray icon and the mute
        button become UAC feature unit writes (HMUsbAudio.ControlChanged), and the stream keeps arriving at full level. `function`
        is "speaker" or "microphone" (the unit), is_mute tells which control was written (muted is valid for a mute write, volume_db
        for a volume write: the UAC1 value / 256, 0 dB = full). The event runs on HIDMaestro's audio event thread, not on the audio
        pump, so reading four properties here is fine. Call it right after the pad is created: a write that comes before the
        subscription is not replayed.

        Returns:
            True when subscribed, False when this HIDMaestro has no ControlChanged (an old version) or the pad has no USB audio.
        """
        audio = getattr(self.ctrl, "UsbAudio", None)
        if audio is None:
            return False
        self._detach_controls()

        def on_control(_audio: Any, args: Any) -> None:
            try:
                sink(str(args.Function), bool(args.IsMute), bool(args.MuteValue), float(args.VolumeDb))
            except Exception:  # noqa: BLE001, S110 - the HIDMaestro event thread: a failing sink must not stall the audio events
                pass

        try:
            audio.ControlChanged += on_control
        except Exception:  # noqa: BLE001 - no such event (HIDMaestro older than the audio controls): the speaker stays at full level
            return False
        self._ctl_handler = on_control                       # a reference: the .NET delegate must not vanish through the GC
        return True

    def _detach_controls(self) -> None:
        """Unsubscribe from the volume / mute writes (no-op when not subscribed)."""
        handler, self._ctl_handler = self._ctl_handler, None
        if handler is None:
            return
        try:
            self.ctrl.UsbAudio.ControlChanged -= handler
        except Exception:  # noqa: BLE001, S110 - best effort: the controller may already be disposed
            pass

    def detach_audio(self) -> None:
        """Stop the audio callbacks (no-op when none is attached)."""
        self._detach_controls()
        self._detach_frames()

    def _detach_frames(self) -> None:
        """Unsubscribe from the PCM stream only (the volume / mute subscription is made earlier and stays)."""
        handler, self._audio_handler = self._audio_handler, None
        if handler is None:
            return
        try:
            self.ctrl.UsbAudio.Output.FramesReceived -= handler
        except Exception:  # noqa: BLE001, S110 - best effort: the controller may already be disposed
            pass

    def close(self) -> None:
        """Unsubscribe from the audio, the output reports, and dispose of the controller."""
        self.detach_audio()
        super().close()
