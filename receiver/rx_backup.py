"""Receiver: kopie ustawień użytkownika przy aktualizacji (receiver/backups/), z przywracaniem w oknie.

Pliki ustawień (settings.json, console.json, remap.json, padcal.json) są w .gitignore, więc git pull ich nie rusza - ale nowa wersja
kodu przy starcie może je poprawić (migracja, walidacja). Kopia powstaje po udanym pobraniu nowego kodu, zanim
receiver uruchomi się od nowa, czyli dokładnie w wersji zapisanej przez stary kod. Trzymamy KEEP ostatnich.

  backups/20261012-134501_a1b2c3d/   settings.json  console.json  remap.json  meta.json
                                     meta.json: {"time", "commit" (stary kod), "new" (nowy kod), "reason"}

Przywrócenie kopiuje pliki z kopii na miejsce bieżących (atomowo, plik po pliku), a wcześniej robi kopię bieżących
(reason "restore") - przywrócenie też da się cofnąć. Plików, których w kopii nie ma, nie rusza. Potem okno
restartuje receiver (ustawienia są czytane przy starcie).
"""
import json
import os
import shutil
import time
from dataclasses import dataclass

from rx_config import C

KEEP = 5
DIR_NAME = "backups"
META = "meta.json"
REASONS = ("update", "restore", "manual")


HERE = os.path.dirname(os.path.abspath(__file__))


def files():
    """{nazwa w kopii: ścieżka bieżącego pliku} - ścieżki z konfiguracji (remap_file da się zmienić); bez konfiguracji
    (update.bat: rx_update bez startu receivera) - domyślne obok skryptu."""
    def path(attr, name):
        return getattr(C, attr, None) or os.path.join(HERE, name)
    remap = path("REMAP_PATH", "remap.json")
    return {"settings.json": path("SETTINGS_PATH", "settings.json"), "console.json": path("CONSOLE_PATH", "console.json"),
            "remap.json": remap, "padcal.json": os.path.join(os.path.dirname(remap), "padcal.json")}


def backup_dir():
    return os.path.join(os.path.dirname(files()["settings.json"]), DIR_NAME)


@dataclass(frozen=True, slots=True)
class Backup:
    name: str              # nazwa folderu (też klucz przy przywracaniu)
    time: float            # unix
    commit: str            # wersja kodu, który zapisał te pliki ("" = nieznana)
    new: str               # wersja po aktualizacji ("" = nie dotyczy)
    reason: str            # update | restore | manual
    files: tuple           # nazwy plików w kopii


def create(reason="update", commit="", new=""):
    """Kopia bieżących plików ustawień. -> Backup albo None (żadnego pliku jeszcze nie ma). Starsze niż KEEP znikają."""
    present = {n: p for n, p in files().items() if os.path.isfile(p)}
    if not present:
        return None
    now = time.time()
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + (f"_{commit[:7]}" if commit else "")
    root = backup_dir()
    path, n = os.path.join(root, base), 1
    while os.path.exists(path):                  # dwie kopie w tej samej sekundzie (np. restore zaraz po update)
        n += 1
        path = os.path.join(root, f"{base}-{n}")
    os.makedirs(path)
    for name, src in present.items():
        shutil.copy2(src, os.path.join(path, name))
    meta = {"time": now, "commit": commit[:7], "new": new[:7], "reason": reason if reason in REASONS else "manual"}
    with open(os.path.join(path, META), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    prune()
    return _read(path)


def _read(path):
    try:
        with open(os.path.join(path, META), encoding="utf-8") as f:
            meta = json.load(f)
        meta = meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        meta = {}
    known = files()
    names = tuple(n for n in known if os.path.isfile(os.path.join(path, n)))
    if not names:
        return None
    try:
        t = float(meta.get("time") or os.path.getmtime(path))
    except (TypeError, ValueError, OSError):
        t = 0.0
    return Backup(os.path.basename(path), t, str(meta.get("commit") or ""), str(meta.get("new") or ""),
                  str(meta.get("reason") or "manual"), names)


def list_backups():
    """Kopie od najnowszej. Folder bez znanych plików (np. ręcznie wyczyszczony) jest pomijany."""
    root = backup_dir()
    try:
        entries = [e.path for e in os.scandir(root) if e.is_dir()]
    except OSError:
        return []
    out = [b for b in map(_read, entries) if b is not None]
    return sorted(out, key=lambda b: (b.time, b.name), reverse=True)


def prune(keep=KEEP):
    for b in list_backups()[keep:]:
        shutil.rmtree(os.path.join(backup_dir(), b.name), ignore_errors=True)


def restore(name, commit=""):
    """Przywraca kopię `name` (najpierw kopia bieżących plików). -> (ok, komunikat)."""
    from rx_i18n import _
    root = os.path.realpath(backup_dir())
    path = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(path) != root or not os.path.isdir(path):         # tylko folder z backups/, bez ../
        return False, _("Nie ma takiej kopii")
    b = _read(path)
    if b is None:
        return False, _("Kopia jest pusta")
    try:
        data = {}
        for n in b.files:                        # najpierw do pamięci: kopia "restore" może usunąć najstarszą (tę)
            with open(os.path.join(path, n), "rb") as f:
                data[n] = f.read()
        create("restore", commit)
        for n, raw in data.items():
            dst = files()[n]
            tmp = dst + ".tmp"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, dst)
    except OSError as e:
        return False, _("Nie udało się przywrócić kopii: {err}").format(err=e)
    return True, _("Przywrócono kopię z {when} - receiver uruchomi się od nowa").format(when=describe_time(b.time))


def describe_time(t):
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(t)) if t else "?"
