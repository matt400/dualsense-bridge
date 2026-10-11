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
import subprocess
import sys
import time
from dataclasses import dataclass

import proto
from proto import UPD_DONE, UPD_FAILED, UPD_NO_CHANGE, UPD_NO_GIT, UPD_NOT_GIT, UPD_RUNNING
from rx_i18n import _

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = "receiver/requirements.txt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Result:
    ok: bool                  # bez błędu (także "brak zmian")
    changed: bool             # pobrano nowy kod - potrzebny restart
    text: str                 # komunikat do okna / konsoli
    old: str = ""
    new: str = ""


# wynik aktualizacji nadajnika (pakiet build) -> tekst w oknie
SENDER_TEXT = {
    UPD_NO_CHANGE: "nadajnik: najnowsza wersja",
    UPD_DONE: "nadajnik: zaktualizowany, uruchamia się od nowa",
    UPD_NOT_GIT: "nadajnik: folder na telefonie to nie klon git - na telefonie: "
                 "git clone https://github.com/matt400/dualsense-bridge i doas ./install-service.sh z nowego folderu",
    UPD_NO_GIT: "nadajnik: brak git na telefonie (doas apk add git)",
    UPD_FAILED: "nadajnik: git pull nie powiódł się (szczegóły w logu na telefonie)",
    UPD_RUNNING: "nadajnik nie odpowiedział",
}
_version = None


def find_git():
    return proto.find_git()


def version():
    """Wersja kodu receivera (commit, czas, tytuł) - liczona raz, kod zmienia się tylko z restartem."""
    global _version
    if _version is None:
        _version = proto.git_version(ROOT)
    return _version


def describe(commit, ts):
    """'a1b2c3d · 12.10.2026' albo 'nieznana' (folder bez gita)."""
    if not commit:
        return _("nieznana")
    return f"{commit} · {time.strftime('%d.%m.%Y', time.localtime(ts))}" if ts else commit


def sender_text(build):
    """Wynik aktualizacji nadajnika z pakietu build (None = nadajnik sprzed tej funkcji)."""
    if build is None:
        return _("nadajnik w starej wersji - zaktualizuj go raz ręcznie (git pull na telefonie)")
    return _(SENDER_TEXT.get(build[2], "nadajnik nie odpowiedział"))


def update(root=ROOT, git=None, pip=True):
    if not os.path.isdir(os.path.join(root, ".git")):
        return Result(False, False, _("Folder nie jest repozytorium git (pobrany jako ZIP?) - sklonuj: "
                                      "git clone https://github.com/matt400/dualsense-bridge"))
    git = git or find_git()
    if git is None:
        return Result(False, False, _("Nie znaleziono git - zainstaluj Git for Windows (git-scm.com)"))
    state, msg, old, new, files = proto.git_pull(root, git)
    if state == UPD_FAILED:
        return Result(False, False, _("git pull nie powiódł się: {msg}").format(msg=msg), old, old)
    if state != UPD_DONE:
        return Result(True, False, _("Masz najnowszą wersję"), old, new)
    count = proto.git(root, "rev-list", "--count", f"{old}..{new}", git_exe=git).stdout.strip() or "?"
    text = _("Zaktualizowano ({n} zmian): {old} → {new}").format(n=count, old=old[:7], new=new[:7])
    if pip and REQUIREMENTS in files:
        text += " · " + _install_requirements(root)
    return Result(True, True, text, old, new)


def _run(cmd, root, timeout):
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW)


def _last_lines(text, n=3):
    lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
    return " / ".join(lines[-n:])


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
