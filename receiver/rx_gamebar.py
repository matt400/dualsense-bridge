"""Receiver: get rid of the "Select an app to open this ms-gamebar link" popup (Windows, HKCU registry, no admin needed).

Why it happens: Windows treats the PS button of the virtual DS4 as the Xbox / Guide button and tries to open the Xbox
Game Bar (the ms-gamebar: link). When Game Bar is uninstalled nobody handles that link, so every PS press brings up the
app chooser. Win+G (the "gamebar" action on a long PS press) does the same through ms-gamingoverlay:.

suppress():
  1. UseNexusForGameBarEnabled = 0 - the pad button no longer opens Game Bar (the same as the switch in Windows
     Settings -> Gaming -> Xbox Game Bar, which cannot be clicked when Game Bar is not installed)
  2. without Game Bar: the ms-gamebar / ms-gamebarservices / ms-gamingoverlay links get a silent handler in
     HKCU\\Software\\Classes (systray.exe - it does nothing on Windows 10/11), so the app chooser never appears.
     The keys carry a MARKER value - when Game Bar is installed again they are removed and the link opens Game Bar once more.
A setting that is already in effect is not written a second time. Result: a list of change descriptions (for the event log).
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
    """Import winreg lazily (it exists only on Windows), so the module still imports on Linux."""
    import winreg
    return winreg


def _subkeys(reg, root, path):
    """Generator over the names of the subkeys of root\\path; yields nothing when the key does not exist."""
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
    """Registry value `name` under root\\path, or None when the key or the value is missing."""
    try:
        with reg.OpenKey(root, path) as k:
            return reg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def gamebar_installed(reg):
    """Whether the Xbox Gaming Overlay package is registered for the current user (the package list is the source of truth)."""
    return any(n.startswith(PACKAGE_PREFIX) for n in _subkeys(reg, reg.HKEY_CURRENT_USER, PACKAGES))


def _ours(reg, scheme) -> bool:
    """Whether the handler of this URL scheme in HKCU was created by us (it carries the MARKER value)."""
    return _value(reg, reg.HKEY_CURRENT_USER, rf"{CLASSES}\{scheme}", MARKER) is not None


def _handled(reg, scheme) -> bool:
    """The link is handled by something other than our key (e.g. a classic program registered in HKLM / HKCU)."""
    try:
        reg.OpenKey(reg.HKEY_CLASSES_ROOT, scheme).Close()
    except OSError:
        return False
    return not _ours(reg, scheme)


def _register_noop(reg, scheme) -> None:
    """Register the silent no-op handler for a URL scheme: a protocol key whose open command runs systray.exe, marked with MARKER."""
    hk = reg.HKEY_CURRENT_USER
    with reg.CreateKey(hk, rf"{CLASSES}\{scheme}") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, f"URL:{scheme}")
        reg.SetValueEx(k, "URL Protocol", 0, reg.REG_SZ, "")
        reg.SetValueEx(k, "NoOpenWith", 0, reg.REG_SZ, "")
        reg.SetValueEx(k, MARKER, 0, reg.REG_SZ, "1")
    with reg.CreateKey(hk, rf"{CLASSES}\{scheme}\shell\open\command") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, f'"{NOOP}"')


def _remove(reg, scheme) -> None:
    """Delete our handler key of a URL scheme (children first, since the registry cannot delete a key that has subkeys)."""
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            reg.DeleteKey(reg.HKEY_CURRENT_USER, rf"{CLASSES}\{scheme}{sub}")
        except OSError:
            pass


def suppress(reg=None):
    """Apply both fixes (see the module docstring), writing only what is not already set -> list of change descriptions.

    With Game Bar installed, stale handlers left from an earlier run are removed; without it, a silent handler is
    registered for every scheme that nobody else handles. `reg` can be a winreg stand-in (tests).
    """
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
