"""Receiver: aktualizacja z GitHuba (git pull) - przycisk "Aktualizuj" w oknie, menu zasobnika albo update.bat.

update() -> Result:
  1. folder repozytorium (nadrzędny dla receiver/) musi być klonem git - pobrany ZIP nie ma historii
  2. git pull --ff-only: tylko "przewinięcie" do wersji z GitHuba; lokalne zmiany w plikach kodu albo własne commity
     -> komunikat gita, nic nie jest nadpisywane ani scalane
  3. zmienił się receiver/requirements.txt -> pip install -r (nowe biblioteki)
Ustawienia użytkownika (console.json, settings.json, remap.json, receiver_ui.ini) są w .gitignore - pull ich nie rusza.
Po udanej aktualizacji okno robi restart receivera (nowy kod działa dopiero w nowym procesie).

Uruchomienie bez okna: python receiver\\rx_update.py
"""
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from rx_i18n import _

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = "receiver/requirements.txt"
GIT_PATHS = (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Result:
    ok: bool                  # bez błędu (także "brak zmian")
    changed: bool             # pobrano nowy kod - potrzebny restart
    text: str                 # komunikat do okna / konsoli
    old: str = ""
    new: str = ""


def find_git():
    if found := shutil.which("git"):
        return found
    local = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")
    return next((p for p in (*GIT_PATHS, local) if os.path.isfile(p)), None)


def _run(cmd, root, timeout):
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW)


def _last_lines(text, n=3):
    lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
    return " / ".join(lines[-n:])


def update(root=ROOT, git=None, pip=True):
    if not os.path.isdir(os.path.join(root, ".git")):
        return Result(False, False, _("Folder nie jest repozytorium git (pobrany jako ZIP?) - sklonuj: "
                                      "git clone https://github.com/matt400/dualsense-bridge"))
    git = git or find_git()
    if git is None:
        return Result(False, False, _("Nie znaleziono git - zainstaluj Git for Windows (git-scm.com)"))
    try:
        old = _run([git, "rev-parse", "HEAD"], root, 15).stdout.strip()
        p = _run([git, "pull", "--ff-only"], root, 120)
        if p.returncode:
            return Result(False, False, _("git pull nie powiódł się: {msg}").format(msg=_last_lines(p.stderr or p.stdout)),
                          old, old)
        new = _run([git, "rev-parse", "HEAD"], root, 15).stdout.strip()
        if new == old:
            return Result(True, False, _("Masz najnowszą wersję"), old, new)
        count = _run([git, "rev-list", "--count", f"{old}..{new}"], root, 15).stdout.strip() or "?"
        files = _run([git, "diff", "--name-only", old, new], root, 15).stdout.split()
    except (OSError, subprocess.TimeoutExpired) as e:
        return Result(False, False, _("git nie odpowiada: {msg}").format(msg=e))
    text = _("Zaktualizowano ({n} zmian): {old} → {new}").format(n=count, old=old[:7], new=new[:7])
    if pip and REQUIREMENTS in files:
        text += " · " + _install_requirements(root)
    return Result(True, True, text, old, new)


def _install_requirements(root):
    try:
        p = _run([sys.executable, "-m", "pip", "install", "-r", os.path.join(root, *REQUIREMENTS.split("/"))], root, 600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _("pip nie zadziałał ({msg}) - uruchom: pip install -r receiver\\requirements.txt").format(msg=e)
    if p.returncode:
        return _("pip: {msg}").format(msg=_last_lines(p.stderr or p.stdout, 2))
    return _("nowe biblioteki zainstalowane")


if __name__ == "__main__":
    r = update()
    print(r.text)
    if r.changed:
        print(_("Uruchom ponownie receiver (przycisk Restart w oknie), żeby działał nowy kod."))
    sys.exit(0 if r.ok else 1)
