r"""Receiver: update from GitHub (git pull) - the "Update" button in the window, the tray menu or update.bat.

update() -> Result:
  1. the repository folder (the parent of receiver/) must be a git clone - a downloaded ZIP has no history
  2. git pull --ff-only: only a fast-forward to the GitHub version; local changes in code files or own commits
     -> git's message, nothing is overwritten or merged. Without login windows (QUIET_ENV) and with a time limit - a pull
     from a background process could wait for an invisible Git Credential Manager window while the button hung on "Updating..."
  2a. code downloaded earlier but not started (a failed restart, a git pull from the console): the pull finds nothing new, but
     the running receiver is older than HEAD -> a restart as well (earlier: "You have the latest version" and the old code carried on)
  3. receiver/requirements.txt changed -> pip install -r (new libraries)
The user's settings (console.json, settings.json, remap.json, receiver_ui.ini) are in .gitignore - a pull does not touch them.
After the new code is downloaded (before the restart) a copy of settings.json / console.json / remap.json is made in receiver/backups/
(rx_backup, the last 5, restore in the window: Settings -> Backups) - the new version may fix them at startup.
After a successful update the window restarts the receiver (the new code only runs in a new process).

Running without a window: python receiver\rx_update.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import proto
import rx_backup
import rx_events as ev
from proto import UPD_DONE, UPD_FAILED, UPD_NO_CHANGE, UPD_NO_GIT, UPD_NONE, UPD_NOT_GIT, UPD_RUNNING
from rx_i18n import _

ROOT: Final = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS: Final = "receiver/requirements.txt"
NO_WINDOW: Final = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PULL_TIMEOUT_S: Final = 90            # git pull (network); longer = an error instead of a hanging button

PIP_TIMEOUT_S: Final = 600
FETCH_TIMEOUT_S: Final = 60
SHORT_HASH: Final = 7
# the percentages reported by update(): fixed stages (the window adds the sender wait above PCT_INSTALLED and 100 before the restart)
PCT_PULL, PCT_PULLED, PCT_BACKED_UP, PCT_INSTALLED = 5, 60, 75, 90


@dataclass(frozen=True)
class Result:
    """The outcome of an update."""

    ok: bool                  # no error (also "no changes")
    changed: bool             # new code downloaded - a restart is needed
    text: str                 # message for the window / console
    old: str = ""
    new: str = ""


# result of the sender update (the build packet) -> text in the window
SENDER_TEXT: Final = {
    UPD_NO_CHANGE: "nadajnik: najnowsza wersja",
    UPD_DONE: "nadajnik: zaktualizowany, uruchamia się od nowa",
    UPD_NOT_GIT: "nadajnik: folder na telefonie to nie klon git - na telefonie: "
                 "git clone https://github.com/matt400/dualsense-bridge i doas ./install-service.sh z nowego folderu",
    UPD_NO_GIT: "nadajnik: brak git na telefonie (doas apk add git)",
    UPD_FAILED: "nadajnik: git pull nie powiódł się (szczegóły w logu na telefonie)",   # an older sender without a reason
    UPD_RUNNING: "nadajnik nie odpowiedział",
}
_version: tuple[Any, ...] | None = None


def find_git() -> str | None:
    """Return the git executable (see proto.find_git) or None."""
    return proto.find_git()


def version() -> tuple[Any, ...]:
    """Return the receiver code version (commit, time, title) - computed once, the code changes only with a restart."""
    global _version
    if _version is None:
        _version = proto.git_version(ROOT)
    return _version


def running(root: str = ROOT) -> str:
    """Return the commit of the running code (from version(), computed at startup) - only for the receiver's own repository."""
    return version()[0] if root == ROOT else ""


def describe(commit: str, ts: float) -> str:
    """Describe a version as 'a1b2c3d · 12.10.2026' or "unknown" (a folder without git)."""
    if not commit:
        return _("nieznana")
    return f"{commit} · {time.strftime('%d.%m.%Y', time.localtime(ts))}" if ts else commit


def sender_state(build: Sequence[Any] | None, old_commit: str = "") -> int | None:
    """Return the sender update state from the build packet.

    After downloading code the sender restarts (os.execv) and the new process reports UPD_NONE - when the receiver
    updated for longer (e.g. pip install), the window saw only that state and wrote "the sender did not answer". A
    commit different from the one before the update (old_commit) = updated.
    """
    return None if build is None else _state_of(build, old_commit)


def _state_of(build: Sequence[Any], old_commit: str) -> int:
    """Return the update state of a sender that did send its build packet."""
    if old_commit and build[0] and build[0] != old_commit and build[2] in (UPD_NONE, UPD_RUNNING):
        return UPD_DONE
    return int(build[2])


def sender_text(build: Sequence[Any] | None, msg: str = "", old_commit: str = "") -> str:
    """Describe the sender update result from the build packet (None = a sender older than this feature).

    msg is the failure reason (updmsg); old_commit the sender commit before the update (see sender_state).
    """
    if build is None:
        return _("nadajnik w starej wersji - zaktualizuj go raz ręcznie (git pull na telefonie)")
    state = _state_of(build, old_commit)
    if state == UPD_FAILED and msg:
        text = _("nadajnik: git pull nie powiódł się: {msg}").format(msg=msg)
        if proto.is_auth_error(msg):
            text += " · " + _("usługa na telefonie nie ma dostępu do prywatnego repozytorium - na telefonie raz: "
                              "sender/setup-update-key.sh (klucz tylko do odczytu, instrukcja w skrypcie)")
        return text
    return _(SENDER_TEXT.get(state, "nadajnik nie odpowiedział"))


# ---------------- checking whether GitHub has a newer version (in the background, without downloading code) ----------------
CHECK_FIRST_S: Final = 20.0           # the first check after startup (pads and the window first)
CHECK_EVERY_S: Final = 30 * 60.0
# in the background git must not ask for anything: no Git Credential Manager login window and no terminal prompt
QUIET_ENV: Final = {"GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}


@dataclass(frozen=True)
class Available:
    """What a background check found: how far GitHub is ahead of the running version."""

    behind: int               # this many commits GitHub has beyond the receiver version (0 = up to date)
    subject: str = ""         # the title of the newest of them
    commit: str = ""          # its short hash (7 characters) - the window uses it to tell that this is a new update to report


available: Available | None = None               # the Available from the last successful check, or None (not checked / error)
# a background git fetch and a git pull from the button at the same time would fight over the same refs ("cannot lock ref ...") - the pull
# could
# fail. An update waits for a running check; a check skips its turn while an update is running.
_git_lock = threading.Lock()


def check_available(root: str = ROOT, git: str | None = None) -> Available | None:
    """Run git fetch (no file changes) and compare with the branch on GitHub.

    Returns Available, or None (no git / network / tracking branch - the window then simply shows nothing).
    """
    git = git or find_git()
    if git is None or not os.path.isdir(os.path.join(root, ".git")):
        return None
    if not _git_lock.acquire(blocking=False):
        return None                            # an update is running - we will check next time
    try:
        return _check(root, git)
    finally:
        _git_lock.release()


def _check(root: str, git: str) -> Available | None:
    """Fetch the upstream and count the commits between the running version and the upstream tip.

    Available(0) when up to date, None on any failure.
    """
    try:
        if proto.git(root, "fetch", "--quiet", git_exe=git, timeout=FETCH_TIMEOUT_S, env=QUIET_ENV).returncode:
            return None
        # from the RUNNING version, not from HEAD: code downloaded but not started is still an update to do
        p = proto.git(root, "rev-list", "--count", f"{running(root) or 'HEAD'}..@{{u}}", git_exe=git)
        if p.returncode:
            return None
        behind = int(p.stdout.strip() or 0)
        if not behind:
            return Available(0)
        head = proto.git(root, "log", "-1", "--format=%h%x00%s", f"--abbrev={SHORT_HASH}", "@{u}", git_exe=git).stdout.strip()
        commit, _sep, subject = head.partition("\0")
        return Available(behind, subject, commit[:SHORT_HASH])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _checker(stop: threading.Event) -> None:
    """Thread body: wait CHECK_FIRST_S, then check every CHECK_EVERY_S.

    Store the result in `available` and log a newly found update once.
    """
    global available
    if stop.wait(CHECK_FIRST_S):
        return
    while True:
        r = check_available()
        if r is not None:
            if r != available and r.behind:
                print(f"Update available: {r.behind} change(s), latest {r.commit} {r.subject}")
                ev.info(_("Dostępna aktualizacja - nowych zmian: {n}").format(n=r.behind) + (f" - {r.subject}" if r.subject else ""))
            available = r
        if stop.wait(CHECK_EVERY_S):
            return


def start_checker(stop: threading.Event) -> None:
    """A background thread: checks GitHub every CHECK_EVERY_S, the result in rx_update.available (the window reads it every 400 ms)."""
    threading.Thread(target=_checker, args=(stop,), name="update check", daemon=True).start()


def update(root: str = ROOT, git: str | None = None, pip: bool = True, progress: Callable[[int], None] | None = None) -> Result:
    """Update the code: git pull --ff-only, then a settings backup and (when requirements changed) pip install.

    Also covers code that is already on disk but not yet running (see the module docstring, 2a). The git lock keeps the pull from
    colliding with the background check. progress(percent) is called at the stages (pull, backup, pip) from this thread -
    the window shows it next to the button label; the stages are fixed steps, not a measurement of the download.
    """
    report = progress or (lambda _pct: None)
    if not os.path.isdir(os.path.join(root, ".git")):
        return Result(False, False, _("Folder nie jest repozytorium git (pobrany jako ZIP?) - sklonuj: "
                                      "git clone https://github.com/matt400/dualsense-bridge"))
    git = git or find_git()
    if git is None:
        return Result(False, False, _("Nie znaleziono git - zainstaluj Git for Windows (git-scm.com)"))
    report(PCT_PULL)
    with _git_lock:                            # not during a background check of GitHub
        state, msg, old, new, files = proto.git_pull(root, git, env=QUIET_ENV, timeout=PULL_TIMEOUT_S)
    report(PCT_PULLED)
    if state == UPD_FAILED:
        return Result(False, False, _("git pull nie powiódł się: {msg}").format(msg=msg), old, old)
    cur = running(root)
    if state != UPD_DONE:
        if not (cur and new and not new.startswith(cur)):
            return Result(True, False, _("Masz najnowszą wersję"), old, new)
        # the new code is already on disk but the old one is running (e.g. the previous restart failed) - start the new one
        old = proto.git(root, "rev-parse", cur, git_exe=git).stdout.strip() or cur
        files = proto.git(root, "diff", "--name-only", old, new, git_exe=git).stdout.splitlines()
        print(f"Update: code {new[:SHORT_HASH]} already downloaded, running {cur} - restart needed")
    count = proto.git(root, "rev-list", "--count", f"{old}..{new}", git_exe=git).stdout.strip() or "?"
    text = _("Zaktualizowano ({n} zmian): {old} → {new}").format(n=count, old=old[:SHORT_HASH], new=new[:SHORT_HASH])
    text += " · " + _backup(old, new)
    report(PCT_BACKED_UP)
    if pip and REQUIREMENTS in files:
        text += " · " + _install_requirements(root)
    report(PCT_INSTALLED)
    return Result(True, True, text, old, new)


def _backup(old: str, new: str) -> str:
    """Copy the settings after the new code is downloaded (before we start it); a failed copy does not undo the update."""
    try:
        b = rx_backup.create("update", old, new)
    except OSError as e:
        print(f"Settings backup failed: {e!r}")
        ev.error(_("kopia ustawień nieudana: {err}").format(err=repr(e)))
        return _("kopia ustawień nieudana: {err}").format(err=e)
    if b is None:
        return _("brak ustawień do skopiowania")
    print(f"Settings backup: {b.name} ({', '.join(b.files)})")
    return _("kopia ustawień zapisana")


def _run(cmd: list[str], root: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run a command in `root` with captured output and a timeout, without a console window."""
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW, check=False)


def _last_lines(text: str, n: int = 3) -> str:
    """Return the last n non-empty lines of a text joined with " / " (the useful end of a pip error)."""
    lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
    return " / ".join(lines[-n:])


def _install_requirements(root: str) -> str:
    """Run pip install -r requirements.txt with the receiver's Python; return a message (success, or what to run by hand)."""
    try:
        p = _run([sys.executable, "-m", "pip", "install", "-r", os.path.join(root, *REQUIREMENTS.split("/"))], root, PIP_TIMEOUT_S)
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
