"""Receiver: Windows functions (timer, priority, a single instance, SendInput, UDP sockets)."""
from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import ntpath
import os
import socket
import struct
import subprocess
import sys
from collections.abc import Sequence
from typing import Any, Final

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
_mutex: int | None = None

# What a failing WinAPI call through ctypes raises: a missing export (older Windows / the Linux stubs), a failed call, a bad argument.
_WINAPI_ERRORS: Final = (AttributeError, OSError, TypeError, ctypes.ArgumentError)

TIMER_PERIOD_MS: Final = 1
HIGH_PRIORITY_CLASS: Final = 0x80
SW_SHOWNORMAL: Final = 1
SHELLEXECUTE_MIN_OK: Final = 32               # ShellExecute returns a value above 32 on success
DETACHED_PROCESS: Final = 0x00000008
CREATE_NEW_PROCESS_GROUP: Final = 0x00000200
CREATE_BREAKAWAY_FROM_JOB: Final = 0x01000000
PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
WM_CLOSE: Final = 0x0010
SE_FILE_OBJECT: Final = 1
OWNER_SECURITY_INFORMATION: Final = 1
STD_INPUT_HANDLE: Final = -10
ENABLE_QUICK_EDIT_MODE: Final = 0x40            # selecting text in the console freezes the program while this is on
ENABLE_EXTENDED_FLAGS: Final = 0x80             # needed for the QuickEdit bit to take effect
THREAD_PRIORITY_HIGHEST: Final = 2
PROCESS_POWER_THROTTLING: Final = 4             # PROCESS_INFORMATION_CLASS value
POWER_THROTTLING_VERSION: Final = 1
THROTTLING_EXECUTION_SPEED: Final = 0x1
THROTTLING_IGNORE_TIMER_RESOLUTION: Final = 0x4


def init_process() -> None:
    """A 1 ms timer, high priority, QuickEdit switched off (selecting text in the console froze the program)."""
    # best effort: every step is an optimisation, none of them may stop the receiver from starting
    with contextlib.suppress(*_WINAPI_ERRORS):
        ctypes.windll.winmm.timeBeginPeriod(TIMER_PERIOD_MS)
        k32.GetCurrentProcess.restype = k32.GetStdHandle.restype = wt.HANDLE
        k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        k32.GetConsoleMode.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        k32.SetConsoleMode.argtypes = [wt.HANDLE, wt.DWORD]
        k32.SetPriorityClass(k32.GetCurrentProcess(), HIGH_PRIORITY_CLASS)
        _no_power_throttling()
        h = k32.GetStdHandle(STD_INPUT_HANDLE)
        m = wt.DWORD()
        if k32.GetConsoleMode(h, ctypes.byref(m)):
            k32.SetConsoleMode(h, (m.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)


APP_ID: Final = "DualSenseBridge.Receiver"


def set_app_id() -> None:
    """A dedicated AppUserModelID.

    Windows treats the window as a separate application, so the taskbar shows the window's icon (the pad), not the
    python.exe / pythonw.exe icon. Call before creating the first window.
    """
    with contextlib.suppress(*_WINAPI_ERRORS):      # best effort: without it the taskbar shows the Python icon
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def _no_power_throttling() -> None:
    """Switch off power throttling of the process (Windows 10/11).

    A process with a hidden window (the tray) may be moved into the efficiency mode (EcoQoS) and lose timer resolution -
    that caused random latency spikes. Both mechanisms are switched off (errors ignored on older Windows versions).
    """
    class PowerThrottlingState(ctypes.Structure):
        _fields_ = [("Version", wt.DWORD), ("ControlMask", wt.DWORD), ("StateMask", wt.DWORD)]
    # best effort: an older Windows has no process power throttling control
    with contextlib.suppress(*_WINAPI_ERRORS):
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        # we control EXECUTION_SPEED and IGNORE_TIMER_RESOLUTION; StateMask 0 = switched off
        st = PowerThrottlingState(POWER_THROTTLING_VERSION, THROTTLING_EXECUTION_SPEED | THROTTLING_IGNORE_TIMER_RESOLUTION, 0)
        k32.SetProcessInformation(k32.GetCurrentProcess(), PROCESS_POWER_THROTTLING, ctypes.byref(st), ctypes.sizeof(st))


def boost_thread() -> None:
    """Raise the priority of the calling thread (network, DS4 reports, DSU) - less waiting for the processor."""
    with contextlib.suppress(*_WINAPI_ERRORS):      # best effort: the priority is only an optimisation
        k32.GetCurrentThread.restype = wt.HANDLE
        k32.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)


def end_timer() -> None:
    """Give back the 1 ms timer resolution requested by init_process."""
    with contextlib.suppress(*_WINAPI_ERRORS):      # best effort: the period ends with the process anyway
        ctypes.windll.winmm.timeEndPeriod(TIMER_PERIOD_MS)


def uptime_s() -> float | None:
    """Return the seconds since Windows started (GetTickCount64), or None."""
    try:
        k32.GetTickCount64.restype = ctypes.c_uint64
        return k32.GetTickCount64() / 1000
    except (AttributeError, OSError, TypeError):
        return None


class _WTSINFOW(ctypes.Structure):
    """WTSINFOW: session information from WTSQuerySessionInformation (WTSSessionInfo)."""

    _fields_ = [("State", ctypes.c_int), ("SessionId", wt.DWORD), *((n, wt.DWORD) for n in (
                    "IncomingBytes", "OutgoingBytes", "IncomingFrames", "OutgoingFrames", "IncomingCompressedBytes",
                    "OutgoingCompressedBytes")),
                ("WinStationName", wt.WCHAR * 32), ("Domain", wt.WCHAR * 17), ("UserName", wt.WCHAR * 21),
                *((n, ctypes.c_int64) for n in ("ConnectTime", "DisconnectTime", "LastInputTime", "LogonTime", "CurrentTime"))]


WTS_CURRENT_SESSION: Final = 0xFFFFFFFF
WTS_SESSION_INFO: Final = 24


def since_logon_s() -> float | None:
    """Return the seconds since logon to the current Windows session, or None.

    The time since Windows started (uptime_s) misleads: with Fast Startup "Shut down" does not restart the kernel, and
    sleep counts too - it gave an hour even though the receiver started right after logon.
    """
    try:
        wts = ctypes.windll.wtsapi32
        buf, size = ctypes.c_void_p(), wt.DWORD()
        if not wts.WTSQuerySessionInformationW(None, WTS_CURRENT_SESSION, WTS_SESSION_INFO, ctypes.byref(buf),
                                               ctypes.byref(size)):
            return None
        try:
            if not buf.value or size.value < ctypes.sizeof(_WTSINFOW):
                return None
            info = _WTSINFOW.from_address(buf.value)
            logon, now = info.LogonTime, info.CurrentTime        # FILETIME, 100 ns units
        finally:
            wts.WTSFreeMemory(buf)
        return (now - logon) / 1e7 if 0 < logon <= now else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


MUTEX_NAME: Final = "Local\\DualSenseBridgeReceiver"
ERROR_ACCESS_DENIED: Final = 5
ERROR_ALREADY_EXISTS: Final = 183
SYNCHRONIZE: Final = 0x00100000


def ensure_single_instance() -> None:
    """A second receiver copy (e.g. autostart + a manual launch) exits with a readable message.

    ERROR_ACCESS_DENIED also means "already running": the lock is held by a receiver started as administrator, and an
    unprivileged process does not get full access to it (CreateMutex then returns NULL instead of ERROR_ALREADY_EXISTS)
    - the second copy used to start and fail only on the busy UDP port with a misleading message.
    """
    global _mutex
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        k.CreateMutexW.restype = wt.HANDLE
        _mutex = k.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if err == ERROR_ALREADY_EXISTS or (not _mutex and err == ERROR_ACCESS_DENIED):
            print("Another receiver instance is already running - exiting.")
            sys.exit(1)
    except (AttributeError, OSError) as e:
        print(f"Single-instance check failed ({e!r}); continuing without lock.")


def instance_running() -> bool:
    """Tell whether a receiver is already running (the single-instance lock exists) without creating it.

    Before elevating: a second copy must not show a UAC prompt just to exit right after.
    """
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
        k.OpenMutexW.restype = wt.HANDLE
        h = k.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
        if h:
            k32.CloseHandle(h)
            return True
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED     # an administrator's lock: it exists, no access
    except (AttributeError, OSError):
        return False


def release_single_instance() -> None:
    """Release the single-instance lock - without this a new process after a restart would see "already running" and exit."""
    global _mutex
    if _mutex:
        with contextlib.suppress(*_WINAPI_ERRORS):      # best effort: the handle is released with the process anyway
            k32.CloseHandle(_mutex)
        _mutex = None


def is_admin() -> bool:
    """Tell whether the process runs as administrator."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except _WINAPI_ERRORS:
        return False


def relaunch_admin(cmd: Sequence[str]) -> bool:
    """Run cmd as administrator (ShellExecute "runas" = a UAC prompt).

    Returns:
        True when it started; False after a refusal in UAC.

    Environment variables do not pass through UAC (the new process gets the user's environment).
    """
    sh = ctypes.windll.shell32
    sh.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
    sh.ShellExecuteW.restype = ctypes.c_void_p
    # always SW_SHOWNORMAL: Windows applies this value to the new process's first ShowWindow - SW_HIDE (pythonw) made
    # the receiver window not appear after clicking the tray icon; pythonw has no console to show anyway
    r = sh.ShellExecuteW(None, "runas", cmd[0], subprocess.list2cmdline(cmd[1:]), os.getcwd(), SW_SHOWNORMAL)
    return (r or 0) > SHELLEXECUTE_MIN_OK


def relaunch(env: dict[str, str] | None = None, admin: bool = False) -> None:
    """Start a new receiver process with the same arguments (as autostart).

    Call after releasing the ports and the instance lock. env: extra environment variables for the new process (e.g. the
    console mode state). admin: through UAC; a refusal = an ordinary restart (the receiver must not vanish).
    """
    cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    if admin:
        if relaunch_admin(cmd):
            print("Restarting as administrator:", subprocess.list2cmdline(cmd))
            return
        print("Administrator restart declined - restarting normally")
    kw: dict[str, Any] = {"cwd": os.getcwd(), "close_fds": True, "env": {**os.environ, **(env or {})}}
    if os.path.basename(sys.executable).lower().startswith("pythonw"):      # autostart: no console, independent of the old one
        kw.update(creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _spawn(cmd, kw)
    print("Restarting:", subprocess.list2cmdline(cmd))


def _spawn(cmd: Sequence[str], kw: dict[str, Any]) -> None:
    """Start the new receiver outside this process's job object, so it survives the exit of the old one.

    A receiver started by Task Scheduler (autostart) runs inside the task's job object. When the old process exits the
    task ends and Windows kills everything left in the job - the freshly started copy included, so after an update the
    receiver just vanished and had to be started by hand. CREATE_BREAKAWAY_FROM_JOB detaches the child from the job. If
    the job does not allow breakaway, CreateProcess fails with "access denied": then start it the old way (inside the
    job) rather than not at all - this is logged, because such a copy may still be killed.
    """
    flags = kw.get("creationflags", 0)
    try:
        subprocess.Popen(cmd, **{**kw, "creationflags": flags | CREATE_BREAKAWAY_FROM_JOB})
    except OSError as e:
        print(f"Restart: breakaway from the job refused ({e!r}) - starting inside it")
        subprocess.Popen(cmd, **kw)


# ---------------- Mouse (SendInput) ----------------
ULONG_PTR = ctypes.c_size_t
INPUT_MOUSE: Final = 0
INPUT_KEYBOARD: Final = 1
DWORD_MASK: Final = 0xFFFFFFFF


class MOUSEINPUT(ctypes.Structure):
    """MOUSEINPUT of SendInput."""

    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    """KEYBDINPUT of SendInput."""

    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _InputUnion(ctypes.Union):
    """The anonymous union inside INPUT (mouse or keyboard)."""

    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    """INPUT of SendInput."""

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _InputUnion)]


INPUT_SIZE = ctypes.sizeof(INPUT)


def send_mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
    """Send one mouse event through SendInput: flags (move / button / wheel), a relative move dx, dy and the wheel data."""
    # mouseData is a DWORD: a negative wheel delta must be passed in two's complement
    inp = INPUT(INPUT_MOUSE, _InputUnion(MOUSEINPUT(dx, dy, data & DWORD_MASK, flags, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), INPUT_SIZE)


VK_LWIN: Final = 0x5B
VK_MENU: Final = 0x12
VK_SNAPSHOT: Final = 0x2C
VK_G: Final = 0x47
VK_ESCAPE: Final = 0x1B
KEYEVENTF_EXTENDEDKEY: Final = 0x1
KEYEVENTF_KEYUP: Final = 0x2
_EXTENDED = {VK_LWIN, VK_SNAPSHOT}


def send_keys(*vks: int) -> None:
    """A key shortcut (e.g. Win+G): pressed in order, released in reverse order, in a single SendInput."""
    def key(vk: int, up: bool) -> INPUT:
        flags = (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
        inp = INPUT(INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return inp
    seq = [key(vk, False) for vk in vks] + [key(vk, True) for vk in reversed(vks)]
    user32.SendInput(len(seq), (INPUT * len(seq))(*seq), INPUT_SIZE)


def foreground_window() -> tuple[int, str | None]:
    """Return (handle, lower-case program name) of the active window: (0, None) without a window, (hwnd, None) without access."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return hwnd, None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return hwnd, None
        return hwnd, ntpath.basename(buf.value).lower() or None
    finally:
        k32.CloseHandle(h)


def foreground_exe() -> str | None:
    """Return the program name (e.g. "eldenring.exe", lower case) of the active window, or None."""
    return foreground_window()[1]


class PROCESSENTRY32W(ctypes.Structure):
    """PROCESSENTRY32W of the Toolhelp process snapshot."""

    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


TH32CS_SNAPPROCESS: Final = 0x2
INVALID_HANDLE: Final = ctypes.c_void_p(-1).value


def running_exes() -> set[str]:
    """Return the program names (lower case, e.g. "forzahorizon5.exe") of all running processes.

    One Toolhelp snapshot (without opening the processes); raises OSError on an error.
    """
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k.CloseHandle.argtypes = [wt.HANDLE]
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        out: set[str] = set()
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.add(e.szExeFile.lower())
            ok = k.Process32NextW(snap, ctypes.byref(e))
        return out
    finally:
        k.CloseHandle(snap)


def force_foreground(hwnd: int) -> bool:
    """Bring a window to the foreground from a background process; True when it is in the foreground.

    Windows blocks SetForegroundWindow for programs that have no active window (the taskbar button flashes instead), so:
    first attach to the input queue of the active window's thread (AttachThreadInput), and when that is not enough - tap
    Alt (the last keyboard input "belongs" to us).
    """
    fg = user32.GetForegroundWindow()
    if not hwnd or fg == hwnd:
        return bool(hwnd)
    me = k32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other and other != me and user32.AttachThreadInput(other, me, True))
    try:
        user32.BringWindowToTop(hwnd)
        done = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(other, me, False)
    if not done:
        send_keys(VK_MENU)
        done = bool(user32.SetForegroundWindow(hwnd))
    return done


def _declare_foreground_api() -> None:
    """Declare argument and return types of the window APIs used above (without them ctypes truncates handles on x64)."""
    try:
        user32.GetForegroundWindow.restype = wt.HWND
        user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.PostMessageW.restype = wt.BOOL
        user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
        user32.AttachThreadInput.restype = wt.BOOL
        user32.SetForegroundWindow.argtypes = [wt.HWND]
        user32.SetForegroundWindow.restype = wt.BOOL
        user32.BringWindowToTop.argtypes = [wt.HWND]
        user32.BringWindowToTop.restype = wt.BOOL
        k32.GetCurrentThreadId.restype = wt.DWORD
    except AttributeError:
        pass


_declare_foreground_api()


def close_window(hwnd: int) -> bool:
    """Post WM_CLOSE to the window (the same as the X button / Alt+F4, but without the keyboard); True when sent."""
    return bool(hwnd) and bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def sleep_pc() -> None:
    """Sleep (S3 / modern standby); hibernation only if Windows is set up that way."""
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def shutdown_pc(delay_s: int = 10) -> None:
    """A delayed shutdown - until then "shutdown /a" in a console aborts it."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    subprocess.run([exe, "/s", "/t", str(int(delay_s))], timeout=15, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def send_click(down_flag: int, up_flag: int) -> None:
    """Press and release in one SendInput call (atomic: nothing slips in between DOWN and UP)."""
    arr = (INPUT * 2)(INPUT(INPUT_MOUSE, _InputUnion(MOUSEINPUT(0, 0, 0, down_flag, 0, 0))),
                      INPUT(INPUT_MOUSE, _InputUnion(MOUSEINPUT(0, 0, 0, up_flag, 0, 0))))
    user32.SendInput(2, arr, INPUT_SIZE)


# ---------------- UDP sockets ----------------
def no_connreset(sk: socket.socket) -> None:
    """Windows: an ICMP 'port unreachable' must not break recvfrom (WSAECONNRESET)."""
    # best effort: not every Windows / socket type supports it
    with contextlib.suppress(OSError, AttributeError, ValueError):
        sk.ioctl(socket.SIO_UDP_CONNRESET, False)  # type: ignore[attr-defined]  # missing from typeshed


def recv_timeout(sk: socket.socket, seconds: float) -> None:
    """A blocking socket with a receive timeout in the kernel (SO_RCVTIMEO): recvfrom is a single system call.

    Python's settimeout() does an extra select() before every receive (a non-blocking socket + waiting). On timeout:
    TimeoutError (Windows, WSAETIMEDOUT) or BlockingIOError (Linux, EAGAIN).
    """
    try:
        sk.settimeout(None)
        if os.name == "nt":
            val = struct.pack("I", max(1, round(seconds * 1000)))              # DWORD [ms]
        else:
            val = struct.pack("ll", int(seconds), round(seconds % 1 * 1e6))    # struct timeval
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, val)
    except (OSError, AttributeError, struct.error):
        sk.settimeout(seconds)


def bind_udp(port: int, what: str, rcvbuf: int = 0, host: str = "0.0.0.0") -> socket.socket:
    """Bind a UDP socket (optionally with a bigger receive buffer); on failure print which port (`what`) is taken and exit."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if rcvbuf:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    try:
        sk.bind((host, port))
    except OSError as e:
        print(f"Cannot bind UDP {port} ({what}): {e}. Is another receiver / DSU server running?")
        sys.exit(1)
    no_connreset(sk)
    return sk


# ---------------- Permissions of the code folder (the receiver runs as administrator) ----------------
ADMINS: Final = "S-1-5-32-544"
SYSTEM: Final = "S-1-5-18"
USERS: Final = "S-1-5-32-545"
TRUSTED_INSTALLER: Final = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
ADMIN_OWNERS: Final = frozenset({ADMINS, SYSTEM, TRUSTED_INSTALLER})


def owner_sid(path: str) -> str | None:
    """Return the SID of a file / folder owner ("S-1-5-...") or None (an error, not Windows).

    The owner can always change permissions, so an ordinary user's folder = code that this user (and any of their
    programs) can replace.
    """
    try:
        adv = ctypes.windll.advapi32
        adv.GetNamedSecurityInfoW.argtypes = [wt.LPCWSTR, ctypes.c_int, wt.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_void_p)]
        adv.GetNamedSecurityInfoW.restype = wt.DWORD
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        owner, sd = ctypes.c_void_p(), ctypes.c_void_p()
        if adv.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION, ctypes.byref(owner), None, None, None,
                                     ctypes.byref(sd)):
            return None
        try:
            text = wt.LPWSTR()
            if not adv.ConvertSidToStringSidW(owner, ctypes.byref(text)):
                return None
            try:
                return text.value
            finally:
                k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        finally:
            k32.LocalFree(sd)
    except _WINAPI_ERRORS:
        return None


def admin_owned(path: str) -> bool | None:
    """Tell who owns the path: True = Administrators / SYSTEM / TrustedInstaller; False = someone else; None = unknown."""
    sid = owner_sid(path)
    return None if sid is None else sid in ADMIN_OWNERS


def harden_dir(path: str) -> tuple[bool, str]:
    """Make a folder writable only by administrators; returns (ok, error description).

    The owner is Administrators, permissions without inheritance from the user profile (Administrators and SYSTEM -
    full, Users - read and execute), files inside inherit from the folder. The receiver (administrator) can still save
    settings and update itself; an ordinary program can no longer replace the code that runs with elevated rights.
    """
    icacls = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
    steps = ([path, "/setowner", f"*{ADMINS}", "/T", "/C", "/Q"],
             [path, "/reset", "/T", "/C", "/Q"],
             [path, "/inheritance:r", "/grant:r", f"*{ADMINS}:(OI)(CI)F", f"*{SYSTEM}:(OI)(CI)F",
              f"*{USERS}:(OI)(CI)RX", "/Q"])
    for args in steps:
        try:
            r = subprocess.run([icacls, *args], capture_output=True, text=True, timeout=300,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as e:
            return False, repr(e)
        if r.returncode:
            return False, (r.stderr or r.stdout or f"icacls exit code {r.returncode}").strip().splitlines()[-1]
    return True, ""
