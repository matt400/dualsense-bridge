"""Receiver: rozdzielczość i skalowanie ekranu TV po włączeniu trybu konsoli (Windows, ctypes).

apply_tv(w, h, scale) czeka, aż po DisplaySwitch /external zostanie jeden aktywny ekran (TV), potem:
  rozdzielczość  ChangeDisplaySettingsExW - tryb w×h z najwyższym odświeżaniem, zapis w rejestrze (Windows pamięta
                 go dla układu "tylko TV", więc układ extend / monitor zostaje nietknięty)
  skalowanie     DisplayConfigSetDeviceInfo z nieudokumentowanym typem -4 (DPI_SCALE_SET) - tak samo robi okno
                 Ustawienia -> Ekran. Skala jest podawana względem zalecanej dla ekranu, więc najpierw odczyt (-3).
Ustawienie już obowiązujące nie jest zmieniane (bez mrugnięcia obrazu). Brak ekranu / nieobsługiwany tryb -> wyjątek
z opisem (rx_console loguje go i działa dalej).
"""
import ctypes
import time

DPI_VALUES = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)   # kolejne kroki skalowania Windows
QDC_ONLY_ACTIVE_PATHS = 2
DEVICE_INFO_GET_SOURCE_NAME, DPI_GET, DPI_SET = 1, -3, -4
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DM_BITSPERPEL, DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x40000, 0x80000, 0x100000, 0x400000
CDS_UPDATEREGISTRY = 0x1
ERROR_INSUFFICIENT_BUFFER = 122
DISP_CHANGE_SUCCESSFUL, DISP_CHANGE_RESTART = 0, 1
SETTLE_S = 6.0          # tyle czekamy, aż Windows przełączy układ ekranów na "tylko TV"


# ---------------- czysta logika (testowalna bez Windows) ----------------
def pick_mode(modes, w, h):
    """modes = [(szer, wys, bpp, Hz)] -> najlepszy tryb w×h (32 bpp, najwyższe Hz) albo None."""
    ok = [m for m in modes if m[0] == w and m[1] == h and m[2] >= 32]
    return max(ok, key=lambda m: m[3]) if ok else None


def scale_relative(percent, min_rel, cur_rel, max_rel):
    """Skala w % -> (wartość względna dla DPI_SET, czy już jest). Zalecana skala ma indeks -min_rel w DPI_VALUES.
    Skala spoza zakresu ekranu -> ValueError."""
    if percent not in DPI_VALUES:
        raise ValueError(f"scale {percent}% is not one of {DPI_VALUES}")
    rel = DPI_VALUES.index(percent) - (-min_rel)
    if not min_rel <= rel <= max_rel:
        allowed = [DPI_VALUES[-min_rel + r] for r in range(min_rel, max_rel + 1) if 0 <= -min_rel + r < len(DPI_VALUES)]
        raise ValueError(f"scale {percent}% not supported by this display (allowed: {allowed})")
    return rel, rel == cur_rel


# ---------------- struktury Windows (typy o stałym rozmiarze - jak w nagłówkach Windows) ----------------
class LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class PATH_SOURCE(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("statusFlags", ctypes.c_uint32)]


class PATH_TARGET(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32), ("scaling", ctypes.c_uint32),
                ("refreshNum", ctypes.c_uint32), ("refreshDen", ctypes.c_uint32), ("scanLineOrdering", ctypes.c_uint32),
                ("targetAvailable", ctypes.c_int32), ("statusFlags", ctypes.c_uint32)]


class PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", PATH_SOURCE), ("targetInfo", PATH_TARGET), ("flags", ctypes.c_uint32)]


class MODE_INFO(ctypes.Structure):       # zawartość (unia trybu) nie jest potrzebna - tylko rozmiar 64 B
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", LUID),
                ("union", ctypes.c_byte * 48)]


class HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int32), ("size", ctypes.c_uint32), ("adapterId", LUID), ("id", ctypes.c_uint32)]


class SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", HEADER), ("viewGdiDeviceName", ctypes.c_wchar * 32)]


class DPI_GET_INFO(ctypes.Structure):
    _fields_ = [("header", HEADER), ("minScaleRel", ctypes.c_int32), ("curScaleRel", ctypes.c_int32),
                ("maxScaleRel", ctypes.c_int32)]


class DPI_SET_INFO(ctypes.Structure):
    _fields_ = [("header", HEADER), ("scaleRel", ctypes.c_int32)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", ctypes.c_uint16), ("dmDriverVersion", ctypes.c_uint16),
                ("dmSize", ctypes.c_uint16), ("dmDriverExtra", ctypes.c_uint16), ("dmFields", ctypes.c_uint32),
                ("dmPositionX", ctypes.c_int32), ("dmPositionY", ctypes.c_int32), ("dmDisplayOrientation", ctypes.c_uint32),
                ("dmDisplayFixedOutput", ctypes.c_uint32), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                ("dmFormName", ctypes.c_wchar * 32), ("dmLogPixels", ctypes.c_uint16), ("dmBitsPerPel", ctypes.c_uint32),
                ("dmPelsWidth", ctypes.c_uint32), ("dmPelsHeight", ctypes.c_uint32), ("dmDisplayFlags", ctypes.c_uint32),
                ("dmDisplayFrequency", ctypes.c_uint32), ("dmICMMethod", ctypes.c_uint32), ("dmICMIntent", ctypes.c_uint32),
                ("dmMediaType", ctypes.c_uint32), ("dmDitherType", ctypes.c_uint32), ("dmReserved1", ctypes.c_uint32),
                ("dmReserved2", ctypes.c_uint32), ("dmPanningWidth", ctypes.c_uint32), ("dmPanningHeight", ctypes.c_uint32)]


def _user32():
    return ctypes.WinDLL("user32")


def _query(u):
    """Aktywne ścieżki ekranów; układ może się zmieniać w trakcie (przełączanie TV) - wtedy ponów."""
    for _ in range(5):
        n_paths, n_modes = ctypes.c_uint32(), ctypes.c_uint32()
        if u.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)):
            raise OSError("GetDisplayConfigBufferSizes failed")
        paths, modes = (PATH_INFO * n_paths.value)(), (MODE_INFO * n_modes.value)()
        r = u.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), paths, ctypes.byref(n_modes), modes, None)
        if r == 0:
            return paths[:n_paths.value]
        if r != ERROR_INSUFFICIENT_BUFFER:
            raise OSError(f"QueryDisplayConfig failed ({r})")
        time.sleep(0.1)
    raise OSError("QueryDisplayConfig: display layout keeps changing")


def active_sources():
    """[(adapterId, sourceId, nazwa GDI np. \\\\.\\DISPLAY2)] aktywnych ekranów."""
    u = _user32()
    out, seen = [], set()
    for p in _query(u):
        src = p.sourceInfo
        key = (src.adapterId.LowPart, src.adapterId.HighPart, src.id)
        if key in seen:
            continue                     # klonowanie: kilka celów na jednym źródle
        seen.add(key)
        name = SOURCE_NAME()
        name.header = HEADER(DEVICE_INFO_GET_SOURCE_NAME, ctypes.sizeof(SOURCE_NAME), src.adapterId, src.id)
        if u.DisplayConfigGetDeviceInfo(ctypes.byref(name)):
            raise OSError("DisplayConfigGetDeviceInfo(source name) failed")
        out.append((src.adapterId, src.id, name.viewGdiDeviceName))
    return out


def set_resolution(device, w, h):
    """-> opis zmiany; nic nie robi, gdy ekran już ma w×h."""
    u = _user32()
    cur = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
    if u.EnumDisplaySettingsW(device, ctypes.c_uint32(ENUM_CURRENT_SETTINGS), ctypes.byref(cur)) and (cur.dmPelsWidth, cur.dmPelsHeight) == (w, h):
        return f"{w}x{h} already set"
    modes, k = [], 0
    dm = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
    while u.EnumDisplaySettingsW(device, ctypes.c_uint32(k), ctypes.byref(dm)):
        modes.append((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmBitsPerPel, dm.dmDisplayFrequency))
        k += 1
    best = pick_mode(modes, w, h)
    if best is None:
        raise ValueError(f"{device}: no {w}x{h} mode (TV / cable may not support it)")
    new = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW), dmPelsWidth=w, dmPelsHeight=h, dmBitsPerPel=best[2],
                   dmDisplayFrequency=best[3], dmFields=DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL | DM_DISPLAYFREQUENCY)
    r = u.ChangeDisplaySettingsExW(device, ctypes.byref(new), None, CDS_UPDATEREGISTRY, None)
    if r not in (DISP_CHANGE_SUCCESSFUL, DISP_CHANGE_RESTART):
        raise OSError(f"ChangeDisplaySettingsExW({device}, {w}x{h}@{best[3]}) = {r}")
    return f"{w}x{h} @ {best[3]} Hz"


def set_scale(adapter, source_id, percent):
    u = _user32()
    get = DPI_GET_INFO()
    get.header = HEADER(DPI_GET, ctypes.sizeof(DPI_GET_INFO), adapter, source_id)
    if u.DisplayConfigGetDeviceInfo(ctypes.byref(get)):
        raise OSError("cannot read display scaling")
    rel, already = scale_relative(percent, get.minScaleRel, get.curScaleRel, get.maxScaleRel)
    if already:
        return f"scale {percent}% already set"
    st = DPI_SET_INFO(HEADER(DPI_SET, ctypes.sizeof(DPI_SET_INFO), adapter, source_id), rel)
    if u.DisplayConfigSetDeviceInfo(ctypes.byref(st)):
        raise OSError(f"cannot set display scaling to {percent}%")
    return f"scale {percent}%"


def apply_tv(w, h, scale, settle_s=SETTLE_S):
    """Po /external: jedyny aktywny ekran = TV -> rozdzielczość, potem skalowanie (0 = bez zmiany). -> opis."""
    end = time.monotonic() + settle_s
    while True:
        src = active_sources()
        if len(src) == 1:
            break
        if time.monotonic() >= end:
            raise RuntimeError(f"{len(src)} active displays after switching to TV - resolution not changed")
        time.sleep(0.25)
    adapter, sid, device = src[0]
    done = []
    if w and h:
        done.append(set_resolution(device, w, h))
        time.sleep(0.5)                  # skala zalecana zależy od rozdzielczości - odczyt po zmianie trybu
    if scale:
        done.append(set_scale(adapter, sid, scale))
    return f"{device}: " + ", ".join(done)
