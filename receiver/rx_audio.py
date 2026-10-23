"""Receiver: Windows audio devices (pycaw): list the active outputs and set the default one.

pycaw / comtypes are imported lazily, only when used - without them the receiver still works, just without audio
switching. Every function initialises COM in the calling thread itself, so they can be used from any thread.
"""
import contextlib
import time

ROLES = ("eConsole", "eMultimedia", "eCommunications")   # same as "Set as default" in the Windows sound settings


@contextlib.contextmanager
def _com():
    """Context manager that initialises COM for the current thread and uninitialises it afterwards (only if we did the init)."""
    import comtypes
    try:
        comtypes.CoInitialize()
        owned = True
    except OSError:          # the thread already has COM in another mode (e.g. a Qt thread) - use the existing one
        owned = False
    try:
        yield
    finally:
        if owned:
            comtypes.CoUninitialize()


def list_outputs():
    """Active output devices as [(id, name)], in system order."""
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.utils import AudioUtilities
    with _com():
        devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        out = [(d.id, d.FriendlyName) for d in devices if d.FriendlyName]
        # release the COM objects BEFORE CoUninitialize: otherwise the list would live until the function returns, i.e. past
        # the exit from _com(), and a Release on an uninitialised COM (console-mode thread, a thread with no other COM
        # users) could crash the whole receiver
        del devices
    return out


def find(name, devices):
    """Find a device id by name: an exact case-insensitive match first, otherwise the only device whose name contains the text."""
    want = name.casefold()
    exact = [dev_id for dev_id, n in devices if n.casefold() == want]
    if exact:
        return exact[0]
    partial = [dev_id for dev_id, n in devices if want in n.casefold()]
    return partial[0] if len(partial) == 1 else None


def set_default(name, wait_s=6.0) -> None:
    """Set the default audio output (all roles) by device name. Polls the device list for up to wait_s until the device
    appears - a TV's HDMI audio only becomes active after its screen is switched on. Device not found -> LookupError."""
    from pycaw.constants import ERole
    from pycaw.utils import AudioUtilities
    deadline = time.monotonic() + wait_s
    while (dev_id := find(name, list_outputs())) is None:
        if time.monotonic() >= deadline:
            raise LookupError(f"audio output {name!r} not found")
        time.sleep(0.5)
    with _com():
        AudioUtilities.SetDefaultDevice(dev_id, [ERole[r] for r in ROLES])
