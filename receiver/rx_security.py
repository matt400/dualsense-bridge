"""Receiver: security settings check - warnings in the log at startup and the Settings -> Security card.

  issues()       list of problems (Issue): no HMAC key, DSU server reachable from the network, receiver or Python folder
                 writable without administrator rights (the receiver runs as administrator - an ordinary program could
                 replace code that the autostart task launches with the highest privileges)
  log_issues()   warnings in the event log (receiver startup)
  harden()       make the receiver folder writable by administrators only (rx_winapi.harden_dir)

Details and recommendations: docs/SECURITY.md.
"""
import os
import sys
from dataclasses import dataclass

from rx_config import C
from rx_i18n import _

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True, slots=True)
class Issue:
    code: str        # key / dsu / folder / python
    text: str        # user-facing description (translated)


def code_dir():
    """Folder holding the receiver's source files."""
    return os.path.dirname(os.path.abspath(__file__))


def python_dir():
    """Folder of the running Python interpreter."""
    return os.path.dirname(os.path.abspath(sys.executable))


def _winapi():
    """rx_winapi on Windows, None elsewhere (the ownership checks only make sense on Windows)."""
    if os.name != "nt":
        return None
    import rx_winapi
    return rx_winapi


def issues():
    """Collect the current security problems. Key and DSU checks work everywhere; the folder and Python ownership checks
    run only on Windows when the receiver is elevated (admin_owned() returns None when it cannot tell, which is not reported).
    """
    out = []
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
        if w.admin_owned(code_dir()) is False:
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


def harden():
    """Restrict write access to the receiver folder to administrators -> (ok, message). Windows with an elevated receiver only."""
    w = _winapi()
    if w is None or not w.is_admin():
        return False, _("Potrzebne uprawnienia administratora")
    ok, err = w.harden_dir(code_dir())
    if ok:
        print(f"Security: {code_dir()} is now writable only by administrators")
        return True, _("Folder zabezpieczony - zapis tylko dla administratorów")
    print(f"Security: hardening {code_dir()} failed: {err}")
    return False, _("Nie udało się zabezpieczyć folderu: {err}").format(err=err)
