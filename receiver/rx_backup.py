"""Receiver: copies of the user's settings made on update (receiver/backups/), with restoring from the window.

The settings files (settings.json, console.json, remap.json, padcal.json) are in .gitignore, so git pull does not touch them - but the new
code version may fix them at startup (migration, validation). A copy is made after the new code has been downloaded
successfully, before the receiver restarts, i.e. exactly in the version written by the old code. The last KEEP are kept.

  backups/20261012-134501_a1b2c3d/   settings.json  console.json  remap.json  meta.json
                                     meta.json: {"time", "commit" (old code), "new" (new code), "reason"}

Restoring copies the files from the backup over the current ones (atomically, file by file) and first makes a copy of the
current ones (reason "restore") - a restore can be undone as well. Files that are not in the backup are left alone. Then the
window restarts the receiver (the settings are read at startup).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from typing import Any, Final

from proto import write_file, write_json
from rx_config import C
from rx_i18n import _

KEEP: Final = 5
DIR_NAME: Final = "backups"
META: Final = "meta.json"
SHORT_COMMIT: Final = 7          # the length of an abbreviated git commit
REASONS: Final = ("update", "restore", "manual")

HERE: Final = os.path.dirname(os.path.abspath(__file__))


def files() -> dict[str, str]:
    """Return {name in the backup: path of the current file}.

    Paths come from the configuration (remap_file can be changed); without a configuration (update.bat: rx_update run
    without starting the receiver) the defaults next to the script are used.
    """
    def path(attr: str, name: str) -> str:
        return getattr(C, attr, None) or os.path.join(HERE, name)
    remap = path("REMAP_PATH", "remap.json")
    return {"settings.json": path("SETTINGS_PATH", "settings.json"), "console.json": path("CONSOLE_PATH", "console.json"),
            "remap.json": remap, "padcal.json": os.path.join(os.path.dirname(remap), "padcal.json")}


def backup_dir() -> str:
    """Return the backups folder, next to settings.json."""
    return os.path.join(os.path.dirname(files()["settings.json"]), DIR_NAME)


@dataclass(frozen=True, slots=True)
class Backup:
    """One backup folder: when, from which code version and why it was made, and which files it holds."""

    name: str              # folder name (also the key when restoring)
    time: float            # unix time
    commit: str            # version of the code that wrote these files ("" = unknown)
    new: str               # version after the update ("" = not applicable)
    reason: str            # update | restore | manual
    files: tuple[str, ...]  # names of the files in the backup


def create(reason: str = "update", commit: str = "", new: str = "") -> Backup | None:
    """Copy the current settings files into a new backup folder named "<timestamp>[_<commit>]".

    Returns the Backup, or None when none of the files exists yet. Backups beyond the newest KEEP are deleted.
    """
    present = {n: p for n, p in files().items() if os.path.isfile(p)}
    if not present:
        return None
    now = time.time()
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + (f"_{commit[:SHORT_COMMIT]}" if commit else "")
    root = backup_dir()
    path, n = os.path.join(root, base), 1
    while os.path.exists(path):                  # two backups in the same second (e.g. a restore right after an update)
        n += 1
        path = os.path.join(root, f"{base}-{n}")
    os.makedirs(path)
    for name, src in present.items():
        shutil.copy2(src, os.path.join(path, name))
    meta = {"time": now, "commit": commit[:SHORT_COMMIT], "new": new[:SHORT_COMMIT], "reason": reason if reason in REASONS else "manual"}
    write_json(os.path.join(path, META), meta, indent=2)
    prune()
    return _read(path)


def _read(path: str) -> Backup | None:
    """Read a backup folder into a Backup (a damaged or missing meta.json is tolerated); None when it holds no known file."""
    try:
        with open(os.path.join(path, META), encoding="utf-8") as f:
            loaded = json.load(f)
        meta: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
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


def list_backups() -> list[Backup]:
    """Return the backups, newest first. A folder without any known file (e.g. emptied by hand) is skipped."""
    root = backup_dir()
    try:
        entries = [e.path for e in os.scandir(root) if e.is_dir()]
    except OSError:
        return []
    out = [b for b in map(_read, entries) if b is not None]
    return sorted(out, key=lambda b: (b.time, b.name), reverse=True)


def prune(keep: int = KEEP) -> None:
    """Delete all but the newest `keep` backups."""
    for b in list_backups()[keep:]:
        shutil.rmtree(os.path.join(backup_dir(), b.name), ignore_errors=True)


def restore(name: str, commit: str = "") -> tuple[bool, str]:
    """Restore backup `name` over the current files (a backup of the current ones is made first); returns (ok, message)."""
    root = os.path.realpath(backup_dir())
    path = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(path) != root or not os.path.isdir(path):         # only a folder directly in backups/, no ../ tricks
        return False, _("Nie ma takiej kopii")
    b = _read(path)
    if b is None:
        return False, _("Kopia jest pusta")
    try:
        data: dict[str, bytes] = {}
        for n in b.files:                        # read into memory first: the "restore" backup may prune the oldest one (this one)
            with open(os.path.join(path, n), "rb") as f:
                data[n] = f.read()
        create("restore", commit)
        for n, raw in data.items():
            write_file(files()[n], raw)
    except OSError as e:
        return False, _("Nie udało się przywrócić kopii: {err}").format(err=e)
    return True, _("Przywrócono kopię z {when} - receiver uruchomi się od nowa").format(when=describe_time(b.time))


def describe_time(t: float) -> str:
    """Format the backup time as "dd.mm.yyyy HH:MM" in local time, "?" when unknown."""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(t)) if t else "?"
