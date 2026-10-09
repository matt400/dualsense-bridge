"""Receiver: urządzenia audio Windows (pycaw): lista aktywnych wyjść i ustawianie domyślnego.

pycaw / comtypes są importowane dopiero przy użyciu - bez nich receiver działa, tylko bez przełączania dźwięku.
Każda funkcja sama inicjuje COM w wątku wołającym, więc można ich używać z dowolnego wątku.
"""
import contextlib
import time

ROLES = ("eConsole", "eMultimedia", "eCommunications")   # tak samo jak "Ustaw jako domyślne" w ustawieniach dźwięku


@contextlib.contextmanager
def _com():
    import comtypes
    try:
        comtypes.CoInitialize()
        owned = True
    except OSError:          # wątek ma już COM w innym trybie (np. wątek Qt) - działamy na istniejącym
        owned = False
    try:
        yield
    finally:
        if owned:
            comtypes.CoUninitialize()


def list_outputs():
    """Aktywne urządzenia wyjściowe: [(id, nazwa)] w kolejności systemu."""
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.utils import AudioUtilities
    with _com():
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        return [(d.id, d.FriendlyName) for d in devices if d.FriendlyName]


def find(name, devices):
    """Id urządzenia po nazwie: dokładnie (bez wielkości liter), a gdy brak - jedyne urządzenie zawierające tekst."""
    want = name.casefold()
    exact = [dev_id for dev_id, n in devices if n.casefold() == want]
    if exact:
        return exact[0]
    partial = [dev_id for dev_id, n in devices if want in n.casefold()]
    return partial[0] if len(partial) == 1 else None


def set_default(name, wait_s=6.0):
    """Domyślne wyjście dźwięku (wszystkie role) wg nazwy. Czeka do wait_s na pojawienie się urządzenia -
    dźwięk HDMI telewizora jest aktywny dopiero po włączeniu jego ekranu. Brak urządzenia -> LookupError."""
    from pycaw.constants import ERole
    from pycaw.utils import AudioUtilities
    deadline = time.monotonic() + wait_s
    while (dev_id := find(name, list_outputs())) is None:
        if time.monotonic() >= deadline:
            raise LookupError(f"audio output {name!r} not found")
        time.sleep(0.5)
    with _com():
        AudioUtilities.SetDefaultDevice(dev_id, [ERole[r] for r in ROLES])
