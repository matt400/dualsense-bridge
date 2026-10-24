"""Receiver: security settings check - warnings in the log at startup and the Settings -> Security card.

  issues()       list of problems (Issue): no HMAC key, DSU server reachable from the network, receiver or Python folder
                 writable without administrator rights (the receiver runs as administrator - an ordinary program could
                 replace code that the autostart task launches with the highest privileges)
  log_issues()   warnings in the event log (receiver startup)
  harden()       make the receiver folder - and, in a git checkout, the whole repository with its .git - writable by
                 administrators only (rx_winapi.harden_dir). The updater runs git as administrator in the repository root:
                 a user-writable .git (hooks, config) would be code that runs as administrator at the next fetch.

Details and recommendations: docs/SECURITY.md.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Final

from rx_config import C
from rx_i18n import _

LOCAL_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class Issue:
    """One security problem: a stable code for tests and the log, and the text shown in the window."""

    code: str        # key / dsu / folder / python
    text: str        # user-facing description (translated)


def code_dir() -> str:
    """Folder holding the receiver's source files."""
    return os.path.dirname(os.path.abspath(__file__))


def repo_root() -> str | None:
    """Return the git repository root (the parent of the code folder), or None when the receiver is not in a checkout.

    The update runs git as administrator in this folder (proto.git, cwd=root), so the root and its .git belong to the
    code that has to be protected - not only receiver/. A copy without .git (a zip) has no such parent to protect: its
    parent folder is just some folder (e.g. Downloads) and must not be touched.
    """
    root = os.path.dirname(code_dir())
    return root if os.path.exists(os.path.join(root, ".git")) else None


def protected_paths() -> list[str]:
    """Return the paths that must not be writable by a regular user: the repository root and its .git, else the code folder."""
    root = repo_root()
    return [root, os.path.join(root, ".git")] if root else [code_dir()]


def python_dir() -> str:
    """Folder of the running Python interpreter."""
    return os.path.dirname(os.path.abspath(sys.executable))


def _winapi() -> ModuleType | None:
    """Return rx_winapi on Windows, None elsewhere (the ownership checks only make sense on Windows)."""
    if os.name != "nt":
        return None
    import rx_winapi
    return rx_winapi


def issues() -> list[Issue]:
    """Collect the current security problems.

    Key and DSU checks work everywhere; the folder and Python ownership checks run only on Windows when the receiver is
    elevated (admin_owned() returns None when it cannot tell, which is not reported).
    """
    out: list[Issue] = []
    if not C.KEY:
        out.append(Issue("key", _("Brak klucza HMAC: każdy w sieci lokalnej może sterować tym komputerem przez receiver "
                                  "(mysz, akcje pada master) i wysyłać polecenia do nadajnika. Ustaw ten sam klucz (key) "
                                  "w receiverze i w nadajniku.")))
    if getattr(C, "DSU_BIND", "127.0.0.1") not in LOCAL_HOSTS:
        out.append(Issue("dsu", _("Serwer DSU jest dostępny z sieci (dsu_bind): każdy w sieci lokalnej widzi ruchy "
                                  "i przyciski padów. Ustaw dsu_bind = \"127.0.0.1\", jeśli emulator działa na tym "
                                  "komputerze.")))
    w = _winapi()
    if w is not None and w.is_admin():
        if any(w.admin_owned(path) is False for path in protected_paths()):
            out.append(Issue("folder", _("Folder receivera należy do zwykłego użytkownika: dowolny program uruchomiony "
                                         "bez uprawnień może podmienić kod, który receiver wykonuje jako "
                                         "administrator. Użyj „Zabezpiecz folder”.")))
        if w.admin_owned(python_dir()) is False:
            out.append(Issue("python", _("Python jest zainstalowany tylko dla tego użytkownika ({dir}) - jego pliki "
                                         "też można podmienić bez uprawnień. Zainstaluj Pythona dla wszystkich "
                                         "użytkowników (Program Files).").format(dir=python_dir())))
    return out


def log_issues() -> None:
    """Print every current issue and add it to the event log as a warning (called once at receiver startup)."""
    import rx_events as ev
    for i in issues():
        print(f"Security: {i.code}")
        ev.warn(i.text)


def harden() -> tuple[bool, str]:
    """Restrict write access to the receiver folder (in a git checkout: the whole repository) to administrators.

    Returns (ok, message). Works on Windows with an elevated receiver only.
    """
    w = _winapi()
    if w is None or not w.is_admin():
        return False, _("Potrzebne uprawnienia administratora")
    target = repo_root() or code_dir()           # the root's /T pass covers receiver/ and .git too
    ok, err = w.harden_dir(target)
    if ok:
        print(f"Security: {target} is now writable only by administrators")
        return True, _("Folder zabezpieczony - zapis tylko dla administratorów")
    print(f"Security: hardening {target} failed: {err}")
    return False, _("Nie udało się zabezpieczyć folderu: {err}").format(err=err)
