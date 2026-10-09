"""Receiver: ikona pada rysowana w kodzie + zasobnik systemowy (Shell_NotifyIcon przez ctypes)."""
import ctypes
import ctypes.wintypes as wt
import struct
import threading
import zlib


def render_icon(rgb, size=32):
    """Piktogram pada jako wiersze RGBA (od góry), supersampling 3x3 dla gładkich krawędzi."""
    def rr(x, y, x0, y0, x1, y1, r):
        cx = min(max(x, x0 + r), x1 - r)
        cy = min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def circ(x, y, cx, cy, r):
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def solid(x, y):
        return rr(x, y, 3, 8, 29, 20, 5) or circ(x, y, 8.5, 20.5, 6) or circ(x, y, 23.5, 20.5, 6)

    def cut(x, y):
        dpad = (abs(x - 10) < 1.2 and abs(y - 14.5) < 3.4) or (abs(y - 14.5) < 1.2 and abs(x - 10) < 3.4)
        return (dpad or circ(x, y, 22.5, 12.5, 1.7) or circ(x, y, 25.7, 15.5, 1.7)
                or circ(x, y, 19.3, 15.5, 1.7) or circ(x, y, 22.5, 18.5, 1.7))

    ss, k = 3, 32.0 / size
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            hits = 0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + 0.5) / ss) * k
                    y = (py + (sy + 0.5) / ss) * k
                    if solid(x, y) and not cut(x, y):
                        hits += 1
            row.append((rgb[0], rgb[1], rgb[2], round(255 * hits / (ss * ss))))
        rows.append(row)
    return rows


def icon_dib(rows):
    """Dane ikony dla CreateIconFromResourceEx: BITMAPINFOHEADER + piksele BGRA (od dołu) + maska AND."""
    n = len(rows)
    hdr = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    px = b"".join(bytes((b, g, r, a)) for row in reversed(rows) for (r, g, b, a) in row)
    return hdr + px + bytes(((n + 31) // 32) * 4 * n)


def icon_png(rows):
    n = len(rows)
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in rows)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


ICON_OK, ICON_LOW = (90, 170, 255), (229, 57, 53)

# ---------------- Zasobnik systemowy (Shell_NotifyIcon przez ctypes, bez zależności) ----------------
WM_APP, WM_DESTROY, WM_CLOSE, WM_TIMER = 0x8000, 0x0002, 0x0010, 0x0113
WM_TRAY, WM_TRAY_UPDATE = WM_APP + 1, WM_APP + 2
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_WARNING = 0x2
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
ID_SHOW, ID_QUIT = 1, 2


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class Tray:
    """Ikona w zasobniku z własnym wątkiem i pętlą komunikatów (ukryte okno odbiera zdarzenia).
    Zdarzenia ("show" / "quit") trafiają do on_event - musi być bezpieczne wątkowo (queue.put)."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.hwnd = None
        self.ok = False
        self.ready = threading.Event()
        self._mu = threading.Lock()
        self._tip, self._low, self._balloon = "DualSense bridge", False, None
        self._icons = {}
        self._added = False
        self._u = self._s = None
        self._proc_ref = None
        self.wm_taskbar = 0

    # ---- API wywoływane z innych wątków ----
    def start(self, timeout=3.0):
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(timeout)
        return self.ok

    def set_status(self, tip, low):
        with self._mu:
            self._tip, self._low = tip[:127], bool(low)
        self._post(WM_TRAY_UPDATE)

    def balloon(self, title, text):
        with self._mu:
            self._balloon = (title[:63], text[:255])
        self._post(WM_TRAY_UPDATE)

    def stop(self):
        self._post(WM_CLOSE)

    def _post(self, msg):
        if self.hwnd and self._u:
            self._u.PostMessageW(self.hwnd, msg, 0, 0)

    # ---- wątek zasobnika ----
    def _nid(self, flags, low):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icons[low]
        nid.szTip = self._tip
        return nid

    def _add(self):
        with self._mu:
            low = self._low
        self._added = bool(self._s.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP, low))))
        return self._added

    def _update(self):
        with self._mu:
            low, balloon = self._low, self._balloon
            self._balloon = None
        flags = NIF_ICON | NIF_TIP | (NIF_INFO if balloon else 0)
        nid = self._nid(flags, low)
        if balloon:
            nid.szInfoTitle, nid.szInfo = balloon
            nid.dwInfoFlags = NIIF_WARNING
        self._s.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _menu(self, hwnd):
        u = self._u
        pt = wt.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        menu = u.CreatePopupMenu()
        u.AppendMenuW(menu, 0, ID_SHOW, "Show window")
        u.AppendMenuW(menu, 0, ID_QUIT, "Quit")
        u.SetForegroundWindow(hwnd)     # wymagane, inaczej menu nie znika po kliknięciu obok
        cmd = u.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None)
        u.PostMessageW(hwnd, 0, 0, 0)   # WM_NULL
        u.DestroyMenu(menu)
        if cmd == ID_SHOW:
            self.on_event("show")
        elif cmd == ID_QUIT:
            self.on_event("quit")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = lparam & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_event("show")
                elif ev == WM_RBUTTONUP:
                    self._menu(hwnd)
                return 0
            if msg == WM_TRAY_UPDATE:
                if self._added:
                    self._update()
                return 0
            if msg == self.wm_taskbar:      # Explorer wystartował ponownie - dodaj ikonę jeszcze raz
                self._add()
                return 0
            if msg == WM_TIMER:             # ponawiaj dodanie ikony (np. autostart przed paskiem zadań)
                if self._add():
                    self._u.KillTimer(hwnd, 1)
                return 0
            if msg == WM_CLOSE:
                self._u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                if self._added:
                    self._s.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(NIF_MESSAGE, False)))
                self._u.PostQuitMessage(0)
                return 0
        except Exception as e:
            print("tray error:", repr(e))
        return self._u.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _make_icon(self, rgb):
        u = self._u
        dib = icon_dib(render_icon(rgb))
        h = u.CreateIconFromResourceEx(dib, len(dib), True, 0x00030000, 32, 32, 0)
        return h or u.LoadIconW(None, 32512)    # awaryjnie standardowa ikona aplikacji

    def _run(self):
        try:
            u = self._u = ctypes.WinDLL("user32", use_last_error=True)
            s = self._s = ctypes.WinDLL("shell32", use_last_error=True)
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            u.DefWindowProcW.restype = ctypes.c_ssize_t
            u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            u.RegisterClassW.restype = wt.ATOM
            u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
                                          wt.HINSTANCE, wt.LPVOID]
            u.CreateWindowExW.restype = wt.HWND
            u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
            u.GetMessageW.restype = wt.BOOL
            u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
            u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
            u.DispatchMessageW.restype = ctypes.c_ssize_t
            u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            u.PostQuitMessage.argtypes = [ctypes.c_int]
            u.DestroyWindow.argtypes = [wt.HWND]
            u.CreatePopupMenu.restype = wt.HMENU
            u.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
            u.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         wt.HWND, ctypes.c_void_p]
            u.TrackPopupMenu.restype = ctypes.c_int
            u.DestroyMenu.argtypes = [wt.HMENU]
            u.SetForegroundWindow.argtypes = [wt.HWND]
            u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
            u.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
            u.RegisterWindowMessageW.restype = wt.UINT
            u.CreateIconFromResourceEx.argtypes = [ctypes.c_char_p, wt.DWORD, wt.BOOL, wt.DWORD,
                                                   ctypes.c_int, ctypes.c_int, wt.UINT]
            u.CreateIconFromResourceEx.restype = wt.HICON
            u.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
            u.LoadIconW.restype = wt.HICON
            u.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
            u.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
            s.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
            s.Shell_NotifyIconW.restype = wt.BOOL
            k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
            k.GetModuleHandleW.restype = wt.HMODULE

            self._icons = {False: self._make_icon(ICON_OK), True: self._make_icon(ICON_LOW)}
            self.wm_taskbar = u.RegisterWindowMessageW("TaskbarCreated")
            proc_t = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
            self._proc_ref = proc_t(self._wndproc)            # trzymaj referencję (GC)
            hinst = k.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._proc_ref, ctypes.c_void_p)
            wc.hInstance = hinst
            wc.lpszClassName = "DualSenseBridgeTray"
            if not u.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.hwnd = u.CreateWindowExW(0, "DualSenseBridgeTray", "DualSenseBridgeTray", 0,
                                          0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._add():
                u.SetTimer(self.hwnd, 1, 2000, None)          # spróbuj ponownie za chwilę
            self.ok = True
        except Exception as e:
            print(f"Tray unavailable: {e!r}")
            self.ready.set()
            return
        self.ready.set()
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
