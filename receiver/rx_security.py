"""Receiver: kontrola ustawień bezpieczeństwa - ostrzeżenia w dzienniku przy starcie i karta Ustawienia -> Bezpieczeństwo.

  issues()       lista problemów (Issue): brak klucza HMAC, serwer DSU dostępny z sieci, folder receivera albo Pythona
                 zapisywalny bez uprawnień administratora (receiver działa jako administrator - zwykły program mógłby
                 podmienić kod, który zadanie autostartu uruchamia z najwyższymi uprawnieniami)
  log_issues()   ostrzeżenia w dzienniku zdarzeń (start receivera)
  harden()       folder receivera tylko do zapisu dla administratorów (rx_winapi.harden_dir)

Szczegóły i zalecenia: docs/SECURITY.md.
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
    text: str        # opis dla użytkownika (przetłumaczony)


def code_dir():
    return os.path.dirname(os.path.abspath(__file__))


def python_dir():
    return os.path.dirname(os.path.abspath(sys.executable))


def _winapi():
    if os.name != "nt":
        return None
    import rx_winapi
    return rx_winapi


def issues():
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


def log_issues():
    import rx_events as ev
    for i in issues():
        print(f"Security: {i.code}")
        ev.warn(i.text)


def harden():
    """-> (ok, komunikat). Tylko Windows i receiver jako administrator."""
    w = _winapi()
    if w is None or not w.is_admin():
        return False, _("Potrzebne uprawnienia administratora")
    ok, err = w.harden_dir(code_dir())
    if ok:
        print(f"Security: {code_dir()} is now writable only by administrators")
        return True, _("Folder zabezpieczony - zapis tylko dla administratorów")
    print(f"Security: hardening {code_dir()} failed: {err}")
    return False, _("Nie udało się zabezpieczyć folderu: {err}").format(err=err)
