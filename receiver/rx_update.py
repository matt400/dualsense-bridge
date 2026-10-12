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
import threading
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
    UPD_FAILED: "nadajnik: git pull nie powiódł się (szczegóły w logu na telefonie)",   # starszy nadajnik bez powodu
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


def sender_text(build, msg=""):
    """Wynik aktualizacji nadajnika z pakietu build (None = nadajnik sprzed tej funkcji); msg = powód błędu (updmsg)."""
    if build is None:
        return _("nadajnik w starej wersji - zaktualizuj go raz ręcznie (git pull na telefonie)")
    if build[2] == UPD_FAILED and msg:
        text = _("nadajnik: git pull nie powiódł się: {msg}").format(msg=msg)
        if proto.is_auth_error(msg):
            text += " · " + _("usługa na telefonie nie ma dostępu do prywatnego repozytorium - na telefonie raz: "
                              "sender/setup-update-key.sh (klucz tylko do odczytu, instrukcja w skrypcie)")
        return text
    return _(SENDER_TEXT.get(build[2], "nadajnik nie odpowiedział"))


# ---------------- sprawdzanie, czy na GitHubie jest nowsza wersja (w tle, bez pobierania kodu) ----------------
CHECK_FIRST_S = 20.0           # pierwsze sprawdzenie po starcie (najpierw pady i okno)
CHECK_EVERY_S = 30 * 60.0
# w tle git nie może o nic pytać: bez okna logowania Git Credential Manager i bez pytania w terminalu
QUIET_ENV = {"GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}


@dataclass(frozen=True)
class Available:
    behind: int               # tyle commitów GitHub ma ponad wersję receivera (0 = aktualna)
    subject: str = ""         # tytuł najnowszego z nich
    commit: str = ""          # jego skrót (7 znaków) - po nim okno poznaje, że to nowa aktualizacja do zgłoszenia


available = None               # Available z ostatniego udanego sprawdzenia albo None (nie sprawdzono / błąd)
# git fetch w tle i git pull z przycisku naraz walczyłyby o te same referencje ("cannot lock ref ...") - pull mógł
# się nie udać. Aktualizacja czeka na trwające sprawdzenie, sprawdzenie pomija kolejkę, gdy trwa aktualizacja.
_git_lock = threading.Lock()


def check_available(root=ROOT, git=None):
    """git fetch (bez zmiany plików) i porównanie z gałęzią na GitHubie. -> Available albo None (brak gita / sieci /
    gałęzi śledzącej - wtedy okno po prostu nic nie pokazuje)."""
    git = git or find_git()
    if git is None or not os.path.isdir(os.path.join(root, ".git")):
        return None
    if not _git_lock.acquire(blocking=False):
        return None                            # trwa aktualizacja - sprawdzimy następnym razem
    try:
        return _check(root, git)
    finally:
        _git_lock.release()


def _check(root, git):
    try:
        if proto.git(root, "fetch", "--quiet", git_exe=git, timeout=60, env=QUIET_ENV).returncode:
            return None
        p = proto.git(root, "rev-list", "--count", "HEAD..@{u}", git_exe=git)
        if p.returncode:
            return None
        behind = int(p.stdout.strip() or 0)
        if not behind:
            return Available(0)
        head = proto.git(root, "log", "-1", "--format=%h%x00%s", "--abbrev=7", "@{u}", git_exe=git).stdout.strip()
        commit, _sep, subject = head.partition("\0")
        return Available(behind, subject, commit[:7])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _checker(stop):
    global available
    if stop.wait(CHECK_FIRST_S):
        return
    while True:
        r = check_available()
        if r is not None:
            if r != available and r.behind:
                print(f"Update available: {r.behind} change(s), latest {r.commit} {r.subject}")
            available = r
        if stop.wait(CHECK_EVERY_S):
            return


def start_checker(stop):
    """Wątek w tle: co CHECK_EVERY_S sprawdza GitHuba, wynik w rx_update.available (okno odczytuje go co 400 ms)."""
    import threading
    threading.Thread(target=_checker, args=(stop,), name="update check", daemon=True).start()


def update(root=ROOT, git=None, pip=True):
    if not os.path.isdir(os.path.join(root, ".git")):
        return Result(False, False, _("Folder nie jest repozytorium git (pobrany jako ZIP?) - sklonuj: "
                                      "git clone https://github.com/matt400/dualsense-bridge"))
    git = git or find_git()
    if git is None:
        return Result(False, False, _("Nie znaleziono git - zainstaluj Git for Windows (git-scm.com)"))
    with _git_lock:                            # nie w trakcie sprawdzania GitHuba w tle
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
