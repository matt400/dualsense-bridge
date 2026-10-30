"""Rozdzielczość TV przez CCD (rx_display), ciche linki Game Bar (rx_gamebar), kolor przycisku "na pewno?"."""
import ctypes
import sys
import types

sys.path.insert(0, sys.argv[1])
sys.path.insert(1, sys.argv[1] + "/../common")
import rx_display as D  # noqa: E402
import rx_gamebar as G  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("OK  " if ok else "FAIL") + "  " + what)
    fails += not ok


# ---------- czysta logika ----------
check(D.pick_mode([(2560, 1440, 32, 120, D.DM_INTERLACED), (2560, 1440, 32, 60, 0)], 2560, 1440)[3] == 60,
      "tryb z przeplotem pomijany")
check(D.refresh_rational(59) == (60000, 1001) and D.refresh_rational(120) == (120, 1) and D.refresh_rational(0) == (0, 0),
      "Hz z GDI -> ułamek CCD (59 = 59,94)")

paths = (D.PATH_INFO * 1)()
paths[0].sourceInfo.modeInfoIdx, paths[0].targetInfo.modeInfoIdx = 2, D.MODE_IDX_INVALID
modes = (D.MODE_INFO * 3)()
modes[2].infoType, modes[2].id = D.MODE_SOURCE, 7
used = D.compact_modes(paths, modes)
check(len(used) == 1 and used[0].id == 7 and paths[0].sourceInfo.modeInfoIdx == 0, "nieużywane tryby wypadają, indeksy przepisane")


# ---------- set_resolution na zaślepce user32 ----------
class FakeUser32:
    def __init__(self, ccd_ok=True, cur=(3840, 2160, 60)):
        self.cur, self.ccd_ok, self.sdc, self.gdi = list(cur), ccd_ok, [], []
        self.modes = [(3840, 2160, 32, 60, 0), (2560, 1440, 32, 60, 0), (2560, 1440, 32, 120, 0)]

    def EnumDisplaySettingsW(self, dev, k, ref):
        dm = ref._obj
        k = k.value
        if k == D.ENUM_CURRENT_SETTINGS:
            dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency = self.cur
            return 1
        if k >= len(self.modes):
            return 0
        dm.dmPelsWidth, dm.dmPelsHeight, dm.dmBitsPerPel, dm.dmDisplayFrequency, dm.dmDisplayFlags = self.modes[k]
        return 1

    def GetDisplayConfigBufferSizes(self, flags, n_paths, n_modes):
        n_paths._obj.value, n_modes._obj.value = 1, 2
        return 0

    def QueryDisplayConfig(self, flags, n_paths, paths, n_modes, modes, _):
        paths[0].sourceInfo.modeInfoIdx, paths[0].targetInfo.modeInfoIdx = 1, 0
        modes[0].infoType, modes[1].infoType = 2, D.MODE_SOURCE
        modes[1].u.sourceMode.width, modes[1].u.sourceMode.height = self.cur[:2]
        return 0

    def SetDisplayConfig(self, n_paths, paths, n_modes, modes, flags):
        p = paths[0]
        src = modes[p.sourceInfo.modeInfoIdx].u.sourceMode
        self.sdc.append((flags, n_modes, src.width, src.height, p.targetInfo.modeInfoIdx,
                         p.targetInfo.refreshNum, p.targetInfo.refreshDen, p.targetInfo.scaling))
        if not self.ccd_ok:
            return 31
        if flags & D.SDC_APPLY:
            self.cur = [src.width, src.height, p.targetInfo.refreshNum or 60]
        return 0

    def ChangeDisplaySettingsExW(self, dev, ref, *a):
        dm = ref._obj
        self.gdi.append((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
        self.cur = [dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency]
        return 0


D.time = types.SimpleNamespace(sleep=lambda s: None, monotonic=__import__("time").monotonic)
u = FakeUser32()
D._user32 = lambda: u
out = D.set_resolution("\\\\.\\DISPLAY2", 2560, 1440)
applied = [c for c in u.sdc if c[0] & D.SDC_APPLY]
check(len(applied) == 1 and applied[0][2:4] == (2560, 1440) and applied[0][4] == D.MODE_IDX_INVALID
      and applied[0][5:7] == (120, 1) and applied[0][7] == D.SCALING_PREFERRED and applied[0][1] == 1
      and applied[0][0] & D.SDC_SAVE_TO_DATABASE and not u.gdi and "CCD" in out,
      f"1440p przez CCD (jak Ustawienia Windows), 120 Hz, zapis w bazie: {out}")
check(D.set_resolution("x", 2560, 1440) == "2560x1440 already set", "obowiązujący tryb nie jest zmieniany")

u = FakeUser32(ccd_ok=False)
D._user32 = lambda: u
out = D.set_resolution("x", 2560, 1440)
check(u.gdi == [(2560, 1440, 120)] and "GDI" in out, f"CCD odmawia -> zapas GDI z pełnym trybem sterownika: {out}")
try:
    D.set_resolution("x", 1234, 567)
    check(False, "brak trybu -> wyjątek")
except ValueError:
    check(True, "brak trybu -> wyjątek")


# ---------- rx_gamebar na zaślepce winreg ----------
class Key:
    def __init__(self, reg, path):
        self.reg, self.path = reg, path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def Close(self):
        pass


class FakeReg:
    HKEY_CURRENT_USER, HKEY_CLASSES_ROOT, REG_SZ, REG_DWORD = "HKCU", "HKCR", 1, 4

    def __init__(self, keys=()):
        self.data = {}
        for k in keys:
            self._add(k)

    def _add(self, full):              # jak w rejestrze: klucz powstaje razem z kluczami nadrzędnymi
        parts = full.split("\\")
        for n in range(2, len(parts) + 1):
            self.data.setdefault("\\".join(parts[:n]), {})

    def _full(self, root, path):
        if root == "HKCR":
            return "HKCU\\" + G.CLASSES + "\\" + path
        return f"{root}\\{path}"

    def OpenKey(self, root, path):
        full = self._full(root, path)
        if full not in self.data:
            raise OSError(full)
        return Key(self, full)

    def CreateKey(self, root, path):
        full = self._full(root, path)
        self._add(full)
        return Key(self, full)

    def EnumKey(self, k, i):
        pre = k.path + "\\"
        subs = sorted({p[len(pre):].split("\\")[0] for p in self.data if p.startswith(pre)})
        if i >= len(subs):
            raise OSError
        return subs[i]

    def QueryValueEx(self, k, name):
        if name not in self.data[k.path]:
            raise OSError(name)
        return self.data[k.path][name], 0

    def SetValueEx(self, k, name, _, typ, value):
        self.data[k.path][name] = value

    def DeleteKey(self, root, path):
        full = self._full(root, path)
        if full not in self.data or any(p.startswith(full + "\\") for p in self.data):
            raise OSError(full)
        del self.data[full]


reg = FakeReg()
done = G.suppress(reg)
cls = "HKCU\\" + G.CLASSES
check(reg.data["HKCU\\" + G.GAMEBAR_KEY][G.NEXUS] == 0, "przycisk pada nie otwiera Game Bar")
check(all(reg.data[f"{cls}\\{s}\\shell\\open\\command"][""].endswith('systray.exe"') for s in G.SCHEMES),
      "bez Game Bar: ms-gamebar* -> cichy program (bez okna wyboru aplikacji)")
check(len(done) == 2 and G.suppress(reg) == [], "drugie uruchomienie niczego nie zmienia")

reg._add("HKCU\\" + G.PACKAGES + "\\Microsoft.XboxGamingOverlay_7.1_x64__8wekyb3d8bbwe")
done = G.suppress(reg)
check(not any(k.startswith((f"{cls}\\ms-gaming", f"{cls}\\ms-gamebar")) for k in reg.data) and len(done) == 1,
      "Game Bar zainstalowany ponownie -> nasze klucze usunięte")

reg = FakeReg([f"{cls}\\ms-gamebar"])        # link obsługuje inny program - nie ruszamy
G.suppress(reg)
check(f"{cls}\\ms-gamebar\\shell\\open\\command" not in reg.data and f"{cls}\\ms-gamingoverlay\\shell\\open\\command" in reg.data,
      "cudzy handler zostaje nietknięty")


# ---------- styl ----------
try:
    import rx_qt_style as ST
except ImportError:
    ST = None
    print("SKIP  styl (brak PySide6)")
css = ST.qss() if ST else ""
armed = [ln for ln in css.split("}") if 'armed="true"' in ln and "background" in ln and ":hover" not in ln]
check(not ST or armed and all("color: #ffffff" in ln for ln in armed) and all(ST.BG not in ln for ln in armed),
      "przycisk 'na pewno?' (Aktualizować?) - biały napis, nie czarny")

print("WYNIK:", "OK" if not fails else f"{fails} błędów")
sys.exit(1 if fails else 0)
