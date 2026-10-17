"""Receiver: funkcje Windows (timer, priorytet, jedna instancja, SendInput, gniazda UDP)."""
import ctypes
import ctypes.wintypes as wt
import ntpath
import os
import socket
import subprocess
import sys

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
_mutex = None


def init_process():
    """Timer 1 ms, wysoki priorytet, wyłączony QuickEdit (zaznaczenie w konsoli zamrażało program)."""
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
    except Exception:
        pass


APP_ID = "DualSenseBridge.Receiver"


def set_app_id():
    """Własny AppUserModelID: Windows traktuje okno jako osobną aplikację, więc pasek zadań pokazuje ikonę okna
    (pad), a nie ikonę python.exe / pythonw.exe. Wołać przed utworzeniem pierwszego okna."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _no_power_throttling():
    """Windows 10/11: proces z ukrytym oknem (zasobnik) bywa przenoszony w tryb efektywności (EcoQoS) i traci
    rozdzielczość timera - to dawało losowe skoki opóźnień. Wyłączamy oba mechanizmy (ignoruj błędy na starszych
    Windowsach)."""
    class PPT(ctypes.Structure):
        _fields_ = [("Version", wt.DWORD), ("ControlMask", wt.DWORD), ("StateMask", wt.DWORD)]
    try:
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        st = PPT(1, 0x1 | 0x4, 0)   # sterujemy EXECUTION_SPEED i IGNORE_TIMER_RESOLUTION; StateMask 0 = wyłączone
        k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st))  # 4 = ProcessPowerThrottling
    except Exception:
        pass


def boost_thread():
    """Podnosi priorytet wątku wołającego (sieć, raporty DS4, DSU) - mniej czekania na procesor."""
    try:
        k32.GetCurrentThread.restype = wt.HANDLE
        k32.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(), 2)   # THREAD_PRIORITY_HIGHEST
    except Exception:
        pass


def end_timer():
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


def uptime_s():
    """Sekundy od startu Windows (GetTickCount64) albo None."""
    try:
        k32.GetTickCount64.restype = ctypes.c_uint64
        return k32.GetTickCount64() / 1000
    except (AttributeError, OSError, TypeError):
        return None


MUTEX_NAME = "Local\\DualSenseBridgeReceiver"
ERROR_ACCESS_DENIED, ERROR_ALREADY_EXISTS = 5, 183
SYNCHRONIZE = 0x00100000


def ensure_single_instance():
    """Druga kopia receivera (np. autostart + ręczne uruchomienie) kończy się czytelnym komunikatem.
    ERROR_ACCESS_DENIED też znaczy "już działa": blokadę ma receiver uruchomiony jako administrator, a proces bez
    uprawnień nie dostaje do niej pełnego dostępu (CreateMutex zwraca wtedy NULL zamiast ERROR_ALREADY_EXISTS) -
    druga kopia startowała i padała dopiero na zajętym porcie UDP z mylącym komunikatem."""
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
    """Czy działa już receiver (blokada jednej instancji istnieje) - bez jej tworzenia. Przed podniesieniem uprawnień:
    druga kopia nie może pokazywać okna UAC tylko po to, żeby zaraz wyjść."""
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
        k.OpenMutexW.restype = wt.HANDLE
        h = k.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
        if h:
            k32.CloseHandle(h)
            return True
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED     # blokada administratora: istnieje, brak dostępu
    except (AttributeError, OSError):
        return False


def release_single_instance():
    """Zwalnia blokadę jednej instancji - bez tego nowy proces po restarcie zobaczyłby "już działa" i by wyszedł."""
    global _mutex
    if _mutex:
        try:
            k32.CloseHandle(_mutex)
        except Exception:
            pass
        _mutex = None


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_admin(cmd):
    """cmd jako administrator (ShellExecute "runas" = okno UAC). -> True, gdy wystartował; odmowa w UAC -> False.
    Zmienne środowiska nie przechodzą przez UAC (nowy proces ma środowisko użytkownika)."""
    sh = ctypes.windll.shell32
    sh.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
    sh.ShellExecuteW.restype = ctypes.c_void_p
    # zawsze SW_SHOWNORMAL: Windows stosuje tę wartość do pierwszego ShowWindow nowego procesu - SW_HIDE (pythonw) sprawiał,
    # że okno receivera nie pojawiało się po kliknięciu ikony w zasobniku; pythonw i tak nie ma konsoli do pokazania
    r = sh.ShellExecuteW(None, "runas", cmd[0], subprocess.list2cmdline(cmd[1:]), os.getcwd(), 1)
    return (r or 0) > 32


def relaunch(env=None, admin=False):
    """Nowy proces receivera z tymi samymi argumentami (jak autostart). Wołać po zwolnieniu portów i blokady instancji.
    env: dodatkowe zmienne środowiska dla nowego procesu (np. stan trybu konsoli). admin: przez UAC; odmowa = zwykły
    restart (receiver nie może zniknąć)."""
    cmd = [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    if admin:
        if relaunch_admin(cmd):
            print("Restarting as administrator:", subprocess.list2cmdline(cmd))
            return
        print("Administrator restart declined - restarting normally")
    kw = {"cwd": os.getcwd(), "close_fds": True, "env": {**os.environ, **(env or {})}}
    if os.path.basename(sys.executable).lower().startswith("pythonw"):      # autostart: bez konsoli, niezależny od starego
        kw.update(creationflags=0x00000008 | 0x00000200,                    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen(cmd, **kw)
    print("Restarting:", subprocess.list2cmdline(cmd))


# ---------------- Mysz (SendInput) ----------------
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


def send_mouse(flags, dx=0, dy=0, data=0):
    # mouseData to DWORD: ujemny skok kółka trzeba podać w kodzie uzupełnieniowym do 2
    inp = INPUT(0, _INPUT_U(MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)))  # 0 = INPUT_MOUSE
    user32.SendInput(1, ctypes.byref(inp), INPUT_SIZE)


VK_LWIN, VK_MENU, VK_SNAPSHOT, VK_G, VK_ESCAPE = 0x5B, 0x12, 0x2C, 0x47, 0x1B
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2
_EXTENDED = {VK_LWIN, VK_SNAPSHOT}


def send_keys(*vks):
    """Skrót klawiszowy (np. Win+G): wciśnięcie po kolei, puszczenie w odwrotnej kolejności, jednym SendInput."""
    def key(vk, up):
        flags = (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
        inp = INPUT(1)                                   # 1 = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return inp
    seq = [key(vk, False) for vk in vks] + [key(vk, True) for vk in reversed(vks)]
    user32.SendInput(len(seq), (INPUT * len(seq))(*seq), INPUT_SIZE)


def foreground_window():
    """(uchwyt, nazwa programu małymi literami) aktywnego okna; (0, None) bez okna, (hwnd, None) bez dostępu."""
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
    """Nazwa programu (np. "eldenring.exe", małe litery) aktywnego okna albo None."""
    return foreground_window()[1]


def force_foreground(hwnd):
    """Okno na pierwszy plan z procesu w tle. Windows blokuje SetForegroundWindow programom, które nie mają
    aktywnego okna (zamiast tego miga przycisk na pasku zadań), więc: najpierw dołączenie do kolejki wejścia wątku
    aktywnego okna (AttachThreadInput), a gdy to nie wystarczy - stuknięcie Alt (ostatnie wejście z klawiatury
    "należy" do nas). -> True, gdy okno jest na pierwszym planie."""
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


def _declare_foreground_api():
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
    """WM_CLOSE do okna (to samo, co krzyżyk / Alt+F4, ale bez klawiatury - działa też z grą jako admin
    nie przez SendInput). -> True, gdy wysłano."""
    return bool(hwnd) and bool(user32.PostMessageW(hwnd, 0x0010, 0, 0))     # WM_CLOSE


def sleep_pc():
    """Uśpienie (S3 / nowoczesne wstrzymanie); hibernacja tylko, jeśli tak ustawiono w Windows."""
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def shutdown_pc(delay_s=10):
    """Wyłączenie z opóźnieniem - do tego czasu "shutdown /a" w konsoli przerywa."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    subprocess.run([exe, "/s", "/t", str(int(delay_s))], timeout=15, check=False,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def send_click(down_flag, up_flag):
    """Wciśnięcie i puszczenie jednym wywołaniem SendInput (atomowo: nic nie wpadnie między DOWN i UP)."""
    arr = (INPUT * 2)(INPUT(0, _INPUT_U(MOUSEINPUT(0, 0, 0, down_flag, 0, 0))),
                      INPUT(0, _INPUT_U(MOUSEINPUT(0, 0, 0, up_flag, 0, 0))))
    user32.SendInput(2, arr, INPUT_SIZE)


# ---------------- Gniazda UDP ----------------
def no_connreset(sk):
    """Windows: ICMP 'port unreachable' nie może wywalać recvfrom (WSAECONNRESET)."""
    try:
        sk.ioctl(socket.SIO_UDP_CONNRESET, False)
    except Exception:
        pass


def bind_udp(port, what, rcvbuf=0):
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if rcvbuf:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    try:
        sk.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"Cannot bind UDP {port} ({what}): {e}. Is another receiver / DSU server running?")
        sys.exit(1)
    no_connreset(sk)
    return sk
