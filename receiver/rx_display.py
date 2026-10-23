"""Receiver: the TV resolution and scaling after console mode is switched on (Windows, ctypes).

apply_tv(w, h, scale) waits until one active display (the TV) is left after DisplaySwitch /external, then:
  resolution     SetDisplayConfig (CCD) - the same as the Settings -> Display window: the source mode w×h, the highest
                 refresh rate of the w×h modes, saved in the Windows layout database (it remembers it for "TV only", the extend /
                 monitor layout stays untouched). The old ChangeDisplaySettingsExW left an empty resolution list in Settings
                 (a GDI mode outside the CCD list) - it stays only as a fallback when CCD refuses.
  scaling        DisplayConfigSetDeviceInfo with the undocumented type -4 (DPI_SCALE_SET) - the same as the
                 Settings -> Display window does. The scale is given relative to the one recommended for the display, so it is read first
                 (-3).
A setting that is already in effect is not changed (no picture flicker). No display / an unsupported mode -> an exception
with a description (rx_console logs it and carries on).
"""
import ctypes
import time

DPI_VALUES = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)   # consecutive Windows scaling steps
QDC_ONLY_ACTIVE_PATHS = 2
DEVICE_INFO_GET_SOURCE_NAME, DPI_GET, DPI_SET = 1, -3, -4
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DM_BITSPERPEL, DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFLAGS, DM_DISPLAYFREQUENCY = 0x40000, 0x80000, 0x100000, 0x200000, 0x400000
DM_INTERLACED = 0x2
CDS_UPDATEREGISTRY = 0x1
ERROR_INSUFFICIENT_BUFFER = 122
DISP_CHANGE_SUCCESSFUL, DISP_CHANGE_RESTART = 0, 1
SETTLE_S = 6.0          # how long we wait for Windows to switch the display layout to "TV only"
SDC_USE_SUPPLIED_DISPLAY_CONFIG, SDC_VALIDATE, SDC_APPLY, SDC_SAVE_TO_DATABASE, SDC_ALLOW_CHANGES = 0x20, 0x40, 0x80, 0x200, 0x400
MODE_IDX_INVALID = 0xFFFFFFFF
MODE_SOURCE = 1
SCALING_PREFERRED = 128
NTSC_HZ = frozenset({23, 29, 47, 59, 71, 119, 143, 239})    # GDI reports 59 for 59.94 Hz etc.
TOPOLOGY = {"internal": 0x1, "clone": 0x2, "extend": 0x4, "tv": 0x8}     # SDC_TOPOLOGY_* (like DisplaySwitch)


# ---------------- pure logic (testable without Windows) ----------------
def pick_mode(modes, w, h):
    """Modes = [(w, h, bpp, Hz[, flags])] -> the best w×h mode (32 bpp, not interlaced, the highest Hz) or None."""
    ok = [m for m in modes if m[0] == w and m[1] == h and m[2] >= 32 and not (m[4:5] and m[4] & DM_INTERLACED)]
    return max(ok, key=lambda m: m[3]) if ok else None


def refresh_rational(hz):
    """Hz from GDI (an integer) -> (numerator, denominator) for CCD; 0 = let Windows choose. NTSC rates become x/1.001."""
    if not hz:
        return 0, 0
    return ((hz + 1) * 1000, 1001) if hz in NTSC_HZ else (hz, 1)


def compact_modes(paths, modes):
    """Only the modes the paths refer to (an abandoned target mode drops out), indexes rewritten -> a list."""
    out, remap = [], {}
    for p in paths:
        for info in (p.sourceInfo, p.targetInfo):
            i = info.modeInfoIdx
            if i == MODE_IDX_INVALID:
                continue
            if i not in remap:
                remap[i] = len(out)
                out.append(modes[i])
            info.modeInfoIdx = remap[i]
    return out


def scale_relative(percent, min_rel, cur_rel, max_rel):
    """Scale in % -> (the relative value for DPI_SET, whether it is already set). The recommended scale has index -min_rel in DPI_VALUES.
    A scale outside the display's range -> ValueError.
    """
    if percent not in DPI_VALUES:
        raise ValueError(f"scale {percent}% is not one of {DPI_VALUES}")
    rel = DPI_VALUES.index(percent) - (-min_rel)
    if not min_rel <= rel <= max_rel:
        allowed = [DPI_VALUES[-min_rel + r] for r in range(min_rel, max_rel + 1) if 0 <= -min_rel + r < len(DPI_VALUES)]
        raise ValueError(f"scale {percent}% not supported by this display (allowed: {allowed})")
    return rel, rel == cur_rel


# ---------------- Windows structures (fixed-size types - as in the Windows headers) ----------------
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


class SOURCE_MODE(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32), ("pixelFormat", ctypes.c_uint32),
                ("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class MODE_UNION(ctypes.Union):         # the target mode / desktop image are not changed - the size is enough (48 B, 8-byte alignment)
    _fields_ = [("sourceMode", SOURCE_MODE), ("raw", ctypes.c_uint64 * 6)]


class MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", LUID), ("u", MODE_UNION)]


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
    """The user32 library (the display configuration functions live there)."""
    return ctypes.WinDLL("user32")


def _query(u):
    """Active display paths -> (paths, modes) as ctypes arrays; the layout may change while we read
    (TV switching) - then retry.
    """
    for _ in range(5):
        n_paths, n_modes = ctypes.c_uint32(), ctypes.c_uint32()
        if u.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)):
            raise OSError("GetDisplayConfigBufferSizes failed")
        paths, modes = (PATH_INFO * n_paths.value)(), (MODE_INFO * n_modes.value)()
        r = u.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), paths, ctypes.byref(n_modes), modes, None)
        if r == 0:
            return (PATH_INFO * n_paths.value)(*paths[:n_paths.value]), (MODE_INFO * n_modes.value)(*modes[:n_modes.value])
        if r != ERROR_INSUFFICIENT_BUFFER:
            raise OSError(f"QueryDisplayConfig failed ({r})")
        time.sleep(0.1)
    raise OSError("QueryDisplayConfig: display layout keeps changing")


def active_sources():
    """[(adapterId, sourceId, GDI name e.g. \\\\.\\DISPLAY2)] of the active displays."""
    u = _user32()
    out, seen = [], set()
    for p in _query(u)[0]:
        src = p.sourceInfo
        key = (src.adapterId.LowPart, src.adapterId.HighPart, src.id)
        if key in seen:
            continue                     # cloning: several targets on one source
        seen.add(key)
        name = SOURCE_NAME()
        name.header = HEADER(DEVICE_INFO_GET_SOURCE_NAME, ctypes.sizeof(SOURCE_NAME), src.adapterId, src.id)
        if u.DisplayConfigGetDeviceInfo(ctypes.byref(name)):
            raise OSError("DisplayConfigGetDeviceInfo(source name) failed")
        out.append((src.adapterId, src.id, name.viewGdiDeviceName))
    return out


def set_topology(mode) -> None:
    """The display layout ("extend" / "internal" / "tv") through SetDisplayConfig - the same as DisplaySwitch, but without
    launching a program and synchronously (it works while Windows is shutting down too). Windows remembers the layout,
    so after a restart the sign-in screen appears where it was set.
    """
    r = _user32().SetDisplayConfig(0, None, 0, None, SDC_APPLY | TOPOLOGY[mode])
    if r:
        raise OSError(f"SetDisplayConfig({mode}) = {r}")


def _current(u, device):
    """The current (w, h, Hz) of a GDI device, or None when it cannot be read."""
    cur = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
    if u.EnumDisplaySettingsW(device, ctypes.c_uint32(ENUM_CURRENT_SETTINGS), ctypes.byref(cur)):
        return cur.dmPelsWidth, cur.dmPelsHeight, cur.dmDisplayFrequency
    return None


def _modes(u, device):
    """[(w, h, bpp, Hz, flags, DEVMODEW)] - all the display modes from GDI."""
    out, k = [], 0
    while True:
        dm = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
        if not u.EnumDisplaySettingsW(device, ctypes.c_uint32(k), ctypes.byref(dm)):
            return out
        out.append((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmBitsPerPel, dm.dmDisplayFrequency, dm.dmDisplayFlags, dm))
        k += 1


def _ccd_resolution(u, w, h, rate, keep_target):
    """The source mode w×h through SetDisplayConfig. keep_target=False: Windows picks a signal for the TV (w×h at `rate`),
    True: the current signal stays, the graphics card scales the picture. -> a Windows error code (0 = OK).
    """
    paths, modes = _query(u)
    for p in paths:
        if p.sourceInfo.modeInfoIdx == MODE_IDX_INVALID or modes[p.sourceInfo.modeInfoIdx].infoType != MODE_SOURCE:
            return 87                    # ERROR_INVALID_PARAMETER - an unexpected layout, let the fallback work
        src = modes[p.sourceInfo.modeInfoIdx].u.sourceMode
        src.width, src.height = w, h
        if not keep_target:
            t = p.targetInfo
            t.modeInfoIdx = MODE_IDX_INVALID
            (t.refreshNum, t.refreshDen), t.scanLineOrdering, t.scaling = rate, 0, SCALING_PREFERRED
    used = compact_modes(paths, modes)
    arr = (MODE_INFO * len(used))(*used)
    flags = SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_SAVE_TO_DATABASE | SDC_ALLOW_CHANGES
    return (u.SetDisplayConfig(len(paths), paths, len(used), arr, flags | SDC_VALIDATE)
            or u.SetDisplayConfig(len(paths), paths, len(used), arr, flags | SDC_APPLY))


def _gdi_resolution(u, device, best):
    """Fallback: exactly the DEVMODE the driver returned (with flags and the output mode), no bare fields."""
    *_, dm = best
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_BITSPERPEL | DM_DISPLAYFREQUENCY | DM_DISPLAYFLAGS
    return u.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)


def set_resolution(device, w, h) -> str:
    """-> a description of the change; does nothing when the display already has w×h. In turn: CCD with a w×h signal, CCD scaling
    the current signal, GDI. After each attempt it reads back - what counts is what is actually on the display.
    """
    u = _user32()
    if (cur := _current(u, device)) and cur[:2] == (w, h):
        return f"{w}x{h} already set"
    best = pick_mode(_modes(u, device), w, h)
    if best is None:
        raise ValueError(f"{device}: no {w}x{h} mode (TV / cable may not support it)")
    attempts = (("CCD", lambda: _ccd_resolution(u, w, h, refresh_rational(best[3]), False)),
                ("CCD any Hz", lambda: _ccd_resolution(u, w, h, (0, 0), False)),
                ("CCD scaled", lambda: _ccd_resolution(u, w, h, (0, 0), True)),
                ("GDI", lambda: _gdi_resolution(u, device, best)))
    errors = []
    for name, attempt in attempts:
        try:
            r = attempt()
        except OSError as e:
            r = repr(e)
        if r == DISP_CHANGE_SUCCESSFUL or (name == "GDI" and r == DISP_CHANGE_RESTART):
            time.sleep(0.3)
            if (cur := _current(u, device)) and cur[:2] == (w, h):
                return f"{w}x{h} @ {cur[2]} Hz ({name})"
            r = f"set, but display reports {cur[:2] if cur else '?'}"
        errors.append(f"{name}: {r}")
    raise OSError(f"{device}: cannot set {w}x{h} - " + "; ".join(errors))


def set_scale(adapter, source_id, percent) -> str:
    """Set the Windows scaling of a display source to `percent` (relative to its recommended scale; read first, then set). -> a
    description.
    """
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
    """After /external: the only active display = the TV -> resolution, then scaling (0 = no change). -> a description."""
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
        time.sleep(0.5)                  # the recommended scale depends on the resolution - read it after the mode change
    if scale:
        done.append(set_scale(adapter, sid, scale))
    return f"{device}: " + ", ".join(done)
