"""Receiver: Windows functions (timer, priority, a single instance, SendInput, UDP sockets)."""
import ctypes
import ctypes.wintypes as wt
import ntpath
import os
import socket
import struct
import subprocess
import sys
from typing import Any

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
_mutex = None


def init_process() -> None:
    """A 1 ms timer, high priority, QuickEdit switched off (selecting text in the console froze the program)."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
        k32.GetCurrentProcess.restype = k32.GetStdHandle.restype = wt.HANDLE
        k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        k32.GetConsoleMode.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        k32.SetConsoleMode.argtypes = [wt.HANDLE, wt.DWORD]
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x80)  # HIGH_PRIORITY_CLASS
        _no_power_throttling()
        h = k32.GetStdHandle(-10)
        m = wt.DWORD()
        if k32.GetConsoleMode(h, ctypes.byref(m)):
            k32.SetConsoleMode(h, (m.value & ~0x40) | 0x80)
    except Exception:  # best effort: ANSI colours in the console are cosmetic
        pass


APP_ID = "DualSenseBridge.Receiver"


def set_app_id() -> None:
    """A dedicated AppUserModelID: Windows treats the window as a separate application, so the taskbar shows the window's icon
    (the pad), not the python.exe / pythonw.exe icon. Call before creating the first window.
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # best effort: without it the taskbar shows the Python icon
        pass


def _no_power_throttling() -> None:
    """Windows 10/11: a process with a hidden window (the tray) may be moved into the efficiency mode (EcoQoS) and lose
    timer resolution - that caused random latency spikes. Both mechanisms are switched off (errors ignored on older
    Windows versions).
    """
    class PPT(ctypes.Structure):
        _fields_ = [("Version", wt.DWORD), ("ControlMask", wt.DWORD), ("StateMask", wt.DWORD)]
    try:
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        st = PPT(1, 0x1 | 0x4, 0)   # we control EXECUTION_SPEED and IGNORE_TIMER_RESOLUTION; StateMask 0 = switched off
        k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st))  # 4 = ProcessPowerThrottling
    except Exception:  # best effort: an older Windows without process power throttling control
        pass


def boost_thread() -> None:
    """Raise the priority of the calling thread (network, DS4 reports, DSU) - less waiting for the processor."""
    try:
        k32.GetCurrentThread.restype = wt.HANDLE
        k32.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(), 2)   # THREAD_PRIORITY_HIGHEST
    except Exception:  # best effort: the priority is only an optimisation
        pass


def end_timer() -> None:
    """Give back the 1 ms timer resolution requested by init_process."""
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:  # best effort: the period ends with the process anyway
        pass


def uptime_s():
    """Seconds since Windows started (GetTickCount64), or None."""
    try:
        k32.GetTickCount64.restype = ctypes.c_uint64
        return k32.GetTickCount64() / 1000
    except (AttributeError, OSError, TypeError):
        return None


class _WTSINFOW(ctypes.Structure):
    _fields_ = [("State", ctypes.c_int), ("SessionId", wt.DWORD), *((n, wt.DWORD) for n in (
                    "IncomingBytes", "OutgoingBytes", "IncomingFrames", "OutgoingFrames", "IncomingCompressedBytes",
                    "OutgoingCompressedBytes")),
                ("WinStationName", wt.WCHAR * 32), ("Domain", wt.WCHAR * 17), ("UserName", wt.WCHAR * 21),
                *((n, ctypes.c_int64) for n in ("ConnectTime", "DisconnectTime", "LastInputTime", "LogonTime", "CurrentTime"))]


WTS_CURRENT_SESSION, WTS_SESSION_INFO = 0xFFFFFFFF, 24


def since_logon_s():
    """Seconds since logon to the current Windows session, or None. The time since Windows started (uptime_s) misleads: with
    Fast Startup "Shut down" does not restart the kernel, and sleep counts too - it gave an hour even though the receiver
    started right after logon.
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


MUTEX_NAME = "Local\\DualSenseBridgeReceiver"
ERROR_ACCESS_DENIED, ERROR_ALREADY_EXISTS = 5, 183
SYNCHRONIZE = 0x00100000


def ensure_single_instance() -> None:
    """A second receiver copy (e.g. autostart + a manual launch) exits with a readable message.
    ERROR_ACCESS_DENIED also means "already running": the lock is held by a receiver started as administrator, and an unprivileged
    process does not get full access to it (CreateMutex then returns NULL instead of ERROR_ALREADY_EXISTS) -
    the second copy used to start and fail only on the busy UDP port with a misleading message.
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


def instance_running():
    """Whether a receiver is already running (the single-instance lock exists) - without creating it. Before elevating:
    a second copy must not show a UAC prompt just to exit right after.
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
        try:
            k32.CloseHandle(_mutex)
        except Exception:  # best effort: the handle is released with the process anyway
            pass
        _mutex = None


def is_admin():
    """Whether the process runs as administrator."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_admin(cmd):
    """Run cmd as administrator (ShellExecute "runas" = a UAC prompt). -> True when it started; a refusal in UAC -> False.
    Environment variables do not pass through UAC (the new process gets the user's environment).
    """
    sh = ctypes.windll.shell32
    sh.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
    sh.ShellExecuteW.restype = ctypes.c_void_p
    # always SW_SHOWNORMAL: Windows applies this value to the new process's first ShowWindow - SW_HIDE (pythonw) made
    # the receiver window not appear after clicking the tray icon; pythonw has no console to show anyway
    r = sh.ShellExecuteW(None, "runas", cmd[0], subprocess.list2cmdline(cmd[1:]), os.getcwd(), 1)
    return (r or 0) > 32


def relaunch(env=None, admin: bool = False) -> None:
    """A new receiver process with the same arguments (as autostart). Call after releasing the ports and the instance lock.
    env: extra environment variables for the new process (e.g. the console mode state). admin: through UAC; a refusal = an ordinary
    restart (the receiver must not vanish).
    """
    cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    if admin:
        if relaunch_admin(cmd):
            print("Restarting as administrator:", subprocess.list2cmdline(cmd))
            return
        print("Administrator restart declined - restarting normally")
    kw: dict[str, Any] = {"cwd": os.getcwd(), "close_fds": True, "env": {**os.environ, **(env or {})}}
    if os.path.basename(sys.executable).lower().startswith("pythonw"):      # autostart: no console, independent of the old one
        kw.update(creationflags=0x00000008 | 0x00000200,                    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen(cmd, **kw)
    print("Restarting:", subprocess.list2cmdline(cmd))


# ---------------- Mouse (SendInput) ----------------
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUT_U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUT_U)]


INPUT_SIZE = ctypes.sizeof(INPUT)


def send_mouse(flags, dx: int = 0, dy: int = 0, data: int = 0) -> None:
    # mouseData is a DWORD: a negative wheel delta must be passed in two's complement
    """Send one mouse event through SendInput: flags (move / button / wheel), a relative move dx, dy and the wheel data."""
    inp = INPUT(0, _INPUT_U(MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)))  # 0 = INPUT_MOUSE
    user32.SendInput(1, ctypes.byref(inp), INPUT_SIZE)


VK_LWIN, VK_MENU, VK_SNAPSHOT, VK_G, VK_ESCAPE = 0x5B, 0x12, 0x2C, 0x47, 0x1B
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2
_EXTENDED = {VK_LWIN, VK_SNAPSHOT}


def send_keys(*vks) -> None:
    """A key shortcut (e.g. Win+G): pressed in order, released in reverse order, in a single SendInput."""
    def key(vk, up):
        flags = (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
        inp = INPUT(1)                                   # 1 = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return inp
    seq = [key(vk, False) for vk in vks] + [key(vk, True) for vk in reversed(vks)]
    user32.SendInput(len(seq), (INPUT * len(seq))(*seq), INPUT_SIZE)


def foreground_window():
    """(handle, lower-case program name) of the active window; (0, None) without a window, (hwnd, None) without access."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
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


def foreground_exe():
    """The program name (e.g. "eldenring.exe", lower case) of the active window, or None."""
    return foreground_window()[1]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


TH32CS_SNAPPROCESS = 0x2
INVALID_HANDLE = ctypes.c_void_p(-1).value


def running_exes():
    """Program names (lower case, e.g. "forzahorizon5.exe") of all running processes - one Toolhelp
    snapshot (without opening the processes). An error -> OSError.
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
        out, ok = set(), k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.add(e.szExeFile.lower())
            ok = k.Process32NextW(snap, ctypes.byref(e))
        return out
    finally:
        k.CloseHandle(snap)


def force_foreground(hwnd):
    """Bring a window to the foreground from a background process. Windows blocks SetForegroundWindow for programs that have
    no active window (the taskbar button flashes instead), so: first attach to the input queue of the active window's thread
    (AttachThreadInput), and when that is not enough - tap Alt (the last keyboard input
    "belongs" to us). -> True when the window is in the foreground.
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


def close_window(hwnd):
    """WM_CLOSE to the window (the same as the X button / Alt+F4, but without the keyboard). -> True when sent."""
    return bool(hwnd) and bool(user32.PostMessageW(hwnd, 0x0010, 0, 0))     # WM_CLOSE


def sleep_pc() -> None:
    """Sleep (S3 / modern standby); hibernation only if Windows is set up that way."""
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def shutdown_pc(delay_s: int = 10) -> None:
    """A delayed shutdown - until then "shutdown /a" in a console aborts it."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    subprocess.run([exe, "/s", "/t", str(int(delay_s))], timeout=15, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def send_click(down_flag, up_flag) -> None:
    """Press and release in one SendInput call (atomic: nothing slips in between DOWN and UP)."""
    arr = (INPUT * 2)(INPUT(0, _INPUT_U(MOUSEINPUT(0, 0, 0, down_flag, 0, 0))),
                      INPUT(0, _INPUT_U(MOUSEINPUT(0, 0, 0, up_flag, 0, 0))))
    user32.SendInput(2, arr, INPUT_SIZE)


# ---------------- UDP sockets ----------------
def no_connreset(sk) -> None:
    """Windows: an ICMP 'port unreachable' must not break recvfrom (WSAECONNRESET)."""
    try:
        sk.ioctl(socket.SIO_UDP_CONNRESET, False)  # type: ignore[attr-defined]  # missing from typeshed
    except Exception:  # best effort: not every Windows / socket type supports it
        pass


def recv_timeout(sk, seconds) -> None:
    """A blocking socket with a receive timeout in the kernel (SO_RCVTIMEO): recvfrom is a single system call.
    Python's settimeout() does an extra select() before every receive (a non-blocking socket + waiting).
    On timeout: TimeoutError (Windows, WSAETIMEDOUT) or BlockingIOError (Linux, EAGAIN).
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


def bind_udp(port, what, rcvbuf: int = 0, host: str = "0.0.0.0"):
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
ADMINS, SYSTEM, USERS = "S-1-5-32-544", "S-1-5-18", "S-1-5-32-545"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
ADMIN_OWNERS = frozenset({ADMINS, SYSTEM, TRUSTED_INSTALLER})


def owner_sid(path):
    """The SID of a file / folder owner ("S-1-5-...") or None (an error, not Windows). The owner can always change
    permissions, so an ordinary user's folder = code that this user (and any of their programs) can replace.
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
        # SE_FILE_OBJECT = 1, OWNER_SECURITY_INFORMATION = 1
        if adv.GetNamedSecurityInfoW(path, 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(sd)):
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
    except Exception:
        return None


def admin_owned(path):
    """True: the owner is Administrators / SYSTEM / TrustedInstaller; False: someone else; None: unknown."""
    sid = owner_sid(path)
    return None if sid is None else sid in ADMIN_OWNERS


def harden_dir(path):
    """Make a folder writable only by administrators: the owner is Administrators, permissions without inheritance from the user
    profile (Administrators and SYSTEM - full, Users - read and execute), files inside inherit
    from the folder. The receiver (administrator) can still save settings and update itself; an ordinary program can no longer replace
    the code that runs with elevated rights. -> (ok, error description).
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
