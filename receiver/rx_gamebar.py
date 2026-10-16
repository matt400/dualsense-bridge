"""Receiver: koniec okienka "Wybierz aplikację, aby otworzyć link ms-gamebar" (Windows, rejestr HKCU, bez admina).

Skąd się bierze: Windows traktuje PS wirtualnego DS4 jak przycisk Xbox / Guide i próbuje otworzyć Xbox Game Bar
(link ms-gamebar:). Gdy Game Bar jest odinstalowany, nikt nie obsługuje tego linku - przy każdym PS pojawia się
okno wyboru aplikacji. To samo robi Win+G (akcja "gamebar" po przytrzymaniu PS) -> ms-gamingoverlay:.

suppress():
  1. UseNexusForGameBarEnabled = 0 - przycisk pada nie otwiera Game Bar (to samo co przełącznik w Ustawieniach
     Windows -> Granie -> Xbox Game Bar, którego bez zainstalowanego Game Bar nie da się kliknąć)
  2. bez Game Bar: linki ms-gamebar / ms-gamebarservices / ms-gamingoverlay dostają w HKCU\\Software\\Classes
     cichy program (systray.exe - w Windows 10/11 nic nie robi), więc okno wyboru aplikacji się nie pojawia.
     Klucze mają znacznik MARKER - po ponownej instalacji Game Bar są usuwane i link znów otwiera Game Bar.
Ustawienie już obowiązujące nie jest zapisywane drugi raz. Wynik: lista opisów zmian (do dziennika zdarzeń).
"""
import os

SCHEMES = ("ms-gamebar", "ms-gamebarservices", "ms-gamingoverlay")
CLASSES = r"Software\Classes"
GAMEBAR_KEY = r"Software\Microsoft\GameBar"
NEXUS = "UseNexusForGameBarEnabled"
PACKAGES = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
PACKAGE_PREFIX = "Microsoft.XboxGamingOverlay_"
MARKER = "DualSenseBridge"
NOOP = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "systray.exe")


def _winreg():
    import winreg
    return winreg


def _subkeys(reg, root, path):
    try:
        with reg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    yield reg.EnumKey(k, i)
                except OSError:
                    return
                i += 1
    except OSError:
        return


def _value(reg, root, path, name):
    try:
        with reg.OpenKey(root, path) as k:
            return reg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def gamebar_installed(reg):
    return any(n.startswith(PACKAGE_PREFIX) for n in _subkeys(reg, reg.HKEY_CURRENT_USER, PACKAGES))


def _ours(reg, scheme):
    return _value(reg, reg.HKEY_CURRENT_USER, rf"{CLASSES}\{scheme}", MARKER) is not None


def _handled(reg, scheme):
    """Link obsługiwany przez coś innego niż nasz klucz (np. klasyczny program w HKLM / HKCU)."""
    try:
        reg.OpenKey(reg.HKEY_CLASSES_ROOT, scheme).Close()
    except OSError:
        return False
    return not _ours(reg, scheme)


def _register_noop(reg, scheme):
    hk = reg.HKEY_CURRENT_USER
    with reg.CreateKey(hk, rf"{CLASSES}\{scheme}") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, f"URL:{scheme}")
        reg.SetValueEx(k, "URL Protocol", 0, reg.REG_SZ, "")
        reg.SetValueEx(k, "NoOpenWith", 0, reg.REG_SZ, "")
        reg.SetValueEx(k, MARKER, 0, reg.REG_SZ, "1")
    with reg.CreateKey(hk, rf"{CLASSES}\{scheme}\shell\open\command") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, f'"{NOOP}"')


def _remove(reg, scheme):
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            reg.DeleteKey(reg.HKEY_CURRENT_USER, rf"{CLASSES}\{scheme}{sub}")
        except OSError:
            pass


def suppress(reg=None):
    reg = reg or _winreg()
    done = []
    if _value(reg, reg.HKEY_CURRENT_USER, GAMEBAR_KEY, NEXUS) != 0:
        with reg.CreateKey(reg.HKEY_CURRENT_USER, GAMEBAR_KEY) as k:
            reg.SetValueEx(k, NEXUS, 0, reg.REG_DWORD, 0)
        done.append("controller button no longer opens Game Bar")
    if gamebar_installed(reg):
        stale = [s for s in SCHEMES if _ours(reg, s)]
        for s in stale:
            _remove(reg, s)
        if stale:
            done.append(f"Game Bar installed - removed silent handlers: {', '.join(stale)}")
        return done
    new = [s for s in SCHEMES if not _ours(reg, s) and not _handled(reg, s)]
    for s in new:
        _register_noop(reg, s)
    if new:
        done.append(f"Game Bar not installed - silent handlers: {', '.join(new)}")
    return done
